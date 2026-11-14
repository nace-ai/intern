# Meta model API — findings for the MetaAdapter

Probed 2026-11-14, 11:02–11:09 PST, from the dev laptop. 60 real API calls. No key material in this file.
Everything under "Evidence" is measured unless it is marked **(docs)**.

## Recommendation

- **Endpoint:** `POST https://api.meta.ai/v1/chat/completions`, `Authorization: Bearer $META_API_KEY`. The key works exactly as stored in `.env`.
- **Model:** `muse-ember-1.0`. It was the fastest and is the cheapest. `.env` currently has `META_DECISION_MODEL=muse-spark-1.3`, which was about 2x slower and had a 4.3 s outlier — change it.
- **Alternate:** `muse-spark-1.2` if the decision budget goes above ~50 calls/min. Ember's live header says 60 requests/min for this team; spark-1.2 says 3000.
- **Always send** `reasoning_effort: "minimal"` and `max_completion_tokens: 1024`. Reasoning cannot be turned off on any text model, and reasoning tokens count against the token budget: with a small budget the reply is `content: null`, `finish_reason: "length"`.
- **Label:** use `response_format` `json_schema` with `strict: true` and an `enum` of the offered labels. Every one of 25 strict json_schema calls returned a valid label.
- **Probabilities and confidence: not obtainable. Report both as `null`.** `logprobs` was rejected on every model tried (ember, spark-1.3, spark-1.2) on both Chat Completions and Responses, with the reason "not supported with reasoning", and `n > 1` is rejected.
- **Timeout:** abort at 5000 ms per attempt. Keep the game-level 3 s decision deadline separate. Do not retry 400/401/404.

### Exact request body

```json
{
  "model": "muse-ember-1.0",
  "reasoning_effort": "minimal",
  "max_completion_tokens": 1024,
  "messages": [
    { "role": "system", "content": "<role line>\nChoose exactly ONE action:\n- report: Tell city police about a directly observed incident\n- gossip: Tell a nearby informant what you saw\n- ignore: Do nothing until there is stronger evidence\nDecide based ONLY on the JSON state in the user message. Respond with JSON only: {\"choice\":\"<label>\"}." },
    { "role": "user", "content": "<JSON.stringify(agent-local state)>" }
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "npc_choice",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": { "choice": { "type": "string", "enum": ["report", "gossip", "ignore"] } },
        "required": ["choice"],
        "additionalProperties": false
      }
    }
  }
}
```

Do not send: `logprobs`, `top_logprobs`, `n`, `reasoning_effort: "none"`, `reasoning`, `thinking`, or `tool_choice` other than `"auto"` — each returns HTTP 400. Leave `temperature` unset.

### How to parse

| Trace field | Source |
|---|---|
| label | `JSON.parse(choices[0].message.content).choice`. Require `finish_reason === "stop"`, then check it against the offered labels again. |
| probabilities | `null` |
| confidence | `null` |
| provider request id | response header `x-request-id` (a UUID; present on 200, 400, 401 and 404) |
| provider response id | body `id` (`chatcmpl-…`) |
| latency | client wall clock around the fetch. The API returns no server timing, so the Drex-style `evaluation_time_ms` is `null`. |
| usage | `usage.prompt_tokens`, `usage.completion_tokens` (includes reasoning), `usage.completion_tokens_details.reasoning_tokens`, `usage.prompt_tokens_details.cached_tokens` |
| model | body `model` |
| rate-limit state | headers `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens` |

Treat `finish_reason: "length"` or a label outside the offered set as a provider failure and fall back, labeled as fallback.

### Fields that must be null for Meta decisions

`probabilities`, `confidence`, `evaluationTimeMs`. The inspector should show "not exposed by provider" instead of a probability bar.

### Open product choice (not decided here)

The model will write its own probability estimates if the schema asks for them. One real call returned `{"choice":"ignore","probabilities":{"gossip":0.22,"ignore":0.68,"report":0.1}}`. These are the model's stated guesses, not token probabilities. If they are shown at all, they need their own field (for example `selfReportedProbabilities`) and an "uncalibrated, self-reported" label, never the Drex `probabilities` slot. Default: off.

