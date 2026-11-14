// Decision scheduler: bounded, event-driven, honest about who decided. Never applies actions or touches room state.
import { createHash } from "node:crypto";
import { RULES } from "@vice/contracts";
import type { DecisionMode, DecisionTrace, ProviderHealth, ProviderName, ProviderStatus, ShadowResult } from "@vice/contracts";
import type { Clock, DecisionProvider, DecisionRequest, ProviderResult, SchedulerPort } from "../domain";
import { ProviderError } from "../domain";

export interface SchedulerDeps {
  drex: DecisionProvider; meta: DecisionProvider; clock: Clock; mode: DecisionMode;
  concurrency: number; maxCallsPerMinute: number;
  onStatus?: (s: ProviderStatus) => void;
  /** In drex mode also ask Meta in the background (never applied) and attach the comparison to the trace. */
  shadowMeta?: boolean;
  /** Called when a shadow result lands after the trace was already delivered. */
  onShadow?: (trace: DecisionTrace) => void;
  /** Overrides for tests. */
  deadlineMs?: Partial<Record<ProviderName, number>>;
  maxQueue?: number;
}

interface Job {
  req: DecisionRequest; trace: DecisionTrace; onDone: (t: DecisionTrace) => void;
  provider: DecisionProvider; gen: number; done: boolean; started: boolean;
  controller: AbortController; timer: ReturnType<typeof setTimeout> | null; deadlineAt: number;
}
interface Health { calls: number[]; inFlight: number; failures: number; circuitOpenUntil: number | null; lastOkAtMs: number | null; lastErrorType: string | null; lastOutcome: "ok" | "fallback" | null }

/**
 * Provider error text is vendor-controlled, is broadcast to every client and is stored in SQLite. Strip anything
 * shaped like a credential (in case a gateway echoes the Authorization header) and bound its length.
 */
export function redactSecrets(message: string): string {
  return message
    .replace(/nace_sk_[A-Za-z0-9_-]+/g, "[redacted-key]")
    .replace(/LLM[|_][0-9]{6,}[|_][A-Za-z0-9_-]+/g, "[redacted-key]")
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .slice(0, 300);
}

export function stateHash(state: unknown): string {
  return createHash("sha256").update(JSON.stringify(state) ?? "null").digest("hex").slice(0, 16);
}

