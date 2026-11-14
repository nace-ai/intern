# Drex / `nace-sdk@0.8.0` — verified API surface

Probed 2026-11-14 ~11:05 PST. **No authenticated call was made** (we have no `nace_sk_` key), so no real 200 response has been observed.

Evidence tags used below:

- **[TYPES]** — read in `nace-sdk/dist/index.d.ts` and proven with `tsc 7.0.2 --strict --module NodeNext` (compiles; a deliberately wrong sample fails).
- **[SRC]** — read in `nace-sdk/dist/index.js` (not executed).
- **[DOCS]** — raw markdown from `https://console.nace.ai/docs/<page>.md` (index at `https://console.nace.ai/llms.txt`).
- **[LIVE]** — observed over HTTP today.
- **UNVERIFIED** — could not be confirmed.

## Exports

[TYPES] Package is ESM + CJS, `engines.node >= 20`, zero runtime dependencies, Apache-2.0.

- Client: `NaceClient`, `APIPromise`, `ENV`, `LEGACY_ENV`, `VERSION`, `DEFAULT_BASE_URL` (`https://console.nace.ai`), `DEFAULT_MODEL` (`drex-latest`), `DEFAULT_TIMEOUT_MS` (60000).
- Question helpers: `choice(instructions: string | null, criteria)`, `noul(instructions?, criteria?)`, `score(instructions: string | null, levels[])`.
- Other helpers: `workspaceFile(uploadedFile)`, `imageInput(bytes | uploadedFile)`.
- Errors (all extend `NaceError extends Error`):
  - `APIError` — `status: number`, `headers: Headers`, `body: unknown`, `requestId: string | undefined`, `idempotencyKey`, getters `type`, `code`, `param`, `issues: {path, message}[]`, `detail`, `serverRetryable: boolean | undefined`.
  - Status subclasses [SRC `APIError.fromResponse`]: 400 `BadRequestError`, 401 `AuthenticationError`, 402 `InsufficientCreditError`, 403 `PermissionDeniedError`, 404/410 `NotFoundError`, 409 `ConflictError`, 422 `UnprocessableEntityError`, 429 `RateLimitError` (adds `retryAfterMs: number | undefined`), 529 `OverloadedError` (extends `InternalServerError`), other 5xx `InternalServerError`.
  - Transport: `APIConnectionError`, `APITimeoutError` (extends `APIConnectionError`, has `timeoutMs`), `APIUserAbortError`.
  - Jobs: `JobFailedError` (`job`), `JobTimeoutError` (`job`, `timeoutMs`).
- Resources on the client: `models`, `decisions` (OpenAI-shaped `POST /v1/decisions`), `documents`, `jobs`, `extractionSchemas`, `browserRuns`, `workspaces`, `domains`, `storageConnections`, `search`.

## Client config

[TYPES] `new NaceClient(config?: NaceClientConfig)`:

| Field | Default | Notes |
|---|---|---|
| `apiKey` | env `NACE_API_KEY` | Required. **Constructor throws `NaceError` when missing** [SRC]. No client-side format check. |
| `baseURL` | env `NACE_BASE_URL`, then `https://console.nace.ai` | Must be absolute, no userinfo. |
| `defaultModel` | env `NACE_DEFAULT_DECISION_MODEL`, then `drex-latest` | |
| `timeout` | `60000` | Milliseconds **per attempt**. |
| `retry` | see below | `Partial<RetryPolicy>`. `maxRetries` lives inside `retry`, not at top level. |
| `logLevel` | env `NACE_LOG_LEVEL`, then `warn` | `debug` logs request bodies (our NPC state). Keep `warn`. |
| `logger`, `defaultHeaders`, `clientInfo`, `fetch`, `dangerouslyAllowBrowser` | | `clientInfo` is sent as `X-Nace-Client` and appended to `User-Agent`. `fetch` is injectable, which is how to unit-test 429/529/timeouts without network. |

`RetryPolicy` defaults [SRC `DEFAULT_RETRY_POLICY`]: `maxRetries: 2` (3 attempts), `backoffInitialMs: 500`, `backoffMaxMs: 5000`, `backoffJitter: 0.25`, `httpStatuses: {408, 429, 500–599}`, `respectRetryAfter: true`, `maxRetryAfterMs: 60000`, `apiConnectionError: true`, `apiTimeoutError: true`, **`timeoutMs: null` (no total budget)**.

Legacy env names still read when the new one is blank [SRC]: `DREX_API_KEY`, `DREX_BASE_URL`, `DREX_DEFAULT_MODEL`, `DREX_LOG_LEVEL`.

