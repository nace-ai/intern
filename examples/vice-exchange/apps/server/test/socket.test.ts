// Two real Socket.IO clients against the real application (in-memory database, scripted decisions, no network calls).
// Covers FR-001/002/006/007/008, the shared view half of AT-04, private wallets and command validation.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { io as connect, type Socket } from "socket.io-client";
import { SOCKET } from "@vice/contracts";
import type { CommandAck, GameEvent, JoinResult, ListingView, RoomSnapshot, WalletView, WorldTick } from "@vice/contracts";
import { createApp, originAllowed, type App } from "../src/transport/app";

let app: App; let url = "";
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const clients: Socket[] = [];

function client() {
  const s = connect(url, { transports: ["websocket"], forceNew: true, reconnection: false });
  clients.push(s);
  const got: Record<string, unknown[]> = {};
  s.onAny((name: string, payload: unknown) => { (got[name] ??= []).push(payload); });
  let n = 0;
  const send = <R = unknown>(cmd: Record<string, unknown>) => new Promise<CommandAck<R>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`no ack for ${String(cmd.type)}`)), 3000);
    s.emit(SOCKET.command, cmd, (a: CommandAck<R>) => { clearTimeout(timer); resolve(a); });
  });
  return {
    s, got, send,
    cid: () => `cmd-${Date.now()}-${++n}`,
    events: () => (got[SOCKET.event] ?? []) as GameEvent[],
    ticks: () => (got[SOCKET.tick] ?? []) as WorldTick[],
    list: <T>(name: string) => (got[name] ?? []) as T[],
    join: (nickname: string, role: string, extra: Record<string, unknown> = {}) => send<JoinResult>({ type: "room.join", roomCode: "VICE-42", nickname, role, requestId: `join-${nickname}`, ...extra }),
  };
}

beforeAll(async () => {
  app = createApp({
    port: 0, databaseUrl: ":memory:", directorSecret: "", tickHz: 10, defaultMode: "scripted", bridge: false,
    providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 },
  });
  url = `http://127.0.0.1:${await app.listen()}`;
});
afterAll(async () => { for (const c of clients) c.close(); await app.close(); });