## Reference implementation (run for real: one success, one 401)

```js
export async function metaChoice({
  apiKey,
  baseUrl = "https://api.meta.ai/v1",
  model = "muse-ember-1.0",
  instructions,
  labels, // { label: description }
  state, // agent-local snapshot (object)
  timeoutMs = 5000,
  fetchImpl = fetch,
}) {
  const offered = Object.keys(labels);
  const system =
    `${instructions}\nChoose exactly ONE action:\n` +
    offered.map((l) => `- ${l}: ${labels[l]}`).join("\n") +
    `\nDecide based ONLY on the JSON state in the user message. Respond with JSON only: {"choice":"<label>"}.`;
  const body = {
    model,
    reasoning_effort: "minimal", // "none" is rejected; every text model reasons
    max_completion_tokens: 1024, // reasoning tokens count against this; small budgets return content:null
    messages: [
      { role: "system", content: system },
      { role: "user", content: JSON.stringify(state) },
    ],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "npc_choice",
        strict: true,
        schema: {
          type: "object",
          properties: { choice: { type: "string", enum: offered } },
          required: ["choice"],
          additionalProperties: false,
        },
      },
    },
  };

  const started = performance.now();
  let res;
  try {
    res = await fetchImpl(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (e) {
    return {
      ok: false,
      errorType: e?.name === "TimeoutError" || e?.name === "AbortError" ? "timeout" : "network_error",
      retryable: true,
      httpStatus: null,
      providerRequestId: null,
      latencyMs: Math.round(performance.now() - started),
      message: String(e?.message ?? e),
    };
  }
  const latencyMs = Math.round(performance.now() - started);
  const providerRequestId = res.headers.get("x-request-id");
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {}

  if (!res.ok) {
    const err = json?.error ?? {};
    const retryAfter = res.headers.get("retry-after");
    return {
      ok: false,
      errorType: err.code ?? err.type ?? `http_${res.status}`,
      retryable: res.status === 429 || res.status >= 500,
      retryAfterMs: retryAfter ? Number(retryAfter) * 1000 : null,
      httpStatus: res.status,
      providerRequestId,
      latencyMs,
      message: err.message ?? text.slice(0, 200),
    };
  }

  const c = json?.choices?.[0];
  const usage = {
    inputTokens: json?.usage?.prompt_tokens ?? null,
    outputTokens: json?.usage?.completion_tokens ?? null, // includes reasoning tokens
    reasoningTokens: json?.usage?.completion_tokens_details?.reasoning_tokens ?? null,
    cachedInputTokens: json?.usage?.prompt_tokens_details?.cached_tokens ?? null,
  };
  let label = null;
  try {
    label = JSON.parse(c?.message?.content ?? "null")?.choice ?? null;
  } catch {}
  if (c?.finish_reason !== "stop" || !offered.includes(label)) {
    return {
      ok: false,
      errorType: c?.finish_reason === "length" ? "truncated_no_answer" : "invalid_label",
      retryable: false,
      httpStatus: res.status,
      providerRequestId,
      latencyMs,
      message: `finish_reason=${c?.finish_reason} content=${JSON.stringify(c?.message?.content)?.slice(0, 120)}`,
      usage,
    };
  }
  return {
    ok: true,
    decisionSource: "meta",
    model: json.model,
    choice: label,
    offered,
    probabilities: null, // not exposed: logprobs are rejected on reasoning models, n>1 rejected
    confidence: null, // never synthesize
    providerRequestId, // x-request-id response header (UUID)
    providerResponseId: json.id, // chatcmpl-...
    latencyMs, // client-measured wall clock
    evaluationTimeMs: null, // API returns no server-side timing
    usage,
  };
}
```

Real output of that function:

```text
success path: {"ok":true,"decisionSource":"meta","model":"muse-ember-1.0","choice":"report","offered":["report","gossip","ignore"],"probabilities":null,"confidence":null,"providerRequestId":"77867835-28fd-47ec-836b-3a162537fd4d","providerResponseId":"chatcmpl-01a1db76-50f3-7535-a19b-cd3f0ee504b2","latencyMs":876,"evaluationTimeMs":null,"usage":{"inputTokens":208,"outputTokens":44,"reasoningTokens":30,"cachedInputTokens":0}}
bad-key path: {"ok":false,"errorType":"invalid_api_key","retryable":false,"retryAfterMs":null,"httpStatus":401,"providerRequestId":"76f4f66f-8f74-4df2-99b3-d73a1a3539ef","latencyMs":96,"message":"Unauthorized"}
```