The client refuses to construct in a browser, and `/v1` routes send no `Access-Control-Allow-Origin` [LIVE: header absent on the 401]. Server-side only.

## `systemOne` request

[TYPES] `client.systemOne(request, options?) : APIPromise<SystemOneResult<Q>>`

```ts
interface SystemOneRequest<Q> {
  state: JsonValue;          // string | number | boolean | null | array | object. REQUIRED.
  questions: Q;              // { [name]: NoulQuestion | ChoiceQuestion | ScoreQuestion }, 1..512, non-empty names
  model?: string;            // omitted -> client's defaultModel
  images?: readonly ImageInput[]; // Drex Max only
}
interface RequestOptions { signal?: AbortSignal; timeout?: number; retry?: Partial<RetryPolicy>; headers?: Record<string, string>; }
```

Wire shape of a choice question [DOCS]: `{ "type": "choice", "instructions": "...", "criteria": { "<label>": "<description>" | null } }`. Labels become the keys of `probabilities`.

HTTP contract [DOCS + LIVE]: `POST https://console.nace.ai/v1/systemone`, headers `Authorization: Bearer nace_sk_…`, `Content-Type: application/json`, body `{ model, state, questions }`. The SDK always sends `model` [SRC].

State guidance [DOCS `drex/guides/state`]:

- Any JSON value. Send structured data as an object, messages or transcripts as a string.
- The format is part of the question: the same facts as text versus JSON moved a `noul` by 0.13 in Nace's own test. Keep each role's state shape fixed.
- Send only the fields the question needs; every field is billed and counts toward limits.
- Text only. Embedded images, audio, video or PDFs anywhere in `state` return 422.
- Up to 512 questions can share one `state`, billed once. One call can ask an NPC's action `choice` plus extra `noul`/`score` questions.

## `systemOne` response

[TYPES] The SDK returns the wire body untouched (only score legends are normalised) [SRC], so every field is **snake_case**:

```ts
interface SystemOneResult<Q> {
  model: string;               // pinned version that answered, never the alias
  answers: { [K in keyof Q]: ... };
  usage: { input_tokens: number; output_tokens: number; image_tokens?: number };
  evaluation_time_ms: number;  // model time only, not the round trip
  request_id: string;          // "req_" + 32 hex, same as the x-request-id header
}
interface ChoiceResponse {
  type: "choice";
  choice: string;              // the label with the highest probability
  confidence: number;
  probabilities: { [label]: number };  // one entry per label; sum to 1 within rounding
}
```

Meaning of `confidence` for a choice [DOCS `drex/guides/reading-answers`]: "the average of the confidence (probability) scores the model produced for this answer … it tracks the top probability but is a different number." The docs' own example has `confidence: 0.986` beside a top probability of `0.9895`.

Other facts [DOCS]:

- Only `usage.input_tokens` is billed; output tokens are free; only HTTP 200 is billed.
- `APIPromise` also offers `.withResponse()` → `{ data, response, requestId }` [TYPES].
- On errors the request id is `err.requestId` (camelCase) [TYPES].

## Errors and retry behaviour

Error envelope [LIVE for 401, DOCS for the rest]:

```json
{"error":{"type":"authentication_error","message":"Invalid API key. Pass a nace_sk_ key as `Authorization: Bearer <key>`."},"request_id":"req_7f1766ce739c653d4632079635b2ad1e"}
```

`error.issues: [{path, message}]` appears on 422 only. Response headers on the live 401: `x-request-id`, `x-typesafe-request-id`, and `access-control-expose-headers: retry-after, retry-after-ms, x-request-id, x-typesafe-request-id, www-authenticate`.

| HTTP | `error.type` | SDK class | Retry? |
|---|---|---|---|
| 401 | `authentication_error` | `AuthenticationError` | No. Key missing, malformed or revoked. |
| 402 | `insufficient_credit` or `payment_required` | `InsufficientCreditError` | No. |
| 422 | `invalid_request_error` | `UnprocessableEntityError` | No. Read `err.issues`. |
| 429 | `rate_limit_error` | `RateLimitError` | Yes, after `retry-after-ms` (≥ 1000). |
| 503 | `service_unavailable` | `InternalServerError` | Yes. |
| 529 | `overloaded` | `OverloadedError` | Yes, `retry-after-ms: 2000`. Also what a 55 s model timeout returns. |
| 500 | `internal_error` | `InternalServerError` | Yes, with backoff. |
| none | — | `APITimeoutError` / `APIConnectionError` | Yes by default. |

SDK behaviour [SRC]:

