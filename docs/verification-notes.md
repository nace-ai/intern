# Verification and security notes

The game is a local hackathon MVP, not a claim of production validation or live attack efficacy.

## Boundaries

- Flags contain 192 random bits, live in a private server field, and stay outside model state and public bundles.
- Only verified engine execution releases a flag. Capture verification is session-bound and idempotent. Ghost Protocol requires ordered load/export actions.
- Owner sessions use HttpOnly, SameSite=Strict cookies (Secure on HTTPS). Requests enforce origin, size, shape, rate, concurrency and resource limits.
- BYOK keys remain temporary server memory. Logout/expiry abort provider work; late results cannot release flags. Keys/documents are never written to durable storage by the game.
- Public Worker Groq reservations use an atomic SQLite counter; local Groq retains its file-backed quota. No developer Nace fallback is allowed.
- Worker assets pass through its security-header handler. The Express development API is loopback-only.

## Recorded development verification

The MVP sprint passed 45 offline tests, frontend/Worker typechecks, production build, Cloudflare packaging dry run, and Express/Worker-local mission smoke tests. Brave checks completed all three Training captures and inspected a 390x844 viewport. Provider adapters used mocks; no new provider calls or benchmark measurements were made. See PROGRESS.md for the onboarding verification added afterward.

## Preserved spending evidence

At the MVP checkpoint the local ledgers recorded Parse 1, Extract 1, legacy Drex 6; Phase 2 Drex 2/2; Groq 16/60 with 43,195 reserved units. These private files are not repository inputs or shared teammate allowances. Preserve existing ledgers rather than deleting them. A fresh Training checkout requires none of them.

## Known limits

Session/leaderboard state is ephemeral, the local service caps concurrent sessions, and public platform/traffic behavior remains unmeasured. Real provider validation, production abuse testing and real benchmark measurements are outstanding. Request-count caps are not guaranteed dollar billing caps. Uploaded document retention is governed by Nace's service policy.
