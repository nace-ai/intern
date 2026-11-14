"""Hybrid person matching between directory editions: rules first, Drex Max for the uncertain middle, people for the rest.

Usage:
  python bench/hybrid_link.py eval              # score on the 100 sampled 1928-1930 pairs against the model jury
  python bench/hybrid_link.py run [--jobs 8]    # every <y1>-<y2>.pairs.jsonl in results/linking/editions

Stage 1, rules (free, deterministic):
  - no link: different "widow of", or first given names that disagree after expanding abbreviations
    and comparing sound codes, or no shared clue at all (no spouse, address or occupation in common)
  - link: same first given name (or same sound code, or one is an initial of the other) AND same spouse or same address,
    with no Jr/Sr disagreement
  - anything else goes to stage 2
Stage 2, Drex Max v1.1: p >= 0.75 link, p <= 0.25 no link, otherwise stage 3.
Stage 3, review: left for a family member to confirm.
Writes results/linking/hybrid/<y1>-<y2>.jsonl, one decision per pair.
"""
import argparse, json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import link_directories as L

ROOT = Path(__file__).resolve().parent.parent
ED = ROOT / "results/linking/editions"
OUT = ROOT / "results/linking/hybrid"
LINK, NOLINK, REVIEW = "link", "no_link", "review"
DREX_HI, DREX_LO = 0.75, 0.25

# Given-name abbreviations used in US city directories of the period.
ABBR = {
    "jno": "john", "jas": "james", "wm": "william", "chas": "charles", "geo": "george", "thos": "thomas",
    "saml": "samuel", "benj": "benjamin", "jos": "joseph", "edw": "edward", "edwd": "edward", "eliz": "elizabeth",
    "robt": "robert", "richd": "richard", "danl": "daniel", "fredk": "frederick", "alex": "alexander",
    "margt": "margaret", "hy": "henry", "hen": "henry", "nathl": "nathaniel", "jesse": "jesse", "isaac": "isaac",
    "wash": "washington", "zach": "zachariah", "theo": "theodore", "andw": "andrew", "archd": "archibald",
    "stephn": "stephen", "mich": "michael", "patk": "patrick", "lemi": "lemuel", "matt": "matthew",
}
SUFFIX = {"jr", "sr", "2d", "3d", "ii", "iii", "mrs", "miss", "dr", "rev"}

# Occupation abbreviations that name the same job (mined from Drex Max decisions, checked by hand).
# Job changes Drex also linked (cook/dom, lab/porter, clk/slsmn) are deliberately NOT here: they are different jobs.
OCC_ABBR = {
    "lndrs": "laundress", "slsmn": "salesman", "pntr": "painter", "bkpr": "bookkeeper", "bkkpr": "bookkeeper",
    "firemn": "fireman", "hlpr": "helper", "gro": "grocer", "lab": "laborer", "carp": "carpenter",
    "sten": "stenographer", "tchr": "teacher", "clk": "clerk", "chauf": "chauffeur", "mach": "machinist",
    "drsmkr": "dressmaker", "brklyr": "bricklayer", "plmbr": "plumber", "elec": "electrician", "electn": "electrician",
}

ONLY_NAME = "only the name matches"
ONLY_NAME_HI = 0.9  # Drex over-links these, so it must be very sure


def occ_word(e):
    ws = words(e.get("occupation"))
    return OCC_ABBR.get(ws[0], ws[0]) if ws else ""


def _load_drex_cache():
    cache = {}
    for f in list((ROOT / "results/linking/hybrid").glob("*.jsonl")) + [ROOT / "results/linking/hybrid-eval.jsonl"]:
        if f.exists():
            for line in open(f):
                r = json.loads(line)
                if r.get("p_same") is not None:
                    cache[r["pair_id"]] = (r["p_same"], r.get("change"))
    return cache


DREX_CACHE = _load_drex_cache()


def words(s):
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()


def soundex(w):
    if not w:
        return ""
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
             "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
    out, last = w[0], codes.get(w[0], "")
    for ch in w[1:]:
        c = codes.get(ch, "")
        if c and c != last:
            out += c
        if ch not in "hw":
            last = c
    return (out + "000")[:4]


