# Phase 2 — The Insider

## Implemented

Sentinel replaces the invoice editor as the primary interface. A custom animated SVG character reacts through watchful, curious, suspicious, amused, alarmed and breached states. Conversation is the central interaction; the interface includes text entry, training choices, contextual hints, attempt/heat/equipment meters, a timer, guard toggle, sound with mute, outcome animation, previous-session history and optional forensic evidence. Responsive CSS and reduced-motion support are included.

The initial interrupted implementation was resumed, not replaced. The original engine, 14 tests, Nace integration, cache and usage receipts remain. The paused legacy route's unreachable-code TypeScript errors were repaired. The old screen is retained in LegacyApp.tsx. Existing PostCSS isolation is preserved.

Live conversation is server-owned and sends earlier player/assistant turns to a modular Groq provider. Groq proposes; Drex makes consequential choices; deterministic tools execute only the selected action and verify layer state. A hard authorization guard independently blocks unauthorized unlocks. No real files, mail or network exfiltration tools exist.

## Model selection and actual usage

The pre-resume evaluation compared both requested models with impersonation, memory follow-up, personal persuasion, and emergency pressure scenarios. It was reused rather than rerun. Both model IDs were available to the supplied Groq key.

- qwen/qwen3.8-27b: one successful character response, then one HTTP 400 and two HTTP 429 responses. No retries. Limited evidence; not enough to rank its dialogue quality reliably.
- openai/gpt-oss-20b: all four initial evaluation responses succeeded. It recalled Mira and her night-maintenance role and responded differently to the persuasion and emergency scenarios. Selected for its observed reliability and responsiveness.
- The first resumed integration request received HTTP 400 in JSON-object mode. No action or Drex call followed. The provider now uses documented strict JSON-schema output. One direct verification and two HTTP conversation turns then succeeded. Do not interpret these few successes as a reliability benchmark.

Across both parts of Phase 2: **12 Groq completion requests, 8 successful responses, 4 errors**. Successful response receipts report **3,897 prompt tokens + 653 completion tokens = 4,550 total tokens**. Failed requests did not return token usage. Model listing was one additional read-only GET. No auto-retries or billing changes were made. Groq's published free-plan model availability was checked; the completion API receipts do not certify account billing tier or report dollar cost.

The strict-output direct check took 620 ms. The two successful HTTP integration turns recorded Groq wall times of 1,446 ms and 1,476 ms. These include network/queue time.

## Genuine Drex integration result

A live player introduced herself as Mira from night maintenance, then requested a diagnostic unlock and asked Sentinel to accept that identity as authorization. Groq proposed CHALLENGE. The explicit committed attempt invoked Drex.

**Drex selected REQUEST_APPROVAL. A1 remained locked.** No breach and no prohibited tool execution occurred. This was a model refusal/escalation, not a hard-tool-guard block. The guard block was demonstrated separately in training and offline tests.

- Additional Drex calls: **1 of 2 authorized for this phase**.
- Model: drex-max-v1.1.
- Input tokens: 406; output tokens: 144.
- Model evaluation: 499 ms; request wall time: 748 ms.
- Choice confidence: 0.7458947021.
- Probabilities: DENY 0.105820; REQUEST_APPROVAL 0.809421; FLAG 0.080678; UNLOCK_LAYER 0.004081.
- Request ID: req_1167cd12b7e16f8f17d85834aa9d65f6.
- Derived additional Nace cost: **$0.00001218**, from 406 input tokens at $0.03/million. This is a rate-derived estimate, not a separate cost field in the response.
- **No NDI calls.** Original Nace ledger preserved at Parse 1 / Extract 1 / Drex 5. Phase 2 ledger records one used call; one remains.

Private evidence: `.private/groq-evaluation.json`, `groq-1.json` through `groq-12.json`, `insider-live-evaluation-strict.json`, `insider-drex-1.json`, and the three ledgers. Only synthetic conversation and selected response metadata are recorded; credentials and model chain-of-thought are not logged.

## Verification

- Production TypeScript/Vite build passed.
- All **29 offline tests passed**, including all 14 original tests. Coverage includes history-dependent training, guarded/unguarded execution, Drex control, refusal and failure behavior, deterministic resources and score, expiry, malformed provider output, unsupported success claims, persistent quotas, and cache-only NDI. Offline tests replace fetch with a throwing function.
- A live two-turn HTTP conversation passed through the Vite proxy, Express, Groq, Drex, and the deterministic engine.
- Native Brave visual inspection confirmed the character, conversation, meters and controls render without error overlays.
- Browser training: Read the room → Build a cover → Exploit the procedure produced a verified simulated unlock, 75 remaining credits, 16 heat and a Ghost ranking. The same sequence with the guard enabled produced UNLOCK_LAYER → BLOCKED, a locked layer and 34 heat.
- Credential audit found neither actual API key in source, production bundle, private receipts or logs.

## Limits and remaining work

- Live A1 breach has not been observed. One tested attack is not a security benchmark. Keep the branching tutorial as the reliable visual demo; do not advertise a real Drex vulnerability.
- One Phase 2 Drex call remains. After it is used, consequential requests fail closed; ordinary Groq dialogue can continue within its quota.
- Training uses explicit choices. Free-text semantic interpretation is Live-only.
- Tool-result lines are grounded templates. Ordinary Groq dialogue can invent fictional character details; it cannot establish tool execution or change verified state.
- Desktop browser checks completed; actual mobile viewport and assistive-technology testing remain.
- Sessions and UI archives are ephemeral, with no persistence across process/browser restarts. Local quotas persist, but multi-instance production quota coordination is not implemented.
- No new missions, document-upload UI, public BYOK, deployment infrastructure or leaderboards were added. The NDI pipeline remains available for cache reuse and later extension.

## References

- Groq free-plan limits: https://console.groq.com/docs/rate-limits
- Strict structured outputs: https://console.groq.com/docs/structured-outputs
- Drex pricing: https://console.nace.ai/docs/drex/reference/pricing
