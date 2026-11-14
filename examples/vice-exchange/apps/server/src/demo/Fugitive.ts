// The hunted agent: an autonomous OUTLAW that thinks with the room's decision scheduler (Drex in drex mode) and acts
// only through the same commands a human client sends, so every rule, check and event is identical to a human's.
import { RULES, SPAWN_POINTS, dist, hasLineOfSight, wantedLevel } from "@vice/contracts";
import type { DecisionTrace, NpcRole, Vec2 } from "@vice/contracts";
import type { CharacterState, DecisionRequest } from "../domain";
import type { RoomEngine } from "../engine/RoomEngine";
import { findPath } from "../engine/pathfinding";

export type FugitiveAction = "do_job" | "lay_low" | "flee" | "fire";
/** Decision traces are typed by NPC role; the fugitive is not one of the four, so its kind is carried at runtime. */
export const FUGITIVE_ROLE = "FUGITIVE" as unknown as NpcRole;
const NAME = "Ryder";
const PACE = 0.6; // of player speed: slower than a pursuing officer, so the hunt can end
const SPAWN_DELAY_MS = 3000;
const HIDEOUTS: Vec2[] = [...SPAWN_POINTS.OUTLAW, ...SPAWN_POINTS.HUNTER];
interface Pursuer { id: string; kind: "police" | "hunter"; name: string; x: number; z: number; d: number }

export class FugitiveAgent {
  playerId: string | null = null;
  action: FugitiveAction = "lay_low";
  decisions = 0;
  private bornAt: number; private runId = ""; private nextThinkAt = 0; private pending = false; private recent: string[] = [];
  private path: Vec2[] = []; private goal: Vec2 | null = null; private n = 0; private downAt: number | null = null;
  private wasWanted = false; private seenSeq = 0; private lastFireAt = 0; private lastThreat: Vec2 | null = null; private moving = false;

  constructor(private engine: RoomEngine) { this.bornAt = engine.deps.clock.now(); }

  private now(): number { return this.engine.deps.clock.now(); }
  character(): CharacterState | null {
    const p = this.playerId ? this.engine.state.players[this.playerId] : undefined;
    return p?.characterId ? this.engine.state.characters[p.characterId] ?? null : null;
  }
  /** What this agent can see right now: police and hunters within the perception radius with line of sight. */
  pursuers(c: Vec2): Pursuer[] {
    const s = this.engine.state; const out: Pursuer[] = [];
    for (const n of Object.values(s.npcs)) {
      if (n.role !== "POLICE") continue;
      const d = dist(c, n);
      if (d <= RULES.perceptionRadius && hasLineOfSight(c, n)) out.push({ id: n.id, kind: "police", name: n.name, x: n.x, z: n.z, d });
    }
    for (const h of Object.values(s.characters)) {
      if (h.role !== "HUNTER" || (h.status !== "ACTIVE" && h.status !== "WANTED")) continue;
      const d = dist(c, h);
      if (d <= RULES.perceptionRadius && hasLineOfSight(c, h)) out.push({ id: h.id, kind: "hunter", name: h.nickname, x: h.x, z: h.z, d });
    }
    return out.sort((a, b) => a.d - b.d);
  }

  /** Call once per engine tick, before engine.tick(). */
  step(): void {
    const e = this.engine; const now = this.now();
    if (!this.playerId) {
      if (now - this.bornAt < SPAWN_DELAY_MS) return;
      this.playerId = e.addPlayer({ nickname: NAME, role: "OUTLAW", isBot: true, sessionToken: `fugitive_${e.opts.roomId}` }).id;
      this.runId = e.state.runId; this.nextThinkAt = now + 1000;
      return;
    }
    if (e.state.runId !== this.runId) { // demo.reset: same player, fresh character
      this.runId = e.state.runId; this.pending = false; this.path = []; this.goal = null; this.downAt = null; this.recent = [];
      this.action = "lay_low"; this.wasWanted = false; this.nextThinkAt = now + 1500; this.lastThreat = null;
    }
    const c = this.character();
    if (!c || (c.status !== "ACTIVE" && c.status !== "WANTED")) {
      if (this.downAt === null) { this.downAt = now; this.path = []; this.goal = null; this.pending = false; return; }
      if (c && now - this.downAt >= RULES.fugitive.respawnDelayMs) {
        const ack = e.dispatch(this.playerId, { type: "character.respawn", commandId: `fug-respawn-${++this.n}` });
        if (ack.ok) { this.downAt = null; this.action = "lay_low"; this.recent = []; this.wasWanted = false; this.nextThinkAt = now + 1500; }
        else this.downAt = now; // try again after another delay
      }
      return;
    }
    this.downAt = null;
    const seen = this.pursuers(c);
    if (seen[0]) this.lastThreat = { x: seen[0].x, z: seen[0].z };
    // Think on a timer, and at once when it becomes wanted or is fired on.
    let urgent = false;
    const wanted = c.status === "WANTED";
    if (wanted && !this.wasWanted) urgent = true;
    this.wasWanted = wanted;
    for (let i = e.events.length - 1; i >= 0; i--) {
      const ev = e.events[i]!;
      if (ev.serverSequence <= this.seenSeq) break;
      if (ev.type === "STUNNED" && (ev.payload as { targetId?: string }).targetId === c.id) urgent = true;
    }
    this.seenSeq = e.state.lastEventSequence;
    if (!this.pending && (urgent || now >= this.nextThinkAt)) this.think(c, seen);
    this.act(c, seen);
  }