def given(e):
    """(expanded first given name, set of other initials, jr flag)."""
    ws = [w for w in words(e.get("given_names")) if w not in SUFFIX]
    first = ABBR.get(ws[0], ws[0]) if ws else ""
    return first, {w[0] for w in ws[1:]}, "jr" in words(e.get("given_names"))


def first_token(s):
    ws = [ABBR.get(w, w) for w in words(s) if w not in SUFFIX]
    return ws[0] if ws else ""


def same_name(a, b):
    if not a or not b:
        return None  # unknown
    if a == b or soundex(a) == soundex(b):
        return True
    if len(a) == 1 or len(b) == 1:  # an initial
        return a[0] == b[0]
    return a.startswith(b) or b.startswith(a)  # "fred" / "frederick"


def rules(a, b):
    """Returns (decision or None, reason)."""
    ga, ia, jra = given(a)
    gb, ib, jrb = given(b)
    wa, wb = first_token(a.get("widow_of")), first_token(b.get("widow_of"))
    spouse = same_name(first_token(a.get("spouse")), first_token(b.get("spouse")))
    addr = bool(words(a.get("address"))) and words(a.get("address")) == words(b.get("address"))
    occ = bool(occ_word(a)) and occ_word(a) == occ_word(b)
    conflicts = [why for bad, why in [
        (bool(wa and wb and not same_name(wa, wb)), "different husband (widow of)"),
        (same_name(ga, gb) is False, "first names differ"),
        (bool(ia and ib and not (ia & ib)), "middle initials differ"),
        (spouse is False, "different spouse"),
    ] if bad]
    if conflicts:
        # A conflict is decisive only when nothing else ties the two entries together;
        # with the same address or spouse it may be a misprint or misreading, so a model looks at it.
        if spouse or addr:
            return None, conflicts[0] + ", but same " + ("spouse" if spouse else "address")
        return NOLINK, conflicts[0]
    if jra != jrb:
        return None, "Jr/Sr differs"
    if spouse or addr:
        return LINK, "same name and " + ("spouse" if spouse else "address")
    if occ:
        # mined rule: Drex Max linked 680 of 697 such pairs and rejected none; the 4-model jury agreed on all 7 in the check set
        return LINK, "same name and occupation"
    return None, ONLY_NAME


from functools import lru_cache
from link_editions import YEARS

IDENT_FOR_YEAR = {}
for ident, year in YEARS.items():
    IDENT_FOR_YEAR.setdefault(year, ident)


@lru_cache(maxsize=4096)
def page_entries(path):
    return json.loads(Path(path).read_text())["data"]["entries"]


def entry(eid):
    """Rebuild an entry from its id '<year>:<page stem>#<index>', using the same source link_editions chose."""
    y, rest = eid.split(":", 1)
    stem, k = rest.rsplit("#", 1)
    candidates = [ROOT / "results/directory-full" / y / f"{stem}.json"]
    ident = IDENT_FOR_YEAR.get(int(y))
    if ident:
        candidates += [ROOT / "results/directory-nace-url" / ident / f"{stem}.json",
                       ROOT / "results/directory-deepinfra/nvidia-nemotron-3.5-super-vl" / ident / f"{stem}.json"]
    for f in candidates:
        if f.exists():
            return page_entries(str(f))[int(k)]
    raise KeyError(eid)


def decide(key, pair, years):
    a_id, b_id = pair["pair_id"].split("|")
    a, b = entry(a_id), entry(b_id)
    d, why = rules(a, b)
    rec = {"pair_id": pair["pair_id"], "a": pair["a"], "b": pair["b"], "stage": "rules", "decision": d, "reason": why}
    if d is None:
        if pair["pair_id"] in DREX_CACHE:  # reuse an earlier Drex answer for the same pair
            p, change = DREX_CACHE[pair["pair_id"]]
        else:
            L.DREX_MODEL = "drex-max-v1.1"
            L.STATE = ("Two entries from Wilmington, North Carolina city directories, as printed.\n"
                       f"A ({years[0]}): {{a}}\nB ({years[1]}): {{b}}")
            r = L.ask_drex(key, pair)
            p, change = r.get("p_same"), r.get("change")
        rec.update(stage="drex", p_same=p, change=change)
        hi = ONLY_NAME_HI if why == ONLY_NAME else DREX_HI
        if p is None:
            rec.update(decision=REVIEW, reason="drex unavailable")
        else:
            rec["decision"] = LINK if p >= hi else NOLINK if p <= DREX_LO else REVIEW
            if rec["decision"] == REVIEW:
                rec["stage"] = "review"
    return rec


