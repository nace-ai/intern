// UDP fast path for native world clients: real datagram sockets against the real app, bridge and engine.
import { createSocket, type Socket as Dgram } from "node:dgram";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import WebSocket from "ws";
import type { CommandAck, JoinResult } from "@vice/contracts";
import { createApp, type App } from "../src/transport/app";
import { createUdpChannel } from "../src/bridge/udp";

let app: App; let port = 0;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
type Snap = { characters: Array<{ id: string; x: number; z: number }> };
const snapshot = async () => (await fetch(`http://127.0.0.1:${port}/api/rooms/VICE-42/snapshot`)).json() as Promise<Snap>;
const health = async () => (await fetch(`http://127.0.0.1:${port}/health`)).json() as Promise<{ udp: { enabled: boolean; port: number | null; clients: number } | null }>;

interface Client { ws: WebSocket; udp: Dgram; token: string; udpPort: number; characterId: string; states: Array<{ q: number; c: unknown[][]; n: unknown[][] }>; send(body: unknown, token?: string): void; cmd(c: Record<string, unknown>): Promise<CommandAck>; close(): void }

async function join(nickname: string): Promise<Client> {
  const ws = new WebSocket(`ws://127.0.0.1:${port}/bridge`);
  const acks = new Map<number, CommandAck>(); let offer: { port: number; token: string } | null = null; let n = 0;
  ws.on("message", (d) => {
    const m = JSON.parse(String(d)) as { t: string; n: number; ack: CommandAck; port: number; token: string };
    if (m.t === "ack") acks.set(m.n, m.ack);
    if (m.t === "udp") offer = { port: m.port, token: m.token };
  });
  await new Promise((r) => ws.once("open", r));
  const cmd = async (c: Record<string, unknown>) => { const k = ++n; ws.send(JSON.stringify({ t: "cmd", n: k, cmd: c })); for (let i = 0; i < 100 && !acks.has(k); i++) await wait(10); return acks.get(k)!; };
  const j = await cmd({ type: "room.join", roomCode: "VICE-42", nickname, role: "OUTLAW", requestId: `join-${nickname}` });
  expect(j.ok).toBe(true);
  for (let i = 0; i < 100 && !offer; i++) await wait(10);
  expect(offer).not.toBeNull();
  const udp = createSocket("udp4");
  await new Promise<void>((r) => udp.bind(0, "127.0.0.1", () => r()));
  const states: Client["states"] = [];
  udp.on("message", (b) => { states.push(JSON.parse(b.toString("utf8"))); });
  const o = offer!;
  return {
    ws, udp, token: o.token, udpPort: o.port, characterId: (j.result as JoinResult).characterId!, states, cmd,
    send: (body, token = o.token) => udp.send(Buffer.from(token + JSON.stringify(body)), o.port, "127.0.0.1"),
    close: () => { try { udp.close(); } catch { /* closed */ } ws.close(); },
  };
}
const posOf = async (id: string) => (await snapshot()).characters.find((c) => c.id === id)!;

beforeAll(async () => {
  process.env.UDP_PORT = "0"; // ephemeral, so parallel test files never fight over a port
  app = createApp({
    port: 0, databaseUrl: ":memory:", directorSecret: "", tickHz: 10, defaultMode: "scripted", bridge: true,
    providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 },
  });
  port = await app.listen();
  await wait(100); // the bridge module is mounted with a dynamic import
});
afterAll(async () => { delete process.env.UDP_PORT; await app.close(); });