  private think(c: CharacterState, seen: Pursuer[]): void {
    const e = this.engine; const now = this.now(); const s = e.state;
    const mission = Object.values(s.missions).find((m) => m.status === "AVAILABLE");
    const view = e.characterView(c);
    const ammo = view.ammo ?? 0;
    const inRange = seen.filter((p) => p.d <= RULES.weapon.rangeM);
    const options: Record<string, string> = {
      lay_low: "Hide and wait. Right when I am wanted or a pursuer was just near, because heat cools while I stay out of sight. Wrong when nobody is after me and a job is open, because waiting earns little",
    };
    if (mission) options.do_job = "Go and take the open job. Right when I am not wanted and no pursuer is in sight: a job raises my price far more than waiting does. Wrong when police or hunters are close";
    if (seen.length) options.flee = "Run from the nearest pursuer I can see. Right when one is closing in and I cannot or should not stun them";
    if (inRange.length && ammo > 0) options.fire = "Stun the nearest pursuer in range, then run. Right when they are about to reach me: it stops them for three seconds, costs one charge and adds heat";
    const wanted = c.status === "WANTED";
    // Nothing to choose between: keep hiding and do not spend a model call on a one-option question.
    if (Object.keys(options).length < 2) { this.action = "lay_low"; this.nextThinkAt = now + 2000; return; }
    const state = {
      role: "fugitive", name: NAME,
      goal: "Make my own ticker as valuable as I can and never get captured. Jobs raise my price the most, surviving adds a little every few seconds, and capture ends me at zero.",
      situation: wanted ? "I am WANTED: police and hunters may capture me." : seen.length ? "Not wanted, but a pursuer is in sight." : "Not wanted and nobody is after me right now.",
      heat: Math.round(c.heat), wanted, wanted_level: wantedLevel(c.heat),
      my_ticker: c.ticker, my_price: c.ticker ? e.marketSummary(c.ticker)?.spotPrice ?? null : null, bounty_on_me: e.deps.economy.bountyFor(s, c.id, now),
      seconds_survived: Math.round((now - c.spawnTimeMs) / 1000), jobs_done: c.completedMissions, stun_charges: ammo,
      open_job: mission ? { label: mission.label, distance_m: Math.round(dist(c, mission)) } : null,
      pursuers_in_sight: seen.map((p) => ({ kind: p.kind, name: p.name, distance_m: Math.round(p.d) })),
      my_recent_actions: this.recent.slice(-4),
    };
    const scripted = wanted ? (options.flee ? "flee" : "lay_low") : (options.do_job ? "do_job" : "lay_low");
    const decisionId = e.nextId("dec");
    const req: DecisionRequest = {
      decisionId, roomId: s.id, runId: s.runId, agentId: c.id, agentName: NAME, role: FUGITIVE_ROLE,
      question: "As this hunted outlaw, what do you do next to stay free and grow your own value?",
      options, state, scriptedAction: scripted, safeAction: "lay_low",
      triggerEventId: e.events[e.events.length - 1]?.eventId ?? "", correlationId: `hunt_${c.id}`,
      requestedAtMs: now, requestedAtSequence: s.lastEventSequence, citations: [], dedupeKey: `${c.id}:${decisionId}`,
    };
    this.pending = true; this.nextThinkAt = now + RULES.fugitive.thinkEveryMs;
    const first = e.deps.scheduler.request(req, (t) => this.resolved(req, t));
    if (!first) { this.pending = false; return; }
    first.agentKind = "FUGITIVE";
    e.emit("DECISION_REQUESTED", c.id, { decisionId, agentId: c.id, role: FUGITIVE_ROLE, mode: s.mode, offeredActions: Object.keys(options), triggerEventId: req.triggerEventId, stateHash: first.stateHash }, { correlationId: req.correlationId });
    e.recordAgentTrace(first);
  }

