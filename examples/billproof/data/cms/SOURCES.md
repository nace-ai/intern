# CMS data sources — data/cms/

All files are US federal government open data, direct public downloads, no sign-in
or licence click-through required. Retrieved **2026-11-13** by `scripts/fetch_cms.py`.
Reproduce: `cd billproof && PYTHONPATH=src .venv/bin/python scripts/fetch_cms.py`
(raw zips land in the gitignored `data/raw/`).

## 1. MUE (Medically Unlikely Edits) — Medicare, Practitioner Services

- URL: https://www.cms.gov/files/zip/medicare-ncci-2026-q4-practitioner-services-mue-table.zip
- Page: https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits-mues
- File: `MCR_MUE_PractitionerServices_Eff_10-01-2026.csv` (407,831 B zip, verified)
- Version: **2026 Q4**, effective dates of service **2026-10-01 … 2026-12-31**
- Columns: HCPCS/CPT Code · Practitioner Services MUE Values · MUE Adjudication Indicator
  (1 = claim-line edit, 2 = date-of-service policy edit, 3 = date-of-service clinical edit) · MUE Rationale
- Coverage: **all 138 catalog codes found** → all `mue`/`mai` values are REAL.
- MUE = 0 codes (97010, 99395, 99396): CMS published value 0 = never separately payable
  (bundled / non-covered) — kept as real.

## 2. NCCI PTP edits — Medicaid, Practitioner Services

- URL: https://www.cms.gov/files/zip/medicaid-ncci-2026-q4-ptp-edits-practitioner-services.zip
- Page: https://www.cms.gov/medicare/coding-billing/ncci-medicaid/medicaid-ncci-edit-files
- File: `MCD-PTP-PractitionerServices-Effective_10012026.txt` (67,815,070 B zip →
  178,688,678 B tab-delimited, ~2.5M edit pairs, verified)
- Version: **2026 Q4**, effective 2026-10-01 … 2026-12-31
- Columns: Col1 · Col2 · EffDt · DelDt · Modifier Indicator (0 not allowed / 1 allowed /
  9 not applicable) · PTP Edit Rationale. **Empty DelDt = active edit** (what we ship).
- The *Medicare* PTP tables sit behind an AMA licence click-page; the *Medicaid*
  practitioner file is a direct open download (same column-one/column-two semantics).
- Subset shipped: **1,033 active pairs** among our 138 codes (85 indicator-0, 948
  indicator-1) → all REAL, `source: "medicaid-ncci-practitioner"`.

## 3. Physician Fee Schedule — Relative Value File RVU26D (Oct-2026)

- URL: https://www.cms.gov/files/zip/rvu26d-updated-08-26-2026.zip
- Page: https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files
- File: `PPRRVU2026_Oct_nonQPP.csv` (6,181,360 B zip, verified)
- Version: **CY2026, October update (RVU26D)**; conversion factor **33.4009** (non-QPP,
  read from the file itself).
- Formula: `fee = total_rvu × 33.4009`; non-facility uses NON-FACILITY TOTAL,
  facility uses FACILITY TOTAL — or the `26`-modifier (professional component) row
  for split PC/TC codes (imaging, ECG). Rounded to cents.
- **REAL for 105 of 138 codes** (status A/T/B/etc. with total RVU > 0).

### Provisional fees (33 codes — NOT priced on the PFS)

`sources.fee_provisional_codes` in catalog.json lists them. They keep hand estimates:
clinical labs (paid under **CLFS**, status X in the RVU file — values approximate the
CLFS national limitation amounts), J-code drugs (**ASP** pricing), ambulance A0425/A0428
(**ambulance fee schedule**), and status-N non-covered preventive 99395/99396.
Treat these amounts as plausible national amounts, not adjudicated truth.

## Licence / use notes

- The source files embed the AMA CPT copyright preamble. CPT *codes* are used; we
  never ship AMA descriptor text — every `label` in catalog.json is our own
  plain-language wording written for BillProof.
- MUE values for some HCPCS codes are confidential and unpublished nationally; our
  catalog only uses codes present in the published file.
- Everything here is reference data for a synthetic-billing benchmark — not medical,
  coding, or billing advice.
