"""Link people between consecutive Wilmington directory editions and let Drex decide each candidate pair.

Usage: python bench/link_editions.py [--jobs 24] [--min-pages 300] [--years 1928,1930]
Loads every edition read so far (Nace full, Nace by URL, DeepInfra Nemotron), builds candidate pairs between
consecutive years that have at least --min-pages pages read, and asks Drex about each pair.
Writes results/linking/editions/<y1>-<y2>.pairs.jsonl and .drex.jsonl; skips pairs already asked.
"""
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from link_directories import norm, printed, score, ask_drex
from run_nace import load_key
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results/linking/editions"
YEARS = {  # Internet Archive identifier -> edition year
    "hillswilmingtonn1963hill": 1963, "hillswilmingtonn1962hill": 1962, "hillswilmingtonn1961hill": 1961,
    "hillswilmingtonn1960hill": 1960, "hillswilmingtonn1959hill": 1959, "hillswilmingtonn1958hill": 1958,
    "hillswilmingtonn1956hill": 1956, "hillswilmingtonn1952hill": 1952, "hillswilmingtonn00hill": 1948,
    "hillswilmingtonn28hill": 1947, "hillswilmingtonn25hill": 1943, "hillswilmingtonn24hill": 1942,
    "hillswilmingtonn23hill": 1940, "hillswilmingtonn22hill": 1938, "hillswilmingtonn201934": 1934,
    "hillswilmingtonn1932": 1932, "hillswilmingtonn19hill": 1930, "hilldirectory1926wilm": 1926,
    "hilldirectory1924wilm": 1924, "hilldirectorycos1922hil": 1922, "wilmingtonncdire1919hill": 1919,
    "wilmingtonncdire1918hill": 1918, "wilmingtonncdire1917hill": 1917, "wilmingtonncdire1913hill": 1913,
    "wilmingtonncdire1911hill": 1911, "wilmingtonncdire1909hill": 1909, "wilmingtonncdire1907hill": 1907,
    "wilmingtonncdire1905hill": 1905, "jlhillprintingco1900jlhi": 1899, "jlhillprintingco1897rich": 1897,
    "directoryofcityo1889boni": 1889, "sheriffswilmingt7980sher": 1879, "sheriffswilmingt1877sher": 1877,
    "haddockswilmingt1871hadd": 1871, "smawswilmingtond1867smaw": 1867, "wilmingtondirect1865fult": 1865,
    "kelleyswilmingto1860kell": 1860,
}


def editions():
    """{year: (source, [entries])}, one source per year, preferring Nace full, then Nace URL, then Nemotron."""
    eds = {}
    for y in ("1928", "1930"):
        files = sorted((ROOT / "results/directory-full" / y).glob("*.json"))
        if files:
            eds[int(y)] = ("nace-full", files)
    for base, name in (("results/directory-nace-url", "nace-url"),
                       ("results/directory-deepinfra/nvidia-nemotron-3.5-super-vl", "nemotron")):
        for d in sorted((ROOT / base).glob("*")):
            y = YEARS.get(d.name)
            if y and y not in eds and any(d.glob("*.json")):
                eds[y] = (name, sorted(d.glob("*.json")))
    out = {}
    for y, (src, files) in eds.items():
        rows = []
        for f in files:
            r = json.loads(f.read_text())
            for k, e in enumerate((r.get("data") or {}).get("entries") or []):
                if isinstance(e, dict) and norm(e.get("surname")) and norm(e.get("given_names")):
                    rows.append({**e, "id": f"{y}:{f.stem}#{k}"})
        out[y] = (src, len(files), rows)
    return out


def pairs_for(a_rows, b_rows):
    by_s = {}
    for b in b_rows:
        by_s.setdefault(norm(b["surname"]), []).append(b)
    for a in a_rows:
        ga = norm(a["given_names"])
        cands = []
        for b in by_s.get(norm(a["surname"]), []):
            gb = norm(b["given_names"])
            if ga[:1] == gb[:1] and SequenceMatcher(None, ga, gb).ratio() >= 0.5:
                cands.append((score(a, b), b))
        for s, b in sorted(cands, key=lambda x: -x[0])[:3]:
            yield {"pair_id": f"{a['id']}|{b['id']}", "a": printed(a), "b": printed(b), "heuristic": round(s, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--min-pages", type=int, default=300)
    ap.add_argument("--years")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    eds = editions()
    ready = sorted(y for y, (_, n, _) in eds.items() if n >= a.min_pages)
    print({y: (eds[y][0], eds[y][1], len(eds[y][2])) for y in sorted(eds)}, flush=True)
    links = list(zip(ready, ready[1:]))
    if a.years:
        y1, y2 = map(int, a.years.split(","))
        links = [(y1, y2)]
    key = load_key()
    import link_directories
    for y1, y2 in links:
        link_directories.STATE = ("Two entries from Wilmington, North Carolina city directories, as printed.\n"
                                  f"A ({y1}): {{a}}\nB ({y2}): {{b}}")
        q = dict(link_directories.QUESTIONS)
        q["same_person"] = {**q["same_person"], "instructions": q["same_person"]["instructions"]
                            .replace("entry A (1928)", f"entry A ({y1})").replace("entry B (1930)", f"entry B ({y2})")
                            .replace("in two years", f"in {y2 - y1} years")}
        link_directories.QUESTIONS = q
        pairs = list(pairs_for(eds[y1][2], eds[y2][2]))
        ppath, dpath = OUT / f"{y1}-{y2}.pairs.jsonl", OUT / f"{y1}-{y2}.drex.jsonl"
        ppath.write_text("".join(json.dumps(p) + "\n" for p in pairs))
        done = {json.loads(l)["pair_id"] for l in open(dpath) if '"p_same"' in l} if dpath.exists() else set()
        todo = [p for p in pairs if p["pair_id"] not in done]
        print(f"{y1}->{y2}: {len(eds[y1][2])} x {len(eds[y2][2])} entries, {len(pairs)} pairs, {len(todo)} to ask", flush=True)
        with open(dpath, "a") as f, ThreadPoolExecutor(a.jobs) as ex:
            for i, rec in enumerate(ex.map(lambda p: ask_drex(key, p), todo)):
                f.write(json.dumps(rec) + "\n")
                if i % 2000 == 0:
                    print(f"  {y1}->{y2} {i}", rec.get("p_same"), rec.get("rtt_ms"), flush=True)


if __name__ == "__main__":
    main()
