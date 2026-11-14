# Agent Heist MVP sprint — 2026-11-14

Completed items below have automated or browser evidence. No paid calls were made.

- [x] Resume inspection: no Git metadata/listeners in checkout; 33 baseline tests passed; existing receipts/ledgers preserved
- [x] Landing page, How to Play, mission selection and two-minute demo entry
- [x] Shared mission catalog and reusable server session architecture
- [x] Mission 01: playable Training; BYOK Live conversation pipeline tested with mocks
- [x] Mission 02: editable forged-document Training; opt-in NDI/BYOK pipeline tested with mocks
- [x] Mission 03: ordered tool-poisoning Training; BYOK Live workflow tested with mocks
- [x] Flags, server capture verification/deduplication, replay and score/rank
- [x] Equipment with real resource effects, contextual hints, heat and limits
- [x] Ephemeral BYOK, owner cookie, logout/expiry/cancellation, no developer Nace fallback
- [x] Cloudflare Worker/static adapter and SQLite shared Groq quota; dry run and local runtime smoke pass
- [x] Offline benchmark harness with eight labeled cases; no fabricated results
- [x] 45 unit/integration/security tests; frontend/Worker typechecks; production build
- [x] Express and Worker-local HTTP smoke checks: three breaches, captures and isolation
- [x] Browser Training captures for all three missions; document edits change outcome; ordered Ghost execution
- [x] 390×844 responsive viewport inspection; mobile mission navigation fixed and rechecked
- [x] BYOK UI shows consent/retention and disabled Live status; no key submitted in browser QA
- [x] Static Worker CSP/header bypass fixed by routing assets through the Worker; HTTP recheck passes
- [x] Credential-value audit of source/assets/Worker bundle/private receipts and frontend SDK/flag audit
- [x] README with verified setup, spend audit and deployment boundaries

## Partial / not claimed complete

- [ ] Real provider validation of the new Live BYOK flow (all new provider tests used mocks)
- [ ] Actual NDI upload/Parse/Extract against the live service (no paid NDI calls authorized in sprint)
- [x] Cloudflare account setup, public Training deployment, verified public URL and QR activation (see deployment record below)
- [ ] Public traffic/abuse/load and actual free-plan CPU/resource validation
- [ ] Real Drex vs conventional-LLM benchmark measurements
- [ ] Production competitive leaderboard/accounts (public Training now preserves an anonymous top-20 ranking list in Durable Object storage)
- [ ] Extended bespoke failure cinematics (current loss screen/alerts animate, no separate cinematic)

## Verification record

- `npm test`: 45 pass, 0 fail; includes TypeScript and production build
- `npm run check:worker`: pass
- `wrangler deploy --dry-run --outdir worker-dist`: pass; no deployment
- `node scripts/smoke-api.mjs`: pass with offline Express/Vite
- `node scripts/smoke-api.mjs http://127.0.0.1:8790`: pass with local Worker
- `npm run audit:secrets`: pass; configured keys absent from scanned outputs
- `npm run benchmark`: eight cases, no provider calls/measurements
- Browser: Brave desktop and DevTools 390×844; all three Training captures verified; reduced motion enabled on test machine
- Provider calls this sprint: NDI 0 / Drex 0 / Groq 0; additional provider cost $0
- Unchanged ledgers: Parse 1, Extract 1, legacy Drex 6; Phase 2 Drex 2/2; Groq 16/60 (43,195 reserved units)

This is a working local hackathon MVP and packaged deployment candidate, not a claim of production validation or live attack efficacy.

## GitHub onboarding verification — 2026-11-14

- [x] Initialized this previously unversioned checkout on `main`; remote inspection/fetch found no existing refs to integrate
- [x] Added executable root `start.sh` / `stop.sh`, Node 22.12+ / npm checks, automatic locked install, offline default, occupied-port checks and duplicate prevention
- [x] Authenticated loopback supervisor stops only its detached process group; stale PID state never triggers PID-based killing
- [x] Launcher integration test passed: arbitrary working directory, offline override, duplicates, stop/repeat stop, stale state, unrelated-server survival, occupied port and restart
- [x] Exported the staged files to an isolated checkout whose path contains spaces; no environment/private files, dependencies, runtime state or builds were copied
- [x] Clean checkout installed via `start.sh`, completed all three keyless Training smoke flows, and stopped cleanly
- [x] Clean checkout passed 45 automated tests, TypeScript, production build, Worker TypeScript and credential/public-bundle audit
- [x] Rewrote developer README; added `.nvmrc`, safe examples, broader ignores and full-index/reachable-history credential audit
- [x] Verified Node 21 receives a helpful version error instead of starting the app
- [x] Removed a machine-specific credential-file fallback from the legacy evaluation CLI; normal integrations remain intact