---

## Evidence

Prompt family used throughout: a witness NPC in a fictional city simulation choosing `report | gossip | ignore` from a small JSON state. Two states: a clear one (direct sighting of an incident, confidence 0.9) and an ambiguous one (possible sighting, confidence 0.45, witness distrusts police).

### 1. Reasoning cannot be turned off

| Attempt | Result |
|---|---|
| `reasoning_effort: "none"` on spark-1.3, ember, wonder | 400 `reasoning_effort 'none' is not supported for model '…'. Supported values: [minimal, low, medium, high, xhigh, max]` |
| `reasoning_effort: "none"` on spark-1.2, spark-1.1 | 400 `"reasoning_effort" does not support "none" with this model.` |
| `reasoning: {effort:"none"}`, `reasoning: {enabled:false}` | 400 ``unknown parameter `reasoning` `` |
| `thinking: {type:"disabled"}` | 400 ``unknown parameter `thinking` `` |
| No effort set, `max_tokens: 16`: ember, wonder, spark-1.1, spark-1.2, spark-1.3-contributor | 200, `content: null`, `finish_reason: "length"`, 14 of 16 tokens spent reasoning |
| spark-1.3 `minimal` and `low`, `max_tokens: 16` | Same: `content: null`, 14 of 16 tokens spent reasoning |
| spark-1.3, no effort set, `max_tokens: 512` | 200 in 3661 ms, `content: null`, 510 of 512 tokens spent reasoning |
| spark-1.3 `minimal`, `max_tokens: 256` | 200 in 1706 ms, `content: "report"`, 140 reasoning tokens |
| spark-1.3 `low`, `max_tokens: 2048` | 200 in 5079 ms, 386 reasoning tokens |

All six text models tested reason: muse-ember-1.0, muse-wonder-1.0, muse-spark-1.1, -1.2, -1.3 and muse-spark-1.3-contributor. muse-spark-1.2-contributor was not called. `minimal` is the floor.
**(docs)** Default effort is `max` on Wonder and Ember and `high` on Spark; "reasoning models always reason."
Adding "Decide instantly, with no deliberation" to the prompt did not reduce reasoning tokens (194, against 117–199 without it).

### 2. Logprobs do not exist for these models

| Attempt | Result |
|---|---|
| ember, `logprobs: true` | 400 `` `logprobs` is not supported with reasoning. `` |
| spark-1.3 `minimal` + `logprobs: true` | 400 `` `logprobs` cannot be true when `reasoning_effort` is `minimal`. logprobs is not supported with reasoning. `` |
| `/v1/responses` `minimal` + `include: ["message.output_text.logprobs"]` | 400 `` `message.output_text.logprobs` cannot be included when `reasoning.effort` is `minimal`. logprobs is not supported with reasoning. `` |
| `n: 5` | 400 `` `n` must be 1, got 5 `` |

So real per-label probabilities are not obtainable. Question 6 (single-letter labels, one question per label) is moot: there are no logprobs to clean up.

Repeated sampling is not a substitute. On the ambiguous state, 19 of 19 calls across five models returned `ignore`; on the clear state every call returned `report`. A vote over k calls would read 1/0/0 and cost k billed calls.

### 3. Getting a guaranteed-valid label

| Method | Result |
|---|---|
| `response_format` `json_schema`, `strict: true`, enum | Works. `content: "{\"choice\":\"report\"}"`. Valid label in 25 of 25 calls. One reply had a space after the colon, so parse with `JSON.parse`. |
| `response_format` `json_object` | Works (`{"choice": "report"}`), no enum guarantee |
| Tool with enum, `tool_choice: "required"` | 400 ``only `"auto"` is supported for `tool_choice` `` |
| Tool with enum, `tool_choice: "auto"` | Works in one call (`finish_reason: "tool_calls"`, arguments `{"choice":"report"}`), but it also wrote prose, used 679 prompt tokens, and calling the tool is not guaranteed |
| Schema that also asks for probabilities | Works; see "Open product choice" above |

