// Real decision providers. Neither ever invents a number: what the API does not return is null.
import {
  APIConnectionError, APIError, APITimeoutError, APIUserAbortError, AuthenticationError, InsufficientCreditError,
  NaceClient, OverloadedError, RateLimitError, UnprocessableEntityError, choice, type JsonValue,
} from "nace-sdk";
import type { DecisionProvider, ProviderCall, ProviderResult } from "../domain";
import { ProviderError } from "../domain";

type FetchLike = typeof fetch;
export interface DrexProviderConfig { apiKey: string; model?: string; baseURL?: string; fetch?: FetchLike }
export interface MetaProviderConfig { apiKey: string; baseUrl?: string; model?: string; fetch?: FetchLike }

const CALL_HEADER = "x-vice-call";
function readHeader(init: RequestInit | undefined, name: string): string | null {
  const h = init?.headers;
  if (!h) return null;
  if (h instanceof Headers) return h.get(name);
  if (Array.isArray(h)) return h.find(([k]) => k.toLowerCase() === name)?.[1] ?? null;
  for (const [k, v] of Object.entries(h as Record<string, string>)) if (k.toLowerCase() === name) return v;
  return null;
}

/** Nace Drex via the official SDK: one typed `choice` question per call. */
export function createDrexProvider(cfg: DrexProviderConfig): DecisionProvider {
  const model = cfg.model || "drex-latest";
  const attempts = new Map<string, number>();
  let seq = 0;
  let client: NaceClient | null = null;
  const baseFetch: FetchLike = cfg.fetch ?? fetch;
  const countingFetch: FetchLike = (input, init) => {
    const id = readHeader(init, CALL_HEADER) ?? (input instanceof Request ? input.headers.get(CALL_HEADER) : null);
    if (id) attempts.set(id, (attempts.get(id) ?? 0) + 1);
    return baseFetch(input, init);
  };
  const getClient = (): NaceClient => {
    // Lazy: `new NaceClient()` throws without a key, and a keyless server must still boot.
    client ??= new NaceClient({
      apiKey: cfg.apiKey, defaultModel: model, logLevel: "warn", clientInfo: "vice-exchange/0.1.0",
      fetch: countingFetch, ...(cfg.baseURL ? { baseURL: cfg.baseURL } : {}),
    });
    return client;
  };

  return {
    name: "drex",
    model,
    isConfigured: () => cfg.apiKey.length > 0,
    async decide(call: ProviderCall, opts): Promise<ProviderResult> {
      if (!cfg.apiKey) throw new ProviderError("unconfigured", "NACE_API_KEY is not set", { retryable: false });
      const callId = `c${++seq}`;
      const controller = new AbortController();
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, opts.timeoutMs);
      const onOuter = (): void => controller.abort();
      if (opts.signal?.aborted) controller.abort();
      opts.signal?.addEventListener("abort", onOuter, { once: true });
      const started = performance.now();
      try {
        const result = await getClient().systemOne(
          { state: call.state as JsonValue, questions: { next_action: choice(call.question, call.options) } },
          {
            signal: controller.signal, timeout: opts.timeoutMs,
            // At most one retry and never past the caller's deadline. 401/402/422 are never retried by the SDK.
            retry: { maxRetries: 1, timeoutMs: opts.timeoutMs },
            headers: { [CALL_HEADER]: callId },
          },
        );
        const latencyMs = Math.round(performance.now() - started);
        const answer = result.answers.next_action;
        const requestId = result.request_id ?? null;
        if (!answer || typeof answer.choice !== "string" || !Object.hasOwn(call.options, answer.choice)) {
          throw new ProviderError("invalid_response", `Drex returned a label that was not offered: ${String(answer?.choice)}`, { retryable: false, requestId });
        }
        return {
          provider: "drex", model: result.model ?? null, choice: answer.choice,
          confidence: typeof answer.confidence === "number" ? answer.confidence : null,
          probabilities: answer.probabilities ? { ...answer.probabilities } : null,
          requestId, latencyMs,
          providerEvalMs: typeof result.evaluation_time_ms === "number" ? result.evaluation_time_ms : null,
          usage: { inputTokens: result.usage?.input_tokens ?? null, outputTokens: result.usage?.output_tokens ?? null },
          attempts: attempts.get(callId) ?? 1,
        };
      } catch (err) {
        throw mapDrexError(err, timedOut);
      } finally {
        clearTimeout(timer);
        opts.signal?.removeEventListener("abort", onOuter);
        attempts.delete(callId);
      }
    },
  };
}

function mapDrexError(err: unknown, timedOut: boolean): ProviderError {
  if (err instanceof ProviderError) return err;
  const msg = err instanceof Error ? err.message : String(err);
  if (err instanceof APIUserAbortError) {
    return timedOut ? new ProviderError("timeout", "Drex call exceeded the decision deadline", { retryable: true })
      : new ProviderError("aborted", "Drex call aborted", { retryable: false });
  }
  if (err instanceof APITimeoutError) return new ProviderError("timeout", msg, { retryable: true });
  if (err instanceof APIConnectionError) return new ProviderError("network", msg, { retryable: true });
  if (err instanceof APIError) {
    const o = { status: err.status, requestId: err.requestId ?? null };
    if (err instanceof AuthenticationError || err.status === 401) return new ProviderError("auth", msg, { ...o, retryable: false });
    if (err instanceof InsufficientCreditError || err.status === 402) return new ProviderError("credit", msg, { ...o, retryable: false });
    if (err instanceof RateLimitError || err.status === 429) return new ProviderError("rate_limit", msg, { ...o, retryable: true, retryAfterMs: (err as RateLimitError).retryAfterMs });
    if (err instanceof OverloadedError || err.status === 529) return new ProviderError("overloaded", msg, { ...o, retryable: true });
    if (err instanceof UnprocessableEntityError || err.status === 422) return new ProviderError("invalid_request", msg, { ...o, retryable: false });
    if (err.status >= 500) return new ProviderError("server", msg, { ...o, retryable: true });
    return new ProviderError("invalid_request", msg, { ...o, retryable: false });
  }
  if (timedOut) return new ProviderError("timeout", msg, { retryable: true });
  return new ProviderError("network", msg, { retryable: true });
}