describe("UDP fast path on the world bridge", () => {
  it("handshake: a joined bridge client is offered a port and a 32-hex token over WebSocket, and health reports the channel", async () => {
    const a = await join("Udp1");
    expect(a.token).toMatch(/^[0-9a-f]{32}$/);
    expect(a.udpPort).toBeGreaterThan(0);
    const h = await health();
    expect(h.udp).toMatchObject({ enabled: true, port: a.udpPort });
    expect(h.udp!.clients).toBe(0); // no datagram yet: the address is not confirmed
    a.send({ hello: 1 }); await wait(80);
    expect((await health()).udp!.clients).toBe(1);
    a.close();
  });

  it("a pose over UDP moves the character through the validated path; state datagrams arrive with increasing seq", async () => {
    const a = await join("Udp2");
    const start = await posOf(a.characterId);
    await wait(250); // let the speed budget accrue
    a.send({ q: 1, x: start.x - 1.5, z: start.z, h: 1.57, f: 1 });
    await wait(120);
    const moved = await posOf(a.characterId);
    expect(moved.x).toBeCloseTo(start.x - 1.5, 1);
    await wait(300);
    expect(a.states.length).toBeGreaterThanOrEqual(5); // ~30 Hz
    for (let i = 1; i < a.states.length; i++) expect(a.states[i]!.q).toBeGreaterThan(a.states[i - 1]!.q);
    const last = a.states.at(-1)!;
    const me = last.c.find((c) => c[0] === a.characterId)!;
    expect(me[1]).toBeCloseTo(start.x - 1.5, 1);
    expect(me[5]).toBe(1.57); expect(me[6]).toBe(1); // heading and in-car flag are relayed
    expect(last.n).toHaveLength(8);
    a.close();
  });

  it("rejects what the WebSocket path would reject: a speed-hack pose, a pose outside the schema, an unknown token", async () => {
    const a = await join("Udp3");
    const start = await posOf(a.characterId);
    a.send({ q: 1, x: start.x - 60, z: start.z });           // teleport: the engine's speed check refuses it
    a.send({ q: 2, x: "nope", z: start.z });                  // not a pose
    a.send({ q: 3, x: start.x - 1, z: start.z }, "f".repeat(32)); // unknown token
    a.udp.send(Buffer.from("short"), a.udpPort, "127.0.0.1");
    a.udp.send(Buffer.alloc(1500, 97), a.udpPort, "127.0.0.1"); // oversized
    await wait(150);
    const after = await posOf(a.characterId);
    expect(after.x).toBeCloseTo(start.x, 1);
    expect(after.z).toBeCloseTo(start.z, 1);
    a.close();
  });

  it("drops out-of-order and duplicate sequence numbers", async () => {
    const a = await join("Udp4");
    const start = await posOf(a.characterId);
    await wait(250);
    a.send({ q: 10, x: start.x - 1, z: start.z }); await wait(80);
    a.send({ q: 9, x: start.x + 1, z: start.z });  // older than what was applied
    a.send({ q: 10, x: start.x + 1, z: start.z }); // duplicate
    await wait(120);
    expect((await posOf(a.characterId)).x).toBeCloseTo(start.x - 1, 1);
    a.close();
  });

  it("the WebSocket fallback is untouched: poses and ticks still work without any datagram", async () => {
    const a = await join("Udp5");
    const ticks: unknown[] = [];
    a.ws.on("message", (d) => { const m = JSON.parse(String(d)) as { t: string }; if (m.t === "tick") ticks.push(m); });
    const start = await posOf(a.characterId);
    await wait(250);
    const ack = await a.cmd({ type: "player.pose", x: start.x, z: start.z - 1.5 });
    expect(ack.ok).toBe(true);
    expect((await posOf(a.characterId)).z).toBeCloseTo(start.z - 1.5, 1);
    await wait(250);
    expect(ticks.length).toBeGreaterThanOrEqual(2);
    expect(a.states).toHaveLength(0); // never said hello over UDP, so nothing is sent to an unconfirmed address
    a.close();
  });
});

describe("UDP channel unit", () => {
  it("rate-limits a flooding session and never passes more than the burst to the pose path", async () => {
    let poses = 0;
    const ch = createUdpChannel({ port: () => 0, pose: () => { poses += 1; return true; }, view: () => null });
    const offer = (await ch.register(1))!;
    const sock = createSocket("udp4");
    await new Promise<void>((r) => sock.bind(0, "127.0.0.1", () => r()));
    for (let i = 1; i <= 400; i++) sock.send(Buffer.from(offer.token + JSON.stringify({ q: i, x: 1, z: 1 })), offer.port, "127.0.0.1");
    await wait(200);
    expect(poses).toBeGreaterThan(0);
    expect(poses).toBeLessThanOrEqual(140); // burst 120 plus a little refill
    expect(ch.stats().dropped).toBeGreaterThan(200);
    sock.close(); ch.close();
  });

  it("is off when disabled or when the port cannot be bound, leaving callers on WebSocket", async () => {
    const off = createUdpChannel({ disabled: true, port: () => 0, pose: () => true, view: () => null });
    expect(await off.register(1)).toBeNull();
    const first = createUdpChannel({ port: () => 0, pose: () => true, view: () => null });
    const taken = (await first.register(1))!.port;
    const clash = createUdpChannel({ port: () => taken, pose: () => true, view: () => null });
    expect(await clash.register(1)).toBeNull();
    expect(clash.stats().enabled).toBe(false);
    first.close(); clash.close(); off.close();
  });

  it("dead-reckons an entity between engine ticks and stops when it stops", async () => {
    let t = 1000; let x = 10;
    const tick = () => ({ tick: 1, serverTimeMs: t, characters: [], npcs: [{ id: "npc_p1", x, z: 5, activity: "PATROL" as const, pending: false }] });
    const ch = createUdpChannel({ port: () => 0, now: () => t, pose: () => true, view: () => ({ tick: tick(), characterId: null }) });
    const offer = (await ch.register(1))!;
    const sock = createSocket("udp4");
    await new Promise<void>((r) => sock.bind(0, "127.0.0.1", () => r()));
    const got: number[] = [];
    sock.on("message", (b) => { got.push((JSON.parse(b.toString()) as { n: number[][] }).n[0]![1]!); });
    sock.send(Buffer.from(offer.token + JSON.stringify({ hello: 1 })), offer.port, "127.0.0.1");
    await wait(60);
    got.length = 0;
    ch.flush();                 // first sighting at x=10
    t += 100; x = 10.3; ch.flush(); // moved 0.3 m in 100 ms: 3 m/s
    t += 33; ch.flush();        // between ticks: extrapolated to ~10.4
    t += 33; ch.flush();        // ~10.5
    t += 200; ch.flush();       // long past a tick with no movement: it stopped, no drift
    await wait(80);
    // The channel's own 30 Hz timer also sends at the frozen clock, so assert on what was seen, not on exact positions in the list.
    expect(got).toContain(10.4);      // 33 ms after the tick, at the measured 3 m/s
    expect(got).toContain(10.5);      // 66 ms after
    expect(Math.max(...got)).toBe(10.5); // never runs ahead of the cap
    expect(got.at(-1)).toBe(10.3);    // stopped: back on the measured position, no drift
    sock.close(); ch.close();
  });
});
