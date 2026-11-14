# DECISIONS — status: DONE (12:21). Typecheck clean, 27 tests pass, live Drex + Meta verified.

Files: `apps/server/src/decisions/{index,providers,scheduler}.ts`, `scheduler.test.ts`, `liveSmoke.ts` (manual: `npx tsx src/decisions/liveSmoke.ts`).

## API (import from `./decisions` / `../decisions`)
```ts
createDrexProvider(cfg: { apiKey: string; model?: string; baseURL?: string; fetch?: typeof fetch }): DecisionProvider
createMetaProvider(cfg: { apiKey: string; baseUrl?: string; model?: string; fetch?: typeof fetch }): DecisionProvider
// Two accepted shapes, both return SchedulerPort and never throw at construction (empty keys are fine):
createScheduler({ roomId?, mode, clock, naceApiKey, naceModel, metaApiKey, metaBaseUrl, metaModel, concurrency, maxCallsPerMinute,
                  onStatus?, shadowMeta?, onShadow? })                       // ENGINE's flat form, one per room
createScheduler({ drex, meta, clock, mode, concurrency, maxCallsPerMinute, onStatus?, shadowMeta?, onShadow?, deadlineMs?, maxQueue? })
stateHash(state): string   // sha256 prefix, same value the trace carries
```

## Behaviour callers rely on
- `request(req, onDone)` returns the trace (status REQUESTED, decisionSource null) or `null` when dropped (same dedupeKey still pending, or queue full).
  `onDone` fires exactly once, always asynchronously, with the SAME trace object mutated to its final state. It is never called after `cancelAll()`.
- `trace.chosenAction` is always a key of `req.options`. Brains should apply `chosenAction`, then set status APPLIED / REJECTED and effectEventIds.
- Final states:
  | case | status | decisionSource | chosenAction | notes |
  |---|---|---|---|---|
  | scripted mode | RESOLVED | scripted | req.scriptedAction | provider null, no call made, all provider fields null |
  | provider answered | RESOLVED | drex / meta | provider's label | real confidence/probabilities/requestId/latency/evalMs/usage (Meta: confidence, probabilities, evalMs are null) |
  | Drex confidence < 0.34 | RESOLVED | drex_low_confidence_guardrail | req.safeAction | providerChoice + real numbers kept |
  | deadline passed | TIMEOUT | fallback | req.safeAction | errorType "timeout"; a late answer is discarded |
  | provider error | ERROR | fallback | req.safeAction | errorType = auth, credit, invalid_request, rate_limit, overloaded, server, network, invalid_response |
  | not attempted | ERROR | fallback | req.safeAction | errorType = unconfigured, circuit_open, budget_exceeded |
- Deadline (RULES.decisions.deadlineMs: drex 3000, meta 8000) counts from request(), including queue wait.
- The scheduler never retries. The Drex SDK may retry once inside the deadline for 429/5xx; 401/402/422 are never retried.
- Circuit breaker: 3 consecutive failures (or one auth/credit error) opens it for 15 s; decisions then fall back with "circuit_open".
- `status().badge`: "SCRIPTED", "DREX: LIVE" / "DREX: FALLBACK", "META: LIVE" / "META: FALLBACK". `pending` = queued + in flight.
- `setMode` affects new requests only. `onStatus` fires on every request/completion/mode change.
- Shadow (off by default): with `shadowMeta: true` in drex mode, Meta is asked in parallel and `trace.shadow` is filled, never applied;
  if it lands after onDone, `onShadow(trace)` is called. Shadow calls count toward Meta's per-minute budget (Meta ember allows 60/min).
- Budget and concurrency are per scheduler instance. With one scheduler per room, three experiment rooms share the account's real limits
  (Drex free tier: 120 req/min, 8 in flight) — keep concurrency <= 2 per experiment room.

## Measured live through the scheduler (12:20, one call each, same witness state)
- Drex: `report`, confidence 0.993, probabilities report 0.995 / gossip 0.002 / ignore 0.002, model drex-max-v1.1, round trip 343 ms, provider eval 133.7 ms, 184 in / 118 out tokens, request id req_8322…3029.
- Meta: `report`, model muse-ember-1.0, round trip 1196 ms, 195 in / 68 out tokens, request id (x-request-id) ee586985-…, confidence/probabilities null.

## Notes for BRAINS
- State shape matters to Drex: keep each role's state keys fixed. Send a plain JSON object; no images.
- Give `scriptedAction` and `safeAction` as keys of `options`; if they are not, the first option is used.
