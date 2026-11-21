"""Tool CLI that Intern (Nace's agent harness) drives, one step per command.

    python triage.py list                      # pending failures (no labels shown)
    python triage.py extract case_007          # NDI fields (real NDI if NACE_API_KEY, else local regex)
    python triage.py decide case_007           # Drex P(flaky) for that failure (live)
    python triage.py act case_007 0.07         # policy action from current thresholds
    python triage.py feedback case_007 real    # record a human outcome; refits thresholds on all feedback so far
    python triage.py thresholds                # current thresholds
Every call appends one JSON line to results/intern_actions.jsonl.
"""
from __future__ import annotations

import json
import os
import sys
import time

from drexflaky.common import DEFAULT_HI, DEFAULT_LO, LOGS, RESULTS, has_key, load_costs, load_env, load_labels

STATE = RESULTS / "intern_thresholds.json"
FEEDBACK = RESULTS / "intern_feedback.json"
JOURNAL = RESULTS / "intern_actions.jsonl"


def _log(rec):
    rec["ts"] = time.time()
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=1))


def thresholds():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"lo": DEFAULT_LO, "hi": DEFAULT_HI}


def main(argv):
    load_env()
    cmd = argv[0] if argv else "help"
    if cmd == "list":
        print("\n".join(sorted(p.stem for p in LOGS.glob("*.log"))))
    elif cmd == "extract":
        cid = argv[1]; path = LOGS / f"{cid}.log"
        from ndi_extract import local_extract, ndi_extract
        if has_key("NACE_API_KEY"):
            from nace_sdk import NaceClient
            try:
                with NaceClient() as c:
                    out = ndi_extract(c, path)
                _log({"tool": "extract", "case_id": cid, "path": "ndi", "fields": out["fields"], "ndi_cost_usd": out["cost"]}); return
            except Exception as e:  # noqa: BLE001
                reason = f"{type(e).__name__}"
        else:
            reason = "no NACE_API_KEY"
        _log({"tool": "extract", "case_id": cid, "path": "local_regex", "fallback_reason": reason, "fields": local_extract(path.read_text())})
    elif cmd == "decide":
        cid = argv[1]; text = (LOGS / f"{cid}.log").read_text()
        from ndi_extract import local_extract
        fields = None
        if JOURNAL.exists():
            for line in JOURNAL.read_text().splitlines()[::-1]:
                r = json.loads(line)
                if r.get("tool") == "extract" and r.get("case_id") == cid:
                    fields = r["fields"]; break
        fields = fields or local_extract(text)
        if not has_key("NACE_API_KEY"):
            from drex_decide import mock_probability
            _log({"tool": "decide", "case_id": cid, "p_flaky": mock_probability(cid, fields, text), "MOCK": True}); return
        import requests
        from drex_decide import BASE, CLIENT_ID, PREFERRED_MODEL, build_state, call_drex
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {os.environ['NACE_API_KEY'].strip()}", "X-Nace-Client": CLIENT_ID})
        out = call_drex(s, PREFERRED_MODEL, build_state(fields, text))
        _log({"tool": "decide", "case_id": cid, "p_flaky": out["p_flaky"], "model": out["model"], "request_id": out["request_id"],
              "latency_ms": out["latency_ms"]})
    elif cmd == "act":
        from policy import decide, reason
        cid, p = argv[1], float(argv[2]); t = thresholds()
        _log({"tool": "act", "case_id": cid, "p_flaky": p, "action": decide(p, t["lo"], t["hi"]), "why": reason(p, t["lo"], t["hi"]), **t})
    elif cmd == "feedback":
        from learn import fit_thresholds
        cid, truth = argv[1], argv[2].lower()
        assert truth in ("flaky", "real"), "truth must be flaky|real"
        fb = json.loads(FEEDBACK.read_text()) if FEEDBACK.exists() else {}
        p = None
        for line in JOURNAL.read_text().splitlines()[::-1]:
            r = json.loads(line)
            if r.get("tool") == "decide" and r.get("case_id") == cid:
                p = r["p_flaky"]; break
        if p is None:
            print("decide this case first"); sys.exit(2)
        fb[cid] = {"p_flaky": p, "truth": truth}
        FEEDBACK.write_text(json.dumps(fb, indent=1))
        t0 = thresholds()
        lo, hi, c = fit_thresholds([v["p_flaky"] for v in fb.values()], [v["truth"] for v in fb.values()], load_costs(), t0["lo"], t0["hi"])
        STATE.write_text(json.dumps({"lo": lo, "hi": hi}))
        _log({"tool": "feedback", "case_id": cid, "truth": truth, "n_feedback": len(fb), "thresholds_before": t0,
              "thresholds_after": {"lo": lo, "hi": hi}, "feedback_cost": c})
    elif cmd == "thresholds":
        print(json.dumps(thresholds()))
    elif cmd == "truth":  # the "human" in the scripted Intern run: reveals a label from labels.csv
        cid = argv[1]
        print(next(r["truth"] for r in load_labels() if r["case_id"] == cid))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
