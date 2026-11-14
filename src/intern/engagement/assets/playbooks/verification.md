---
name: verification
title: Independent verification — try to disprove every finding
triggers: Load as the verifier before the report, and whenever a HIGH/CRITICAL finding is confirmed. The goal is to break findings, not to defend them; a finding that survives this pass is one the client can trust.
---

The tester who found a bug is the worst judge of it. Take each finding cold, reproduce it from scratch, and look for the ordinary explanation before accepting the extraordinary one.

## 1. Pick the set
`state_query what=vulns`: every `confirmed` finding, every HIGH/CRITICAL regardless of status, and anything `decided_by=agent` at high confidence without drex triage. Work highest severity first.

## 2. Reproduce from scratch
- Read only the finding's title, host and evidence; rebuild the request yourself (fresh session, fresh tokens). Don't reuse the original tester's cookies, cached responses or tunnels unless the finding is about them.
- Re-run it now, live. If it no longer reproduces, find out why before calling it remediated: patched, credential rotated, rate-limited, IP-blocked, or the original was never real.

## 3. Attack the evidence
Ask of each one:
- **Whose data is it?** Public content, the tester's own account, or demo/seed data is not a breach. IDOR proof needs another principal's object.
- **Whose service answered?** Middlebox, captive portal, CDN cache, proxy on the tester's path (VPN, `*_PROXY`, `/etc/hosts`) — check the vantage (doctrine: same URL ≠ same backend). For network findings, run the dead-IP control.
- **Is it expected behaviour?** Documented feature, intended public endpoint, a 200 that is actually an error page or SPA fallback (compare body length/content with a nonexistent path).
- **Single shot or differential?** A finding without a control pair is still at `detected` — run the control now.
- **Is the screenshot what it claims?** Authenticated page vs login/OIDC redirect, correct host, timestamp present.

## 4. Check the severity against the demonstration
- Severity = what was **shown**: data actually read, action actually performed, access actually obtained. "Could lead to", "bypass possible", theoretical chains → downgrade.
- Compensating controls you observe (CSP blocking execution, `HttpOnly`, MFA on the reached account, egress filtering on the SSRF) reduce severity — record them.
- Missing chain: a HIGH that was never chained is fine only with a `stop_reason` that makes sense.

## 5. Record the verdict
- Still reproduces → `state_update action=recheck id=<vN> remediated=false notes=<what you re-ran, from where, when>`.
- Fixed during the test → `action=recheck remediated=true notes=<how you know it's fixed, not just failing>`.
- Doesn't hold → `state_update action=vuln id=<vN> status=false_positive` (or a lower `severity`) with the reason in `evidence`; never delete the history.
- Wrong or missing OWASP tags → fix them (`owasp=[...]`); coverage depends on them.
- Finish with `drex_triage` so the decision model re-checks the impact gate on what changed, then `state_query what=shallow` and report what still owes depth.
