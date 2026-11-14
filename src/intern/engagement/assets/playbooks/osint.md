---
name: osint
title: OSINT — public exposure of the target
triggers: Load at the start of an external engagement, in parallel with network recon, and whenever you find an organization name, domain, product name, developer handle or internal hostname worth searching for.
attack: reconnaissance
owasp: A02
payloads: API Key Leaks; Insecure Source Code Management; Methodology and Resources/Web Attack Surface
---

What the target has already published is often the shortest path in: subdomains nobody remembers, secrets in public repos, CI logs, leaked credentials. Public sources only; never contact, phish or social-engineer people.

## 1. Domains and hosts
- Certificate transparency: `curl -s "https://crt.sh/?q=%25.<domain>&output=json"` → unique names; also `subfinder`/`amass passive` if installed.
- Passive DNS and historical records where available; ASN / IP ranges owned by the org (`whois`, bgp.he.net).
- Feed every new name to recon (`state_update action=host`) and flag the ones resolving outside the CDN (origin exposure).

## 2. Code and secrets
- Search code hosts for the domain, internal hostnames, product names and unique strings from the JS bundles: GitHub/GitLab code search (`gh search code "<domain>"`), gists, paste sites.
- Look for: API keys, cloud keys (`AKIA…`, service-account JSON), connection strings, `.env` files, private keys, internal URLs, hard-coded passwords. Check commit history and deleted files, not just HEAD (`trufflehog`/`gitleaks` on cloned public repos, bounded).
- Employee/personal repos that mention the org often leak more than the org's own.

## 3. Exposed build and docs
- Public CI logs and artifacts (GitHub Actions logs of public repos, public GitLab pipelines, Travis/Circle history) → `ci-cd`.
- Public buckets and file shares referenced in code or docs → `cloud-enum`.
- Documents (PDF/DOCX on the website): metadata authors, usernames, software versions, internal paths (`exiftool`).
- Swagger/Postman collections published publicly (Postman public workspaces) → `api-surface`.

## 4. People and credentials (passive only)
- Email format from public pages and commits (`first.last@`), used to build username lists for authorized auth testing — not for phishing.
- Breach and infostealer exposure: `leak_check` the in-scope domains, then the accounts you discover (LeakRadar, Have I Been Pwned, LeakCheck, DeHashed, Snusbase, Intelligence X, Hudson Rock — whichever the operator configured; it refuses anything out of scope). LeakRadar results arrive locked: the exposure counts, leaked URLs and subdomains are free intelligence; `unlock=true` reveals credentials at 1 point each, after the operator confirms the cost. Stealer-log login URLs on the target become assets; leaked credentials are recorded unverified and show up as pivot opportunities — **ask the operator before trying any**, then one attempt per service. Never buy, download or use raw breach dumps outside these services.

## 5. Verify and record
- Every leaked secret: **one** read-only validity check (e.g. `aws sts get-caller-identity`, a GET with the API key), then record it — a dead key is info, a live one is a finding.
- `state_update action=vuln owasp=[A02]` (exposure of sensitive information); live credentials also as `action=cred source=<URL>`. Evidence = the public URL, captured with a timestamp (save the page — public things disappear).
- Chain: live credentials → the matching service (cloud, API, CI, VPN) and its role/playbook.
