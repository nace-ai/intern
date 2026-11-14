---
name: jwt
title: JWT authentication attacks
triggers: Load when auth rides on a JSON Web Token — an `Authorization: Bearer eyJ...`, a cookie or storage value with three base64url parts, or any session that decodes to header.payload.signature; especially when testing auth bypass, privilege escalation via claims, or token forgery.
attack: initial-access, credential-access
owasp: A07, API2, A04
payloads: JSON Web Token
---

JWTs fail at the *verification* step, not the crypto. Decode first, then attack the server's trust assumptions in order of impact. Every forged token must be proven against a protected endpoint — a token that "should work" is not a finding until the server honours it.

## 1. Decode and read the header
Base64url-decode header and payload (no verification needed to read them). Note:
- `alg` — the signing algorithm the token *claims*.
- `kid`, `jku`, `jwk`, `x5u` — key-selection fields. Each is an attack surface if the server trusts attacker input here.
- Payload claims — `sub`, `role`/`scope`/`admin`, `iss`, `aud`, `exp`, `iat`. Mark anything authorization depends on.

## 2. `alg: none`
Set `alg` to `none` (and case variants `None`/`NONE`), drop the signature (keep the trailing dot), tamper a claim (e.g. bump `role` to admin), send it. If the server accepts it → signature verification is off entirely. Prove it on a protected endpoint, not the login page.

## 3. Algorithm confusion (RS256 → HS256)
If the token is RS256 (asymmetric) and the server's **public** key is obtainable (JWKS endpoint, TLS cert, docs, a `jwk` in a prior token): re-sign a tampered token with `HS256` using that public key *as the HMAC secret*. A server that verifies with a single "verify(key)" call treats the public key as the shared secret → forgery. Confirm against a protected resource.

## 4. Weak HMAC secret
For HS256 tokens, the whole security is one secret. Try an **offline, bounded** dictionary against the signature (a wordlist of common/weak secrets — not an online brute). A crack means total forgery; stop at the first hit and prove one forged token. Don't run an unbounded keyspace sweep.

## 5. Key-selection injection (`kid` / `jku` / `jwk` / `x5u`)
- `kid`: if it feeds a file path or query, try traversal (`../../dev/null` → empty key → sign with empty) or SQLi. 
- `jku` / `x5u`: if the server fetches a key from a URL you control, host your own key and point it there (also an SSRF primitive — note both).
- `jwk`: some libraries trust a public key embedded in the header itself — embed your own and self-sign.
Each only matters if the server actually *uses* attacker-controlled key material — prove the forged token is accepted.

## 6. Logic: claims, expiry, scope
- Does the server enforce `exp`? Replay an expired token. Enforce `aud`/`iss`? Reuse a token from another audience/tenant.
- Tamper authorization claims (`role`, `tenant`, `uid`) — if unsigned-`none` or a forgery path works, escalate horizontally (another user's `sub`) and vertically (admin).
- Sensitive data in the payload (it's only base64, not encrypted) — PII/secrets in claims is its own finding.

## Record & validate
- A forgery is confirmed **only** by a protected endpoint honouring the forged token — capture that request/response as evidence (`evidence`), then `state_update action=vuln`.
- `jku`/`x5u` that fetch your URL → also record the SSRF.
- Unproven manipulation stays `suspected` for `drex_triage`. Severity = the access the accepted token actually granted. Hold ROE: forge and read, don't destroy.
