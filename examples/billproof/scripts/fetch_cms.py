"""Fetch official CMS open files and rebuild data/cms/catalog.json with real values.

Sources (all direct open downloads, no click-through):
  - Medicare NCCI MUE, Practitioner Services, 2026 Q4 (eff. 2026-10-01)
  - Medicaid NCCI PTP edits, Practitioner Services, 2026 Q4 (eff. 2026-10-01)
    (the Medicare PTP file sits behind an AMA licence page; Medicaid's does not)
  - Physician Fee Schedule Relative Value File RVU26D Oct-2026 (non-QPP, CF 33.4009)

Raw zips/txts land in data/raw/ (gitignored). Committed outputs:
  data/cms/{catalog.json, mue_subset.csv, ptp_subset.csv, pfs_subset.csv, SOURCES.md}

Fee provenance: codes priced on the PFS (status A/T/etc. with total RVU > 0) get real
national amounts = total_rvu x conversion factor. Codes the PFS doesn't price
(labs->CLFS, J drugs->ASP, ambulance->AFS, status-N non-covered) keep a provisional
estimate and are listed under sources.fee_provisional_codes.

Run:  cd billproof && PYTHONPATH=src .venv/bin/python scripts/fetch_cms.py
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PKG_ROOT / "data" / "raw"
CMS_DIR = PKG_ROOT / "data" / "cms"
CATALOG = CMS_DIR / "catalog.json"

UA = {"User-Agent": "billproof/0.1 (data fetch)"}

DOWNLOADS = {
    "mue_practitioner_2026q4.zip":
        "https://www.cms.gov/files/zip/medicare-ncci-2026-q4-practitioner-services-mue-table.zip",
    "medicaid_ptp_practitioner_2026q4.zip":
        "https://www.cms.gov/files/zip/medicaid-ncci-2026-q4-ptp-edits-practitioner-services.zip",
    "rvu26d.zip":
        "https://www.cms.gov/files/zip/rvu26d-updated-08-26-2026.zip",
}
MUE_CSV = "MCR_MUE_PractitionerServices_Eff_10-01-2026.csv"
PTP_TXT = "MCD-PTP-PractitionerServices-Effective_10012026.txt"
PFS_CSV = "PPRRVU2026_Oct_nonQPP.csv"


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  cached {dest.name} ({dest.stat().st_size:,} B)")
        return
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        f.write(r.read())
    print(f"    -> {dest.name} ({dest.stat().st_size:,} B)")


def unzip_member(zip_path: Path, member: str) -> Path:
    out = RAW_DIR / member
    if not out.exists():
        with zipfile.ZipFile(zip_path) as z:
            z.extract(member, RAW_DIR)
    return out


def parse_mue(path: Path, codes: set[str]) -> dict[str, tuple[int, int]]:
    mue = {}
    with open(path, encoding="cp1252", errors="replace") as f:
        for row in csv.reader(f):
            if len(row) >= 3 and row[0] in codes and row[1].strip().isdigit():
                mai = next((ch for ch in row[2] if ch.isdigit()), None)
                mue[row[0]] = (int(row[1]), int(mai) if mai else None)
    return mue


def parse_ptp(path: Path, codes: set[str]) -> dict[tuple[str, str], int]:
    pairs: dict[tuple[str, str], int] = {}
    with open(path, encoding="cp1252", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5 or p[0] not in codes or p[1] not in codes:
                continue
            if p[3].strip():  # non-empty DelDt = edit no longer active
                continue
            ind = p[4].strip()
            if ind in ("0", "1", "9"):
                pairs[(p[0], p[1])] = int(ind)
    return pairs


def parse_pfs(path: Path, codes: set[str]) -> dict[str, tuple[int, int]]:
    """Return {code: (non_facility_cents, facility_cents)} for codes the PFS prices."""
    with open(path, encoding="cp1252", errors="replace") as f:
        rows = list(csv.reader(f))
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == "HCPCS")
    base: dict[str, tuple[float, float]] = {}
    prof: dict[str, float] = {}
    for r in rows[hdr + 1:]:
        if len(r) < 26 or r[0] not in codes:
            continue
        code, mod = r[0], r[1]
        try:
            cf = float(r[25])
            nf_total, fac_total = float(r[11]), float(r[12])
        except ValueError:
            continue
        if mod == "" and code not in base:
            base[code] = (nf_total, fac_total, cf)
        elif mod == "26" and code not in prof:
            prof[code] = nf_total * cf
    out = {}
    for code, (nf, fac, cf) in base.items():
        if nf <= 0:
            continue
        nf_c = round(nf * cf * 100)
        fac_c = round(prof[code] * 100) if code in prof else round(fac * cf * 100)
        out[code] = (nf_c, fac_c)
    return out


def main() -> int:
    codes = sorted(json.loads(CATALOG.read_text())["codes"])
    code_set = set(codes)
    print(f"catalog: {len(codes)} codes")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in DOWNLOADS.items():
        fetch(url, RAW_DIR / name)

    mue_path = unzip_member(RAW_DIR / "mue_practitioner_2026q4.zip", MUE_CSV)
    ptp_path = unzip_member(RAW_DIR / "medicaid_ptp_practitioner_2026q4.zip", PTP_TXT)
    pfs_path = unzip_member(RAW_DIR / "rvu26d.zip", PFS_CSV)

    mue = parse_mue(mue_path, code_set)
    ptp = parse_ptp(ptp_path, code_set)
    pfs = parse_pfs(pfs_path, code_set)
    print(f"mue: {len(mue)}/{len(codes)} codes priced  ptp pairs: {len(ptp)}  pfs: {len(pfs)}")
    if len(mue) < len(codes) * 0.9:
        print("WARNING: MUE coverage suspiciously low", file=sys.stderr)

    cat = json.loads(CATALOG.read_text())
    provisional_fees = []
    for code in codes:
        e = cat["codes"][code]
        if code in mue:
            e["mue"], e["mai"] = mue[code]
        if code in pfs:
            e["fee_cents"], e["fee_facility_cents"] = pfs[code]
        else:
            provisional_fees.append(code)

    cat["ptp"] = [
        {"col1": a, "col2": b, "modifier_indicator": ind, "source": "medicaid-ncci-practitioner"}
        for (a, b), ind in sorted(ptp.items())
    ]
    cat["version"] = f"{date.today().isoformat()}-cms2026q4"
    cat["sources"] = {
        "fee_cents": "CMS PFS Relative Value File RVU26D Oct-2026 (non-QPP CF 33.4009), "
                     "national non-facility amount = non-facility total RVU x CF; "
                     "facility = facility total RVU x CF, or 26-modifier (professional) row for split codes",
        "fee_provisional_codes": provisional_fees,
        "fee_provisional_reason": "not priced on the PFS: labs (CLFS NLA), J drugs (ASP), "
                                "ambulance (AFS), status-N non-covered - estimated national amounts",
        "mue_mai": "CMS Medicare NCCI MUE Practitioner Services table, 2026 Q4 (eff. 2026-10-01)",
        "ptp": "Medicaid NCCI PTP edits Practitioner Services, 2026 Q4 "
               "(MCD-PTP-PractitionerServices-Effective_10012026.txt; active edits, empty DelDt)",
        "labels": "BillProof plain-language descriptions written for this project; "
                  "no AMA CPT descriptor text used",
        "detail": "data/cms/SOURCES.md",
    }
    CATALOG.write_text(json.dumps(cat, indent=2) + "\n")

    # committed subsets so a teammate can audit without the 178MB source files
    with open(CMS_DIR / "mue_subset.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hcpcs_code", "mue", "mai"])
        w.writerows((c, *mue.get(c, ("", ""))) for c in codes)
    with open(CMS_DIR / "ptp_subset.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["col1", "col2", "modifier_indicator"])
        w.writerows((a, b, i) for (a, b), i in sorted(ptp.items()))
    with open(CMS_DIR / "pfs_subset.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hcpcs_code", "non_facility_cents", "facility_cents", "source"])
        for c in codes:
            if c in pfs:
                w.writerow((c, *pfs[c], "pfs-rvu26d"))
            else:
                e = cat["codes"][c]
                w.writerow((c, e["fee_cents"], e["fee_facility_cents"], "provisional"))

    ind0 = sum(1 for v in ptp.values() if v == 0)
    mai = {1: 0, 2: 0, 3: 0, None: 0}
    for c in codes:
        mai[mue.get(c, (None, None))[1]] += 1
    print(f"done. ptp ind0={ind0} ind1={len(ptp)-ind0}  mai dist={mai}  provisional fees={len(provisional_fees)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
