"""Link people across directory years (1928 -> 1930) and let Drex decide each candidate pair.

Usage:
  python bench/link_directories.py pairs            # build candidate pairs -> results/linking/pairs.jsonl
  python bench/link_directories.py drex [--jobs 8]  # ask Drex about every pair -> results/linking/drex.jsonl

Candidates: for each 1928 entry, 1930 entries with the same surname whose given names are similar
(first initial equal and similarity >= 0.5), up to the 3 best by a simple score. Drex sees only the
two entries as printed, nothing else.
"""
import argparse, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_nace import load_key, call

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results/linking"
DREX_URL = "https://console.nace.ai/v1/systemone"
DREX_MODEL = "drex-v2.0"


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())).strip()


def load_year(year):
    rows = []
    for p in sorted((ROOT / "results/directory-full" / year).glob("*.json")):
        r = json.loads(p.read_text())
        for k, e in enumerate((r.get("data") or {}).get("entries") or []):
            if norm(e.get("surname")) and norm(e.get("given_names")):
                rows.append({**e, "id": f"{r['page_id']}#{k}", "year": year})
    return rows


def printed(e):
    """The entry rebuilt in directory style, so both judges see the same text."""
    paren = "; ".join(x for x in ["c" if e.get("colored_mark") else "", e.get("spouse") or "",
                                    f"wid {e['widow_of']}" if e.get("widow_of") else ""] if x)
    s = f"{e.get('surname')} {e.get('given_names')}" + (f" ({paren})" if paren else "")
    rest = ", ".join(x for x in [e.get("occupation"), e.get("employer")] if x)
    res = f"{e.get('residence_code') or ''} {e.get('address') or ''}".strip()
    return s + (f", {rest}" if rest else "") + (f" {res}" if res else "")


def score(a, b):
    g = SequenceMatcher(None, norm(a["given_names"]), norm(b["given_names"])).ratio()
    same_addr = norm(a.get("address")) and norm(a.get("address")) == norm(b.get("address"))
    same_sp = norm(a.get("spouse")) and norm(a.get("spouse")) == norm(b.get("spouse"))
    return g + 0.5 * bool(same_addr) + 0.3 * bool(same_sp)


def build_pairs():
    a_rows, b_rows = load_year("1928"), load_year("1930")
    by_surname = {}
    for b in b_rows:
        by_surname.setdefault(norm(b["surname"]), []).append(b)
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(OUT / "pairs.jsonl", "w") as f:
        for a in a_rows:
            ga = norm(a["given_names"])
            cands = []
            for b in by_surname.get(norm(a["surname"]), []):
                gb = norm(b["given_names"])
                if ga[:1] != gb[:1] or SequenceMatcher(None, ga, gb).ratio() < 0.5:
                    continue
                cands.append((score(a, b), b))
            for s, b in sorted(cands, key=lambda x: -x[0])[:3]:
                f.write(json.dumps({"pair_id": f"{a['id']}|{b['id']}", "a": printed(a), "b": printed(b),
                                    "a_raw": a, "b_raw": b, "heuristic": round(s, 3)}) + "\n")
                n += 1
    print(f"1928 entries {len(a_rows)}, 1930 entries {len(b_rows)}, candidate pairs {n}")


STATE = "Two entries from Hill's Wilmington, North Carolina city directory, as printed.\nA (1928): {a}\nB (1930): {b}"
QUESTIONS = {
    "same_person": {
        "type": "noul",
        "instructions": (
            "Are entry A (1928) and entry B (1930) the same person? People can move, change jobs, marry or be "
            "widowed in two years, and spellings and abbreviations vary between editions. Different people can "
            "share a name, especially father and son (Jr/Sr) or common names. Judge from all fields together."
        ),
    },
    "change": {
        "type": "choice",
        "criteria": {
            "same_address": "Same person, same address.",
            "moved": "Same person, different address.",
            "relative": "Different person, likely a relative (e.g. father/son, Jr/Sr, spouse).",
            "different": "Different, unrelated people.",
        },
    },
}


