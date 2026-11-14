# Engagement

The engagement harness is intern's pentest layer: one shared, file-backed record of an
authorized engagement, a
decision-gate layer over findings (drex — the session's System 1), evidence and report pipelines,
research tooling, and specialist agents — behind one capability, off by default. Turn it on with
`/caps`, `/engage <host>`, or `[capabilities.engagement] enabled = true`; the headless runner
turns it on itself (`intern engage`).

This is a harness for **authorized, operator-directed** engagements. You are responsible for the
authorization of every target you name; the agent tests what the operator asks for and records
everything it tried — including what it found solid.

## The record

Everything one engagement learns lives in one folder under `out/` in the working directory:

| Path | What |
|---|---|
| `out/<target>/state.json` | hosts, services, findings, credentials, endpoints, footholds, coverage, pivots, events |
| `out/<target>/report/` | `report.md`, `report.html`, `report.pdf`, `figures/` — the only folder made to be handed over |
| `out/<target>/evidence/` | proof files: screenshots, transcripts, sanitized HARs, loot; `raw/` holds the bytes behind terminal captures |
| `out/<target>/tools/` | what tools saved: payloads wordlists, exploit sources, OAST sessions, JS intel |
| `out/<target>/private/` | owner-only (0700): browser logins, the OAST key |
| `out/<target>/work/` | the agent's own files; its shell commands run here |
| `out/<target>/logs/` | `events.jsonl`, `summary.json`, `findings.md`, specialists' transcripts |
| `out/<target>/history/` | earlier runs on the same object, one folder each |

A **new run on the same target** keeps the durable knowledge (hosts, credentials, footholds) and
starts with a clean board of findings; the prior run moves to `history/`, so a delivered report is
never overwritten. `resume=true` (or `--resume`) loads the prior findings back onto the board.

## The tools

State and discipline:

- `set_engagement`, `state_update`, `state_query` — name the target, record everything (hosts,
  findings, creds, tested endpoints, footholds, coverage), and ask the record where you stand.
  Every finding climbs a ladder `detected → confirmed → impact → chained`; a finding that stops
  early needs a stated `stop_reason` and its kind, and `state_query what=shallow` lists the ones
  that still owe depth.
- `drex_triage`, `drex_decide` — the decision gates, on the session's System 1. Triage scores
  every unvalidated finding twice: p(real) ≥ 0.85 confirms, ≤ 0.30 drops as a false positive, the
  middle is left to you; a HIGH/CRITICAL additionally needs p(impact demonstrated at the claimed
  severity) ≥ 0.60 or it is held at `suspected` — and an already-confirmed one whose evidence no
  longer carries the severity is downgraded. Decide answers a single branch question (go deeper?
  which vector? middleman artifact?) with p(yes) — yes ≥ 0.6, no ≤ 0.4.
- Coverage accounting — findings and endpoints are tagged against the OWASP Top 10:2025
  (A01–A10), the API Security Top 10:2023 (API1–API10) and, once an AI feature is tagged, the LLM
  Top 10:2025 (LLM01–LLM10); every category ends the engagement as a finding, tested, or n/a with
  a reason. Untested categories are gaps the report shows.
- CVSS v4.0 — every confirmed finding carries a vector, scored with FIRST's reference algorithm;
  a severity more than one step from the CVSS band needs its rationale written down.

Evidence and reporting:

- `evidence` — attach proof (a dump, a response, loot) linked to the finding it proves; the
  report lists unlinked proof only in its appendix.
- `screenshot` — picture the proof: a live page as a named identity (`mode=page`, auth injected,
  proving text highlighted, secrets masked, login pages refused unless allowed) or a real terminal
  capture (`mode=terminal`: the harness runs the command in a PTY and renders the exact output —
  output is never typed in by the agent). Optional HAR capture alongside, sanitized.
- `browser_session` — named browser identities: real form login (multi-step, TOTP), or imported
  cookies/tokens; every later capture replays them.
- `report_content`, `report_diagram`, `report` — fill the narrative, draw architecture/data-flow
  figures (self-contained SVG), and generate `report.md` + a self-contained `report.html` (+ PDF
  when a Chromium browser is installed). The report tool lists what is still missing — missing
  narrative, findings without impact, unlinked evidence, coverage gaps — and never errors.

Research:

- `load_playbook` + `payloads` — 28 on-demand methodologies (access-control, web-sqli, ssrf,
  active-directory, cloud-enum, llm-apps, …) and the bundled PayloadsAllTheThings snapshot:
  sectioned reading, search, and wordlists written to disk.
- `exploit_research` — a fingerprint (product/version, CPE, package, or every version on the
  record) to the CVEs that apply: NVD, OSV, CISA KEV, Exploit-DB, Sploitus, GitHub PoCs, local
  nuclei/Metasploit, EPSS — ranked by what an attacker cares about. `get` saves exploit source
  for review; third-party exploit code needs operator approval before it runs.
- `oast` — a native interactsh client for blind bugs: unique tagged callback domains per vector,
  polling, interactions kept as evidence. Self-hosted server via `INTERACTSH_SERVER`.
- `js_analyze` — bundles and source maps to routes, internal hostnames, GraphQL/WebSocket
  endpoints and secrets.
- `api_discover` + `api_probe` — the real API inventory (crawl as each identity, OpenAPI, HAR)
  and a method × identity matrix whose anomalies (anonymous access where users are refused, admin
  routes answering non-admins, advertised write methods) are hypotheses to confirm.
- `host_tools` / `install_tools` — what's installed on this machine and the fallbacks; installs
  only with your explicit `confirmed=true`.
- `dispatch` — up to 8 specialist agents running concurrently on the same record (recon, webapp,
  api, infra, identity, cloud, container, cicd, mobile, postex, exploit, verifier, generic), each
  loading its own playbooks; the verifier re-tests every drex call live and only recommends — the
  main agent's `state_update action=review` is the final word.

## The operator surface

- `/engage <host> [objective…]` — name the target (turns the capability on); the board line above
  the prompt follows the engagement live.
- `/dash` — the full dashboard: findings, hosts, endpoints, specialists, activity, coverage,
  credentials (secrets masked), footholds and open pivots; `←→` or `1-8` picks a section, `q`
  closes.
- `/retest` — verify the client's fixes for the original findings; verdicts land in a
  remediation-verification report section.
- `intern engage <target>` — the unattended run: a supervisor drives the agent round by round on
  what the record says is unfinished (untested categories, shallow findings, open pivots), wraps
  up at 85% of the budget (proof capture, live re-check, report), and exits `0` all gates passed ·
  `1` error · `2` stopped with work open · `3` a confirmed finding at/above `--fail-on`. Progress
  on stderr, `logs/summary.json` and `logs/events.jsonl` in the record. `INTERN_NOTIFY_URL`
  (Slack/Mattermost/Discord webhook) and desktop notifications say when it needs you or ends;
  `INTERN_NOTIFY=0` silences them.

The capability declares one install: the **browser** (`playwright` plus its Chromium build), which
the evidence pipeline drives for page screenshots, browser logins and the report PDF. `/caps` (and
`/engage` with the capability off) offers to install it before switching on; a client that cannot
install says why. `cryptography` (the OAST client) and `pyyaml` (OpenAPI YAML) are declared
dependencies, so `oast` and `api_discover` work on any install.

## What it includes, and what it does not

Included: the engagement record and folder layout, the drex decision gates (on intern's
System 1), the drill-down ladder, OWASP/API/LLM coverage, CVSS v4.0,
evidence/screenshots/browser sessions, the report pipeline, playbooks + payloads, exploit
research, OAST, JS/API inventory, host tooling, specialist dispatch, notifications, the headless
supervisor, retest, the board/dashboard, and the startup banner (plain text, so it reads the same
on the terminal and inside the TUI's transcript). The tracker tunes still ship —
the ProTracker renderer and the eight bundled modules live in `intern.engagement.music` — but
nothing plays them: the banner is silent by request. `INTERN_NO_INTRO=1` skips the banner.

Not included: MITRE ATT&CK coverage, a web portal, leak intelligence, second opinions from
another model, a sandbox, secret masking, an approval taxonomy, residential-proxy and SOCKS
routing, Burp Suite integration, and slash-argument completion.
