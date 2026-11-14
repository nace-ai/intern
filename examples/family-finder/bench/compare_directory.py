"""Score readers on the printed directory pages.

Usage: python bench/compare_directory.py results/directory/claude-sonnet results/directory/claude-haiku results/directory/nace-high
Writes results/directory/report.md.

No hand-made answer key exists, so accuracy is estimated two ways:
- OCR support: is the entry's name, and separately its address, found in the Internet Archive's own OCR
  text of the same page (an independent reading of the same print)?
- Majority check: entries are matched across readers by name; for each field, where the other readers
  agree with each other, does this reader agree with them too?
"""
import json, re, statistics, sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["given_names", "spouse", "colored_mark", "occupation", "employer", "residence_code", "address"]


def norm(s):
    s = str(s or "").lower().replace("½", " 1/2")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ]", " ", s)).strip()


def key(e):
    return norm(f"{e.get('surname')} {e.get('given_names')}")


def load(d):
    out = {}
    for p in Path(d).glob("*.json"):
        r = json.loads(p.read_text())
        r["entries"] = (r.get("data") or {}).get("entries") or []
        out[r["page_id"]] = r
    return out


def align(a, b):
    """Pairs (i, j) of entries matched by name, in printed order."""
    ka, kb = [key(e) for e in a], [key(e) for e in b]
    sm = SequenceMatcher(None, ka, kb, autojunk=False)
    pairs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs += list(zip(range(i1, i2), range(j1, j2)))
        elif tag == "replace":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if SequenceMatcher(None, ka[i], kb[j]).ratio() >= 0.8:
                    pairs.append((i, j))
    return pairs


def speed_cost(name, recs):
    walls = [r["wall_ms"] / 1000 for r in recs.values()]
    row = {"reader": name, "pages": len(recs), "median_s_per_page": round(statistics.median(walls), 1)}
    if "cli" in next(iter(recs.values())):
        row["cost_usd"] = round(sum((r.get("cli") or {}).get("total_cost_usd") or 0 for r in recs.values()), 2)
        row["cost_note"] = "API-equivalent list price incl. Claude Code overhead; billed to subscription"
    else:
        row["cost_usd"] = round(sum(float(((r["job"].get("cost") or {}).get("total_usd")) or 0) for r in recs.values()), 4)
        row["cost_note"] = "actual Nace charge"
    return row


def main():
    readers = {Path(d).name: load(d) for d in sys.argv[1:]}
    manifest = json.loads((ROOT / "data/directory/manifest.json").read_text())
    pages = [m["page_id"] for m in manifest if all(m["page_id"] in r for r in readers.values())]
    L = [f"# Printed directory: {len(pages)} pages, Hill's Wilmington NC 1928\n", "## Speed and cost\n"]
    for n, r in readers.items():
        L.append("- " + json.dumps(speed_cost(n, {p: r[p] for p in pages})))

    # OCR support
    L += ["\n## Entries, and support in the Internet Archive's OCR of the same page\n",
          "| reader | entries | name found in OCR | address found in OCR (entries with one) |", "|---|---|---|---|"]
    for n, r in readers.items():
        tot = name_ok = addr_n = addr_ok = 0
        for p in pages:
            ocr = norm((ROOT / f"data/directory/reference/{p}-ia-ocr.txt").read_text())
            for e in r[p]["entries"]:
                tot += 1
                name_ok += key(e) in ocr
                if norm(e.get("address")):
                    addr_n += 1
                    addr_ok += norm(e.get("address")) in ocr
        L.append(f"| {n} | {tot} | {name_ok} ({name_ok/max(tot,1):.0%}) | {addr_ok}/{addr_n} ({addr_ok/max(addr_n,1):.0%}) |")

    # majority check (needs 3 readers)
    names = list(readers)
    if len(names) >= 3:
        L += ["\n## Majority check: where the other two readers agree on a field, does this one?\n",
              "Entries are matched across readers by name. 'missed' = the other two matched an entry this reader doesn't have.\n",
              "| reader | " + " | ".join(FIELDS) + " | missed |", "|---|" + "---|" * (len(FIELDS) + 1)]
        for me in names:
            o1, o2 = [n for n in names if n != me][:2]
            agree = {f: [0, 0] for f in FIELDS}
            missed = 0
            for p in pages:
                A, B, C = readers[me][p]["entries"], readers[o1][p]["entries"], readers[o2][p]["entries"]
                bc = dict(align(B, C))
                b_to_a = {j: i for i, j in align(A, B)}
                for jb, jc in bc.items():
                    if jb not in b_to_a:
                        missed += 1
                        continue
                    a = A[b_to_a[jb]]
                    for f in FIELDS:
                        vb, vc = norm(B[jb].get(f)), norm(C[jc].get(f))
                        if vb == vc:
                            agree[f][1] += 1
                            agree[f][0] += norm(a.get(f)) == vb
            cells = [f"{a}/{t} ({a/max(t,1):.0%})" for a, t in agree.values()]
            L.append(f"| {me} | " + " | ".join(cells) + f" | {missed} |")

    (ROOT / "results/directory/report.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