def ask_drex(key, pair):
    body = {"model": DREX_MODEL, "state": STATE.format(**pair), "questions": QUESTIONS}
    t0 = time.monotonic()
    st, r = call("POST", DREX_URL, key, body)
    rec = {"pair_id": pair["pair_id"], "http": st, "rtt_ms": round((time.monotonic() - t0) * 1000)}
    if st == 200:
        ans = r["answers"]
        rec.update(p_same=ans["same_person"]["noul"], change=ans["change"]["choice"],
                   change_probs=ans["change"].get("probabilities"), eval_ms=r.get("evaluation_time_ms"),
                   input_tokens=r["usage"]["input_tokens"], model=r.get("model"))
    else:
        rec["error"] = r
    return rec


def run_drex(jobs):
    key = load_key()
    pairs = [json.loads(l) for l in open(OUT / "pairs.jsonl")]
    done = set()
    path = OUT / "drex.jsonl"
    if path.exists():
        done = {json.loads(l)["pair_id"] for l in open(path) if '"p_same"' in l}
    todo = [p for p in pairs if p["pair_id"] not in done]
    print(f"{len(todo)} pairs to ask ({len(done)} already done)", flush=True)
    with open(path, "a") as f, ThreadPoolExecutor(jobs) as ex:
        for i, rec in enumerate(ex.map(lambda p: ask_drex(key, p), todo)):
            f.write(json.dumps(rec) + "\n")
            if i % 1000 == 0:
                print(i, rec.get("p_same"), rec.get("rtt_ms"), flush=True)


LLM_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
LLM_PROMPT = (STATE + "\n\n" + QUESTIONS["same_person"]["instructions"] +
              "\nReply with JSON only: {{\"p_same\": <probability 0 to 1 that they are the same person>, "
              "\"change\": \"same_address\" | \"moved\" | \"relative\" | \"different\"}}")


def deepinfra_key():
    for line in (ROOT / ".env.local").read_text().splitlines():
        if line.startswith("DEEPINFRA_API_KEY="):
            return line.split("=", 1)[1].strip()


def ask_llm(key, model, pair):
    import urllib.error, urllib.request
    body = {"model": model, "temperature": 0, "max_tokens": 4000, "reasoning_effort": "low",
            "messages": [{"role": "user", "content": LLM_PROMPT.format(**pair)}]}
    t0 = time.monotonic()
    rec = {"pair_id": pair["pair_id"]}
    for attempt in range(4):
        try:
            req = urllib.request.Request(LLM_URL, data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=120))
            text = r["choices"][0]["message"]["content"] or ""
            v = json.loads(text[text.find("{"): text.rfind("}") + 1])
            rec.update(p_same=float(v["p_same"]), change=v.get("change"), cost_usd=(r.get("usage") or {}).get("estimated_cost"),
                       input_tokens=(r.get("usage") or {}).get("prompt_tokens"))
            break
        except urllib.error.HTTPError as e:
            rec["error"] = e.code
            time.sleep(3 * (attempt + 1))
        except (ValueError, KeyError, TypeError) as e:
            rec["error"] = f"parse: {e}"
            time.sleep(1)
    rec["rtt_ms"] = round((time.monotonic() - t0) * 1000)
    return rec


def run_llm(model, jobs, only=None):
    key = deepinfra_key()
    pairs = [json.loads(l) for l in open(OUT / "pairs.jsonl")]
    if only:
        ids = {json.loads(l)["pair_id"] for l in open(only)}
        pairs = [p for p in pairs if p["pair_id"] in ids]
    path = OUT / f"llm-{model.split('/')[-1].lower()}.jsonl"
    done = {json.loads(l)["pair_id"] for l in open(path) if '"p_same"' in l} if path.exists() else set()
    todo = [p for p in pairs if p["pair_id"] not in done]
    print(f"{model}: {len(todo)} pairs to ask ({len(done)} done)", flush=True)
    with open(path, "a") as f, ThreadPoolExecutor(jobs) as ex:
        for i, rec in enumerate(ex.map(lambda p: ask_llm(key, model, p), todo)):
            f.write(json.dumps(rec) + "\n")
            if i % 1000 == 0:
                print(i, rec.get("p_same"), rec.get("rtt_ms"), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["pairs", "drex", "llm"])
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--model", default="deepseek-ai/DeepSeek-V4-Flash")
    ap.add_argument("--only", help="jsonl of pairs (pair_id) to restrict to")
    a = ap.parse_args()
    if a.step == "pairs":
        build_pairs()
    elif a.step == "drex":
        run_drex(a.jobs)
    else:
        run_llm(a.model, a.jobs, a.only)
