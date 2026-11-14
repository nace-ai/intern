// Cuts the best continuous ~60 s (job through capture) from a record-agents take, adds up to five true lower-third
// captions and an ffmpeg-generated bed with decision ticks and a capture hit. No narration, nothing sped up.
// Usage: TAKE=<dir> WORK=<dir> node demo/assemble-agents.mjs
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, renameSync } from "node:fs";
import path from "node:path";
const TAKE = process.env.TAKE, WORK = process.env.WORK, REPO = path.resolve(import.meta.dirname, ".."); mkdirSync(WORK, { recursive: true });
const CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const ff = (a) => execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...a], { stdio: ["ignore", "pipe", "inherit"] });
const R = JSON.parse(readFileSync(`${TAKE}/run.json`, "utf8")); const E = R.events.filter((e) => e.t >= R.t0 - 1000).sort((a, b) => a.t - b.t);
const name = (id) => R.npcs.find((n) => n.id === id)?.name ?? id;
const job = E.find((e) => e.type === "MISSION_COMPLETED" && /^RYD/.test(e.p.ticker ?? ""));
const cap = E.find((e) => e.type === "CAPTURE_SUCCEEDED" && /^RYD/.test(e.p.ticker ?? "") && (!job || e.t > job.t));
const LEN = 60000;
let s = R.t0;
if (cap) s = Math.max(R.t0, Math.min(job ? job.t - 3000 : R.t0, cap.t + 9000 - LEN)); else if (job) s = Math.max(R.t0, job.t - 3000);
if (s + LEN > R.t1) s = Math.max(R.t0, R.t1 - LEN);
const e1 = s + LEN, inW = (e) => e && e.t >= s && e.t <= e1 - 1500;
const after = (t, pred) => E.find((e) => e.t >= t && pred(e));
const j0 = job?.t ?? s;
const wit = after(j0, (e) => e.type === "DECISION_RESOLVED" && e.p.role === "WITNESS");
const trade = after(j0, (e) => e.type === "TRADE_EXECUTED" && e.p.actorKind === "NPC");
const trDec = after(j0, (e) => e.type === "DECISION_RESOLVED" && e.p.role === "TRADER");
const pol = after(j0, (e) => e.type === "AGENT_ACTION_APPLIED" && e.p.role === "POLICE" && e.p.action === "pursue") ?? after(j0, (e) => e.type === "AGENT_ACTION_APPLIED" && e.p.role === "POLICE");
let caps = [
  inW(job) && { t: job.t, text: `Ryder, a Drex-driven fugitive, takes a job (${job.p.ticker})` },
  inW(wit) && { t: wit.t, text: `A witness decides: ${wit.p.chosenAction}` },
  inW(trade) ? { t: trade.t, text: `Traders react: ${trade.p.actorName} ${trade.p.side === "BUY" ? "buys" : "sells"} ${trade.p.ticker}` } : inW(trDec) && { t: trDec.t, text: `Traders react: ${name(trDec.p.agentId)} chooses ${trDec.p.chosenAction}` },
  inW(pol) && { t: pol.t, text: pol.p.action === "pursue" ? "Police pursue" : `Police ${pol.p.action}` },
  inW(cap) && { t: cap.t, text: `Captured by ${cap.p.captorName}: ${cap.p.ticker} goes to V$0` },
].filter(Boolean).sort((a, b) => a.t - b.t);
let last = -1; caps = caps.map((c) => { let a = Math.max((c.t - s) / 1000, last + 0.3); const b = Math.min(a + 4, LEN / 1000 - 0.2); last = b; return { ...c, a, b }; }).filter((c) => c.b - c.a > 1.5);
const esc = (x) => x.replace(/&/g, "&amp;").replace(/</g, "&lt;");
caps.forEach((c, i) => { const h = `${WORK}/cap${i}.html`; c.png = `${WORK}/cap${i}.png`;
  writeFileSync(h, `<body style="margin:0;background:transparent"><div style="position:absolute;left:60px;bottom:14px;padding:14px 26px;border-radius:12px;background:rgba(10,10,20,.88);border:2px solid #2ee6d6;color:#f2f4ff;font:700 34px -apple-system,'Helvetica Neue',Arial,sans-serif">${esc(c.text)}</div></body>`);
  execFileSync(CH, ["--headless=new", "--hide-scrollbars", "--default-background-color=00000000", "--window-size=1920,110", `--user-data-dir=${WORK}/chrome`, `--screenshot=${c.png}`, `file://${h}`], { stdio: "ignore" }); });
