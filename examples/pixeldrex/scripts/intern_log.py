"""Read Intern's JSON events from stdin: show tool calls and System 1 (Drex) activity live, print the final reply."""

import json
import sys

final, summary = "", {}
for line in sys.stdin:
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = e.get("type", "")
    if t == "tool_call_start":
        args = json.dumps(e.get("arguments") or {})
        print(f"  [turn {e.get('turn')}] {e.get('name')} {args[:150]}", file=sys.stderr, flush=True)
    elif t == "tool_guard":
        print(f"  [guard] {e.get('name')}: {e.get('rule')} - {e.get('reason', '')[:120]}", file=sys.stderr, flush=True)
    elif t == "nudge":
        print(f"  [system1 nudge] {e.get('reason')}: {e.get('text', '')[:120]}", file=sys.stderr, flush=True)
    elif t == "decision" and e.get("purpose"):
        summary[e["purpose"]] = summary.get(e["purpose"], 0) + 1
    elif t == "run_end":
        final = e.get("final_text", "")
        s1 = e.get("system1") or {}
        print(
            f"  [run_end] {e.get('reason')} after {e.get('turns')} turns, {e.get('duration_ms', 0) / 1000:.0f}s; "
            f"System 1 (Drex) asks: {s1.get('asks')} ({', '.join(f'{k}×{v}' for k, v in summary.items())}), "
            f"${s1.get('cost_usd', 0):.4f}",
            file=sys.stderr,
            flush=True,
        )
print(final)
