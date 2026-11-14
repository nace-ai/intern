---
name: tls-crypto
title: TLS and cryptographic failures
triggers: Load for every exposed TLS service (HTTPS, SMTPS, IMAPS, LDAPS, DB TLS) and whenever the app handles secrets — tokens, password storage, encrypted cookies or parameters, signed URLs, API keys — or sends anything sensitive over plain HTTP.
attack: reconnaissance
owasp: A04
payloads: Insecure Randomness; Encoding Transformations
---

Crypto findings are judged by what an attacker can actually do with them. Version lists and cipher grades alone are informational; a downgrade path, a decryptable secret or a forgeable token is a finding.

## 1. Transport
- Protocols and ciphers: `nmap --script ssl-enum-ciphers -p 443 <host>` or `openssl s_client -connect host:443 -tls1` / `-tls1_1` (accepted?). Look for SSLv3/TLS1.0/1.1, NULL/EXPORT/RC4/3DES, no forward secrecy.
- Certificate: hostname mismatch, expired, self-signed in production, weak key/signature, wildcard scope.
- **HSTS** (`Strict-Transport-Security`, `includeSubDomains`, preload) and whether port 80 serves content or redirects; cookies without `Secure`.
- Plaintext protocols carrying credentials (HTTP login forms, FTP, Telnet, LDAP 389 simple bind, SMTP AUTH without STARTTLS) — that's the impactful version of this category.
- Severity honesty: TLS 1.0 enabled alongside 1.2 on a modern site is low; credentials over plain HTTP is high.

## 2. Secrets at rest and in the app
- Password storage visible to you (DB dump, exported users, API leaking hashes): identify the scheme — unsalted MD5/SHA1 = finding; bcrypt/argon2/scrypt = fine. Crack a *sample* offline with a small wordlist to prove weakness; don't attempt the full set.
- Hard-coded keys and secrets: JS bundles, mobile apps, public repos, config leaks (→ `misconfig-errors`, `file-handling`). Prove a key is live with a read-only call.

## 3. App-level crypto
- Encrypted/signed tokens and parameters: bit-flip a byte in an encrypted cookie/param — does a meaningful field change (CBC malleability)? Do padding errors differ from other errors (padding oracle)? Is the same plaintext always the same ciphertext (ECB/deterministic)?
- Predictable tokens: reset tokens, session ids, invite codes, signed URLs — collect several and look for timestamps, counters or short length.
- JWT/HMAC secrets → `jwt`.

## Prove & record
- **Impact:** the decrypted/forged value, the cracked sample hash, the credential captured over plaintext (from your own test login), the live key.
- `state_update action=vuln owasp=[A04]`; evidence = tool output (cipher scan, the forged token and its acceptance, the cracked hash).
- Clean TLS + no app crypto issues → `action=coverage owasp=[A04] coverage_status=tested notes=<hosts/ports scanned, app secrets reviewed>`.