### 4. Latency

`reasoning_effort: "minimal"`, strict json_schema, `max_completion_tokens: 1024`, ambiguous state, sequential calls, wall clock including network.

| Model | n | Median ms | Min | Max | Reasoning tokens | Completion tokens | Prompt tokens |
|---|---|---|---|---|---|---|---|
| muse-ember-1.0 | 5 | 1007 | 666 | 1632 | 113–166 | 127–180 | 217 |
| muse-spark-1.2 | 5 | 1924 | 1660 | 2068 | 204–321 | 218–335 | 168 |
| muse-spark-1.3 | 5 | 2255 | 2139 | 4267 | 242–502 | 256–516 | 217 |
| muse-spark-1.3-contributor | 2 | 2253, 2890 | | | 203, 269 | 217, 283 | 217 |
| muse-wonder-1.0 | 2 | 1914, 1326 | | | 163, 110 | 177, 124 | 168 |

Three more ember `minimal` calls on the clear state took 1184, 1862 and 876 ms. Across all eight ember calls: median 1096 ms, max 1862 ms.
Five calls per model is a small sample. These are observations from one laptop on one network, not a benchmark.

### 5. Error shapes and headers

```text
401  {"error":{"code":"invalid_api_key","message":"Unauthorized","param":null,"type":"authentication_error"}}
404  {"error":{"code":"model_not_found","message":"The requested model was not found. It may not exist, or this API key may not have access to it. Check the model name; GET /v1/models lists the models this API key can use.","param":null,"type":"invalid_request_error"}}
400  {"error":{"code":null,"message":"unknown parameter `reasoning`","param":"reasoning","type":"invalid_request_error"}}
```

Envelope is always `{"error":{code, message, param, type}}`. `x-request-id` was present on every response, including errors.
Headers on a 200: `x-request-id`, `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`, `x-route`.

Rate-limit header values seen for this team:

| Model | Requests | Tokens |
|---|---|---|
| muse-spark-1.3, muse-spark-1.2, muse-wonder-1.0 | 3000 | 4,000,000 |
| muse-spark-1.3-contributor | 150 | 3,000,000 |
| muse-ember-1.0, muse-spark-1.1 | 60 | 2,100,000 |

**(docs)** Limits are per minute and per team, not per key. The docs table lists ember at 3000 requests/min; the live header says 60, so trust the header.
**(docs)** On 429, back off exponentially with jitter from about 500 ms and honor `Retry-After` if present. Long non-streaming requests can return 504 `gateway_timeout`.

### 6. Other protocols and parameters

- `/v1/responses` works (`reasoning: {effort: "minimal"}`, `max_output_tokens`): 1999 ms, 284 reasoning tokens. Same limits, no advantage.
- `/v1/messages` accepts the Bearer key but returns an opaque reasoning `data` block and still reasons. No advantage.
- `max_completion_tokens` is accepted. `max_tokens` also works; **(docs)** it is a deprecated alias.
- `temperature: 0` with `seed: 7` is accepted. **(docs)** `seed` is deprecated and determinism is not guaranteed.
- The older host `api.llama.com` accepts only the pipe-separated form of the key and lists zero models. Do not use it.

### 7. Cost per decision (estimate)

Measured tokens multiplied by list prices **(docs)**: ember $0.10 in / $0.50 out per 1M tokens; spark 1.x $1.25 / $4.25; wonder $4 / $20.

| Model | Estimated cost per decision |
|---|---|
| muse-ember-1.0 | about $0.0001 |
| muse-spark-1.2 | about $0.0011–0.0016 |
| muse-spark-1.3 | about $0.0014–0.0025 |

This assumes reasoning tokens bill as output. `usage.completion_tokens` includes them, but the pricing page does not say so explicitly.

### Not verified

- The 429 response body and whether `Retry-After` is actually sent. No rate limit was hit.
- 5xx and 504 bodies.
- Streaming.
- Whether reasoning tokens are billed as output tokens.