describe("two browsers in one room", () => {
  it("share one authoritative world, market and event log", async () => {
    const A = client(); const B = client();
    const ja = await A.join("Ace", "OUTLAW");
    expect(ja.ok).toBe(true);
    const a = ja.result!;
    expect(a.isDirector).toBe(true);
    expect(a.snapshot.npcs).toHaveLength(8);
    expect(new Set(a.snapshot.npcs.map((n) => n.role)).size).toBe(4);
    expect(a.snapshot.providerStatus.badge).toBe("SCRIPTED"); // no key in this test: nothing may claim to be live Drex
    expect(a.wallet?.balanceCredits).toBe(2000);
    const ticker = a.snapshot.listings[0]!.ticker;

    const jb = await B.join("Bo", "HUNTER");
    const b = jb.result!;
    expect(b.isDirector).toBe(false);
    expect(b.snapshot.characters.map((c) => c.nickname).sort()).toEqual(["Ace", "Bo"]);
    expect(b.snapshot.listings.map((l) => l.ticker)).toEqual([ticker]); // hunters are not listed

    // FR-002: A moves, B sees A's avatar move.
    const startX = b.snapshot.characters.find((c) => c.id === a.characterId)!.x;
    await A.send({ type: "player.move", dirX: -1, dirZ: 0 });
    await wait(500);
    await A.send({ type: "player.move", dirX: 0, dirZ: 0 });
    const seenByB = B.ticks().at(-1)!.characters.find((c) => c.id === a.characterId)!;
    expect(seenByB.x).toBeLessThan(startX - 2);
    expect(B.ticks().at(-1)!.npcs).toHaveLength(8);

    // AT-04: A buys one share; both browsers see the same trade and the same price.
    const buyId = A.cid();
    const buy = await A.send<{ balanceAfter: number }>({ type: "market.buy", ticker, qty: 1, commandId: buyId });
    expect(buy.ok).toBe(true);
    await wait(250);
    const tradeA = A.events().filter((e): e is GameEvent<"TRADE_EXECUTED"> => e.type === "TRADE_EXECUTED");
    const tradeB = B.events().filter((e): e is GameEvent<"TRADE_EXECUTED"> => e.type === "TRADE_EXECUTED");
    expect(tradeA).toHaveLength(1);
    expect(tradeB).toEqual(tradeA);
    const priceA = A.list<ListingView>(SOCKET.listing).at(-1)!; const priceB = B.list<ListingView>(SOCKET.listing).at(-1)!;
    expect(priceA.spotPrice).toBe(priceB.spotPrice);
    expect(priceA.ticker).toBe(ticker);
    // Wallets are private: A sees its own, B never receives A's.
    const walletA = A.list<WalletView>(SOCKET.wallet).at(-1)!;
    expect(walletA.balanceCredits).toBe(buy.result!.balanceAfter);
    expect(walletA.holdings.find((h) => h.ticker === ticker)?.shares).toBe(1);
    expect(B.list<WalletView>(SOCKET.wallet).every((w) => w.ownerId === b.playerId)).toBe(true);
    // Both clients received the same ordered log.
    const seqA = A.events().map((e) => e.serverSequence); const seqB = B.events().map((e) => e.serverSequence);
    expect(seqA).toEqual([...seqA].sort((x, y) => x - y));
    expect(seqB).toEqual(seqA.filter((n) => n >= seqB[0]!));

    // Replayed command id: acknowledged as a duplicate, not executed again.
    const again = await A.send({ type: "market.buy", ticker, qty: 1, commandId: buyId });
    expect(again.duplicate).toBe(true);
    await wait(150);
    expect(A.events().filter((e) => e.type === "TRADE_EXECUTED")).toHaveLength(1);

    // Validation and authorisation.
    expect((await A.send({ type: "market.buy", ticker, qty: 50, commandId: A.cid() })).error?.code).toBe("INVALID_COMMAND");
    expect((await A.send({ type: "wallet.set", balance: 1e9 })).error?.code).toBe("INVALID_COMMAND");
    expect((await A.send({ type: "player.move", dirX: 99, dirZ: 0 })).error?.code).toBe("INVALID_COMMAND");
    expect((await B.send({ type: "demo.reset", commandId: B.cid() })).error?.code).toBe("FORBIDDEN");
    expect((await B.send({ type: "mission.interact", missionId: a.snapshot.missions[0]!.id, commandId: B.cid() })).error?.code).toBe("NOT_ALLOWED_FOR_ROLE");
    const stranger = client();
    expect((await stranger.send({ type: "market.buy", ticker, qty: 1, commandId: stranger.cid() })).error?.code).toBe("NOT_JOINED");

    // FR-007: A's browser disappears; the room keeps simulating for B.
    A.s.close();
    await wait(300);
    const before = B.ticks().length;
    await wait(400);
    expect(B.ticks().length).toBeGreaterThan(before + 2);
    expect(B.events().some((e) => e.type === "PLAYER_LEFT")).toBe(true);

    // FR-008: A comes back with its session token and gets the same character, wallet and director rights.
    const A2 = client();
    const back = await A2.join("Ace", "OUTLAW", { sessionToken: a.sessionToken });
    expect(back.result).toMatchObject({ playerId: a.playerId, characterId: a.characterId, isDirector: true });
    expect(back.result!.wallet?.holdings.find((h) => h.ticker === ticker)?.shares).toBe(1);
    expect(back.result!.snapshot.characters.filter((c) => c.nickname === "Ace")).toHaveLength(1);

    // FR-060 over the wire: the director resets; everyone gets a fresh snapshot of the new run.
    const runBefore = back.result!.snapshot.runId;
    expect((await A2.send({ type: "demo.reset", commandId: A2.cid() })).ok).toBe(true);
    await wait(250);
    const snapB = B.list<RoomSnapshot>(SOCKET.snapshot).at(-1)!;
    expect(snapB.runId).not.toBe(runBefore);
    expect(snapB.listings.map((l) => l.ticker)).toEqual([ticker]);
    expect(snapB.trades).toEqual([]);
    expect(snapB.players.map((p) => p.id).sort()).toEqual([a.playerId, b.playerId].sort());
    expect(A2.list<WalletView>(SOCKET.wallet).at(-1)).toMatchObject({ balanceCredits: 2000, holdings: [] });
  }, 20000);

  it("serves the public HTTP API and verifies the event log by replay", async () => {
    const get = async (path: string) => { const r = await fetch(url + path); return { status: r.status, body: await r.json() as Record<string, unknown> }; };
    const health = await get("/health");
    expect(health.status).toBe(200);
    expect(health.body).toMatchObject({ ok: true, drexConfigured: false, metaConfigured: false, defaultRoom: "VICE-42", mode: "scripted", badge: "SCRIPTED" });
    expect(JSON.stringify(health.body)).not.toMatch(/nace_sk_|LLM[|_]/); // booleans only, never key material
    expect((await get("/api/rooms/VICE-42/snapshot")).status).toBe(200);
    expect((await get("/api/rooms/NOPE-1/snapshot")).status).toBe(404);
    const ev = await get("/api/rooms/VICE-42/events?after=0");
    expect(Array.isArray(ev.body.events)).toBe(true);
    const trace = await get("/api/rooms/VICE-42/agents/npc_t1/trace");
    expect(trace.status).toBe(200);
    expect((trace.body.agent as { id: string }).id).toBe("npc_t1");
    expect((await get("/api/rooms/VICE-42/agents/nobody/trace")).status).toBe(404);
    const replay = await get("/api/rooms/VICE-42/replay/verify");
    expect(replay.body).toMatchObject({ ok: true, mismatches: [] });
  });
});