const fs = R.frames.filter((f) => f.t >= s && f.t <= e1); let list = "";
fs.forEach((f, i) => { const d = ((i + 1 < fs.length ? fs[i + 1].t : e1) - f.t) / 1000; list += `file '${TAKE}/${f.f}'\nduration ${Math.max(0.01, d).toFixed(3)}\n`; }); list += `file '${TAKE}/${fs[fs.length - 1].f}'\n`;
writeFileSync(`${WORK}/list.txt`, list);
const ins = ["-f", "concat", "-safe", "0", "-i", `${WORK}/list.txt`]; let chain = `[0:v]fps=30,scale=1920:1080,setsar=1[v0]`; let lab = "v0";
caps.forEach((c, i) => { ins.push("-loop", "1", "-i", c.png); chain += `;[${lab}][${i + 1}:v]overlay=0:960:enable='between(t,${c.a.toFixed(2)},${c.b.toFixed(2)})'[v${i + 1}]`; lab = `v${i + 1}`; });
const silent = `${WORK}/video.mp4`;
ff([...ins, "-filter_complex", chain, "-map", `[${lab}]`, "-t", "60", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", "30", "-fps_mode", "cfr", silent]);
const ticks = E.filter((e) => e.type === "DECISION_RESOLVED" && e.t >= s && e.t < e1).slice(0, 36).map((e) => Math.round(e.t - s));
const hit = cap && cap.t >= s && cap.t < e1 ? Math.round(cap.t - s) : null;
const a = ["-f", "lavfi", "-t", "60", "-i", "anoisesrc=color=brown:amplitude=0.05,lowpass=f=220", "-f", "lavfi", "-t", "60", "-i", "sine=f=55:sample_rate=44100"]; let parts = [`[0:a]aformat=channel_layouts=stereo,volume=0.5[b0]`, `[1:a]aformat=channel_layouts=stereo,volume=0.06[b1]`]; let mix = "[b0][b1]", n = 2;
for (const ms of ticks) { a.push("-f", "lavfi", "-t", "0.07", "-i", "sine=f=1250:sample_rate=44100"); parts.push(`[${n}:a]aformat=channel_layouts=stereo,afade=t=out:st=0.02:d=0.05,volume=0.35,adelay=${ms}|${ms}[x${n}]`); mix += `[x${n}]`; n++; }
if (hit != null) { a.push("-f", "lavfi", "-t", "0.9", "-i", "sine=f=58:sample_rate=44100"); parts.push(`[${n}:a]aformat=channel_layouts=stereo,afade=t=out:st=0.1:d=0.8,volume=1.6,adelay=${hit}|${hit}[x${n}]`); mix += `[x${n}]`; n++; }
ff([...a, "-filter_complex", `${parts.join(";")};${mix}amix=inputs=${n}:normalize=0:duration=first,alimiter=limit=0.9[out]`, "-map", "[out]", "-t", "60", "-c:a", "aac", "-b:a", "128k", "-ac", "2", `${WORK}/audio.m4a`]);
const tmp = `${WORK}/agents.final.mp4`;
ff(["-i", silent, "-i", `${WORK}/audio.m4a`, "-c", "copy", "-movflags", "+faststart", "-shortest", tmp]);
const D = Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", tmp]).toString());
if (!(D > 57 && D < 63)) throw new Error("wrong duration " + D);
execFileSync("mv", ["-f", tmp, `${REPO}/demo/vice-exchange-agents-playing.mp4`]);
const inWin = E.filter((e) => e.t >= s && e.t < e1 && e.type === "DECISION_RESOLVED"); const by = {}; for (const e of inWin) by[e.p.decisionSource] = (by[e.p.decisionSource] ?? 0) + 1;
console.log(JSON.stringify({ duration: D, windowStartSec: (s - R.t0) / 1000, takeSec: (R.t1 - R.t0) / 1000, jobAt: job ? (job.t - s) / 1000 : null, captureAt: cap ? (cap.t - s) / 1000 : null, jobToCaptureSec: job && cap ? (cap.t - job.t) / 1000 : null,
  captor: cap ? [cap.p.captorName, cap.p.captorKind, cap.p.ticker, cap.p.bounty] : null, decisionsInWindowBySource: by, runMetrics: R.metrics.decisions.bySource, ticks: ticks.length, captions: caps.map((c) => [c.a.toFixed(1), c.text]),
  nextRyder: E.find((e) => cap && e.t > cap.t && e.type === "LISTING_OPENED" && /^RYD/.test(e.p.ticker ?? ""))?.p.ticker ?? null }, null, 1));
