// Cuts one real run, recorded from three seats at once (record-povs.mjs), into a single continuous 60 s timeline:
// each cut picks up at the same run time on the next page. Tags, lower thirds and narration are worded from the run's events.
// Usage: TAKE=<dir> WORK=<dir> node demo/assemble-povs.mjs
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
const TAKE = process.env.TAKE, WORK = process.env.WORK, REPO = path.resolve(import.meta.dirname, ".."); mkdirSync(WORK, { recursive: true });
const CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", PIPER = "/tmp/vxtts/bin/piper", MODEL = "/tmp/vxvoice/ryan.onnx";
const ff = (a) => execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...a], { stdio: ["ignore", "pipe", "inherit"] });
const dur = (f) => Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).toString().trim());
const R = JSON.parse(readFileSync(`${TAKE}/run.json`, "utf8")); const E = R.events.sort((a, b) => a.t - b.t); const J = R.J;
const npc = (id) => R.npcs.find((n) => n.id === id), chr = (id) => R.chars.find((c) => c.id === id);
const who = (id) => npc(id)?.name ?? chr(id)?.nickname ?? id;
const capA = E.find((e) => e.type === "CAPTURE_SUCCEEDED" && /^ACE/.test(e.p.ticker ?? "") && e.t > J);
const capH = E.find((e) => e.type === "CAPTURE_SUCCEEDED" && e.t > J + 21000);
if (!capH || !R.insOpen || !R.insClose) throw new Error("run lacks a late capture or the inspector marks");
const s = J - 12000, end = s + 60000;
const cut1 = capA && capA.t < J + 3000 ? capA.t + 3400 : J + 4000;
const segs = [
  { seat: "OUTLAW", cam: "A", t0: s, t1: cut1, tag: "OUTLAW: you are the stock" },
  { seat: "TRADING FLOOR", cam: "C", t0: cut1, t1: R.insOpen, tag: "TRADING FLOOR: spectators and traders" },
  { seat: "INSIDE AN AGENT", cam: "C", t0: R.insOpen, t1: R.insClose, tag: "INSIDE AN AGENT: one Drex decision" },
  { seat: "HUNTER", cam: "B", t0: R.insClose, t1: capH.t - 1200, tag: "HUNTER: chasing the bounty" },
  { seat: "CAPTURE", cam: "B", t0: capH.t - 1200, t1: end, tag: "CAPTURE" },
];
const inSeg = (g, pred) => E.find((e) => e.t >= g.t0 && e.t < g.t1 && pred(e));
const job = E.find((e) => e.type === "MISSION_COMPLETED" && /^ACE/.test(e.p.ticker ?? "") && e.t === J) ?? E.find((e) => e.type === "MISSION_COMPLETED" && Math.abs(e.t - J) < 50);
// Lower thirds and narration, each worded from this run's events.
segs[0].lower = capA && capA.t < cut1 ? `Ace takes a job; ${capA.p.captorName} sees it and captures ${capA.p.ticker} ${((capA.t - J) / 1000).toFixed(1)} s later` : `Ace takes a job: ${job?.p.ticker ?? "the ticker"} gains notoriety and heat`;
segs[0].say = [[0.35, "As the outlaw, you are the stock. Take a job, and the city reacts."], capA && capA.t < cut1 ? [(J - s) / 1000 + 1.6, "An officer saw it. Caught, and the price goes to zero."] : null].filter(Boolean);
const d2 = inSeg(segs[1], (e) => e.type === "DECISION_RESOLVED") ?? E.filter((e) => e.type === "DECISION_RESOLVED" && e.t < segs[1].t1).pop();
segs[1].lower = d2 ? `${who(d2.p.agentId)} (${d2.p.role.toLowerCase()}) chose "${d2.p.chosenAction}" · decided by ${d2.p.decisionSource}` : "Every agent's decision appears as a card";
segs[1].say = [[0.3, "On the trading floor, every agent's decision is on screen."]];
const insAgent = npc(R.insAgent); const dIns = E.filter((e) => e.type === "DECISION_RESOLVED" && e.p.agentId === R.insAgent && e.t < R.insClose).pop();
segs[2].lower = dIns ? `${insAgent.name}'s last decision: "${dIns.p.chosenAction}" · ${dIns.p.decisionSource}${dIns.p.latencyMs ? ` · ${Math.round(dIns.p.latencyMs)} ms` : ""}` : `${insAgent.name}: private memory and decision trace`;
segs[2].say = [[0.3, "Inside one agent: its own memory, its options, and the model's answer."]];
const shots = E.filter((e) => e.type === "WEAPON_FIRED" && e.t >= segs[3].t0 && e.t < segs[3].t1);
const byRyder = shots.filter((e) => /^Ryder/.test(chr(e.p.shooterId)?.nickname ?? who(e.p.shooterId) ?? "")), byRio = E.filter((e) => e.type === "WEAPON_FIRED" && e.t >= segs[3].t0 && e.t < end && /^Rio/.test(chr(e.p.shooterId)?.nickname ?? ""));
const tgt = R.hunterTarget ?? "the fugitive";
segs[3].lower = byRyder.length ? `Rio hunts ${tgt}; the fugitive agent fires its stun pulse ${byRyder.length} time${byRyder.length > 1 ? "s" : ""}` : `Rio closes in on ${tgt}`;
segs[3].say = [[0.3, `The hunter goes after ${tgt === "Ryder" ? "Ryder, a wanted fugitive agent" : "the wanted outlaw"}.`], byRyder.length ? [8.5, "The fugitive is an agent too, and it fires back."] : null, byRio.length && byRio[0].t < segs[3].t1 ? [(byRio[0].t - segs[3].t0) / 1000 - 0.2, "The hunter fires his stun."] : null].filter(Boolean);
const human = capH.p.captorKind === "HUMAN";
segs[4].lower = `Captured by ${capH.p.captorName}: ${capH.p.ticker} goes to V$0${capH.p.bounty ? ` · bounty V$${capH.p.bounty}` : " · no bounty paid"}`;
segs[4].say = [[0.5, human ? `The hunter makes the capture. ${capH.p.ticker.replace("-", " ")} drops to zero, and the bounty is his.` : "A police agent gets there first. The ticker drops to zero, and the hunter gets nothing."]];
const esc = (x) => x.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const png = (html, out, h) => { const f = out.replace(/\.png$/, ".html"); writeFileSync(f, `<body style="margin:0;background:transparent;font-family:-apple-system,'Helvetica Neue',Arial,sans-serif">${html}</body>`); execFileSync(CH, ["--headless=new", "--hide-scrollbars", "--default-background-color=00000000", `--window-size=1920,${h}`, `--user-data-dir=${WORK}/chrome`, `--screenshot=${out}`, `file://${f}`], { stdio: "ignore" }); };
const speak = (text, out) => { const raw = out.replace(/\.wav$/, "-raw.wav"); execFileSync(PIPER, ["-m", MODEL, "-f", raw, "--length-scale", "0.88", "--sentence-silence", "0.28"], { input: text, stdio: ["pipe", "ignore", "ignore"] }); ff(["-i", raw, "-af", "loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000", "-ac", "2", out]); };
const ENC = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30", "-fps_mode", "cfr"];
let off = 0; const files = [], voice = [], table = [];
segs.forEach((g, i) => {
  const len = (g.t1 - g.t0) / 1000; const fr = R.frames[g.cam].filter((f) => f.t >= g.t0 && f.t <= g.t1); if (fr.length < 2 || len < 1.5) throw new Error(`segment ${g.seat} has no footage`);
  let list = ""; fr.forEach((f, k) => { const d = ((k + 1 < fr.length ? fr[k + 1].t : g.t1) - f.t) / 1000; list += `file '${TAKE}/${f.f}'\nduration ${Math.max(0.01, d).toFixed(3)}\n`; }); list += `file '${TAKE}/${fr[fr.length - 1].f}'\n`;
  writeFileSync(`${WORK}/s${i}.txt`, list);
  png(`<div style="position:absolute;left:36px;top:26px;padding:10px 22px;border-radius:10px;background:#ff4fa3;color:#0a0a14;font-weight:900;font-size:30px;letter-spacing:1.5px">${esc(g.tag)}</div>`, `${WORK}/tag${i}.png`, 110);
  png(`<div style="position:absolute;left:60px;bottom:14px;max-width:1760px;padding:14px 26px;border-radius:12px;background:rgba(10,10,20,.9);border:2px solid #2ee6d6;color:#f2f4ff;font-weight:700;font-size:32px">${esc(g.lower)}</div>`, `${WORK}/low${i}.png`, 110);
  const out = `${WORK}/seg${i}.mp4`;
  ff(["-f", "concat", "-safe", "0", "-i", `${WORK}/s${i}.txt`, "-loop", "1", "-i", `${WORK}/tag${i}.png`, "-loop", "1", "-i", `${WORK}/low${i}.png`, "-filter_complex", `[0:v]fps=30,scale=1920:1080,setsar=1,tpad=stop_mode=clone:stop_duration=3[v];[v][1:v]overlay=0:0[w];[w][2:v]overlay=0:960:enable='gte(t,0.8)'`, "-t", len.toFixed(3), ...ENC, out]);
  files.push(out); g.say.forEach(([at, text], k) => { const w = `${WORK}/say${i}-${k}.wav`; speak(text, w); voice.push({ at: off + Math.max(0.2, at), w, text, len: dur(w) }); });
  table.push({ seat: g.seat, cam: g.cam, at: off, len, capFps: fr.length / len, lower: g.lower }); off += len;
});
writeFileSync(`${WORK}/concat.txt`, files.map((f) => `file '${f}'`).join("\n") + "\n");
const silent = `${WORK}/video.mp4`; ff(["-f", "concat", "-safe", "0", "-i", `${WORK}/concat.txt`, "-c", "copy", silent]);
const total = dur(silent);
// Same audio treatment as the main demo: the same bed recipe and level, the same Piper voice, decision ticks and the capture hit.
const tick = `${WORK}/tick.wav`, hit = `${WORK}/hit.wav`, bed = `${WORK}/bed.wav`;
ff(["-f", "lavfi", "-i", "sine=f=1320:d=0.07", "-af", "afade=t=out:st=0.01:d=0.06,volume=0.5", tick]);
ff(["-f", "lavfi", "-i", "sine=f=52:d=1.6", "-f", "lavfi", "-i", "anoisesrc=d=1.6:c=brown:a=0.5", "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0,lowpass=f=180,afade=t=out:st=0.05:d=1.5,volume=1.6", hit]);
ff(["-f", "lavfi", "-i", `sine=f=55:d=${total}`, "-f", "lavfi", "-i", `sine=f=82.4:d=${total}`, "-f", "lavfi", "-i", `sine=f=164.8:d=${total}`, "-f", "lavfi", "-i", `anoisesrc=d=${total}:c=pink:a=0.08`,
  "-filter_complex", `[0:a]volume=0.5[a];[1:a]volume=0.35,tremolo=f=0.15:d=0.6[b];[2:a]volume=0.12,tremolo=f=0.23:d=0.8[c];[3:a]lowpass=f=900[d];[a][b][c][d]amix=inputs=4:normalize=0,afade=t=in:d=2,afade=t=out:st=${(total - 3).toFixed(2)}:d=3,volume=0.045`, bed]);