describe("socket origins", () => {
  it("allows this machine and the LAN, refuses other web origins", () => {
    expect(originAllowed(undefined)).toBe(true);
    expect(originAllowed("http://localhost:5173")).toBe(true);
    expect(originAllowed("http://192.168.1.23:5173")).toBe(true);
    expect(originAllowed("http://10.0.0.7:5173")).toBe(true);
    expect(originAllowed("https://demo.example.com", "https://demo.example.com")).toBe(true);
    expect(originAllowed("https://evil.example.com")).toBe(false);
    expect(originAllowed("not a url")).toBe(false);
  });
});

describe("policy over HTTP", () => {
  const none = { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 };

  it("without a loaded policy: no picture, no facts, and the room says so", async () => {
    const r = await fetch(`${url}/api/policy/xray.svg`);
    expect(r.status).toBe(404);
    expect(await r.json()).toEqual({ error: "POLICY_NOT_LOADED" });
    expect(await (await fetch(`${url}/api/policy`)).json()).toEqual({ view: null });
    const health = await (await fetch(`${url}/health`)).json() as { policyLoaded: boolean };
    expect(health.policyLoaded).toBe(false);
    const snap = await (await fetch(`${url}/api/rooms/VICE-42/snapshot`)).json() as RoomSnapshot;
    expect(snap.policy).toBeNull();
    expect(snap.recentEvents.some((e) => e.type === "POLICY_INGESTED")).toBe(false);
  });

  it("with a loaded policy: snapshot, event, report and an inert picture", async () => {
    // Test stand-in for the policy module (the real one is covered by src/policy/*.test.ts).
    const fact = { documentId: "doc-1", documentTitle: "Test Order 1.01", sourceUrl: "https://example.org/order.pdf", page: 2, quote: "Officers shall radio in before a foot pursuit.", extractedField: "radio_first", ndiJobId: "job-test-1" };
    const view = { documentTitle: "Test Order 1.01", sourceUrl: "https://example.org/order.pdf", ingestedVia: "ndi" as const, ndiJobId: "job-test-1", facts: [fact] };
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>Test Order 1.01</text></svg>`;
    const withPolicy = createApp({
      port: 0, databaseUrl: ":memory:", directorSecret: "", tickHz: 10, defaultMode: "scripted", bridge: false, providers: none,
      policy: { view: () => view, factsFor: (role) => (role === "POLICE" ? [fact] : []), status: () => ({ state: "ready", facts: 1 }), xraySvg: () => svg },
    });
    try {
      const base = `http://127.0.0.1:${await withPolicy.listen()}`;
      const pic = await fetch(`${base}/api/policy/xray.svg`);
      expect(pic.status).toBe(200);
      expect(pic.headers.get("content-type")).toMatch(/^image\/svg\+xml/);
      expect(pic.headers.get("x-content-type-options")).toBe("nosniff");
      const csp = pic.headers.get("content-security-policy") ?? "";
      expect(csp).toContain("default-src 'none'"); // no script, no network
      expect(csp).toContain("sandbox");
      expect(csp).not.toMatch(/script-src/);
      expect(pic.headers.get("cache-control")).toBe("no-store");
      expect(await pic.text()).toBe(svg);

      expect(await (await fetch(`${base}/api/policy`)).json()).toEqual({ state: "ready", facts: 1 });
      const health = await (await fetch(`${base}/health`)).json() as { policyLoaded: boolean };
      expect(health.policyLoaded).toBe(true);
      const snap = await (await fetch(`${base}/api/rooms/VICE-42/snapshot`)).json() as RoomSnapshot;
      expect(snap.policy).toEqual(view);
      const ingested = snap.recentEvents.filter((e): e is GameEvent<"POLICY_INGESTED"> => e.type === "POLICY_INGESTED");
      expect(ingested).toHaveLength(1); // recorded once per run
      expect(ingested[0]!.payload).toEqual({ documentTitle: "Test Order 1.01", sourceUrl: "https://example.org/order.pdf", ndiJobId: "job-test-1", factCount: 1 });
      withPolicy.announcePolicy(); // a second announcement in the same run is a no-op
      const again = await (await fetch(`${base}/api/rooms/VICE-42/snapshot`)).json() as RoomSnapshot;
      expect(again.recentEvents.filter((e) => e.type === "POLICY_INGESTED")).toHaveLength(1);
    } finally {
      await withPolicy.close();
    }
  });
});
