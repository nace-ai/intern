"""Retest: after the client fixes the reported findings, verify each one again — the same proof
from the same position, plus the obvious bypasses of the fix — and record fixed / not fixed /
partially fixed with fresh evidence. The report gains a remediation-verification section.
"""

from __future__ import annotations

from intern.engagement.models import Vuln
from intern.engagement.store import EngagementStore


def _line(v: Vuln) -> str:
    port = f":{v.service_port}" if v.service_port else ""
    return f"{v.id} [{v.severity}] {v.title} — {v.host}{port}"


def retest_kickoff(store: EngagementStore) -> str:
    """Enter retest mode and return what the agent is asked to do."""
    store.start_retest()
    todo = [v for v in store.state.vulns if v.status == "confirmed"]
    return "\n".join(
        [
            f"# Retest of {store.state.target}",
            "",
            "The client reports these findings as fixed. Verify each one; don't start new testing outside them.",
            *(_line(v) for v in todo),
            "",
            "For each finding:",
            "1. Re-run the original proof from the same position: same role or account, same network "
            "vantage, same request (state_query what=vulns shows the recorded evidence and request).",
            "2. If it no longer works, try the obvious variations of the same weakness: other "
            "parameters or endpoints of the same kind, other roles, encodings. A fix that only blocks "
            "the exact original request is partial.",
            "3. Record the verdict: state_update action=retest id=<vN> retest_result=fixed|not_fixed|"
            "partial notes=<what you observed>.",
            "4. Capture proof of the verdict with evidence linked to the finding. A fix needs proof "
            "too: the request that is now refused.",
            "If the fix introduced a new problem, record it as a new finding. Then run the report "
            "tool: the report gains a remediation-verification section covering every verdict.",
        ]
    )