export function createScheduler(deps: SchedulerDeps): SchedulerPort {
  let mode = deps.mode;
  let gen = 0;
  const queue: Job[] = [];
  const active = new Map<string, Job>(); // dedupeKey -> job (queued or in flight)
  const health: Record<ProviderName, Health> = {
    drex: { calls: [], inFlight: 0, failures: 0, circuitOpenUntil: null, lastOkAtMs: null, lastErrorType: null, lastOutcome: null },
    meta: { calls: [], inFlight: 0, failures: 0, circuitOpenUntil: null, lastOkAtMs: null, lastErrorType: null, lastOutcome: null },
  };
  const providers: Record<ProviderName, DecisionProvider> = { drex: deps.drex, meta: deps.meta };
  const now = (): number => deps.clock.now();
  const maxQueue = deps.maxQueue ?? RULES.decisions.maxQueue;
  const deadlineFor = (p: ProviderName): number => deps.deadlineMs?.[p] ?? RULES.decisions.deadlineMs[p];

  const callsLastMinute = (p: ProviderName): number => {
    const h = health[p], cutoff = now() - 60000;
    while (h.calls.length && h.calls[0]! <= cutoff) h.calls.shift();
    return h.calls.length;
  };
  const circuitOpen = (p: ProviderName): boolean => {
    const h = health[p];
    if (h.circuitOpenUntil !== null && now() >= h.circuitOpenUntil) { h.circuitOpenUntil = null; h.failures = 0; }
    return h.circuitOpenUntil !== null;
  };
  const totalInFlight = (): number => health.drex.inFlight + health.meta.inFlight;

  function healthView(p: ProviderName): ProviderHealth {
    const h = health[p], prov = providers[p], configured = prov.isConfigured();
    const open = circuitOpen(p);
    const state: ProviderHealth["state"] = !configured ? "UNCONFIGURED" : open ? "DOWN" : h.lastErrorType ? "DEGRADED" : "LIVE";
    return {
      configured, state, model: prov.model, lastOkAtMs: h.lastOkAtMs, lastErrorType: h.lastErrorType,
      callsLastMinute: callsLastMinute(p), maxCallsPerMinute: deps.maxCallsPerMinute, inFlight: h.inFlight,
      circuitOpenUntilMs: h.circuitOpenUntil,
    };
  }
  function status(): ProviderStatus {
    const drex = healthView("drex"), meta = healthView("meta");
    let badge = "SCRIPTED";
    if (mode !== "scripted") {
      // LIVE only after the provider really answered the latest decision; PENDING before its first answer;
      // FALLBACK whenever the latest decision in this mode was not decided by the provider.
      const h = mode === "drex" ? drex : meta, last = health[mode].lastOutcome;
      const word = !h.configured || h.state === "DOWN" || last === "fallback" ? "FALLBACK" : last === null ? "PENDING" : "LIVE";
      badge = `${mode.toUpperCase()}: ${word}`;
    }
    return { mode, drex, meta, pending: active.size, badge };
  }
  const publish = (): void => { try { deps.onStatus?.(status()); } catch { /* observer errors never break decisions */ } };

  function newTrace(req: DecisionRequest): DecisionTrace {
    return {
      id: req.decisionId, roomId: req.roomId, runId: req.runId, agentId: req.agentId, agentName: req.agentName, role: req.role,
      status: "REQUESTED", mode, decisionSource: null, provider: null, model: null,
      question: req.question, offeredActions: { ...req.options }, chosenAction: null, providerChoice: null,
      confidence: null, probabilities: null, providerRequestId: null, latencyMs: null, providerEvalMs: null, usage: null,
      attempts: 0, errorType: null, errorMessage: null, rejectReason: null,
      stateSnapshot: req.state, stateHash: stateHash(req.state),
      triggerEventId: req.triggerEventId, correlationId: req.correlationId, effectEventIds: [],
      requestedAtMs: req.requestedAtMs, resolvedAtMs: null, requestedAtSequence: req.requestedAtSequence,
      citations: req.citations ?? [], shadow: null,
    };
  }
  const offered = (req: DecisionRequest, label: string): boolean => Object.hasOwn(req.options, label);
  const valid = (req: DecisionRequest, label: string): string => (offered(req, label) ? label : Object.keys(req.options)[0]!);

  function finish(job: Job): void {
    if (job.done) return;
    job.done = true;
    if (job.timer) clearTimeout(job.timer);
    if (active.get(job.req.dedupeKey) === job) active.delete(job.req.dedupeKey);
    const qi = queue.indexOf(job);
    if (qi >= 0) queue.splice(qi, 1);
    job.trace.resolvedAtMs = now();
    if (job.gen === gen) {
      try { job.onDone(job.trace); } catch (e) { console.error("[decisions] onDone threw", e); }
    }
    publish();
    pump();
  }
  function fallback(job: Job, errorType: string, message: string, status: "ERROR" | "TIMEOUT" = "ERROR"): void {
    if (job.done) return;
    const t = job.trace;
    t.status = status; t.decisionSource = "fallback"; t.chosenAction = valid(job.req, job.req.safeAction);
    health[job.provider.name].lastOutcome = "fallback";
    t.errorType = errorType; t.errorMessage = redactSecrets(message);
    // No provider answer was used: confidence/probabilities stay null by construction.
    finish(job);
  }
  function recordFailure(p: ProviderName, err: ProviderError): void {
    const h = health[p];
    h.lastErrorType = err.kind;
    if (err.kind === "aborted") return;
    h.failures += 1;
    const fatal = err.kind === "auth" || err.kind === "credit";
    if (fatal || h.failures >= RULES.decisions.circuitBreakerFailures) {
      const wait = Math.max(RULES.decisions.circuitBreakerOpenMs, err.opts.retryAfterMs ?? 0);
      h.circuitOpenUntil = now() + wait;
    }
  }

  function runShadow(job: Job): void {
    const p: ProviderName = "meta";
    if (!deps.shadowMeta || job.provider.name !== "drex" || !deps.meta.isConfigured() || circuitOpen(p)) return;
    if (callsLastMinute(p) >= deps.maxCallsPerMinute) return;
    health[p].calls.push(now());
    const base: ShadowResult = { provider: "meta", model: deps.meta.model, chosenAction: null, latencyMs: null, providerRequestId: null, errorType: null, agrees: null };
    const attach = (s: ShadowResult): void => {
      if (job.gen !== gen) return;
      s.agrees = s.chosenAction !== null && job.trace.providerChoice !== null ? s.chosenAction === job.trace.providerChoice : null;
      job.trace.shadow = s;
      if (s.errorType === null) { health[p].failures = 0; health[p].lastOkAtMs = now(); health[p].lastErrorType = null; }
      if (job.done) { try { deps.onShadow?.(job.trace); } catch { /* ignore */ } }
    };
    deps.meta.decide({ question: job.req.question, options: job.req.options, state: job.req.state }, { timeoutMs: deadlineFor("meta") })
      .then((r) => attach({ ...base, model: r.model, chosenAction: r.choice, latencyMs: r.latencyMs, providerRequestId: r.requestId }))
      .catch((e) => {
        // A rejected or rate-limited Meta key must show in Meta's health and open its breaker, not be retried on every decision.
        recordFailure(p, e instanceof ProviderError ? e : new ProviderError("network", String(e), { retryable: true }));
        attach({ ...base, errorType: e instanceof ProviderError ? e.kind : "network" });
      });
  }

  function start(job: Job): void {
    const p = job.provider.name, h = health[p];
    job.started = true;
    if (circuitOpen(p)) return fallback(job, "circuit_open", `${p} circuit breaker is open`);
    if (callsLastMinute(p) >= deps.maxCallsPerMinute) return fallback(job, "budget_exceeded", `${p} call budget of ${deps.maxCallsPerMinute}/min reached`);
    const remaining = job.deadlineAt - now();
    if (remaining <= 0) return fallback(job, "timeout", "decision deadline passed while queued", "TIMEOUT");
    h.calls.push(now());
    h.inFlight += 1;
    job.trace.provider = p; job.trace.model = job.provider.model;
    runShadow(job);
    let settled = false;
    // A freed slot always restarts the queue (a timed-out or cancelled call frees its slot only when its promise settles).
    const release = (): void => { if (!settled) { settled = true; h.inFlight -= 1; queueMicrotask(pump); } };
    job.provider.decide({ question: job.req.question, options: job.req.options, state: job.req.state }, { signal: job.controller.signal, timeoutMs: remaining })
      .then((r: ProviderResult) => {
        release();
        if (job.gen !== gen) return;
        if (job.done) { h.lastOkAtMs = now(); return; } // late answer after the deadline: discarded
        if (!offered(job.req, r.choice)) {
          const e = new ProviderError("invalid_response", `provider returned unoffered label "${r.choice}"`, { requestId: r.requestId });
          recordFailure(p, e);
          job.trace.providerRequestId = r.requestId; job.trace.latencyMs = r.latencyMs; job.trace.attempts = r.attempts;
          return fallback(job, e.kind, e.message);
        }
        h.failures = 0; h.lastOkAtMs = now(); h.lastErrorType = null; h.lastOutcome = "ok";
        const t = job.trace;
        t.model = r.model ?? job.provider.model; t.providerChoice = r.choice;
        // The Meta shadow may have answered first: agreement can only be judged once Drex's own label is known.
        if (t.shadow && t.shadow.chosenAction !== null) t.shadow.agrees = t.shadow.chosenAction === r.choice;
        t.confidence = r.confidence; t.probabilities = r.probabilities;
        t.providerRequestId = r.requestId; t.latencyMs = r.latencyMs; t.providerEvalMs = r.providerEvalMs;
        t.usage = r.usage; t.attempts = r.attempts; t.status = "RESOLVED";
        if (p === "drex" && r.confidence !== null && r.confidence < RULES.decisions.lowConfidenceThreshold) {
          t.decisionSource = "drex_low_confidence_guardrail"; t.chosenAction = valid(job.req, job.req.safeAction);
        } else {
          t.decisionSource = p; t.chosenAction = r.choice;
        }
        finish(job);
      })
      .catch((err: unknown) => {
        release();
        if (job.gen !== gen) return;
        const e = err instanceof ProviderError ? err : new ProviderError("network", err instanceof Error ? err.message : String(err), { retryable: true });
        recordFailure(p, e);
        if (job.done) return;
        job.trace.providerRequestId = e.opts.requestId ?? null;
        fallback(job, e.kind, e.message, e.kind === "timeout" ? "TIMEOUT" : "ERROR");
      });
  }

  function pump(): void {
    while (queue.length && totalInFlight() < Math.max(1, deps.concurrency)) {
      const job = queue.shift()!;
      if (job.done) continue;
      start(job);
    }
  }

  return {
    request(req, onDone) {
      if (active.has(req.dedupeKey)) return null;
      if (queue.length >= maxQueue) return null;
      const trace = newTrace(req);
      const job: Job = {
        req, trace, onDone, provider: mode === "meta" ? deps.meta : deps.drex, gen, done: false, started: false,
        controller: new AbortController(), timer: null, deadlineAt: 0,
      };
      active.set(req.dedupeKey, job);
      if (mode === "scripted") {
        // Scripted baseline: no provider is called, by design.
        job.timer = setTimeout(() => {
          trace.status = "RESOLVED"; trace.decisionSource = "scripted"; trace.chosenAction = valid(req, req.scriptedAction);
          finish(job);
        }, 0);
      } else if (!job.provider.isConfigured()) {
        job.timer = setTimeout(() => fallback(job, "unconfigured", `${job.provider.name} API key is not configured`), 0);
      } else {
        const d = deadlineFor(job.provider.name);
        job.deadlineAt = now() + d;
        job.timer = setTimeout(() => {
          if (job.done) return;
          job.controller.abort();
          const e = new ProviderError("timeout", `no ${job.provider.name} answer within ${d} ms`, { retryable: true });
          if (job.started) recordFailure(job.provider.name, e);
          fallback(job, "timeout", e.message, "TIMEOUT");
        }, d);
        queue.push(job);
        // Start on a microtask, never inside request(): a circuit-open or budget fallback would otherwise call onDone
        // before the caller has the trace, and the NPC would stay pending forever.
        queueMicrotask(pump);
      }
      publish();
      return trace;
    },
    setMode(m) { mode = m; publish(); },
    getMode: () => mode,
    status,
    pending: () => active.size,
    cancelAll() {
      gen += 1;
      for (const job of active.values()) { job.done = true; if (job.timer) clearTimeout(job.timer); job.controller.abort(); }
      active.clear(); queue.length = 0;
      publish();
    },
  };
}
