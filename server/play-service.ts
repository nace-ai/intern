import { randomBytes, randomUUID } from "node:crypto";
import { Mission, type TrainingMissionCheckpoint } from "./mission";
import {
  campaignTraining,
  campaignResolve,
  type CampaignGame,
} from "./campaign-engine";
import { coreLiveTurn } from "./live-core";
import {
  checkTurn,
  conversation,
  resolveTurn,
  type Decision,
} from "../src/insider";
import {
  missions,
  strategies,
  type MissionId,
  type TrainingChoice,
} from "../src/missions";
import { usageCaps, type Runtime, type RuntimeFactory } from "./byok";
import { rank } from "../src/engine";
type Session = {
  mission: Mission;
  owner: string;
  runtime?: Runtime;
  busy: boolean;
  last: number;
  captured: boolean;
};
export type TrainingCheckpoint = {
  version: 1;
  sessions: {
    id: string;
    owner: string;
    mission: TrainingMissionCheckpoint;
    last: number;
    captured: boolean;
  }[];
  rates: [string, { since: number; count: number }][];
  scores: { missionId: string; mode: string; score: number; rank: string }[];
};
export class PlayService {
  #sessions = new Map<string, Session>();
  #rates = new Map<string, { since: number; count: number }>();
  #scores: { missionId: string; mode: string; score: number; rank: string }[] =
    [];
  constructor(
    private factory: RuntimeFactory,
    private liveAvailable = false,
    private now = Date.now,
  ) {}
  // Server-internal snapshots deliberately exclude Live sessions and Runtime objects.
  trainingCheckpoint(): TrainingCheckpoint {
    this.sweep();
    return structuredClone({
      version: 1,
      sessions: [...this.#sessions]
        .filter(([, s]) => s.mission.game.mode === "tutorial")
        .map(([id, s]) => ({
          id,
          owner: s.owner,
          mission: s.mission.trainingCheckpoint(),
          last: s.last,
          captured: s.captured,
        })),
      rates: [...this.#rates],
      scores: this.#scores.filter((s) => s.mode === "tutorial"),
    });
  }
  restoreTraining(saved: TrainingCheckpoint) {
    if (saved.version !== 1) throw Error("Unknown Training state version.");
    this.dispose();
    for (const s of saved.sessions)
      this.#sessions.set(s.id, {
        mission: Mission.restoreTraining(s.mission),
        owner: s.owner,
        last: s.last,
        captured: s.captured,
        busy: false,
      });
    this.#rates = new Map(saved.rates);
    this.#scores = structuredClone(saved.scores);
    this.sweep();
  }
  get hasExpiringState() {
    return this.#sessions.size > 0 || this.#rates.size > 0;
  }
  sweep() {
    for (const [id, s] of this.#sessions)
      if (this.now() - s.mission.game.started >= 600000) {
        s.runtime?.dispose();
        this.#sessions.delete(id);
      }
    for (const [ip, r] of this.#rates)
      if (this.now() - r.since > 60000) this.#rates.delete(ip);
  }
  dispose() {
    for (const s of this.#sessions.values()) s.runtime?.dispose();
    this.#sessions.clear();
  }
  async handle(req: Request, ip = "local"): Promise<Response> {
    this.sweep();
    const url = new URL(req.url);
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    };
    const respond = (data: unknown, status = 200) =>
      new Response(JSON.stringify(data), { status, headers });
    if (req.headers.get("origin") && req.headers.get("origin") !== url.origin)
      return respond({ error: "Same-origin request required." }, 403);
    if (req.method === "GET" && url.pathname === "/api/play/status")
      return respond({
        live: this.liveAvailable,
        usageCaps,
        missions: missions.map((m) => m.id),
        scores: this.#scores,
      });
    if (req.method !== "POST")
      return respond({ error: "Unknown endpoint." }, 404);
    if (!req.headers.get("content-type")?.startsWith("application/json"))
      return respond({ error: "JSON required." }, 415);
    // Enforce bytes even if Content-Length is absent or forged.
    const reader = req.body?.getReader();
    let size = 0;
    let raw = "";
    if (reader) {
      const decoder = new TextDecoder();
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 12000) {
          await reader.cancel();
          return respond({ error: "Request exceeds 12 KB." }, 413);
        }
        raw += decoder.decode(chunk.value, { stream: true });
      }
      raw += decoder.decode();
    }
    let body: any;
    try {
      body = JSON.parse(raw);
    } catch {
      return respond({ error: "Invalid JSON." }, 400);
    }
    if (!body || Array.isArray(body) || typeof body !== "object")
      return respond({ error: "Invalid request." }, 400);
    const bucket = `${ip}:${url.pathname.endsWith("/session") ? "create" : "action"}`;
    const rate = this.#rates.get(bucket) ?? { since: this.now(), count: 0 };
    if (this.#rates.size > 5000 && !this.#rates.has(bucket))
      return respond({ error: "Capacity reached." }, 429);
    this.#rates.set(bucket, rate);
    if (++rate.count > (bucket.endsWith("create") ? 8 : 60))
      return respond({ error: "Rate limit reached. Wait one minute." }, 429);
    const owner = /(?:^|;\s*)heist_owner=([a-f0-9]{64})(?:;|$)/.exec(
      req.headers.get("cookie") ?? "",
    )?.[1];
    if (url.pathname === "/api/play/session") {
      if (
        !missions.some((m) => m.id === body.missionId) ||
        !["tutorial", "live"].includes(body.mode) ||
        typeof body.hardened !== "boolean"
      )
        return respond({ error: "Choose a mission, mode and guard." }, 400);
      if (this.#sessions.size >= 100)
        return respond({ error: "Session capacity reached." }, 429);
      if (
        body.mode === "live" &&
        (!this.liveAvailable ||
          body.consent !== true ||
          typeof body.naceKey !== "string" ||
          !/^nace_sk_[A-Za-z0-9_-]{12,240}$/.test(body.naceKey))
      )
        return respond(
          {
            error:
              "Live requires availability, your Nace key, and explicit request-limit consent.",
          },
          403,
        );
      const id = randomUUID();
      const token = owner ?? randomBytes(32).toString("hex");
      const mission = new Mission(body.mode, body.hardened);
      mission.game = {
        ...mission.game,
        started: this.now(),
        missionId: body.missionId,
        workflow: [],
        strategy: "Maintenance cover",
        equipment: [],
        intel: [],
      } as CampaignGame;
      const runtime =
        body.mode === "live"
          ? this.factory(body.naceKey, body.allowDocuments === true)
          : undefined;
      this.#sessions.set(id, {
        mission,
        owner: token,
        runtime,
        busy: false,
        last: 0,
        captured: false,
      });
      headers["Set-Cookie"] =
        `heist_owner=${token}; HttpOnly; SameSite=Strict; Path=/api/play${url.protocol === "https:" ? "; Secure" : ""}`;
      return respond({
        id,
        ...mission.response(),
        usage: runtime?.usage ?? null,
      });
    }
    const s =
      typeof body.id === "string" ? this.#sessions.get(body.id) : undefined;
    if (!s || !owner || s.owner !== owner)
      return respond(
        { error: "Session expired or unavailable. Begin a new mission." },
        404,
      );
    if (url.pathname === "/api/play/end") {
      s.runtime?.dispose();
      this.#sessions.delete(body.id);
      return respond({ ended: true });
    }
    if (s.busy) return respond({ error: "Wait for the current action." }, 409);
    if (url.pathname === "/api/play/capture") {
      if (
        typeof body.flag !== "string" ||
        body.flag.length > 100 ||
        !s.mission.verify(body.flag)
      )
        return respond({ error: "No verified flag capture." }, 403);
      if (!s.captured) {
        s.captured = true;
        this.#scores.push({
          missionId: (s.mission.game as CampaignGame).missionId,
          mode: s.mission.game.mode,
          score: s.mission.game.score,
          rank: rank(s.mission.game.score),
        });
        this.#scores.sort((a, b) => b.score - a.score);
        this.#scores = this.#scores.slice(0, 20);
      }
      s.runtime?.dispose();
      s.runtime = undefined;
      return respond({
        captured: true,
        score: s.mission.game.score,
        rank: rank(s.mission.game.score),
        scores: this.#scores,
      });
    }
    const g = s.mission.game as CampaignGame;
    if (g.status !== "active")
      return respond(
        { error: "Mission ended. Capture the flag or replay." },
        400,
      );
    if (url.pathname === "/api/play/equip") {
      if (
        body.item === "intel" &&
        !g.equipment.includes("intel") &&
        g.budget >= 10
      ) {
        g.budget -= 10;
        g.equipment.push("intel");
        g.intel.push(missions.find((m) => m.id === g.missionId)!.intel);
      } else if (
        body.item === "coolant" &&
        !g.equipment.includes("coolant") &&
        g.budget >= 15
      ) {
        g.budget -= 15;
        g.heat = Math.max(0, g.heat - 20);
        g.equipment.push("coolant");
      } else
        return respond(
          { error: "Equipment unavailable or already used." },
          400,
        );
      return respond(s.mission.response());
    }
    if (url.pathname !== "/api/play/turn")
      return respond({ error: "Unknown endpoint." }, 404);
    if (
      typeof body.message !== "string" ||
      !body.message.trim() ||
      body.message.length > 1200 ||
      typeof body.commit !== "boolean" ||
      (body.payload !== undefined &&
        (typeof body.payload !== "string" || body.payload.length > 2000)) ||
      (body.strategy !== undefined && !strategies.includes(body.strategy)) ||
      (body.choice !== undefined &&
        (g.mode !== "tutorial" ||
          ![
            "observe",
            "cover",
            "diagnostic",
            "threaten",
            "reset",
            "inspect",
            "prepare",
            "execute",
          ].includes(body.choice)))
    )
      return respond(
        { error: "Invalid turn. Check message, payload and strategy." },
        400,
      );
    if (g.mode === "live" && this.now() - s.last < 3000)
      return respond({ error: "Wait three seconds between Live turns." }, 429);
    s.busy = true;
    s.last = this.now();
    try {
      checkTurn(g, body.message, body.commit, this.now());
      const state = { ...g, strategy: body.strategy ?? g.strategy };
      let next: CampaignGame;
      if (g.mode === "tutorial")
        next = campaignTraining(
          state,
          body.message,
          body.commit,
          body.choice as TrainingChoice,
          body.payload,
        );
      else if (g.missionId === "insider")
        next = {
          ...state,
          ...(await coreLiveTurn(
            state,
            body.message,
            body.commit,
            s.runtime!.dialogue,
            async (_g, messages, proposal) => {
              const result = await s.runtime!.decide(state, messages, proposal);
              return {
                decision: result.action as Decision,
                evidence: result.evidence,
              };
            },
            this.now,
          )),
        };
      else {
        const history = [
          ...conversation(g),
          {
            role: "user" as const,
            content:
              body.message +
              (body.payload && g.missionId === "ghost"
                ? "\nUntrusted tool result: " + body.payload
                : ""),
          },
        ];
        const dialogue = await s.runtime!.dialogue.respond(history, state);
        if (body.commit) {
          let evidence: unknown = body.payload ?? "";
          if (g.missionId === "credentials")
            evidence = await s.runtime!.extract(body.payload ?? "");
          const d = await s.runtime!.decide(
            state,
            history,
            dialogue.character.proposal,
            evidence,
          );
          next = campaignResolve(state, body.message, d.action, "live", {
            security: d.evidence,
            ...(g.missionId === "credentials" ? { extraction: evidence } : {}),
          });
        } else
          next = {
            ...state,
            ...resolveTurn(
              state,
              body.message,
              dialogue.character.reply.replace(
                /\b(unlocked|access granted|breach successful)\b/gi,
                "authorization still required",
              ),
              dialogue.character.mood,
              {
                source: "live",
                proposal: "TALK",
                execution: "none",
                evidence: dialogue.evidence,
              },
              false,
              this.now(),
            ),
          };
      }
      if (!this.#sessions.has(body.id) || this.now() - g.started >= 600000) {
        s.runtime?.dispose();
        this.#sessions.delete(body.id);
        return respond(
          { error: "Session ended before execution. No flag released." },
          400,
        );
      }
      s.mission.accept(next);
      return respond({
        ...s.mission.response(),
        usage: s.runtime?.usage ?? null,
      });
    } catch {
      return respond(
        {
          error:
            "Action unavailable: check JSON, session resources, provider configuration and request limits. No breach recorded; provider failures are not retried.",
          usage: s.runtime?.usage ?? null,
        },
        400,
      );
    } finally {
      s.busy = false;
    }
  }
}
