// Manual live check of the three-mode baseline experiment (scripted vs Meta vs Drex) against a running server.
// Makes real provider calls. Usage: tsx test/liveExperiment.ts [seconds]
import { io } from "socket.io-client";
import type { CommandAck, ExperimentResult, JoinResult } from "@vice/contracts";
const url = process.env.URL ?? "http://localhost:3001"; const secs = Number(process.argv[2] ?? 30);
const s = io(url, { transports: ["websocket"] });
const send = <R>(c: Record<string, unknown>) => new Promise<CommandAck<R>>((r) => s.emit("command", c, r));
const j = await send<JoinResult>({ type: "room.create", nickname: "Director", role: "SPECTATOR", requestId: "exp-join" });
if (!j.ok) { console.log("create failed", j); process.exit(1); }
console.log("private room", j.result!.roomCode, "director", j.result!.isDirector, "badge", j.result!.snapshot.providerStatus.badge);
const done = new Promise<ExperimentResult>((resolve) => s.on("experiment.update", (r: ExperimentResult) => {
  console.log(`[${r.status}] ` + r.rows.map((x) => `${x.mode}: dec=${x.decisions} calls=${x.realProviderCalls} fb=${x.fallbacks} msgs=${x.messages} cap=${x.captured}`).join(" | "));
  if (r.status !== "RUNNING") resolve(r);
}));
const a = await send<{ experimentId: string }>({ type: "demo.runExperiment", commandId: `exp-${Date.now()}`, durationSec: secs });
console.log("ack", JSON.stringify(a));
if (!a.ok) process.exit(1);
const r = await done;
console.log(JSON.stringify(r, null, 1));
s.close(); process.exit(0);
