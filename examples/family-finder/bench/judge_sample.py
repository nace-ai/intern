"""Run DeepInfra judges on the 100 labeling pairs (results/linking/label-sample.json).

Usage: python bench/judge_sample.py MODEL [MODEL ...]
Writes results/linking/sample-<short model>.jsonl.
"""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import link_directories as L

ROOT = Path(__file__).resolve().parent.parent
L.STATE = "Two entries from Wilmington, North Carolina city directories, as printed.\nA (1928): {a}\nB (1930): {b}"
L.LLM_PROMPT = (L.STATE + "\n\n" + L.QUESTIONS["same_person"]["instructions"] +
                "\nReply with JSON only: {{\"p_same\": <probability 0 to 1 that they are the same person>, "
                "\"change\": \"same_address\" | \"moved\" | \"relative\" | \"different\"}}")
sample = json.loads((ROOT / "results/linking/label-sample.json").read_text())
key = L.deepinfra_key()
for model in sys.argv[1:]:
    out = ROOT / f"results/linking/sample-{model.split('/')[-1].lower()}.jsonl"
    with ThreadPoolExecutor(16) as ex:
        recs = list(ex.map(lambda p: {**L.ask_llm(key, model, p), "id": p["id"]}, sample))
    out.write_text("".join(json.dumps(r) + "\n" for r in recs))
    ok = [r for r in recs if "p_same" in r]
    print(model, len(ok), "answered; median rtt", sorted(r["rtt_ms"] for r in ok)[len(ok)//2] if ok else None,
          "cost", round(sum(float(r.get("cost_usd") or 0) for r in ok), 4))