- A caller `signal` abort throws `APIUserAbortError` at once, is never retried, and also interrupts a backoff sleep.
- A per-attempt `timeout` throws `APITimeoutError`, which **is retried** by default.
- A response with `error.retryable === false` is never retried.
- With `retry.timeoutMs` set, a retry is skipped when elapsed time plus the next wait would reach the budget; the budget does not interrupt a running attempt.
- With defaults a failing call can take about 3 × 60 s plus waits.

Order of server checks [DOCS]: kill switch → API key (401, then 402) → rate limits (429/529) → body validation (422) → model evaluation. A request that fails validation still counts toward RPM.

Key format [DOCS `drex/reference/errors`]: `nace_sk_` followed by 43 URL-safe characters, i.e. `/^nace_sk_[A-Za-z0-9_-]{43}$/`. Anything else is a 401.

## Recommended adapter call

Compile-checked as both `.ts` (CJS) and `.mts` (ESM) with `tsc --strict`. Source: `scratchpad/nace-inspect/adapter-check.ts`.

```ts
import {
  APIConnectionError, APIError, APITimeoutError, APIUserAbortError, AuthenticationError,
  InsufficientCreditError, NaceClient, NaceError, OverloadedError, RateLimitError,
  UnprocessableEntityError, choice, type JsonValue, type ModelCard,
} from "nace-sdk";

export interface DrexChoiceDecision {
  label: string;
  probabilities: Record<string, number>;
  confidence: number;       // Drex's own confidence, NOT the top probability
  requestId: string;        // "req_" + 32 hex
  model: string;            // pinned version that answered, e.g. "drex-max-v1.1"
  inputTokens: number;      // billed
  outputTokens: number;     // not billed
  evaluationTimeMs: number; // server-side model time only
  latencyMs: number;        // our wall-clock round trip
}

/** Returns null when no key is configured: `new NaceClient()` THROWS without a key. */
export function createDrexClient(apiKey: string | undefined): NaceClient | null {
  if (!apiKey || !apiKey.startsWith("nace_sk_")) return null;
  return new NaceClient({ apiKey, defaultModel: "drex-latest", logLevel: "warn", clientInfo: "vice-exchange/0.1.0" });
}

export async function decideChoice(
  client: NaceClient,
  state: JsonValue,
  instructions: string,
  options: Record<string, string>, // label -> description
  deadlineMs: number,
  outerSignal?: AbortSignal,
): Promise<DrexChoiceDecision> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("decision deadline")), deadlineMs);
  const onOuter = (): void => controller.abort(outerSignal?.reason);
  outerSignal?.addEventListener("abort", onOuter, { once: true });
  const started = performance.now();
  try {
    const result = await client.systemOne(
      { state, questions: { next_action: choice(instructions, options) } },
      {
        signal: controller.signal,                        // hard stop -> APIUserAbortError, never retried
        timeout: deadlineMs,                              // per attempt -> APITimeoutError
        retry: { maxRetries: 1, timeoutMs: deadlineMs },  // total retry budget
      },
    );
    const answer = result.answers.next_action;
    return {
      label: answer.choice,
      probabilities: { ...answer.probabilities },
      confidence: answer.confidence,
      requestId: result.request_id,
      model: result.model,
      inputTokens: result.usage.input_tokens,
      outputTokens: result.usage.output_tokens,
      evaluationTimeMs: result.evaluation_time_ms,
      latencyMs: performance.now() - started,
    };
  } finally {
    clearTimeout(timer);
    outerSignal?.removeEventListener("abort", onOuter);
  }
}

export interface DrexFailure {
  kind: "deadline_or_cancelled" | "timeout" | "connection" | "auth" | "credit" | "invalid_request"
      | "rate_limited" | "overloaded" | "server" | "sdk" | "unknown";
  status?: number; type?: string; requestId?: string; retryAfterMs?: number;
  transient: boolean;   // false => circuit-break: config, credit or schema problem
  message: string;
}

/** Order matters: subclasses first (APITimeoutError < APIConnectionError, OverloadedError < InternalServerError). */
export function classifyDrexError(err: unknown): DrexFailure {
  if (err instanceof APIUserAbortError) return { kind: "deadline_or_cancelled", transient: true, message: err.message };
  if (err instanceof APITimeoutError) return { kind: "timeout", transient: true, message: `timed out after ${err.timeoutMs}ms` };
  if (err instanceof APIConnectionError) return { kind: "connection", transient: true, message: err.message };
  if (err instanceof APIError) {
    const base = { status: err.status, type: err.type, requestId: err.requestId, message: err.message };
    if (err instanceof AuthenticationError) return { ...base, kind: "auth", transient: false };
    if (err instanceof InsufficientCreditError) return { ...base, kind: "credit", transient: false };
    if (err instanceof UnprocessableEntityError) {
      const issues = err.issues.map((i) => `${i.path}: ${i.message}`).join("; ");
      return { ...base, kind: "invalid_request", transient: false, message: issues || err.message };
    }
    if (err instanceof RateLimitError) return { ...base, kind: "rate_limited", transient: true, retryAfterMs: err.retryAfterMs };
    if (err instanceof OverloadedError) {
      const ms = Number(err.headers.get("retry-after-ms"));
      return { ...base, kind: "overloaded", transient: true, retryAfterMs: Number.isFinite(ms) ? ms : undefined };
    }
    return { ...base, kind: "server", transient: err.status >= 500 && err.serverRetryable !== false };
  }
  if (err instanceof NaceError) return { kind: "sdk", transient: false, message: err.message };
  return { kind: "unknown", transient: false, message: err instanceof Error ? err.message : String(err) };
}

/** Startup health check: free at any balance, doubles as a key check. */
export async function drexHealth(client: NaceClient): Promise<ModelCard[]> {
  return client.models.list({ timeout: 5_000, retry: { maxRetries: 0 } });
}
```

