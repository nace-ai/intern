// World stream relay (/stream): one game publishes frames, browsers watch, and only the SAME player's browser may
// send input back to the game.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { io as connect, type Socket } from "socket.io-client";
import WebSocket from "ws";
import { SOCKET } from "@vice/contracts";
import type { CommandAck, JoinResult } from "@vice/contracts";
import { createApp, type App } from "../src/transport/app";

let app: App; let port = 0; const socks: Socket[] = []; const wss: WebSocket[] = [];
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const join = async (nickname: string, role: string) => {
  const s = connect(`http://127.0.0.1:${port}`, { transports: ["websocket"], forceNew: true, reconnection: false });
  socks.push(s);
  const ack = await new Promise<CommandAck<JoinResult>>((res) => s.emit(SOCKET.command, { type: "room.join", roomCode: "VICE-42", nickname, role, requestId: `join-${nickname}` }, res));
  return ack.result!;
};
const open = async (q: string) => {
  const ws = new WebSocket(`ws://127.0.0.1:${port}/stream?${q}`);
  wss.push(ws);
  const got: Array<string | Buffer> = [];
  ws.on("message", (d: Buffer, isBinary: boolean) => got.push(isBinary ? d : d.toString("utf8")));
  ws.on("error", () => { /* refused upgrades surface as errors */ });
  const ok = await new Promise<boolean>((r) => { ws.once("open", () => r(true)); ws.once("close", () => r(false)); ws.once("error", () => r(false)); });
  return { ws, got, ok };
};
const frame = (seq: number) => { const b = Buffer.alloc(12 + 8); b.writeUInt32LE(seq, 0); b.writeDoubleLE(Date.now(), 4); b.fill(0xff, 12); return b; };

beforeAll(async () => {
  app = createApp({
    port: 0, databaseUrl: ":memory:", directorSecret: "", tickHz: 10, defaultMode: "scripted", bridge: true,
    providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 },
  });
  port = await app.listen();
  await wait(150); // the relay is mounted by a dynamic import
});
afterAll(async () => { for (const w of wss) { try { w.close(); } catch { /* closed */ } } for (const s of socks) s.close(); await app.close(); });

describe("world stream relay", () => {
  it("refuses a publisher without a real session token", async () => {
    expect((await open("role=pub&token=deadbeefdeadbeefdeadbeef")).ok).toBe(false);
    expect((await open("role=pub")).ok).toBe(false);
  });

  it("relays frames to viewers, gives control only to the same player's browser, and reports itself in /health", async () => {
    const ace = await join("Ace", "HUNTER");
    const rio = await join("Rio", "HUNTER");
    const own = await open(`role=sub&token=${ace.sessionToken}`);
    const other = await open(`role=sub&token=${rio.sessionToken}`);
    const guest = await open("role=sub&room=VICE-42");
    expect(own.ok && other.ok && guest.ok).toBe(true);
    const pub = await open(`role=pub&token=${ace.sessionToken}`);
    expect(pub.ok).toBe(true);
    await wait(80);
    const status = (g: Array<string | Buffer>) => JSON.parse([...g].reverse().find((m) => typeof m === "string" && m.includes('"status"')) as string) as { publishing: boolean; control: boolean };
    expect(status(own.got)).toMatchObject({ publishing: true, control: true });
    expect(status(other.got)).toMatchObject({ publishing: true, control: false });
    expect(status(guest.got)).toMatchObject({ publishing: true, control: false });

    pub.ws.send(frame(1), { binary: true });
    await wait(80);
    for (const v of [own, other, guest]) {
      const bin = v.got.filter((m) => typeof m !== "string") as Buffer[];
      expect(bin).toHaveLength(1);
      expect(bin[0]!.readUInt32LE(0)).toBe(1);
    }

    // input: only the controlling subscriber reaches the game, and only well-formed input events
    own.ws.send(JSON.stringify({ t: "k", code: "KeyW", down: true }));
    other.ws.send(JSON.stringify({ t: "k", code: "KeyS", down: true }));
    guest.ws.send(JSON.stringify({ t: "b", button: 0, down: true }));
    own.ws.send(JSON.stringify({ t: "quit" }));
    own.ws.send("x".repeat(5000));
    await wait(80);
    const inputs = pub.got.filter((m) => typeof m === "string" && !m.includes('"viewers"')) as string[];
    expect(inputs.map((m) => JSON.parse(m) as { t: string; code?: string })).toEqual([{ t: "k", code: "KeyW", down: true }]);

    const health = await (await fetch(`http://127.0.0.1:${port}/health`)).json() as { stream: { publishers: number; viewers: number; frames: number } };
    expect(health.stream).toMatchObject({ publishers: 1, viewers: 3, frames: 1 });

    pub.ws.close(); await wait(80);
    expect(status(own.got)).toMatchObject({ publishing: false, control: false });
  });
});
