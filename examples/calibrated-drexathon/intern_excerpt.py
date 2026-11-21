"""Readable excerpt of a recorded Intern run (results/intern_run.jsonl) -> results/intern_excerpt.md.

    python intern_excerpt.py          # writes the markdown and prints it (rich) for the stage
"""
import json
from collections import Counter

from drexflaky.common import RESULTS

ev = [json.loads(l) for l in open(RESULTS / "intern_run.jsonl") if l.strip()]
start = next(e for e in ev if e["type"] == "run_start")
end = next(e for e in ev if e["type"] == "run_end")
dec = [e for e in ev if e["type"] == "decision"]
out = ["# Intern run (recorded)", "",
       f"- System 2 (agent, DeepInfra): `{start.get('system2', start.get('model', 'deepinfra/Qwen/Qwen3.8-27B'))}`",
       f"- System 1 (supervisor + guard): `{dec[0]['backend'] if dec else 'drex'}`; {end['system1']['asks']} asks, {end['system1']['failed']} failed",
       f"- Ended: **{end['reason']}** after {end['turns']} turns; System 1 checks by purpose: {dict(Counter(d['purpose'] for d in dec))}", "",
       "## Tool calls, in order", ""]
for e in ev:
    if e["type"] == "tool_call_start" and e["name"] == "bash":
        g = next((d for d in dec if d["purpose"] == "guard" and d.get("turn") == e["turn"]), None)
        s = next((d for d in dec if d["purpose"] == "supervise" and d.get("turn") == e["turn"]), None)
        cmd = e["arguments"]["command"].replace("cd /workspace/drex-flaky && ", "").replace(".venv/bin/python ", "")
        line = f"{e['turn']}. `{cmd}`"
        extras = []
        if g:
            extras.append(f"Drex guard check: {'ran' if g['ok'] else 'failed'} ({g['duration_ms']} ms); call ran")
        if s and s.get("answers"):
            m = s["answers"].get("mode", {})
            extras.append(f"supervisor: {m.get('choice')} (p={m.get('probabilities', {}).get(m.get('choice'), 0):.2f})")
        out.append(line + ("  \n   " + " · ".join(extras) if extras else ""))
out += ["", "## Final report from the agent", "", end.get("final_text", "")]
md = "\n".join(out)
(RESULTS / "intern_excerpt.md").write_text(md)
try:
    from rich.console import Console
    from rich.markdown import Markdown
    Console().print(Markdown(md))
except ImportError:
    print(md)
