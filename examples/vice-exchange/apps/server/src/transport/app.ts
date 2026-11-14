// HTTP + Socket.IO application factory. index.ts boots it from config; tests boot it on an ephemeral port.
import { mkdirSync } from "node:fs";
import { createServer, type Server as HttpServer } from "node:http";
import type { AddressInfo } from "node:net";
import path from "node:path";
import express from "express";
import { Server as SocketIOServer } from "socket.io";
import { CREDITS_NOTICE, DEFAULT_ROOM_CODE } from "@vice/contracts";
import type { DecisionMode } from "@vice/contracts";
import { ExperimentRunner } from "../demo/experiment";
import type { PersistencePort, PolicyPort } from "../domain";
import { createRoomFactory, type ProviderConfig } from "../engine/compose";
import { createPersistence } from "../persistence";
import { verifyReplay } from "../replay";
import { RoomManager } from "../rooms/RoomManager";
import { attachTransport } from "./socket";

export interface AppOptions {
  port: number; databaseUrl: string; providers: ProviderConfig; directorSecret: string; tickHz: number;
  /** Live room mode at boot. Defaults to drex when a Nace key is configured, otherwise scripted. */
  defaultMode?: DecisionMode;
  webOrigin?: string;
  /** Put the autonomous hunted outlaw in every live room. Default false. */
  fugitive?: boolean;
  /** Mount the plain-WebSocket world bridge (external 3D clients) at /bridge. Default true. */
  bridge?: boolean;
  /**
   * Document-grounded police policy (NDI). Optional extras: `status` is its detailed report for /api/policy,
   * `xraySvg` a picture of the source document with the cited regions (null until real facts are loaded).
   */
  policy?: (PolicyPort & { status?: () => unknown; xraySvg?: () => string | null }) | null;
  persistence?: PersistencePort;
}
export interface App {
  httpServer: HttpServer; io: SocketIOServer; rooms: RoomManager; persistence: PersistencePort; runner: ExperimentRunner;
  listen(): Promise<number>; close(): Promise<void>;
  /** Call when policy ingestion has finished: every live room records POLICY_INGESTED once and refreshes its snapshot. */
  announcePolicy(): void;
}

const PRIVATE_HOST = /^(localhost|127\.0\.0\.1|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[a-z0-9-]+\.local)$/i;
/** Browsers on this machine or the same LAN (second laptop) may connect directly; other web origins may not. */
export function originAllowed(origin: string | undefined, webOrigin?: string): boolean {
  if (!origin) return true; // non-browser clients send no Origin
  if (webOrigin && origin === webOrigin) return true;
  try { return PRIVATE_HOST.test(new URL(origin).hostname); } catch { return false; }
}

