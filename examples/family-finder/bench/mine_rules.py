"""Propose new matching rules from Drex Max's decisions (read-only: nothing is changed).

Usage: python bench/mine_rules.py [--min-support 5] [--min-share 0.9]
Reads results/linking/hybrid/*.jsonl (pairs the rules could not settle, decided by Drex Max) and proposes:
  1. given-name variants: first names that differ but Drex confidently linked (e.g. jno <-> john)
  2. occupation synonyms: occupations that differ in confidently linked pairs (e.g. dom <-> cook)
  3. no-link signals: reasons the rules sent to Drex that Drex almost always rejected
A proposal needs --min-support confident decisions and --min-share of them pointing the same way.
Writes results/linking/proposed-rules.json and prints a summary.
"""
import argparse, json, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hybrid_link as H

ROOT = Path(__file__).resolve().parent.parent


def first_raw(e):
    ws = [w for w in H.words(e.get("given_names")) if w not in H.SUFFIX]
    return ws[0] if ws else ""


def occ(e):
    ws = H.words(e.get("occupation"))
    return " ".join(ws[:2]) if ws else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-support", type=int, default=5)
    ap.add_argument("--min-share", type=float, default=0.9)
    a = ap.parse_args()
    recs = [json.loads(l) for f in sorted((ROOT / "results/linking/hybrid").glob("*.jsonl")) for l in open(f)]
    drexed = [r for r in recs if r["stage"] in ("drex", "review") and r.get("p_same") is not None]
    print(f"{len(recs)} pairs decided, {len(drexed)} went to Drex Max")

    names = defaultdict(lambda: [0, 0])   # (a, b) -> [confident links, confident no-links]
    occs = defaultdict(lambda: [0, 0])
    reasons = defaultdict(lambda: [0, 0, 0])  # reason -> [links, no-links, review]
    for r in drexed:
        p = r["p_same"]
        conf_link, conf_no = p >= H.DREX_HI, p <= H.DREX_LO
        base = r["reason"].split(",")[0]
        reasons[base][0 if conf_link else 1 if conf_no else 2] += 1
        try:
            ea, eb = (H.entry(x) for x in r["pair_id"].split("|"))
        except (KeyError, IndexError):
            continue
        na, nb = first_raw(ea), first_raw(eb)
        if na and nb and na != nb and H.ABBR.get(na, na) != H.ABBR.get(nb, nb):
            k = tuple(sorted((na, nb)))
            if conf_link: names[k][0] += 1
            if conf_no: names[k][1] += 1
        oa, ob = occ(ea), occ(eb)
        if oa and ob and oa != ob:
            k = tuple(sorted((oa, ob)))
            if conf_link: occs[k][0] += 1
            if conf_no: occs[k][1] += 1

    def proposals(table, kind):
        out = []
        for (x, y), (yes, no) in table.items():
            n = yes + no
            if n >= a.min_support and yes / n >= a.min_share:
                out.append({"kind": kind, "a": x, "b": y, "links": yes, "no_links": no})
        return sorted(out, key=lambda d: -d["links"])

    name_props = proposals(names, "name_variant")
    occ_props = proposals(occs, "occupation_synonym")
    signal_props = []
    for why, (yes, no, rev) in reasons.items():
        n = yes + no + rev
        if n >= a.min_support * 4:
            share_no, share_yes = no / n, yes / n
            if share_no >= a.min_share:
                signal_props.append({"kind": "no_link_signal", "reason": why, "no_links": no, "links": yes, "review": rev})
            elif share_yes >= a.min_share:
                signal_props.append({"kind": "link_signal", "reason": why, "links": yes, "no_links": no, "review": rev})
    report = {"inputs": {"pairs": len(recs), "drex_decisions": len(drexed), "min_support": a.min_support, "min_share": a.min_share},
              "name_variants": name_props, "occupation_synonyms": occ_props, "signals": signal_props,
              "reasons_seen": {k: {"links": v[0], "no_links": v[1], "review": v[2]} for k, v in sorted(reasons.items(), key=lambda kv: -sum(kv[1]))}}
    (ROOT / "results/linking/proposed-rules.json").write_text(json.dumps(report, indent=1))

    print(f"\nname variants proposed: {len(name_props)}")
    for d in name_props[:25]:
        print(f"  {d['a']:>12} <-> {d['b']:<12} {d['links']} confident links, {d['no_links']} no-links")
    print(f"\noccupation synonyms proposed: {len(occ_props)}")
    for d in occ_props[:25]:
        print(f"  {d['a']:>14} <-> {d['b']:<14} {d['links']} links, {d['no_links']} no-links")
    print("\nwhy pairs reached Drex, and what Drex decided (links / no-links / review):")
    for k, v in list(report["reasons_seen"].items())[:10]:
        print(f"  {k:50s} {v['links']:5d} {v['no_links']:5d} {v['review']:5d}")
    print(f"\nsignals strong enough to become rules: {len(signal_props)}")
    for d in signal_props:
        print("  ", d)


if __name__ == "__main__":
    main()
