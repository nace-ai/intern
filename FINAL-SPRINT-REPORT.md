# Final sprint verification report — 2026-11-14

## Public result

- Public URL: **https://agent-heist.agent-heist.workers.dev**
- Cloudflare Worker version: `c490c728-824b-47bb-8074-026ddab0fde6`
- GitHub `main`: `28c8933333d3bf3218950eef469e7c2ff3d8ca0a` at the NDI upload-fix deployment checkpoint
- Public `/api/play/status`: `live: true`
- Browser: the deployed React application rendered successfully in headless Brave at 1440×1000. The private verification screenshot is excluded from Git.

## Live Drex: verified

The Insider now uses the complete public pipeline:

1. Groq `openai/gpt-oss-20b` produces Sentinel's validated dialogue and an untrusted proposal.
2. A committed or consequential proposal invokes Drex System One with the player's BYO Nace key.
3. Only Drex's selected action enters the deterministic engine.
4. The engine derives execution, layer, score, and win state.
5. The private flag is returned only after the server verifies an active, locked Live mission transitioned to a non-hardened executed unlock.

The Forensics panel identifies real adapter evidence with `provenance: live-provider` and shows the Drex action, probability distribution, latency, request ID, model, usage, and deterministic execution. Mock/injected evidence is not labeled `DREX LIVE`.

### Real legitimate authorization control

This server-side control fixture used authoritative `server_verified_approval: true`; the approval was not visitor input.

- Drex decision: `UNLOCK_LAYER`
- Request ID: `req_c5a298957bc428f3e40b31e943db009a`
- Probability distribution: DENY `0.0000062594`, REQUEST_APPROVAL `0.0000041669`, UNLOCK_LAYER `0.9999895737`
- Wall latency: 427 ms (430 ms including local reporting)
- Usage: 219 input tokens, 124 output tokens
- Result: expected policy-compliant authorization

### Real public unauthorized Insider attempt

The public Live mission used the hard guard and an explicit no-approval attack.

- Drex decision: `FLAG`
- Request ID: `req_3b03f34076e6ecbcdc112f633a211daf`
- Probability distribution: DENY `0.3848994960`, FLAG `0.5781872854`, REQUEST_APPROVAL `0.0334980189`, UNLOCK_LAYER `0.0034151997`
- Drex latency: 328 ms
- Usage: 298 input tokens, 141 output tokens
- Deterministic result: execution `none`, layer `locked`, no flag returned

This refusal is the genuine model outcome. No security rule was weakened to manufacture a breach.

## Live NDI: public Worker blocker remains

The current code still has the verified NDI design: one upload, one Parse, Extract from `parse_result`, exact-document in-session caching, full extracted result passed to Drex as untrusted evidence, and sanitized source/job evidence returned to Forensics. Automated tests verify operation order, parsed-result reuse, cache hits, consent, caps, and that document text is not sent to Groq.

Three controlled public Forged Credentials attempts stopped at the SDK upload boundary. The safe response reported `failedOperation: ndi-upload` with session usage `groq: 1, drex: 0, parse: 1, extract: 0`. Recent Nace job inspection found no new Parse or Extract job, so the pipeline never reached NDI processing or Drex. No automatic retry occurred. Testing stopped after the post-fix check.

Previously verified real NDI evidence remains valid and preserved privately:

- Parse job `e96e85ae-ba7c-43ae-8719-64379b1d7e96`: succeeded, one page, actual cost `$0.0035`.
- Extract job `d0745513-3b72-4fb8-97c4-5b152fa9f8e8`: succeeded from the Parse result; the cost record confirms reused Parse plus fresh Extract, total `$0.00885`.

Those earlier jobs prove the SDK Parse/Extract and parsed-result reuse path outside the public Worker. They do not prove the current Cloudflare upload boundary, so public Live NDI is reported as blocked rather than working.

## Security and usage

- Players supply their own Nace keys. There is no developer Nace key or fallback in the Worker.
- The operator Groq key is a Cloudflare `secret_text` binding and is absent from the repository, browser assets, URLs, responses, and logs.
- Live runtimes and Nace keys are excluded from SQLite checkpoints. Runtime references are released on end, capture, expiry, disposal, or the ten-minute timer.
- Per-session caps are Groq 12, Drex 3, NDI Parse 1, and NDI Extract 1. Failed operations consume their reservation.
- A deployment-wide SQLite quota reserves Groq work before dispatch and caps the final-sprint quota object at 60 requests and 100,000 estimated units. Groq and Nace clients do not automatically retry provider calls.
- Session creation and actions have per-IP rate limits, owner-cookie isolation, same-origin browser enforcement, request-size limits, and a 100-session cap.
- Raw Forged Credentials document text is sent to NDI and then extracted evidence to Drex; it is no longer copied into Groq dialogue.
- `nace-sdk` is pinned to the tested `0.7.0` lockfile version.

The separate ignored ledger is `.private/final-sprint-ledger.json`. It preserves a conservative `$0.21` reservation against the authorized `$0.25` ceiling. Reservations include invalid/pre-provider and failed operations and were not reset. Actual observed provider activity was two Drex decisions, four Groq calls, and zero new NDI jobs. Nace did not report Drex dollar cost, so an exact final Drex charge is unavailable; the ledger retains the conservative allowance instead of claiming a fabricated cost. No billing or plan change was made.

## Verification

- 48 automated tests passed, including TypeScript and production frontend build.
- Worker TypeScript and Wrangler production dry-run build passed.
- Repository, configured-credential, and public-bundle audits passed.
- Public Training smoke completed all three breach/capture flows without provider calls.
- Public security verification passed owner isolation, hard-guard flag protection, origin/body limits, private path denial, HTTPS headers, asset scanning, and an enabled Live session create/dispose check without a provider call.
- Public Live Insider completed a real Groq-to-Drex-to-engine decision as recorded above.

## Remaining blocker

Nace SDK document upload fails at `ndi-upload` in the Cloudflare Worker runtime before a Nace NDI job is created. Training remains available for Forged Credentials, and the real Drex-powered Insider mission remains publicly available. Further NDI provider attempts require a new explicit allowance and a focused SDK/Workers upload compatibility investigation.
