---
name: web-sqli
title: SQL injection
triggers: Load before testing any parameter that may reach a database — a search box, filter, id, sort/order field, JSON body, header or cookie that comes back as data; or when an error leaks SQL, a response changes on a quote, or timing shifts on a sleep payload.
attack: initial-access, collection
owasp: A05
payloads: SQL Injection; NoSQL Injection; ORM Leak
---

A disciplined SQLi pass: find every sink, confirm the injection with a *differential* (never a single payload), fingerprint the engine, then prove impact **read-only** — pull a bounded sample, never mutate.

## 1. Map the sinks
List every input that can reach the query layer, not just the obvious ones:
- URL query params, path segments, `POST` body fields, JSON values, GraphQL args.
- `Cookie`, `Authorization`, `X-Forwarded-For`, `Referer`, `User-Agent` — headers end up in logs/analytics queries surprisingly often.
- `ORDER BY` / `sort` / `direction` — these sit *outside* quotes and need column/expression payloads, not string breakouts.
Record each as an endpoint (`state_update action=endpoint`), tested or not, so coverage is visible.

## 2. Confirm by differential, never by one shot
A single payload "working" proves nothing — prove the *branch*:
- **Boolean:** send a TRUE and a FALSE condition and show the responses differ.
  `' AND 1=1-- -` vs `' AND 1=2-- -` (and the numeric/no-quote variants).
- **Time:** only when boolean has no visible signal. Pair a long and a zero sleep and show the latency gap; repeat to rule out jitter.
  `'; SELECT pg_sleep(5)-- -` / `' AND SLEEP(5)-- -` / `' WAITFOR DELAY '0:0:5'-- -` vs a `0`-second control.
- **Error:** if the app leaks DB errors, provoke one to read the engine/version from the message — fastest fingerprint when available.
Keep payloads bounded; a time-based sweep is slow and noisy — don't carpet-bomb.

## 3. Fingerprint the engine
Branch everything else on this. Quick tells:
- `version()` / `@@version` (MySQL/MSSQL), `version()` (Postgres), `banner FROM v$version` (Oracle), `sqlite_version()`.
- Comment styles (`-- -`, `#`, `/* */`), string concat (`||` vs `CONCAT`), stacked-query support (MSSQL/Postgres yes, MySQL usually no).
- Context: inside a string? numeric? `ORDER BY`? inside `LIKE`? Each needs a different breakout.

## 4. Enumerate, then prove — read-only
Escalate only as far as proving impact needs:
- UNION path (when column count + types align): `ORDER BY n` to find columns, then select `schema/table/column` names from the information schema, then a **bounded** sample (`LIMIT 20`) of one sensitive table.
- Blind path: extract just enough to prove access (DB name, current user, one row of a sensitive table) — not the whole database.
- **Never** `INSERT`/`UPDATE`/`DELETE`/`DROP`, never dump at scale, never stack a destructive query. Proving you *can* read `users` (schema + a few rows, secrets redacted in the report) is the finding; exfiltrating it is not.

## 5. WAF in the way?
If clean payloads get blocked but the sink clearly reflects: it's a WAF-pattern problem, not a dead end. Vary *form*, not intent — inline comments, case, encoding, alternate whitespace, equivalent functions. Note the bypass as part of the evidence (the exact payload that pierced it).

## 6. sqlmap (when available)
Point it at a captured request, scope it tight (`--batch`, specific `-p`, low `--level/--risk` first), read-only (`--technique`, no `--os-*`, no `--file-write`). If `sqlmap` isn't installed, the differential method above *is* the fallback — don't stall.

## Record & validate
- Confirmed injection → `state_update action=vuln` with the **differential** as evidence (the TRUE/FALSE or sleep/no-sleep pair, the engine, the reached data). Save the proof to a file and attach via `evidence`.
- Candidate without a working differential → `status=suspected`; let `drex_triage` judge it.
- Severity = what you *demonstrated* (which table/data you reached read-only), not "SQLi is usually critical". Hold ROE: bounded, read-only, no mutation.
