"""Compare systems on the overlap set.

Usage: python bench/compare.py results/claude-sonnet results/nace-high [...]
Writes results/report.md and results/review.json (rows for hand checking).

- Names: each system (and NARA's machine index, as a baseline) against the volunteer corrections, per line.
- Other fields: agreement between the first two systems; every disagreement plus a random
  sample of 50 agreeing rows goes to review.json for checking against the scan.
"""
import json, random, re, statistics, sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["relationship", "race", "sex", "age", "marital_status", "birthplace", "occupation"]


def norm(s):
    s = re.sub(r"[^a-z ]", " ", (s or "").lower())
    return " ".join(sorted(s.split()))


def name_score(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


SUFFIX = {"jr", "sr", "nd", "rd", "th", "ii", "iii"}


def parts(name):
    """(first name, surname) in written order, ignoring initials and suffixes like 'Jr' or '2nd'."""
    words = [w for w in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(w) > 1 and w not in SUFFIX]
    if not words:
        return "", ""
    return words[0], (words[-1] if len(words) > 1 else "")


def field_norm(f, v):
    v = (v or "").strip().lower().rstrip(".")
    if f == "marital_status":
        v = {"married": "mar", "never married": "nev", "widowed": "wd", "divorced": "d", "div": "d", "separated": "sep"}.get(v, v)
    if f == "sex":
        v = v[:1]
    if f == "race":
        v = {"white": "w", "negro": "neg"}.get(v, v)
    return re.sub(r"\s+", " ", v)


def load(dir_):
    out = {}
    for p in Path(dir_).glob("*.json"):
        r = json.loads(p.read_text())
        people = (r.get("data") or {}).get("people") or []
        r["by_line"] = {}
        for person in people:
            if isinstance(person.get("line"), int):
                r["by_line"].setdefault(person["line"], person)
        out[r["page_id"]] = r
    return out


def volunteer(m):
    vol = {}
    for t in (m["volunteer_names"] or []):
        if t.get("row"):
            vol.setdefault(t["row"], set()).add(t["name"])
    return vol


def align(systems, manifest):
    """Some readers number lines differently (Nace often reads line 1 as 2). For each system and page,
    pick the shift from -3 to +3 that best matches the volunteer names, applied to every system alike,
    and re-key by_line to the true line. Returns {system: {shift: pages}}."""
    shifts = {}
    for name, recs in systems.items():
        counts = {}
        for m in manifest:
            r = recs.get(m["page_id"])
            if not r:
                continue
            vol = volunteer(m)
            def score(k):
                s = 0.0
                for line, refs in vol.items():
                    p = r["by_line"].get(line + k)
                    if p:
                        s += max(name_score(p["full_name"], ref) for ref in refs)
                return s
            best = max(range(-3, 4), key=lambda k: (score(k), -abs(k)))
            r["by_line"] = {line - best: p for line, p in r["by_line"].items()}
            r["shift"] = best
            counts[best] = counts.get(best, 0) + 1
        shifts[name] = counts
    return shifts


def words(name):
    return {w for w in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(w) > 1 and w not in SUFFIX}


def speed_cost(name, recs):
    walls = [r["wall_ms"] / 1000 for r in recs.values()]
    row = {"system": name, "pages": len(recs),
           "median_wall_s": round(statistics.median(walls), 1), "max_wall_s": round(max(walls), 1)}
    if name.startswith("claude"):
        api = [r["cli"]["duration_api_ms"] / 1000 for r in recs.values() if r.get("cli")]
        cost = sum(r["cli"]["total_cost_usd"] or 0 for r in recs.values() if r.get("cli"))
        row.update(median_model_s=round(statistics.median(api), 1), cost_usd=round(cost, 2),
                   cost_note="API-equivalent list price; billed to subscription")
    elif not all("job" in r for r in recs.values()):
        row["cost_usd"] = round(sum(float(r.get("cost_usd") or 0) for r in recs.values()), 4)
        row["cost_note"] = "DeepInfra estimated_cost"
    else:
        tot = [((r.get("job_settled") or {}).get("timing") or r["job"].get("timing") or {}).get("total_ms") for r in recs.values()]
        tot = [t / 1000 for t in tot if t]
        if tot:
            row["median_model_s"] = round(statistics.median(tot), 1)
        costs = [((r.get("job_settled") or {}).get("cost") or r["job"].get("cost") or {}).get("total_usd") for r in recs.values()]
        row["cost_usd"] = round(sum(float(c) for c in costs if c), 4)
        row["cost_note"] = f"actual Nace charge; {sum(1 for c in costs if not c)} pages without a settled cost"
    return row


def main():
    dirs = sys.argv[1:]
    systems = {Path(d).name: load(d) for d in dirs}
    manifest = json.loads((ROOT / "data/reference/manifest.json").read_text())
    pages = [m for m in manifest if all(m["page_id"] in s for s in systems.values())]
    shifts = align(systems, pages)
    lines = [f"# Overlap set: {len(pages)} pages read by every system\n"]
    lines.append("Line-number shifts applied before scoring ({shift: pages}): " + json.dumps(shifts) + "\n")

    # speed and cost
    lines.append("## Speed and cost\n")
    for name, recs in systems.items():
        lines.append("- " + json.dumps(speed_cost(name, {p["page_id"]: recs[p["page_id"]] for p in pages})))

    # names against volunteer corrections
    lines.append("\n## Names against NARA volunteer corrections\n")
    lines.append("Volunteer corrections are themselves imperfect (they often drop middle initials), so 'exact' undercounts. "
                 "All name checks ignore word order (the machine index and Nace often write the surname first) and initials. "
                 "'first name': the volunteer's first name is among the reader's words. 'surname': the volunteer's surname is among "
                 "the reader's words, counted only where the reader gave two or more words "
                 "(a page that starts mid-household has the surname on the previous page).\n")
    lines.append("| reader | rows | exact | close (>=0.85) | first name | surname (rows with one) | missing line |")
    lines.append("|---|---|---|---|---|---|---|")
    readers = dict(systems)
    readers["nara-machine-index"] = None
    for name, recs in readers.items():
        n = exact = close = missing = first_ok = sur_ok = sur_n = 0
        for m in pages:
            vol = {}
            for t in (m["volunteer_names"] or []):
                if t.get("row"):
                    vol.setdefault(t["row"], set()).add(t["name"])
            for line, refs in vol.items():
                n += 1
                if recs is None:
                    cands = [x["name"] for x in (m["nara_machine_names"] or []) if x.get("row") == line]
                else:
                    p = recs[m["page_id"]]["by_line"].get(line)
                    cands = [p["full_name"]] if p else []
                if not cands:
                    missing += 1
                    continue
                best = max(name_score(c, r) for c in cands for r in refs)
                exact += best == 1.0
                close += best >= 0.85
                cw = set().union(*(words(c) for c in cands))
                first_ok += any(parts(r)[0] and parts(r)[0] in cw for r in refs)
                if len(cw) >= 2:
                    sur_n += 1
                    sur_ok += any(parts(r)[1] and parts(r)[1] in cw for r in refs)
        fn = f"{first_ok} ({first_ok/n:.0%})"
        sn = f"{sur_ok}/{sur_n} ({sur_ok/max(sur_n,1):.0%})"
        lines.append(f"| {name} | {n} | {exact} ({exact/n:.0%}) | {close} ({close/n:.0%}) | {fn} | {sn} | {missing} |")

    # field agreement between the first two systems
    if len(systems) >= 2:
        (a_name, a), (b_name, b) = list(systems.items())[:2]
        lines.append(f"\n## Field agreement: {a_name} vs {b_name}\n")
        lines.append("| field | rows both read | agree |")
        lines.append("|---|---|---|")
        review, agreed = [], []
        for f in FIELDS + ["full_name"]:
            n = agree = 0
            for m in pages:
                ra, rb = a[m["page_id"]]["by_line"], b[m["page_id"]]["by_line"]
                for line in sorted(set(ra) & set(rb)):
                    va, vb = ra[line].get(f), rb[line].get(f)
                    same = (name_score(va, vb) == 1.0) if f == "full_name" else field_norm(f, va) == field_norm(f, vb)
                    n += 1
                    agree += same
                    item = {"page_id": m["page_id"], "line": line, "field": f, a_name: va, b_name: vb}
                    (agreed if same else review).append(item)
            lines.append(f"| {f} | {n} | {agree} ({agree/max(n,1):.0%}) |")
        only_a = sum(len(set(a[m['page_id']]['by_line']) - set(b[m['page_id']]['by_line'])) for m in pages)
        only_b = sum(len(set(b[m['page_id']]['by_line']) - set(a[m['page_id']]['by_line'])) for m in pages)
        lines.append(f"\nLines only {a_name} read: {only_a}. Lines only {b_name} read: {only_b}.")
        random.seed(1950)
        sample = random.sample([x for x in agreed if x["field"] != "full_name"], min(50, len(agreed)))
        for x in sample:
            x["agreed"] = True
        (ROOT / "results/review.json").write_text(json.dumps(review + sample, indent=1))
        lines.append(f"\nFor hand checking: {len(review)} disagreements and {len(sample)} sampled agreements in results/review.json.")

    (ROOT / "results/report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