const ins = ["-i", bed]; const parts = ["[0:a]aformat=channel_layouts=stereo[x0]"]; let n = 1;
for (const v of voice) { ins.push("-i", v.w); const ms = Math.round(v.at * 1000); parts.push(`[${n}:a]aformat=channel_layouts=stereo,adelay=${ms}|${ms}[x${n}]`); n++; }
const cues = E.filter((e) => (e.type === "DECISION_RESOLVED" || e.type === "CAPTURE_SUCCEEDED") && e.t >= s && e.t < end).slice(0, 44);
for (const c of cues) { ins.push("-i", c.type === "CAPTURE_SUCCEEDED" ? hit : tick); const ms = Math.round(c.t - s); parts.push(`[${n}:a]aformat=channel_layouts=stereo,adelay=${ms}|${ms}[x${n}]`); n++; }
ff([...ins, "-filter_complex", `${parts.join(";")};${Array.from({ length: n }, (_, k) => `[x${k}]`).join("")}amix=inputs=${n}:normalize=0:duration=first,alimiter=limit=0.95[out]`, "-map", "[out]", "-t", total.toFixed(2), "-c:a", "aac", "-b:a", "160k", "-ac", "2", `${WORK}/audio.m4a`]);
const tmp = `${WORK}/povs.final.mp4`; ff(["-i", silent, "-i", `${WORK}/audio.m4a`, "-c", "copy", "-movflags", "+faststart", "-shortest", tmp]);
const D = dur(tmp); if (!(D >= 58 && D <= 62)) throw new Error("wrong duration " + D);
execFileSync("mv", ["-f", tmp, `${REPO}/demo/vice-exchange-povs.mp4`]);
const dec = E.filter((e) => e.type === "DECISION_RESOLVED" && e.t >= s && e.t < end); const by = {}; for (const e of dec) by[e.p.decisionSource] = (by[e.p.decisionSource] ?? 0) + 1;
console.log(JSON.stringify({ duration: D, table: table.map((t) => ({ ...t, at: +t.at.toFixed(1), len: +t.len.toFixed(1), capFps: +t.capFps.toFixed(1) })), voice: voice.map((v) => [+v.at.toFixed(1), +v.len.toFixed(1), v.text]), decisionsBySource: by,
  captures: E.filter((e) => e.type === "CAPTURE_SUCCEEDED" && e.t >= s && e.t < end).map((e) => [+((e.t - s) / 1000).toFixed(1), e.p.ticker, e.p.captorName, e.p.captorKind, e.p.bounty]), shotsByRyder: byRyder.length, shotsByRio: byRio.length }, null, 1));
