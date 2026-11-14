// Assembles the demo videos from a recorded take (see record.mjs): cuts segments by the recorder's marks, burns in
// caption bars (rendered as PNGs by headless Chrome, since this ffmpeg has no drawtext), adds title/end cards,
// narration synthesised with macOS `say`, an ffmpeg-generated bed and effect cues, and muxes H.264 + AAC.
// Usage: TAKE=<take dir> WORK=<work dir> node demo/assemble.mjs
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

const TAKE = process.env.TAKE, WORK = process.env.WORK, REPO = path.resolve(import.meta.dirname, "..");
const TESTS = process.env.TESTS ?? "";
mkdirSync(WORK, { recursive: true });
const CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const ff = (args) => execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { stdio: ["ignore", "pipe", "inherit"] });
const dur = (f) => Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).toString().trim());
const D = JSON.parse(readFileSync(`${TAKE}/marks.json`, "utf8"));
const M = Object.fromEntries(D.marks.map((m) => [m.name, m]));
const T = (n) => M[n]?.t;
const has = (...n) => n.every((k) => M[k]);
const BG = "0x0a0a14";
const CAPT = (D.events ?? []).find((e) => e.type === "CAPTURE_SUCCEEDED")?.t ?? M.crash?.t;
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");

function shot(html, out, w = 1920, h = 1080) {
  const f = `${WORK}/${path.basename(out)}.html`; writeFileSync(f, html);
  execFileSync(CH, ["--headless=new", "--hide-scrollbars", `--window-size=${w},${h}`, `--user-data-dir=${WORK}/chrome-cards`, `--screenshot=${out}`, `file://${f}`], { stdio: "ignore" });
}
const FONT = `font-family:-apple-system,'Helvetica Neue',Arial,sans-serif`;
function caption(text, name, tag = "", src = "") {
  const full = `${WORK}/${name}-full.png`, out = `${WORK}/${name}.png`;
  const pill = tag ? `<span style="flex:none;margin-right:22px;padding:6px 16px;border-radius:8px;background:#ff4fa3;color:#0a0a14;font-size:24px;font-weight:900;letter-spacing:2px">${esc(tag)}</span>` : "";
  const small = src ? ` <span style="font-size:17px;font-weight:400;color:#8f96bd">(${esc(src)})</span>` : "";
  shot(`<body style="margin:0;background:#0a0a14"><div style="position:absolute;left:0;top:990px;width:1920px;height:90px;display:flex;align-items:center;justify-content:center;background:#0a0a14;border-top:2px solid #2ee6d6;${FONT};color:#f2f4ff;font-size:${text.length > 105 ? 24 : 28}px;font-weight:600;line-height:1.15;text-align:left;padding:0 50px;box-sizing:border-box">${pill}<span>${esc(text)}${small}</span></div></body>`, full);
  ff(["-i", full, "-vf", "crop=1920:90:0:990", out]);
  return out;
}
function page(inner, name) { const out = `${WORK}/${name}.png`; shot(`<body style="margin:0;background:radial-gradient(ellipse at 50% 10%,#2a1050 0%,#0a0a14 70%);${FONT};color:#f2f4ff;height:1080px;box-sizing:border-box;padding:70px 110px">${inner}</body>`, out); return out; }
function card(lines, name) {
  const out = `${WORK}/${name}.png`;
  shot(`<body style="margin:0;background:radial-gradient(ellipse at 50% 20%,#2a1050 0%,#0a0a14 70%);${FONT};color:#f2f4ff;display:flex;flex-direction:column;align-items:center;justify-content:center;height:1080px;text-align:center">
  <div style="font-size:124px;font-weight:900;letter-spacing:10px"><span style="color:#ff4fa3;text-shadow:0 0 28px #ff4fa3aa">VICE</span> <span style="color:#2ee6d6;text-shadow:0 0 28px #2ee6d6aa">EXCHANGE</span></div>
  ${lines.map((l, i) => `<div style="margin-top:${i ? 18 : 44}px;font-size:${i ? 34 : 44}px;color:${i ? "#b9bfe0" : "#f2f4ff"};max-width:1500px">${esc(l)}</div>`).join("")}</body>`, out);
  return out;
}
function listFile(frames, t0, t1, name) {
  const fs = frames.filter((f) => f.t >= t0 && f.t <= t1);
  if (fs.length < 2) throw new Error(`no frames for ${name}`);
  let s = "";
  fs.forEach((f, i) => { const d = ((i + 1 < fs.length ? fs[i + 1].t : t1) - f.t) / 1000; s += `file '${TAKE}/${f.f}'\nduration ${Math.max(0.02, d).toFixed(3)}\n`; });
  s += `file '${TAKE}/${fs[fs.length - 1].f}'\n`;
  const out = `${WORK}/${name}.txt`; writeFileSync(out, s);
  return { out, real: (t1 - t0) / 1000, t0 };
}
const FPS = 30;
const ENC = ["-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", String(FPS), "-fps_mode", "cfr"];
const FRAME = `scale=1760:990,pad=1920:1080:80:0:color=${BG}`;

/** Renders one segment to mp4 of exactly `len` seconds; returns { file, len, map(wallMs) -> seconds into segment }. */
function seg(s, i, prefix) {
  const out = `${WORK}/${prefix}-${String(i).padStart(2, "0")}-${s.id}.mp4`;
  const cap = s.caption ? caption(s.caption, `${prefix}-cap-${i}`, s.tag, s.kind === "clip" ? "" : s.src) : null;
  const len = s.len;
  let speed = 1, t0 = 0;
  if (s.kind === "card") {
    // The game keeps playing behind every card: real footage from the take under the card at about 75 percent opacity.
    let bg = null;
    if (s.bg != null && D.framesC?.length) { try { bg = listFile(D.framesC, s.bg, s.bg + len * 1000, `${prefix}-${i}-bg`); } catch { bg = null; } }
    if (bg) ff(["-f", "concat", "-safe", "0", "-i", bg.out, "-loop", "1", "-i", s.png, "-filter_complex", `[0:v]fps=${FPS},scale=1920:1080,tpad=stop_mode=clone:stop_duration=20[b];[1:v]fps=${FPS},scale=1920:1080,format=yuv420p[c];[b][c]blend=all_expr='A*0.26+B*0.74'`, "-t", String(len), ...ENC, out]);
    else ff(["-loop", "1", "-i", s.png, "-t", String(len), "-vf", `fps=${FPS},scale=1920:1080`, ...ENC, out]);
  } else if (s.kind === "still") {
    ff(["-loop", "1", "-i", s.png, "-loop", "1", "-i", cap, "-filter_complex", `[0:v]fps=${FPS},${FRAME}[v];[v][1:v]overlay=0:990`, "-t", String(len), ...ENC, out]);
  } else if (s.kind === "clip") {
    // Real time only: never sped up. The window is cut to the segment length (tail: keep the end of the shot).
    const win = Math.min(len, s.end - s.start), st = s.tail ? s.end - win : s.start;
    const base = `framerate=fps=${FPS},loop=loop=-1:size=${Math.max(2, Math.floor(win * FPS) - 1)}:start=0,setpts=N/${FPS}/TB`;
    if (!cap) ff(["-ss", String(st), "-t", String(win), "-i", s.src, "-vf", `${base},scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080`, "-t", String(len), ...ENC, out]);
    else ff(["-ss", String(st), "-t", String(win), "-i", s.src, "-loop", "1", "-i", cap, "-filter_complex", `[0:v]${base},${FRAME}[v];[v][1:v]overlay=0:990`, "-t", String(len), ...ENC, out]);
  } else if (s.kind === "duo") {
    const a = listFile(D.framesA, s.t0, s.t1, `${prefix}-${i}-a`), b = listFile(D.framesB, s.t0, s.t1, `${prefix}-${i}-b`);
    speed = Math.max(1, a.real / len); t0 = s.t0;
    ff(["-f", "concat", "-safe", "0", "-i", a.out, "-f", "concat", "-safe", "0", "-i", b.out, "-loop", "1", "-i", cap, "-filter_complex",
      `[0:v]setpts=PTS/${speed.toFixed(4)},fps=${FPS},scale=960:540[a];[1:v]setpts=PTS/${speed.toFixed(4)},fps=${FPS},scale=960:540[b];[a][b]hstack,pad=1920:1080:0:225:color=${BG},tpad=stop_mode=clone:stop_duration=20[v];[v][2:v]overlay=0:990`,
      "-t", String(len), ...ENC, out]);
  } else {
    // Real time only: the window is cut to the segment length (tail: keep the end), never sped up.
    const winMs = Math.min(len * 1000, s.t1 - s.t0), w0 = s.tail ? s.t1 - winMs : s.t0, w1 = w0 + winMs;
    const l = listFile(D[`frames${s.cam}`], w0, w1, `${prefix}-${i}`);
    speed = 1; t0 = w0; s.t0 = w0; s.t1 = w1;
    const sp = `fps=${FPS}`;
    if (s.crop === "split") {
      // Game on the left, trading-floor terminal on the right, both full height: the frame is always filled.
      const g0 = s.game ?? 26;
      ff(["-f", "concat", "-safe", "0", "-i", l.out, "-ss", String(g0), "-t", String(len + 1), "-i", CLIP, "-loop", "1", "-i", cap, "-filter_complex",
        `[0:v]${sp},crop=836:860:764:40,scale=962:990,tpad=stop_mode=clone:stop_duration=20[t];[1:v]framerate=fps=${FPS},scale=1760:990,crop=958:990:401:0,eq=brightness=-0.05,tpad=stop_mode=clone:stop_duration=20[g];[g][t]hstack=inputs=2,pad=1920:1080:0:0:color=${BG}[v];[v][2:v]overlay=0:990`, "-t", String(len), ...ENC, out]);
    } else {
      // Zoom: a 16:9 region of the terminal scaled up to fill the frame so the panel can be read.
      const z = s.crop === "inspector" ? "crop=836:470:764:300" : s.crop === "cards" ? "crop=836:470:764:40" : s.crop === "table" ? "crop=1100:619:0:281" : null;
      const fr = z ? `${z},scale=1760:990,pad=1920:1080:80:0:color=${BG}` : FRAME;
      ff(["-f", "concat", "-safe", "0", "-i", l.out, "-loop", "1", "-i", cap, "-filter_complex",
        `[0:v]${sp},${fr},tpad=stop_mode=clone:stop_duration=20[v];[v][1:v]overlay=0:990`, "-t", String(len), ...ENC, out]);
    }
  }
  return { file: out, len, speed, map: (t) => (t0 && t >= s.t0 && t <= s.t1 ? (t - s.t0) / 1000 / speed : null) };
}

const PIPER = process.env.PIPER ?? "/tmp/vxtts/bin/piper", PIPER_MODEL = process.env.PIPER_MODEL ?? "/tmp/vxvoice/ryan.onnx";
const HAVE_PIPER = existsSync(PIPER) && existsSync(PIPER_MODEL);
export const VOICE = HAVE_PIPER ? "Piper neural voice en_US-ryan-high (rhasspy/piper-voices)" : "macOS say, voice Daniel (fallback)";
/** One narration line -> loudness-normalised 48 kHz stereo WAV. Piper neural voice, or `say -v Daniel` if Piper is absent. */
function speak(text, out) {
  const raw = out.replace(/\.wav$/, "-raw.wav");
  if (HAVE_PIPER) execFileSync(PIPER, ["-m", PIPER_MODEL, "-f", raw, "--length-scale", "0.88", "--sentence-silence", "0.28"], { input: text, stdio: ["pipe", "ignore", "ignore"] });
  else { const a = raw.replace(/\.wav$/, ".aiff"); execFileSync("say", ["-v", "Daniel", "-r", "165", "-o", a, text.replace(/\. /g, ". [[slnc 250]] ")]); ff(["-i", a, raw]); }
  ff(["-i", raw, "-af", "loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000", "-ac", "2", out]);
}
function build(name, segsIn) {
  let segs = segsIn.filter(Boolean);
  // Narration first: a segment is never shorter than its line.
  segs.forEach((s, i) => {
    if (!s.say) return;
    s.aiff = `${WORK}/${name}-say-${i}.wav`;
    speak(s.say, s.aiff);
    s.sayLen = dur(s.aiff);
    s.len = Math.max(s.len, Math.ceil((s.sayLen + 0.55) * 10) / 10);
  });
  // Stay at or under 3:08: drop the least important shots first.
  for (const drop of ["replay", "traders", "ota-carheli", "memory"]) { if (segs.reduce((a, s) => a + s.len, 0) <= 188) break; if (name === "vice-exchange-demo") { console.log("over length, dropping", drop); segs = segs.filter((s) => s.id !== drop); } }
  const done = segs.map((s, i) => seg(s, i, name));
  writeFileSync(`${WORK}/${name}-concat.txt`, done.map((d) => `file '${d.file}'`).join("\n") + "\n");
  const silent = `${WORK}/${name}-video.mp4`;
  ff(["-f", "concat", "-safe", "0", "-i", `${WORK}/${name}-concat.txt`, "-c", "copy", silent]);
  const total = done.reduce((a, d) => a + d.len, 0);
  const offs = []; done.reduce((a, d, i) => ((offs[i] = a), a + d.len), 0);
  // Effect cues from the real event log: a soft tick per resolved decision, a low hit on capture.
  const cues = [];
  for (const e of D.events ?? []) {
    if (e.type !== "DECISION_RESOLVED" && e.type !== "CAPTURE_SUCCEEDED") continue;
    for (let i = 0; i < segs.length; i++) { const at = done[i].map(e.t); if (at !== null && at < done[i].len) { cues.push({ at: offs[i] + at, kind: e.type === "CAPTURE_SUCCEEDED" ? "hit" : "tick" }); } }
  }
  const tick = `${WORK}/tick.wav`, hit = `${WORK}/hit.wav`, bed = `${WORK}/${name}-bed.wav`;
  ff(["-f", "lavfi", "-i", "sine=f=1320:d=0.07", "-af", "afade=t=out:st=0.01:d=0.06,volume=0.5", tick]);
  ff(["-f", "lavfi", "-i", "sine=f=52:d=1.6", "-f", "lavfi", "-i", "anoisesrc=d=1.6:c=brown:a=0.5", "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0,lowpass=f=180,afade=t=out:st=0.05:d=1.5,volume=1.6", hit]);
  ff(["-f", "lavfi", "-i", `sine=f=55:d=${total}`, "-f", "lavfi", "-i", `sine=f=82.4:d=${total}`, "-f", "lavfi", "-i", `sine=f=164.8:d=${total}`, "-f", "lavfi", "-i", `anoisesrc=d=${total}:c=pink:a=0.08`,
    "-filter_complex", `[0:a]volume=0.5[a];[1:a]volume=0.35,tremolo=f=0.15:d=0.6[b];[2:a]volume=0.12,tremolo=f=0.23:d=0.8[c];[3:a]lowpass=f=900[d];[a][b][c][d]amix=inputs=4:normalize=0,afade=t=in:d=2,afade=t=out:st=${(total - 3).toFixed(2)}:d=3,volume=0.045`, bed]);
  const ins = ["-i", bed]; const parts = ["[0:a]aformat=channel_layouts=stereo[x0]"]; let n = 1;
  segs.forEach((s, i) => { if (!s.aiff) return; ins.push("-i", s.aiff); const ms = Math.round((offs[i] + 0.35) * 1000); parts.push(`[${n}:a]aformat=channel_layouts=stereo,volume=1.0,adelay=${ms}|${ms}[x${n}]`); n++; });
  for (const c of cues.slice(0, 40)) { ins.push("-i", c.kind === "hit" ? hit : tick); const ms = Math.round(c.at * 1000); parts.push(`[${n}:a]aformat=channel_layouts=stereo,adelay=${ms}|${ms}[x${n}]`); n++; }
  const mixIn = Array.from({ length: n }, (_, k) => `[x${k}]`).join("");
  const audio = `${WORK}/${name}-audio.m4a`;
  ff([...ins, "-filter_complex", `${parts.join(";")};${mixIn}amix=inputs=${n}:normalize=0:duration=first,alimiter=limit=0.95[out]`, "-map", "[out]", "-t", total.toFixed(2), "-c:a", "aac", "-b:a", "160k", "-ac", "2", audio]);
  // Build to a temp name inside WORK and move into demo/ only when complete, so the good copy is never truncated.
  const final = `${REPO}/demo/${name}.mp4`, tmpFinal = `${WORK}/${name}.final.mp4`;
  ff(["-i", silent, "-i", audio, "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", "-shortest", tmpFinal]);
  if (!(dur(tmpFinal) > 20)) throw new Error("assembled file too short, keeping the existing copy");
  if (process.env.OUTDIR) { execFileSync("cp", [tmpFinal, `${process.env.OUTDIR}/${name}.mp4`]); return tmpFinal; }
  execFileSync("mv", ["-f", tmpFinal, final]);
  const table = segs.map((s, i) => ({ at: offs[i], len: done[i].len, id: s.id, speed: Number(done[i].speed.toFixed(2)), caption: s.caption ?? "(card)", say: s.say ?? "" }));
  writeFileSync(`${WORK}/${name}-shots.json`, JSON.stringify({ total, cues: cues.length, table }, null, 1));
  console.log(name, "duration", dur(final).toFixed(2), "segments", table.map((t) => `${t.id}@${t.at.toFixed(1)}x${t.speed}`).join(" "));
  return final;
}

const FR = `${REPO}/integrations/ota-bridge/frames`;
const clips = readdirSync(FR).filter((f) => f.endsWith(".mp4")).map((f) => `${FR}/${f}`).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
// The Open Theft Auto footage is the spine. demo/ota-shots.json (optional) names the clip and the in/out seconds of each shot.
const SHOTS = existsSync(`${REPO}/demo/ota-shots.json`) ? JSON.parse(readFileSync(`${REPO}/demo/ota-shots.json`, "utf8")) : {};
const CLIP = SHOTS.file ? path.resolve(REPO, SHOTS.file) : (clips.find((c) => /ota_hunt/.test(c)) ?? clips.find((c) => /full_chain/.test(c)) ?? clips[0]), CLIPLEN = dur(CLIP);
const CLIP2 = clips.find((c) => /guns_cars_heli/.test(c)) ?? CLIP, CLIP2LEN = dur(CLIP2);
const part = (name, a, b) => { const r = SHOTS.shots?.[name]; return r ? { src: CLIP, start: r[0], end: r[1] } : { src: CLIP, start: CLIPLEN * a, end: CLIPLEN * b }; };
const carHeli = SHOTS.shots?.carheli ? { src: CLIP, start: SHOTS.shots.carheli[0], end: SHOTS.shots.carheli[1] } : (CLIP2 !== CLIP ? { src: CLIP2, start: 1, end: CLIP2LEN - 0.5 } : { src: CLIP, start: CLIPLEN * 0.82, end: CLIPLEN - 0.3 });
const allDrex = Object.keys(M.proof?.metrics?.bySource ?? { x: 1 }).every((k) => k === "drex");
const who = allDrex ? "Every card in this run is a real Drex call: options, probabilities, latency." : "Each card shows who decided, the options, probabilities and latency.";
const cap = M.crash?.by?.[0];

const mt = M.proof?.metrics ?? { total: 0, bySource: {} }, bs = mt.bySource;
const nDrex = bs.drex ?? 0, nGuard = bs.drex_low_confidence_guardrail ?? 0, nFb = bs.fallback ?? 0, nOther = mt.total - nDrex - nGuard - nFb;
const countsCap = `This run's metrics panel: ${mt.total} NPC decisions: ${nDrex} decided by Drex, ${nGuard} low-confidence guardrail, ${nFb} fallback${nOther ? `, ${nOther} other` : ""}.`;
const countsSay = `In this run the metrics panel counts ${mt.total} decisions: ${nDrex} decided by Drex, ${nGuard} by the low confidence guardrail, and ${nFb} by fallback.`;
const lat = M.proof?.latency?.drex;
const box = (t, d, c) => `<div style="flex:1;border:2px solid ${c};border-radius:14px;padding:18px 20px;background:#12122299"><div style="font-size:27px;font-weight:800;color:${c}">${t}</div><div style="margin-top:8px;font-size:22px;color:#c9cfee;line-height:1.3">${d}</div></div>`;
const arrow = `<div style="flex:none;font-size:44px;color:#ff4fa3;align-self:center;padding:0 10px">&#8594;</div>`;
const trackPng = page(`<div style="font-size:30px;letter-spacing:5px;color:#2ee6d6;font-weight:800">NACE AI DREXATHON &middot; THE TRACK</div>
<div style="margin-top:26px;font-size:54px;font-weight:900;color:#ff4fa3">Swarm Intelligence: multi-agent systems</div>
<div style="margin-top:22px;font-size:39px;line-height:1.35;font-weight:500">&ldquo;Build systems where multiple agents coordinate, compete, or negotiate, with Drex making every agent's calls. Think traders in a simulated market, NPCs that think for themselves, or a team of agents dividing up a research task.&rdquo;</div>
<div style="margin-top:44px;font-size:28px;color:#8f96bd">VICE EXCHANGE is two of those examples at once:</div>
<div style="margin-top:16px;display:flex;gap:26px">${box("Traders in a simulated market", "Two trader NPCs buy, hold or sell character tickers on one shared exchange.", "#2ee6d6")}${box("NPCs that think for themselves", "Witnesses, informants and police each decide from their own private memory.", "#ff4fa3")}${box("Bonus: NDI, real-world documents", "Police decisions carry facts NDI parsed from a real San Francisco Police Department order.", "#b98cff")}</div>`, "card-track");
const buildPng = page(`<div style="font-size:30px;letter-spacing:5px;color:#2ee6d6;font-weight:800">HOW IT IS BUILT &middot; ONE STEP OF THE SWARM</div>
<div style="margin-top:50px;display:flex">${box("1 &middot; Trigger", "An NPC witnesses an event or receives a message.", "#ffd166")}${arrow}${box("2 &middot; The NPC", "Role, goal and private memory. Nothing it was not shown or told.", "#ff4fa3")}${arrow}${box("3 &middot; One Drex call", "systemOne with a typed choice question built only from that NPC's knowledge.", "#2ee6d6")}</div>
<div style="margin-top:34px;display:flex">${box("4 &middot; Drex answers", "A label with a probability for every option.", "#2ee6d6")}${arrow}${box("5 &middot; Server enforces", "Checks the label against the actions that are legal right now, then applies it.", "#b98cff")}${arrow}${box("6 &middot; Effect", "A message to another agent, a trade on the shared market, or a police move.", "#ffd166")}</div>
<div style="margin-top:46px;font-size:34px;font-weight:700;text-align:center;color:#f2f4ff">Each effect is the trigger for the next agent: witness &#8594; police &#8594; public bulletin &#8594; traders &#8594; market &#8594; capture.</div>
<div style="margin-top:22px;font-size:24px;text-align:center;color:#8f96bd">The model chooses. Code owns positions, wallets, prices and the capture rule.</div>`, "card-build");
const EV = D.events ?? [];
const firstAfter = (type, t, pred = () => true) => EV.find((e) => e.type === type && e.t >= t && pred(e))?.t;
const C0 = T("city");
const tJob = C0 ? firstAfter("MISSION_COMPLETED", C0 - 60000, (e) => /^RYD/.test(e.p?.ticker ?? "")) ?? firstAfter("MISSION_COMPLETED", C0 - 60000) : null;
const tWit = tJob ? firstAfter("DECISION_RESOLVED", tJob, (e) => e.p?.role === "WITNESS") : null;
const tBul = tJob ? firstAfter("BULLETIN_PUBLISHED", tJob) : null;
const tCapR = M.rydercap?.t ?? null;
const inTake = (t) => t && C0 && t >= C0 && t <= (T("ryder1") ?? 0);
// Real trader numbers, read from the snapshots saved during the take.
const trA = M.city?.traders ?? [], trB = (M.proof ?? M.traders1 ?? M.ryder1)?.traders ?? [], trades = (M.proof ?? M.traders1 ?? M.ryder1)?.trades ?? [];
const tr = (list, re) => list.find((x) => re.test(x.name));
const shares = (x) => Object.values(x?.holdings ?? {}).reduce((a, b) => a + b, 0);
const p1 = tr(trB, /Castellan/), p0 = tr(trA, /Castellan/), r1 = tr(trB, /Halvorsen/), r0 = tr(trA, /Halvorsen/);
const nBuy = (re) => trades.filter((t) => re.test(t[0]) && t[1] === "BUY").length, nSell = (re) => trades.filter((t) => re.test(t[0]) && t[1] === "SELL").length;
const say$ = (n) => `${n} credits`;
const traderCap = p1 && r1 ? `Priya Castellan (momentum): ${nBuy(/Castellan/)} buys, ${nSell(/Castellan/)} sells, wallet V$${p1.credits}, ${shares(p1)} shares. Rook Halvorsen (risk-averse): ${nBuy(/Halvorsen/)} buys, ${nSell(/Halvorsen/)} sells, wallet V$${r1.credits}, ${shares(r1)} shares.` : "Two trader agents decide on the same ticker, each from its own memory and persona.";
const traderSay = p1 && r1 ? `Traders in a simulated market, each deciding with Drex to grow its own money. Priya is a momentum trader: in this run she bought ${nBuy(/Castellan/)} ${nBuy(/Castellan/) === 1 ? "time" : "times"}${nSell(/Castellan/) ? ` and sold ${nSell(/Castellan/)}` : ""}, and her wallet stands at ${say$(p1.credits)}. Rook is risk averse: ${nBuy(/Halvorsen/) + nSell(/Halvorsen/) === 0 ? "he held every time and kept his" : `he made ${nBuy(/Halvorsen/) + nSell(/Halvorsen/)} trades and has`} ${say$(r1.credits)}.` : "Two trader agents decide on the same stock, each with its own persona.";
const traderSay2 = p1 && r1 ? `Priya chases momentum. In this run she bought ${nBuy(/Castellan/)} ${nBuy(/Castellan/) === 1 ? "time" : "times"}${nSell(/Castellan/) ? ` and sold ${nSell(/Castellan/)}` : ""}, and has ${say$(p1.credits)} left${shares(p1) ? ` and ${shares(p1)} ${shares(p1) === 1 ? "share" : "shares"}` : ""}. Rook is risk averse. ${nBuy(/Halvorsen/) + nSell(/Halvorsen/) === 0 ? "He held every time and kept all" : `He made ${nBuy(/Halvorsen/) + nSell(/Halvorsen/)} trades and has`} ${say$(r1.credits)}.` : "Each trader has its own persona and its own wallet.";
const gamePng = page(`<div style="font-size:30px;letter-spacing:5px;color:#2ee6d6;font-weight:800">THE GAME</div>
<div style="margin-top:26px;font-size:64px;font-weight:900;color:#ff4fa3">Open Theft Auto, modified</div>
<div style="margin-top:24px;font-size:38px;line-height:1.35;font-weight:500">An open-source 3D city (MIT license, Godot), rebuilt as a trading floor: every fugitive is a stock, and every agent in the city decides with Drex.</div>
<div style="margin-top:26px;font-size:30px;line-height:1.35;color:#ffd166;font-weight:700">Every agent is out for its own profit. They team up, and they turn on each other. The longer one survives, the more it is worth, and the bigger the bounty on its head.</div>
<div style="margin-top:30px;display:flex;gap:26px">${box("The city", "Open Theft Auto's streets, cars and helicopter. Our server drives who is where and what they decide.", "#2ee6d6")}${box("The swarm", "Witnesses, informants, traders, police and a fugitive: nine agents, one Drex call per decision.", "#ff4fa3")}${box("The trading floor", "A browser terminal shows every decision, message, trade and price as it happens.", "#b98cff")}</div>`, "card-game");
const hasRyder = !!M.city?.ryder;
const main = [
  { id: "hype", kind: "clip", ...part("flyover", 0, 0.08), len: 6, say: "Are you guys ready for G T A six? Say woo! Well, it's not out yet. So, inspired by open-world games like it, we built our own. Vice Exchange: a city where every agent is a trader." },
  { id: "title", kind: "card", bg: (T("bg0") ?? C0) + 0, len: 3, png: card(["Open Theft Auto, rebuilt as a trading floor where every agent decides with Drex.", "Nace AI Drexathon · V$ are fictional game credits"], "card-title"),
    say: "Built on Open Theft Auto, an open source city. All the money is fictional game credits." },
  { id: "track", kind: "card", bg: (T("bg0") ?? C0) + 6500, len: 15, png: trackPng,
    say: "The track is Swarm Intelligence: multi-agent systems. Build systems where multiple agents coordinate, compete, or negotiate, with Drex making every agent's calls. Think traders in a simulated market, characters that think for themselves, or a team of agents dividing up a research task. This project is the first two at once." },
  { id: "game", kind: "card", bg: (T("bg0") ?? C0) + 27000, len: 8, png: gamePng,
    say: "Every agent is out for its own profit. They team up, and they turn on each other. The longer one survives, the more it is worth, and the bigger the bounty on its head. The game is Open Theft Auto, an open source city, modified so that a swarm of agents runs it." },
  { id: "how-built", kind: "card", bg: (T("bg0") ?? C0) + (process.env.ONLY === "opening" ? 42500 : 0), len: 14, png: buildPng,
    say: "One step of the swarm. An agent sees something, or gets a message. One Drex call, built only from what that agent knows, returns a choice with probabilities. The server checks it is legal, applies it, and that triggers the next agent." },
  { id: "ota-city", kind: "clip", ...part("ryder", 0.2, 0.3), len: 6, tag: "ONE CITY · NINE AGENTS", caption: "Inside the modified Open Theft Auto: witnesses, informants, traders, police and Ryder, a Drex-driven fugitive with his own ticker.",
    say: "Nine agents decide with Drex: two witnesses, two informants, two traders, two police, and Ryder, a fugitive who plays the outlaw by himself." },
  inTake(tWit) && { id: "terminal-chain", cam: "C", game: 18,  t0: Math.max(C0, tWit - 2500), t1: Math.min(tCapR ? tCapR - 300 : T("ryder1"), (tBul ?? tWit) + 9000), len: 8, tag: "EVERY ARROW IS ONE DREX DECISION", caption: "The trading floor terminal, same server: each agent's decision card with options, probabilities and latency, and the swarm graph of who told whom.", src: "live values from this recording's run",
    say: "They coordinate. A witness reports a job, police put out a bulletin, and the price falls. On the trading floor terminal, each card is one Drex decision, with its options, probabilities and latency." },
  !inTake(tWit) && has("city", "ryder1") && { id: "terminal-chain", cam: "C", game: 18,  t0: C0, t1: tCapR ? tCapR - 300 : T("ryder1"), len: 8, tag: "EVERY CARD IS ONE DECISION", caption: "The trading floor terminal, same server: each agent's decision card with options, probabilities and latency, and the swarm graph.", src: "live values from this recording's run",
    say: "They coordinate. A witness reports a job, police put out a bulletin, and the price falls. On the trading floor terminal, each card is one Drex decision, with its options, probabilities and latency." },
  has("crew0") && { id: "crew", cam: "C", game: 39,  t0: T("crew0") + 2500, t1: Math.min(T("crew0") + 10500, tCapR ? tCapR - 300 : Infinity), len: 6, tag: "THEY TEAM UP", caption: "Two human hunters form a crew to split the bounty. The trader agents see it and each decides what to do about it.", src: "decision cards: live values from this run",
    say: "They team up. Two human hunters form a crew to split the bounty. The traders see it, and each one decides what to do about it." },
  tCapR && has("ryder1") && T("ryder1") - tCapR > 7000 && { id: "pnl", cam: "C", crop: "inspector", t0: tCapR + 5500, t1: T("ryder1"), len: 5, tag: "AGENT P&L", caption: "After the capture: the officer is paid the bounty, the witness who reported gets a tip, and the traders mark their books.", src: "earnings shown are this run's",
    say: "Every agent is out for its own profit. The officer earns the bounty. The witness who reported earns a tip. The traders mark their books." },
  has("insWitness", "insTrader1end") && { id: "memory", cam: "C", crop: "inspector", t0: T("insWitness"), t1: T("insTrader1end"), len: 6, tag: "PRIVATE MEMORY", caption: "Each agent knows only what it saw or was told: source and hop count on every fact.",
    say: "Each agent knows only what it saw, or what it was told. Every fact carries its source and how many hands it passed through." },
  has("traders0", "traders1") && { id: "traders", cam: "C", crop: "cards", t0: T("traders0"), t1: T("traders1"), len: 6, tag: "TRADERS IN A SIMULATED MARKET", caption: "Traders in a simulated market, each deciding with Drex to grow its own V$: a new ticker lists and both trader agents decide.", src: "live values from this run",
    say: "Traders in a simulated market, each deciding with Drex to grow its own money. A new stock lists, and both traders make their call." },
  has("insTrader1", "insTrader2end") && { id: "traders-wallets", cam: "C", crop: "inspector", t0: T("insTrader1"), t1: T("insTrader2end"), len: 6, tag: "OUT FOR PROFIT", caption: traderCap, src: "wallets and trades read from this run",
    say: traderSay2 },
  { id: "ota-hunt", kind: "clip", tail: true, ...part("hunt", 0.42, 0.62), len: 6, tag: "THEY TURN ON EACH OTHER", caption: "Ryder fires his stun pulse at a human hunter. An officer pursues on a Drex decision and captures him: V$0.",
    say: "Ryder turns on a human hunter and stuns him. Then an officer, on a Drex decision, runs him down. His stock goes to zero." },
  { id: "ota-capture", kind: "clip", tail: true, ...part("capture", 0.62, 0.84), len: 6, tag: "COMPETE FOR THE BOUNTY", caption: "The hunter chases the next fugitive, stuns him and makes the capture: the ticker goes to exactly V$0 and the bounty is paid.",
    say: "The hunter goes after the next one, stuns him, and takes the bounty. The longer he survived, the bigger it is." },
  { id: "ota-carheli", kind: "clip", tail: true, ...carHeli, len: 5, caption: "Still Open Theft Auto (MIT-licensed, open source): steal a car or take the helicopter.",
    say: "And it is still Open Theft Auto. Steal a car, or take the helicopter." },
  has("insPolice", "insPoliceend") && { id: "ndi", cam: "C", crop: "inspector", t0: T("insPolice"), t1: T("insPoliceend"), len: 5, tag: "NDI", caption: "Police decisions cite facts from a real document.", src: "SFPD General Order 5.25 Foot Pursuits, parsed by Nace NDI",
    say: "Police decisions cite a real San Francisco police order, parsed with N D I." },
  has("proof", "proofend") && { id: "experiment", cam: "C", tail: true, crop: "table", t0: T("proof") + 3000, t1: T("proofend"), len: 9, tag: "EVERY CALL, COUNTED", caption: countsCap + " Then one seed, three ways: scripted, Meta LLM, Drex (sped up).", src: "numbers read from this recording's own run",
    say: `${countsSay} Then the same seed runs three ways: scripted, a language model, and Drex.` },
  has("verify") && { id: "replay", kind: "still", png: `${TAKE}/verify.png`, len: 3, caption: `The event log replayed with no model calls matches live state (ok=${M.verify.ok}, ${M.verify.events} events, ${M.verify.mismatches} mismatches).`,
    say: "And the log replays with zero model calls." },
  { id: "end", kind: "card", bg: C0 + 14500, len: 4, png: card(["Every agent is out for its own profit. They team up, and they turn on each other.", "The longer one survives, the more it is worth, and the bigger the bounty on its head.", `Recorded from real runs · ${TESTS ? TESTS + " automated tests passing · " : ""}built on Open Theft Auto (MIT)`], "card-end"),
    say: "Every agent is out for its own profit. They team up, and they turn on each other. The longer one survives, the more it is worth, and the bigger the bounty on its head. Vice Exchange." },
];
const npcs = [
  { id: "ota-city", kind: "clip", ...part("ryder", 0.2, 0.3), len: 8, caption: "Nine agents in the modified Open Theft Auto city: witnesses, informants, traders, police and the fugitive Ryder.",
    say: "Nine agents share this city: two witnesses, two informants, two traders, two police, and Ryder, a fugitive. Every one of them decides with Drex, and none can see what the others know." },
  has("city", "ryder1") && { id: "terminal", cam: "C", game: 18, t0: C0, t1: tCapR ? tCapR - 300 : T("ryder1"), len: 12, caption: `Several agents decide at once. ${who}`,
    say: "A witness reports a job. Police put out a bulletin. Then traders and officers each make their own call, shown live on the trading floor, with the options and the probabilities for every decision." },
  has("crew0") && { id: "crew", cam: "C", game: 39, t0: T("crew0") + 2500, t1: Math.min(T("crew0") + 10500, tCapR ? tCapR - 300 : Infinity), len: 6, caption: "Two human hunters form a crew; the trader agents see it and each decides.",
    say: "Two human hunters team up as a crew. The traders see it, and each one decides what to do about it." },
  { id: "ota-hunt", kind: "clip", tail: true, ...part("hunt", 0.42, 0.62), len: 8, caption: "The fugitive agent stuns a human hunter; an officer captures him on a Drex decision.",
    say: "The fugitive turns on a hunter and stuns him. Then an officer runs him down, and his stock goes to zero." },
  { id: "ota-capture", kind: "clip", tail: true, ...part("capture", 0.62, 0.84), len: 8, caption: "The hunter stuns the next fugitive and takes the bounty.",
    say: "The hunter goes after the next one, stuns him, and takes the bounty. Every agent is out for its own profit." },
];
const which = process.env.ONLY;
if (which === "cards") { console.log("cards rendered:", trackPng, buildPng, gamePng, "voice:", VOICE); }
else if (which === "voice") { const o = `${WORK}/voice-test.wav`; speak(main[1].say, o); console.log("voice:", VOICE, "test line", dur(o).toFixed(1), "s ->", o); }
else if (which === "opening") { build("opening", main.filter(Boolean).slice(0, 5)); }
else {
  if (!which || which === "main") build("vice-exchange-demo", main);
  if (!which || which === "npcs") build("vice-exchange-npcs", npcs);
  console.log("voice:", VOICE, "clip:", CLIP, "shots file:", Object.keys(SHOTS.shots ?? {}).join(",") || "none");
}