export function createApp(o: AppOptions): App {
  if (!o.persistence && o.databaseUrl !== ":memory:") mkdirSync(path.dirname(o.databaseUrl), { recursive: true });
  const persistence = o.persistence ?? createPersistence(o.databaseUrl);

  let runner: ExperimentRunner | null = null;
  const build = createRoomFactory({
    persistence, providers: o.providers, policy: o.policy ?? null,
    experiments: () => runner?.list() ?? [],
    onRunExperiment: (engine, durationSec) => runner?.start(engine.state.seed, durationSec) ?? null,
  });
  // The live room starts on Drex when a key is configured; otherwise it is honestly scripted.
  const rooms = new RoomManager(build, { directorSecret: o.directorSecret, defaultMode: o.defaultMode ?? (o.providers.naceApiKey ? "drex" : "scripted"), tickHz: o.tickHz, fugitive: !!o.fugitive });

  const app = express();
  app.disable("x-powered-by");
  app.use(express.json({ limit: "64kb" }));
  const httpServer = createServer(app);
  const io = new SocketIOServer(httpServer, {
    cors: { origin: (origin, cb) => cb(null, originAllowed(origin, o.webOrigin)) },
    maxHttpBufferSize: 64 * 1024,
  });
  const transport = attachTransport(io, rooms);
  const exp = new ExperimentRunner(build, persistence, (r) => transport.experiment(r), o.tickHz);
  runner = exp;
  transport.wire(rooms.ensureDefault());

  let bridge: { clients(): number; close(): void; udp?(): { enabled: boolean; port: number | null; clients: number } } | null = null;
  if (o.bridge !== false) {
    // Optional module owned by the 3D-world team; the server must boot even if it fails to load.
    import("../bridge/index").then((m) => { bridge = m.attachWorldBridge(httpServer, rooms); })
      .catch((err: unknown) => console.error("[bridge] not mounted:", err instanceof Error ? err.message : err));
  }
  // One game engine publishes its frames here and browsers watch (and the same player's browser plays) in the city pane.
  let stream: { stats(): { publishers: number; viewers: number; frames: number }; close(): void } | null = null;
  import("../bridge/stream").then((m) => { stream = m.attachWorldStream(httpServer, rooms); })
    .catch((err: unknown) => console.error("[stream] not mounted:", err instanceof Error ? err.message : err));

  const room = (code: unknown) => rooms.get(String(code ?? ""));
  app.get("/health", (_req, res) => {
    const r = rooms.get(DEFAULT_ROOM_CODE);
    res.json({
      ok: true, drexConfigured: o.providers.naceApiKey.length > 0, metaConfigured: o.providers.metaApiKey.length > 0,
      rooms: rooms.rooms.size, defaultRoom: DEFAULT_ROOM_CODE, mode: r?.engine.state.mode ?? null, badge: r?.scheduler.status().badge ?? null,
      shadowMeta: !!o.providers.shadowMeta && o.providers.naceApiKey.length > 0 && o.providers.metaApiKey.length > 0,
      policyLoaded: !!o.policy?.view(),
      bridgeClients: bridge?.clients() ?? 0, udp: (() => { const u = bridge?.udp?.(); return u ? { enabled: u.enabled, port: u.port, clients: u.clients } : null; })(), credits: CREDITS_NOTICE,
      stream: stream?.stats() ?? null,
    });
  });
  app.get("/api/rooms/:code/snapshot", (req, res) => { const r = room(req.params.code); return r ? res.json(r.engine.snapshot()) : res.status(404).json({ error: "ROOM_NOT_FOUND" }); });
  app.get("/api/rooms/:code/events", (req, res) => {
    const r = room(req.params.code); if (!r) return res.status(404).json({ error: "ROOM_NOT_FOUND" });
    const after = Math.max(0, Number(req.query.after ?? 0) || 0);
    const limit = Math.min(1000, Math.max(1, Number(req.query.limit ?? 500) || 500));
    const events = r.engine.events.filter((e) => e.serverSequence > after).slice(0, limit);
    return res.json({ roomCode: r.code, runId: r.engine.state.runId, lastEventSequence: r.engine.state.lastEventSequence, events });
  });
  /** Sanitized decision trace for one NPC: its own memories, its decisions and the messages it sent or received. */
  app.get("/api/rooms/:code/agents/:id/trace", (req, res) => {
    const r = room(req.params.code); if (!r) return res.status(404).json({ error: "ROOM_NOT_FOUND" });
    const id = String(req.params.id);
    if (!r.engine.state.npcs[id]) return res.status(404).json({ error: "AGENT_NOT_FOUND" });
    return res.json({ agent: r.engine.agentView(id), decisions: r.engine.brains.traces().filter((t) => t.agentId === id), messages: r.engine.state.messages.filter((m) => m.senderId === id || m.recipientId === id) });
  });
  /** Folds the recorded event log (no model calls) and compares it with live wallets, listings and characters. */
  app.get("/api/rooms/:code/replay/verify", (req, res) => {
    const r = room(req.params.code); if (!r) return res.status(404).json({ error: "ROOM_NOT_FOUND" });
    const s = r.engine.state;
    let events = r.engine.events.filter((e) => e.runId === s.runId); let source = "memory";
    try { const stored = persistence.loadEvents(s.id, s.runId); if (stored.length >= events.length) { events = stored; source = "store"; } } catch { /* fall back to memory */ }
    const v = verifyReplay(events, s);
    return res.json({ ok: v.ok, source, runId: s.runId, eventCount: events.length, mismatches: v.mismatches, projection: v.projection });
  });
  app.get("/api/experiments", (_req, res) => res.json(exp.list()));
  /** Where the police policy came from: document, NDI job ids, extracted facts with page and quote. */
  app.get("/api/policy", (_req, res) => res.json(o.policy?.status?.() ?? { view: o.policy?.view() ?? null }));
  /**
   * Animated picture of the policy document as NDI parsed it. 404 until real NDI facts are loaded.
   * It contains text taken from an external document, so it is served as an inert image: no script, no network, sandboxed.
   */
  app.get("/api/policy/xray.svg", (_req, res) => {
    const svg = o.policy?.xraySvg?.() ?? null;
    if (!svg) return res.status(404).json({ error: "POLICY_NOT_LOADED" });
    return res.type("image/svg+xml").set({
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    }).send(svg);
  });

  return {
    httpServer, io, rooms, persistence, runner: exp,
    announcePolicy: () => { for (const r of rooms.rooms.values()) { r.engine.announcePolicy(); transport.flush(r); } },
    listen: () => new Promise<number>((resolve, reject) => {
      httpServer.once("error", reject);
      httpServer.listen(o.port, "0.0.0.0", () => resolve((httpServer.address() as AddressInfo).port));
    }),
    close: () => new Promise<void>((resolve) => {
      rooms.stop();
      try { bridge?.close(); } catch { /* already closed */ }
      try { stream?.close(); } catch { /* already closed */ }
      void io.close(() => { if (!o.persistence) { try { persistence.close(); } catch { /* already closed */ } } resolve(); });
    }),
  };
}