RULE_STRENGTH = {"same name and spouse": 1.0, "same name and address": 0.99, "same name and occupation": 0.95}


def strength(r):
    return RULE_STRENGTH.get(r["reason"], 0.9) if r["stage"] == "rules" else (r.get("p_same") or 0)


def one_to_one(recs):
    """Each entry links to at most one entry in the other year; the strongest candidate wins, the others become no_link."""
    used_a, used_b = set(), set()
    for r in sorted((r for r in recs if r["decision"] == LINK), key=strength, reverse=True):
        a_id, b_id = r["pair_id"].split("|")
        if a_id in used_a or b_id in used_b:
            r.update(decision=NOLINK, reason="a stronger link exists for this person", stage=r["stage"])
        else:
            used_a.add(a_id)
            used_b.add(b_id)
    return recs


def run(jobs):
    key = L.load_key()
    OUT.mkdir(parents=True, exist_ok=True)
    for pf in sorted(ED.glob("*.pairs.jsonl")):
        years = tuple(int(y) for y in pf.name.split(".")[0].split("-"))
        out = OUT / f"{years[0]}-{years[1]}.jsonl"
        pairs = [json.loads(l) for l in open(pf)]
        cached = sum(p["pair_id"] in DREX_CACHE for p in pairs)
        print(f"{years}: {len(pairs)} pairs ({cached} with an earlier Drex answer)", flush=True)
        with ThreadPoolExecutor(jobs) as ex:
            recs = list(ex.map(lambda p: decide(key, p, years), pairs))
        out.write_text("".join(json.dumps(r) + "\n" for r in recs))  # every decision, before the one-to-one limit
        final = one_to_one([dict(r) for r in recs])
        (OUT / f"{years[0]}-{years[1]}.final.jsonl").write_text("".join(json.dumps(r) + "\n" for r in final))
        tally = {}
        for r in final:
            k = (r["stage"], r["decision"])
            tally[k] = tally.get(k, 0) + 1
        print("  ", {f"{s}:{d}": n for (s, d), n in sorted(tally.items())}, flush=True)


def evaluate():
    key = L.load_key()
    S = json.loads((ROOT / "results/linking/label-sample.json").read_text())
    def load(n):
        return {r["id"]: r["p_same"] for r in map(json.loads, open(ROOT / f"results/linking/sample-{n}.jsonl")) if "p_same" in r}
    J = [load(n) for n in ["deepseek-v4-pro", "kimi-k3.1-pro", "claude-sonnet-5-5", "gemini-3.1-pro"]]
    with ThreadPoolExecutor(8) as ex:
        recs = list(ex.map(lambda s: {**decide(key, {**s}, (1928, 1930)), "id": s["id"]}, S))
    (ROOT / "results/linking/hybrid-eval.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    jury = {}
    for s in S:
        v = [j[s["id"]] >= 0.5 for j in J if s["id"] in j]
        if len(v) == 4 and (all(v) or not any(v)):
            jury[s["id"]] = v[0]
    by = {r["id"]: r for r in recs}
    stats = {}
    for i, truth in jury.items():
        r = by[i]
        st = stats.setdefault(r["stage"], {"n": 0, "right": 0})
        st["n"] += 1
        if r["decision"] != REVIEW:
            st["right"] += (r["decision"] == LINK) == truth
    print("on the", len(jury), "pairs the 4-model jury agrees on:")
    for k, v in stats.items():
        print(f"  {k:7s} {v['n']:3d} pairs" + ("" if k == "review" else f", {v['right']} decided right"))
    wrong = [by[i] for i in jury if by[i]["decision"] not in (REVIEW,) and (by[i]["decision"] == LINK) != jury[i]]
    for r in wrong:
        print("  WRONG", r["stage"], r["decision"], r.get("reason"), r.get("p_same"), "|", r["a"], "||", r["b"])
    all_stages = {}
    for r in recs:
        all_stages[r["stage"]] = all_stages.get(r["stage"], 0) + 1
    print("all 100 pairs by stage:", all_stages)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["eval", "run"])
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    evaluate() if a.step == "eval" else run(a.jobs)