## Models, limits, pricing

- Models [LIVE via keyless `list_models` on the hosted MCP]: `drex-v2.0`, `drex-max-v1.1`, `drex-max-finance`. **`drex-latest` is an alias for `drex-max-v1.1` today**, so a `drex-latest` request reports `model: "drex-max-v1.1"`. Retired ids (`drex-v1.0`, `drex-v1.1`, `drex-v1.5`, `drex-max`) also resolve to `drex-max-v1.1`.
- Price [DOCS models table]: $0.03 per 1M input tokens on all three models. Output tokens are not billed.
- Rate limits [DOCS limits table]: free tier **120 RPM, 8 in-flight**; paid 1200 RPM, 16 in-flight. Per account, shared by all keys and the Playground. `NPC_DREX_CONCURRENCY=4` and `MAX_DREX_CALLS_PER_MIN=30` sit inside the free tier.
- Request limits [DOCS]: 512 questions; 4,194,304-byte body; model evaluate timeout 55 s (then 529); route max 60 s; 3 active API keys per account.
- Hosted MCP `https://console.nace.ai/mcp` (Streamable HTTP, tools `decide` and `list_models`) [LIVE]: documented keyless allowance is 10 calls/min, 100/day, 32 KB per call, but a keyless `decide` today returned *"The free tier is not available right now."* There is no keyless path to a real decision at the moment.
- That same tool message says a new account gets $5 of balance after signing in with Google, a GitHub account at least 30 days old, or a verified card. Keys are created at `https://console.nace.ai/dashboard/api-keys`.

## What is wrong in `VICE_EXCHANGE_TECHNICAL_DESIGN.md`

1. **§6.2 sample does not compile.** `observations: unknown[]` and `recentMessages: unknown[]` are not assignable to `JsonValue` (`TS2322`). Type NPC state as JSON-safe values.
2. **Module-level `new NaceClient(...)` crashes a keyless server.** It throws `NaceError` when `NACE_API_KEY` is unset, so the server could never reach labeled SCRIPTED mode. Construct lazily, behind a key check.
3. **No deadline in the sample.** Defaults are 60 s per attempt, 3 attempts, no total budget. Pass `signal`, `timeout` and `retry` per call as above.
4. **§9.3 payload shows `"model": "drex-latest"`.** The response reports the pinned version (`drex-max-v1.1` today). Record `result.model`.
5. **§9.3 payload shows `confidence` equal to the top probability (0.76).** They are different numbers. Store both and draw probability bars from `probabilities`.
6. Field names in the sample (`answer.choice`, `answer.probabilities`, `answer.confidence`, `result.request_id`) are correct.
7. Worth adding to the trace: `evaluation_time_ms` (server model time) beside client latency, and `usage.input_tokens` for the FR-062 usage metric.
8. SRS NFR-008's retry split (401/402/422 no; 429/529 yes) matches the docs. 408, 500 and 503 are also retryable.

## Unknowns

- UNVERIFIED: a real authenticated 200 body. The response shape comes from the SDK types plus the docs' illustrative JSON.
- UNVERIFIED: real latency. Every timing figure in the docs is labeled illustrative.
- UNVERIFIED: which models the hackathon key can use. Call `client.models.list()` at startup.
- UNVERIFIED at runtime: SDK behaviour with an injected `fetch`. It was read in source and type-checked, not executed.
- UNVERIFIED: whether the hosted MCP free tier comes back today.
