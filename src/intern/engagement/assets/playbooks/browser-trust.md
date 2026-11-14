---
name: browser-trust
title: Browser trust boundaries — CSRF, CORS, open redirect, clickjacking
triggers: Load for any app used from a browser with cookie-based sessions, any API that returns `Access-Control-Allow-*` headers, any `redirect`/`next`/`return_url` parameter, or sensitive one-click actions (change email/password, transfer, delete, role change).
attack: initial-access
owasp: A01, A02
payloads: CORS Misconfiguration; Clickjacking; Cross-Site Request Forgery; Open Redirect; Tabnabbing; XS-Leak; DOM Clobbering
---

These bugs abuse what the victim's browser will do on the attacker's behalf. Each one is proven with an attacker-origin page against your own test account — and its severity depends on the action it reaches.

## CSRF
- Targets: state-changing requests authenticated by cookies (not by a header the attacker can't set).
- Check: remove the CSRF token; send an empty/other user's token; switch `POST`→`GET`; switch `Content-Type` to `text/plain` or `application/x-www-form-urlencoded` (no preflight); check `SameSite` on the session cookie (`Lax` still allows top-level GET navigation; `None` allows everything).
- **Prove:** an HTML page on a different origin that auto-submits the request; load it in headless Chrome while logged in as test user A and show the change on A's account. Pick a meaningful action (email change > newsletter toggle).

## CORS
- Send `Origin: https://evil.example` and look at `Access-Control-Allow-Origin` + `Access-Control-Allow-Credentials: true`. Also test `null`, a lookalike (`target.com.evil.example`, `eviltarget.com`), and subdomains if any subdomain could be attacker-controlled.
- Reflected origin **with** credentials on an endpoint returning private data = cross-origin data theft. Wildcard `*` without credentials on public data = informational.
- **Prove:** a cross-origin `fetch(url, {credentials:"include"})` page that reads test user A's private response in headless Chrome.

## Open redirect
- Parameters: `redirect`, `next`, `url`, `return`, `continue`, `dest`, post-login and post-logout redirects, OAuth `redirect_uri`.
- Bypasses: `//evil.example`, `/\evil.example`, `https:evil.example`, `https://target.com@evil.example`, `https://evil.example#target.com`, encoded variants.
- On its own: low. **Chain:** OAuth code/token theft (→ `auth-session`), SSRF filters that follow redirects (→ `ssrf`), phishing credibility.

## Clickjacking
- Missing `X-Frame-Options` and CSP `frame-ancestors` on pages with sensitive one-click actions. Prove with a framing page; skip it for pages with no sensitive action (record as info).

## Record & validate
- CSRF/CORS → `state_update action=vuln owasp=[A01]` (misconfigured CORS policy: also `A02`); open redirect → `A01`; clickjacking → `A02`. Evidence = the attacker page source plus the screenshot/response showing the effect.
- Severity follows the action reached (account takeover vs preference change); record honestly.
- Tested clean → `action=endpoint owasp=[A01]` noting token/SameSite/CORS behaviour seen.