/** Meta model API (OpenAI-style chat completions) as the LLM baseline. Probabilities/confidence are not exposed: null. */
export function createMetaProvider(cfg: MetaProviderConfig): DecisionProvider {
  const model = cfg.model || "muse-ember-1.0";
  const baseUrl = (cfg.baseUrl || "https://api.meta.ai/v1").replace(/\/+$/, "");
  const doFetch: FetchLike = cfg.fetch ?? fetch;
  return {
    name: "meta",
    model,
    isConfigured: () => cfg.apiKey.length > 0,
    async decide(call: ProviderCall, opts): Promise<ProviderResult> {
      if (!cfg.apiKey) throw new ProviderError("unconfigured", "META_API_KEY is not set", { retryable: false });
      const offered = Object.keys(call.options);
      const system = `${call.question}\nChoose exactly ONE action:\n${offered.map((l) => `- ${l}: ${call.options[l]}`).join("\n")}\n`
        + `Decide based ONLY on the JSON state in the user message. Respond with JSON only: {"choice":"<label>"}.`;
      const body = {
        model, reasoning_effort: "minimal", max_completion_tokens: 1024,
        messages: [{ role: "system", content: system }, { role: "user", content: JSON.stringify(call.state) }],
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "npc_choice", strict: true,
            schema: { type: "object", properties: { choice: { type: "string", enum: offered } }, required: ["choice"], additionalProperties: false },
          },
        },
      };
      const controller = new AbortController();
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, opts.timeoutMs);
      const onOuter = (): void => controller.abort();
      if (opts.signal?.aborted) controller.abort();
      opts.signal?.addEventListener("abort", onOuter, { once: true });
      const started = performance.now();
      try {
        let res: Response;
        try {
          res = await doFetch(`${baseUrl}/chat/completions`, {
            method: "POST", headers: { Authorization: `Bearer ${cfg.apiKey}`, "Content-Type": "application/json" },
            body: JSON.stringify(body), signal: controller.signal,
          });
        } catch (e) {
          if (timedOut) throw new ProviderError("timeout", "Meta call exceeded the decision deadline", { retryable: true });
          if (opts.signal?.aborted) throw new ProviderError("aborted", "Meta call aborted", { retryable: false });
          throw new ProviderError("network", e instanceof Error ? e.message : String(e), { retryable: true });
        }
        const latencyMs = Math.round(performance.now() - started);
        const requestId = res.headers.get("x-request-id");
        const text = await res.text();
        let json: any = null;
        try { json = JSON.parse(text); } catch { /* handled below */ }
        if (!res.ok) {
          const m = String(json?.error?.message ?? `HTTP ${res.status}`);
          const o = { status: res.status, requestId };
          const ra = Number(res.headers.get("retry-after-ms")) || (Number(res.headers.get("retry-after")) * 1000) || undefined;
          if (res.status === 401 || res.status === 403) throw new ProviderError("auth", m, { ...o, retryable: false });
          if (res.status === 402) throw new ProviderError("credit", m, { ...o, retryable: false });
          if (res.status === 429) throw new ProviderError("rate_limit", m, { ...o, retryable: true, retryAfterMs: ra });
          if (res.status === 529) throw new ProviderError("overloaded", m, { ...o, retryable: true, retryAfterMs: ra });
          if (res.status >= 500) throw new ProviderError("server", m, { ...o, retryable: true });
          throw new ProviderError("invalid_request", m, { ...o, retryable: false });
        }
        const choiceMsg = json?.choices?.[0];
        const content = choiceMsg?.message?.content;
        if (typeof content !== "string" || choiceMsg?.finish_reason !== "stop") {
          throw new ProviderError("invalid_response", `Meta returned no usable content (finish_reason=${String(choiceMsg?.finish_reason)})`, { retryable: false, requestId });
        }
        let label: unknown;
        try { label = JSON.parse(content).choice; } catch { label = undefined; }
        if (typeof label !== "string" || !Object.hasOwn(call.options, label)) {
          throw new ProviderError("invalid_response", "Meta returned a label that was not offered", { retryable: false, requestId });
        }
        return {
          provider: "meta", model: typeof json.model === "string" ? json.model : model, choice: label,
          confidence: null, probabilities: null, // not exposed by this API
          requestId, latencyMs, providerEvalMs: null,
          usage: { inputTokens: json.usage?.prompt_tokens ?? null, outputTokens: json.usage?.completion_tokens ?? null },
          attempts: 1,
        };
      } finally {
        clearTimeout(timer);
        opts.signal?.removeEventListener("abort", onOuter);
      }
    },
  };
}
