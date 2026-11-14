---
name: misconfig-errors
title: Misconfiguration, exposed components, error handling and logging
triggers: Load on every web target during enumeration — headers, default pages, admin/debug/actuator endpoints, directory listing, verbose errors, exposed files (.git, .env, backups), component versions, and to assess error handling (fail-open) and logging/alerting.
attack: reconnaissance, discovery
owasp: A02, API8, A03, A09, A10
payloads: Insecure Management Interface; Reverse Proxy Misconfigurations; Request Smuggling; Web Cache Deception; Virtual Hosts; CRLF Injection
---

Configuration and error handling decide whether the app fails safe. Hunt for what was left on, what leaks, and what happens when things go wrong — then prove the consequence, not the presence.

## 1. Exposed management and debug surface (A02, API8)
- Spring Boot `/actuator` (`/env`, `/heapdump`, `/mappings`, `/configprops`), `/debug`, `/console`, `/_profiler`, `/phpinfo.php`, `/server-status`, `/metrics`, `/trace`, Django/Flask/Laravel debug pages, Kubernetes/Docker APIs, admin consoles (Tomcat manager, Jenkins, Grafana, Kibana) — with default creds where the product has them.
- Files: `/.git/` (HEAD, config → reconstruct source), `/.env`, `/.DS_Store`, `/backup.zip`, `*.bak`, `web.config`, `/.svn/`, `composer.json`, `package.json`, source maps (`*.js.map`).
- Directory listing, default install pages, sample apps.
- Cloud storage referenced by the app (S3/GCS/Azure blob URLs): anonymous list/read (→ `cloud-enum`).
- **Prove the consequence:** the secret in `/env`, the source from `.git`, the credentials in the heap dump (search it, don't publish it), the admin console logged in with default creds.

## 2. Headers and policy (A02)
- CSP, HSTS, `X-Content-Type-Options`, `X-Frame-Options`/`frame-ancestors`, `Referrer-Policy`, cookie flags, permissive CORS (→ `browser-trust`), `Server`/`X-Powered-By` disclosure.
- Missing headers are **info/low** unless they enable a demonstrated attack — batch them into one finding, don't pad the report.
- Walk the gateway/WAF policy by patterns when access is uniformly denied (doctrine: blanket 401/403 is a config, configs have holes).

## 3. Components (A03)
- Collect versions (headers, bundles, error pages, `/version`, `/health`, package files) → map advisories → test the trigger. Report only applicable ones with the concrete upgrade target.
- Third-party scripts loaded from CDNs without SRI on sensitive pages; dependency confusion signals (internal package names exposed in `package.json`).

## 4. Exceptional conditions (A10)
- Provoke errors: wrong types (`id=abc`, arrays where scalars are expected `id[]=1`), missing params, oversized values, malformed JSON/XML, unexpected methods, broken encodings.
- **Fail-open:** does an exception in an auth/authz/payment path grant access or skip a check? (e.g. invalid token type → treated as anonymous-admin; timeout in a fraud check → order approved). That's the high-impact version.
- **Leakage:** stack traces, SQL fragments, internal paths/hosts, framework versions in errors → feed back into recon and injection testing.

## 5. Logging & alerting (A09)
- Externally you mostly can't see logs. Assess what you can: were rate limits/lockouts or WAF blocks triggered during the test? Did anything visibly react (account notification, session revocation, IP block)? Is a log-viewer/monitoring UI exposed?
- Injection into logs (CRLF, log4j-style lookups where the stack is Java) → `server-injection`.
- If there is no way to observe logging, record `action=coverage owasp=[A09] coverage_status=na notes=<what you observed; requires operator-side log review>` and recommend the client-side check — don't invent a finding.

## Record & validate
- Each issue → `state_update action=vuln` with the matching id (`A02`/`API8`, `A03`, `A09`, `A10`); evidence = the response and what it gave you.
- Areas checked clean → `action=coverage` for those ids with what was checked.
