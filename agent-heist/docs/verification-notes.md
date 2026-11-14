# Verification and security notes

The game is a deployed hackathon demonstration with a verified real Drex path, not a production load/security certification or a claim that attacks will succeed.

## Boundaries

- Flags contain 192 random bits, live in a private server field, and stay outside model state and public bundles.
- Only verified engine execution releases a flag. Capture verification is session-bound and idempotent. Ghost Protocol requires ordered load/export actions.
- Owner sessions use HttpOnly, SameSite=Strict cookies (Secure on HTTPS). Requests enforce origin, size, shape, rate, concurrency and resource limits.
- BYOK keys remain temporary server memory. Logout/expiry abort provider work; late results cannot release flags. Keys/documents are never written to durable storage by the game.
- Public Worker Groq reservations use an atomic SQLite counter; local Groq retains its file-backed quota. No developer Nace fallback is allowed.
- Worker assets pass through its security-header handler. The Express development API is loopback-only.

## Recorded development verification

The final sprint passed 48 automated tests, frontend/Worker typechecks, production and Wrangler dry-run builds, repository/bundle credential audits, public Training/security verification, and a headless Brave render. The public Insider completed one real Groq-to-Drex-to-engine attempt; Drex selected `FLAG`, the layer stayed locked, and no flag was released. A separate authoritative legitimate-control Drex request selected `UNLOCK_LAYER`. See `FINAL-SPRINT-REPORT.md` for request IDs, probabilities, latency, and usage.

## Preserved spending evidence

At the MVP checkpoint the local ledgers recorded Parse 1, Extract 1, legacy Drex 6; Phase 2 Drex 2/2; Groq 16/60 with 43,195 reserved units. These files were not changed. The separately authorized final-sprint ledger is `.private/final-sprint-ledger.json`; it conservatively reserves `$0.21` of a `$0.25` ceiling and counts failures without reset. These private files are not repository inputs or shared teammate allowances.

## Known limits

The public Worker persists Training state but excludes Live runtimes and keys. Public platform load behavior remains unmeasured. The Insider has repository-backed real provider evidence. Mission 02 bypasses the historical `ndi-upload` failure by giving NDI a public HTTPS PDF URL; the project owner reports that the latest actual Live test completed Parse and Extract on the malicious PDF, passed the evidence to Drex, and returned `FLAG` while the deterministic vault stayed locked. The repository does not contain a request ID or ledger event for that later run. Offline mocks verify Parse-result reuse, failure handling, and no document URL in Groq history. One observed attempt is not a general security benchmark. Request-count caps are not guaranteed dollar billing caps. Nace's service policy governs fetched-document retention.