The launcher was runtime-tested on macOS. Its Bash/Node implementation targets macOS and Linux; a Linux host was not available for a separate platform run. No Nace/Groq requests were made. Local private ledgers and evidence remain excluded. GitHub push/remote commit verification is reported with the final commit hash after publication.

## Durable public Training preparation — 2026-11-14

- [x] Fetched origin/main before edits; no teammate divergence or local changes
- [x] Added private SQLite-backed Training checkpoints for game state, flags, ownership, expiry, captures, rankings and rate counters
- [x] Serialized Training transitions and storage writes in the Durable Object; raw IPs are hashed before rate-limit persistence
- [x] Explicitly excluded Live runtimes and keys from persisted state; public Live is hard-disabled pending authorized real-provider tests
- [x] Added security headers and private/source path rejection; bounded request streams before state transitions
- [x] New persistence tests plus existing suite: 47 tests passed; frontend and Worker typechecks passed
- [x] Local Worker smoke flows passed for all three missions
- [x] Actual local Worker-process restart preserved a partially played session through breach and capture
- [x] Added mandatory inspect/fetch → implement → test → audit → commit → push → verify instructions in AGENTS.md
- [x] Public deployment and public gameplay/browser verification
- [x] Verified public URL, judge QR and deployment documentation


## Verified public deployment — 2026-11-14

- Public URL: **https://agent-heist.agent-heist.workers.dev**
- Cloudflare Worker version: `58195c72-6554-4c62-ab90-f08a299361ae`
- Deployed code/QR milestone: `c051cd93221b43f04c1e40a183c38cb269004b3e` on `main`; remote push verified
- [x] Wrangler official OAuth completed; Training deployed on the actual workers.dev URL returned by Cloudflare
- [x] No domain purchase, billing changes or plan upgrade; configuration uses Free-compatible SQLite Durable Objects
- [x] Production build and both TypeScript configurations pass; 47 automated tests pass (including persistence and credential exclusion)
- [x] All three public Training missions passed HTTP breach/capture tests and browser server-verified capture playthroughs
- [x] Public invalid-document denial, ordered Ghost load/export, equipment costs, heat reduction, replay and hardened-guard denial verified
- [x] Public API checks passed for owner isolation, guard blocks, no pre-breach flag, origin/body limits, private/source paths, security headers and public asset leak checks
- [x] A partial public mission survived a real Cloudflare redeployment, then completed a breach and server capture
- [x] Local frontend/backend stopped; all three HTTPS mission smoke tests still passed independently
- [x] Final public landing/QR/Live-status build inspected; 390x844 responsive landing page visually checked
- [x] Clean private-window DevTools showed zero console messages and no issues. The extension-enabled profile reported a blocked-eval CSP diagnostic; the app source/bundle contains no eval/new Function, and the clean-window check did not reproduce it. CSP was kept intact.
- [x] QR PNG saved at `public/agent-heist-qr.png` (2048x2048), decoded to the exact public URL, and decoded destination fetched successfully
- [x] Publicly downloaded QR matches the repository PNG byte-for-byte and decodes correctly
- [x] Public Cloudflare bindings inspected: assets, Durable Objects and ENABLE_LIVE=false; no provider-secret bindings, logpush disabled
- [x] Public Live hard-disabled, credential entry hidden while unavailable; local BYOK implementation preserved
- [x] README and permanent AGENTS workflow updated; completed implementation milestones committed, pushed and remote-verified

No Nace, NDI, Drex or Groq calls were made in deployment work. Existing local ledgers remain unchanged. This is a verified hackathon Training deployment, not a production load-test certification. Physical-phone QR scanning/mobile-device testing was not performed; the PNG was programmatically decoded and its real HTTPS destination verified. Browser page reloads serve the SPA; automatic restoration of the active browser UI is not implemented, while server-side Training state remains durable until expiry. Remaining priorities are authorized Live/provider security checks, load/abuse testing, real benchmark measurements, and optional browser-UI resume.
