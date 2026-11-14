// The browser UI's WORLD view embeds a 3D world client that plays AS the page's own player: it presents the page's
// session token on /bridge. The browser socket must keep the session; the bridge socket only acts for the player.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { io as connect, type Socket } from "socket.io-client";
import WebSocket from "ws";
import { SOCKET } from "@vice/contracts";
import type { CommandAck, JoinResult } from "@vice/contracts";
import { createApp, type App } from "../src/transport/app";

let app: App; let port = 0; let browser: Socket;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const snapshot = async () => (await fetch(`http://127.0.0.1:${port}/api/rooms/VICE-42/snapshot`)).json() as Promise<{ players: Array<{ connected: boolean }>; characters: Array<{ x: number; z: number }> }>;

beforeAll(async () => {
  app = createApp({
    port: 0, databaseUrl: ":memory:", directorSecret: "", tickHz: 10, defaultMode: "scripted", bridge: true,
    providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 },
  });
  port = await app.listen();
});
afterAll(async () => { browser?.close(); await app.close(); });

describe("companion attach on /bridge", () => {
  it("acts for the page's player without a second player, accepts world poses, and leaves the browser session alone on close", async () => {
    browser = connect(`http://127.0.0.1:${port}`, { transports: ["websocket"], forceNew: true, reconnection: false });
    const joined = await new Promise<CommandAck<JoinResult>>((res) => browser.emit(SOCKET.command, { type: "room.join", roomCode: "VICE-42", nickname: "Ace", role: "OUTLAW", requestId: "join-ace" }, res));
    expect(joined.ok).toBe(true);
    const token = joined.result!.sessionToken;

    const ws = new WebSocket(`ws://127.0.0.1:${port}/bridge`);
    const acks = new Map<number, CommandAck>();
    ws.on("message", (d) => { const m = JSON.parse(String(d)) as { t: string; n: number; ack: CommandAck }; if (m.t === "ack") acks.set(m.n, m.ack); });
    await new Promise((r) => ws.once("open", r));
    const cmd = async (n: number, c: Record<string, unknown>) => { ws.send(JSON.stringify({ t: "cmd", n, cmd: c })); for (let i = 0; i < 50 && !acks.has(n); i++) await wait(20); return acks.get(n)!; };

    const j = await cmd(1, { type: "room.join", roomCode: "VICE-42", nickname: "Ace", role: "OUTLAW", requestId: "join-world", sessionToken: token });
    expect(j.ok).toBe(true);
    expect((j.result as JoinResult).playerId).toBe(joined.result!.playerId);
    expect((j.result as JoinResult).characterId).toBe(joined.result!.characterId);
    expect((await snapshot()).players).toHaveLength(1);

    // The 3D world owns its geometry: a pose that is inside a building on the server's map is accepted from the bridge
    // (step there in small moves to stay under the speed limit), while the same pose from the browser socket is not.
    const start = (await snapshot()).characters[0]!;
    await wait(300);
    const near = await cmd(2, { type: "player.pose", x: start.x, z: start.z - 2 });
    expect(near.ok).toBe(true);
    expect((await snapshot()).characters[0]!.z).toBeCloseTo(start.z - 2, 1);

    ws.close(); await wait(150);
    const after = await snapshot();
    expect(after.players).toHaveLength(1);
    expect(after.players[0]!.connected).toBe(true); // closing the world client did not disconnect the browser's player
  });
});
