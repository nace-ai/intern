---
name: auth-session
title: Authentication and session management
triggers: Load whenever there is a login, registration, password reset, MFA, "remember me", SSO/OAuth/OIDC flow, API key or session cookie — i.e. on essentially every app. Pair with `jwt` when tokens are JWTs.
attack: initial-access, credential-access
owasp: A07, API2
payloads: Account Takeover; Brute Force Rate Limit; OAuth Misconfiguration; SAML Injection; Cross-Site Request Forgery; Insecure Randomness
---

Authentication fails in the flows around the password check: enumeration, reset, MFA, session lifecycle, SSO glue. Walk each flow end to end and prove takeover on your own test accounts. Use `browser_session action=login` (multi-step IdPs, `totp_secret` for MFA test seeds) to hold each legitimate identity; a takeover is proven by a `screenshot` of the victim's authenticated view under the attacker's session.

## 1. Login
- **Default/known creds** for the identified product (vendor docs, `admin:admin`-class pairs) — a handful, not a brute force.
- **User enumeration:** diff responses for valid vs invalid usernames — message, status, length, timing, and the same on registration ("email taken") and reset. Enumeration matters when combined with weak lockout.
- **Lockout / rate limit:** send ~10 wrong passwords to your own test account and observe: lockout, CAPTCHA, delay, or nothing. Never spray real users; record the measured limit as the finding.
- **Bypass:** SQL/NoSQL injection in login (`web-sqli`, `server-injection`), response tampering (`"success":false`→`true` honoured client-side only?), alternate auth endpoints (mobile/legacy API, `/api/v1/login` vs `/v2`) with weaker rules.

## 2. Password reset
- Token quality: length, randomness across several requests, reuse after use, expiry, binding to the account.
- Host header poisoning: `Host:` / `X-Forwarded-Host:` set to your domain — does the reset link point there? (Prove with your own account's email.)
- Parameter tampering: two emails (`email=a@x&email=b@y`, JSON array), changing the user id between steps, reset token in the response body or `Referer` leaks to third parties.

## 3. MFA
- Skip the step: request the post-MFA page/API directly after password success; replay the pre-MFA session elsewhere.
- Code handling: no rate limit on the code (measure on your account, stop after proving no limit), code reuse, code in the response, fallback/backup paths weaker than the primary.

## 4. Session lifecycle
- Cookie flags: `Secure`, `HttpOnly`, `SameSite`; scope (`Domain`, `Path`).
- Fixation: does the session id change at login? Set a known id pre-login and check.
- Logout / password change: is the old session/token still accepted afterwards? Is there server-side invalidation at all?
- Token entropy and predictability; long-lived tokens; tokens in URLs.

## 5. SSO / OAuth / OIDC
- `redirect_uri` validation: other paths, subdomains, open redirects on the allowed host (→ `browser-trust`) to steal codes/tokens.
- Missing/unchecked `state` (login CSRF), missing PKCE on public clients, `response_type=token` leaking tokens in URLs.
- Account linking by unverified email; ID-token signature/audience checks (→ `jwt`).

## Prove & chain
- **Impact = takeover:** log into test account B as A via the flaw, with a `screenshot` of B's authenticated view taken under the attacker's `browser_session`. Weaknesses without takeover (no lockout, enumeration) are real but medium/low — say what an attacker gains.
- **Chain:** a taken-over admin account → `access-control` from the admin vantage; captured tokens → API surface.

## Record & validate
- `state_update action=vuln owasp=[A07]` (API auth: add `API2`); evidence = the request sequence and the resulting session.
- Flows tested solid → `action=coverage owasp=[A07, API2] coverage_status=tested notes=<flows covered>`.