  private resolved(req: DecisionRequest, t: DecisionTrace): void {
    const e = this.engine; const c = this.character();
    this.pending = false; this.nextThinkAt = this.now() + RULES.fugitive.thinkEveryMs;
    t.agentKind = "FUGITIVE";
    const live = !!c && c.id === req.agentId && (c.status === "ACTIVE" || c.status === "WANTED") && t.runId === e.state.runId;
    const chosen = t.chosenAction && t.chosenAction in req.options ? (t.chosenAction as FugitiveAction) : null;
    const source = t.decisionSource ?? "fallback";
    if (!live || !chosen) {
      t.status = "REJECTED"; t.rejectReason = live ? "action not offered" : "stale: the character was captured or the run was reset";
    } else {
      t.status = source === "fallback" ? "FALLBACK_APPLIED" : "APPLIED";
      this.action = chosen; this.path = []; this.goal = null; this.decisions += 1;
      this.recent.push(chosen); if (this.recent.length > 8) this.recent.shift();
    }
    if (t.runId !== e.state.runId) { e.recordAgentTrace(t); return; }
    const opts = { correlationId: req.correlationId };
    const res = e.emit("DECISION_RESOLVED", req.agentId, {
      decisionId: t.id, agentId: req.agentId, role: FUGITIVE_ROLE, status: t.status, decisionSource: source, provider: t.provider, model: t.model,
      chosenAction: t.chosenAction, providerChoice: t.providerChoice, offeredActions: Object.keys(req.options), confidence: t.confidence,
      probabilities: t.probabilities, providerRequestId: t.providerRequestId, latencyMs: t.latencyMs, providerEvalMs: t.providerEvalMs,
      errorType: t.errorType, rejectReason: t.rejectReason,
    }, opts);
    t.effectEventIds.push(res.eventId);
    if (live && chosen) {
      const detail = chosen === "do_job" ? "heading for the open job" : chosen === "lay_low" ? "lying low away from the last pursuer" : chosen === "flee" ? "running from the nearest pursuer" : "firing a stun pulse at the nearest pursuer";
      const ev = e.emit("AGENT_ACTION_APPLIED", req.agentId, { decisionId: t.id, agentId: req.agentId, role: FUGITIVE_ROLE, action: chosen, decisionSource: source, detail }, { ...opts, causationId: res.eventId });
      t.effectEventIds.push(ev.eventId);
    }
    e.recordAgentTrace(t);
  }

  private stop(): void {
    if (!this.moving || !this.playerId) return;
    this.moving = false; this.engine.dispatch(this.playerId, { type: "player.move", dirX: 0, dirZ: 0 });
  }
  private move(c: CharacterState, to: Vec2): void {
    const d = dist(c, to);
    if (d < 0.05 || !this.playerId) return this.stop();
    this.moving = true;
    this.engine.dispatch(this.playerId, { type: "player.move", dirX: ((to.x - c.x) / d) * PACE, dirZ: ((to.z - c.z) / d) * PACE });
  }
  private walkTo(c: CharacterState, goal: Vec2, arriveWithin: number): boolean {
    if (dist(c, goal) <= arriveWithin) { this.stop(); return true; }
    if (!this.goal || this.goal.x !== goal.x || this.goal.z !== goal.z || !this.path.length) { this.goal = { x: goal.x, z: goal.z }; this.path = findPath(c, this.goal, 0.75) ?? [this.goal]; }
    while (this.path.length > 1 && dist(c, this.path[0]!) < 0.7) this.path.shift();
    this.move(c, this.path[0] ?? this.goal);
    return false;
  }
  private hideout(c: CharacterState): Vec2 {
    const from = this.lastThreat ?? c;
    return HIDEOUTS.reduce((best, h) => (dist(h, from) > dist(best, from) ? h : best), HIDEOUTS[0]!);
  }

  private act(c: CharacterState, seen: Pursuer[]): void {
    if (!this.playerId) return;
    const e = this.engine; const now = this.now();
    if (this.action === "do_job") {
      const m = Object.values(e.state.missions).find((x) => x.status === "AVAILABLE");
      if (!m) return this.stop();
      if (this.walkTo(c, m, RULES.missionInteractRadius - 0.5)) e.dispatch(this.playerId, { type: "mission.interact", missionId: m.id, commandId: `fug-job-${++this.n}` });
      return;
    }
    if (this.action === "fire") {
      const target = seen.find((p) => p.d <= RULES.weapon.rangeM);
      if (target && now - this.lastFireAt >= RULES.weapon.cooldownMs) {
        this.lastFireAt = now;
        e.dispatch(this.playerId, { type: "weapon.fire", targetId: target.id, commandId: `fug-fire-${++this.n}` });
      }
    }
    if ((this.action === "flee" || this.action === "fire") && seen[0]) {
      const p = seen[0]; const d = Math.max(0.01, p.d);
      return this.move(c, { x: c.x + ((c.x - p.x) / d) * 6, z: c.z + ((c.z - p.z) / d) * 6 });
    }
    this.walkTo(c, this.hideout(c), 1.5);
  }
}
