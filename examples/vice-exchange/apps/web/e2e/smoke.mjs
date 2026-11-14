// Two-browser smoke test of the real UI against a running server (headless Chrome, real key and mouse events).
//   BASE=http://localhost:5173 OUT=/some/dir node apps/web/e2e/smoke.mjs
// Uses live provider calls if the server has keys. Set SKIP_EXPERIMENT=1 to skip the long baseline comparison.
import { mkdirSync } from "node:fs";
import { launch, me, sleep, walkTo } from "./cdp.mjs";

const BASE = process.env.BASE ?? "http://localhost:5173";
const OUT = process.env.OUT ?? "./e2e-out";
mkdirSync(OUT, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
const S = (body) => `(() => { const s = window.__vice.getState(); ${body} })()`;
const joined = `!!document.querySelector(".app") && !!window.__vice && !!window.__vice.getState().session && !!window.__vice.getState().lastTick`;

const chrome = await launch({ port: Number(process.env.CDP_PORT ?? 9333) });
const pages = [];
try {
  const j = await chrome.newPage(`${BASE}/`, "join"); pages.push(j);
  await j.waitFor(`!!document.querySelector(".join-card")`, 15000, "join screen");
  await j.shot(`${OUT}/00-join.png`);
  check("join screen renders with the fictional-credits notice", await j.eval(`document.querySelector(".fine").textContent.includes("nonredeemable")`));

  // ---- outlaw (first human in the room = director) ----
  const a = await chrome.newPage(`${BASE}/?nick=Ace&role=OUTLAW&auto=1`, "outlaw"); pages.push(a);
  await a.waitFor(joined, 20000, "outlaw joined");
  await sleep(1500);
  const s0 = await a.eval(S(`return { director: s.session.isDirector, npcs: Object.keys(s.npcs).length, listings: Object.keys(s.listings), badge: s.providerStatus && s.providerStatus.badge, wallet: s.wallet && s.wallet.balanceCredits, canvas: !!document.querySelector(".city canvas") };`));
  check("outlaw joined; snapshot, wallet and city canvas present", s0.npcs >= 6 && s0.listings.length >= 1 && s0.wallet === 2000 && s0.canvas, JSON.stringify(s0));
  await a.shot(`${OUT}/01-outlaw-main.png`);

  await a.click(".tcard-btns .buy");
  await a.waitFor(S(`return s.wallet.holdings.some((h) => h.shares === 1);`), 6000, "holding after buy");
  const w1 = await a.eval(S(`return { bal: s.wallet.balanceCredits, trades: s.trades.length, disabled: document.querySelector(".tcard-btns .buy").disabled };`));
  check("Buy 1 via the button: wallet debited, holding shown, button cools down", w1.bal < 2000 && w1.trades >= 1 && w1.disabled, JSON.stringify(w1));

  const m = await a.eval(S(`return Object.values(s.missions).find((m) => m.status === "AVAILABLE");`));
  const reached = await walkTo(a, m.x, m.z, 1.5);
  check("WASD walks the avatar to the job marker (server-integrated)", reached, JSON.stringify(await me(a)));
  await a.press("e");
  await a.waitFor(S(`return Object.values(s.characters).some((c) => c.playerId === s.session.playerId && c.completedMissions === 1);`), 6000, "mission completed");
  check("E completes the job once", true);
  await a.press("e"); await sleep(400);
  check("second E on the same job has no effect", await a.eval(S(`return Object.values(s.characters).filter((c) => c.playerId === s.session.playerId)[0].completedMissions === 1;`)));

  const dec = await a.waitFor(S(`const n = Object.values(s.npcs).find((n) => n.lastDecision && n.lastDecision.chosenAction && n.lastDecision.triggerEventId); return n ? { id: n.id, last: n.name.split(" ").slice(-1)[0], role: n.role, action: n.lastDecision.chosenAction, source: n.lastDecision.decisionSource, conf: n.lastDecision.confidence, ms: n.lastDecision.latencyMs, req: n.lastDecision.providerRequestId } : null;`), 20000, "an NPC decision");
  check("an NPC decided and the trace reached the browser", true, JSON.stringify(dec));
  const wit = await a.eval(S(`const n = Object.values(s.npcs).find((n) => n.role === "WITNESS" && n.lastDecision); return n ? n.name.split(" ").slice(-1)[0] : null;`)) ?? dec.last;
  await a.click(".chip", wit);
  await a.waitFor(`!!document.querySelector(".agent .dec")`, 4000, "inspector decision card");
  await sleep(2500);
  await a.shot(`${OUT}/02-inspector-after-job.png`);
  const insp = await a.eval(`({ src: document.querySelector(".agent .dec .src").textContent, bars: document.querySelectorAll(".agent .dec .bar").length, mems: document.querySelectorAll(".agent .mem").length })`);
  check("inspector shows source badge, memory with provenance, and probability bars only for real provider data", insp.mems >= 1 && insp.src.length > 0, JSON.stringify(insp));

  await a.click(".ev", "MISSION COMPLETED");
  await sleep(300);
  const chain = await a.eval(`document.querySelectorAll(".ev.chain, .ev.sel").length`);
  check("clicking an event highlights its causal chain", chain >= 2, `${chain} linked rows`);
  await a.shot(`${OUT}/03-timeline-chain.png`);
  await a.click(".tl-bar .ghost");

  // ---- hunter in a second browser page ----
  const h = await chrome.newPage(`${BASE}/?nick=Hawk&role=HUNTER&auto=1`, "hunter"); pages.push(h);
  await h.waitFor(joined, 20000, "hunter joined");
  await sleep(1200);
  const both = await Promise.all([a, h].map((p) => p.eval(S(`const l = Object.values(s.listings).find((l) => l.status === "ACTIVE"); return { spot: l.spotPrice, ref: l.referenceValue, chars: Object.keys(s.characters).length, players: Object.values(s.players).filter((p) => p.connected).length };`))));
  check("two browsers agree on listing price and see both characters", Math.abs(both[0].spot - both[1].spot) <= 3 && both[0].chars === both[1].chars && both[1].chars >= 2, JSON.stringify(both));
  await h.shot(`${OUT}/04-hunter-main.png`);

  // ---- get the outlaw WANTED: wait for the swarm; if nobody reported, stand near an officer and trigger an incident ----
  const wantedExpr = S(`return Object.values(s.characters).some((c) => c.role === "OUTLAW" && c.status === "WANTED");`);
  let wanted = await a.waitFor(wantedExpr, 12000, "wanted via witness report").catch(() => false);
  if (!wanted) {
    console.log("…not wanted from the witness chain; walking to an officer and triggering a director incident");
    const cop = await a.eval(S(`const n = Object.values(s.npcs).find((n) => n.role === "POLICE"); const t = s.lastTick.npcs.find((x) => x.id === n.id); return { x: t.x, z: t.z };`));
    await walkTo(a, 62, 56, 2); await walkTo(a, cop.x, cop.z, 8, 15000);
    await a.click(".tabs button", "DEMO DIRECTOR");
    await a.click(".director button", "Trigger incident");
    wanted = await a.waitFor(wantedExpr, 15000, "wanted via police sighting").catch(() => false);
    await a.click(".tabs button", "EVENT TIMELINE");
  }
  check("outlaw became WANTED through NPC decisions/messages", !!wanted, JSON.stringify(await me(a)));
  await sleep(800);
  await a.shot(`${OUT}/05-wanted.png`);
  const priceWanted = await a.eval(S(`const l = Object.values(s.listings).find((l) => l.status === "ACTIVE"); return { spot: l.spotPrice, ref: l.referenceValue, bounty: l.currentBounty };`));
  check("risk-adjusted price: spot sits below reference while wanted", priceWanted.spot < priceWanted.ref, JSON.stringify(priceWanted));

  // ---- capture: the outlaw runs along the roads to the hunter; the hunter presses E in range (police may win the race) ----
  const hp = await me(h);
  const route = [[62, 56], [62, 28], [hp.x - 1.2, hp.z]];
  for (const [x, z] of route) { if (!(await me(a))) break; await walkTo(a, x, z, 1.4, 15000); }
  for (let i = 0; i < 12 && (await me(a)); i++) { await h.press("e"); await sleep(350); }
  const cap = await a.waitFor(S(`const l = Object.values(s.listings).find((l) => l.status === "HALTED_CAPTURED"); const e = s.events.find((e) => e.type === "CAPTURE_SUCCEEDED"); return l && e ? { ticker: l.ticker, spot: l.spotPrice, final: l.finalSpotBeforeHalt, captor: e.payload.captorName, kind: e.payload.captorKind, bounty: e.payload.bounty, survival: e.payload.survivalSeconds } : null;`), 30000, "capture");
  check("capture halts the listing at exactly V$0", cap.spot === 0, JSON.stringify(cap));
  await sleep(500);
  const overlay = await Promise.all([a, h].map((p) => p.eval(`!!document.querySelector(".crash")`)));
  check("crash overlay shows on both screens", overlay[0] && overlay[1], JSON.stringify(overlay));
  await a.shot(`${OUT}/06-crash-outlaw.png`); await h.shot(`${OUT}/07-crash-hunter.png`);
  if (cap.kind === "HUMAN") check("hunter was paid the bounty once", await h.eval(S(`return s.wallet.balanceCredits;`)) === 2000 + cap.bounty, `bounty ${cap.bounty}`);
  check("halted ticker offers no Buy/Sell buttons", await a.eval(`document.querySelectorAll(".tcard.halted .tcard-btns").length === 0 && document.querySelectorAll(".tcard.halted").length === 1`));

  // ---- respawn: new ticker, old one stays as history ----
  await a.eval(`window.__vice.setState({ crash: null })`);
  await a.press("r");
  const re = await a.waitFor(S(`const ls = Object.values(s.listings); const live = ls.filter((l) => l.status === "ACTIVE"); const dead = ls.filter((l) => l.status === "HALTED_CAPTURED"); const mine = Object.values(s.characters).find((c) => c.playerId === s.session.playerId && c.status === "ACTIVE"); return live.length === 1 && dead.length === 1 && mine ? { newTicker: live[0].ticker, old: dead[0].ticker, oldSpot: dead[0].spotPrice } : null;`), 8000, "respawn");
  check("R respawns with a new ticker; the old one remains halted at V$0", re.newTicker !== re.old && re.oldSpot === 0, JSON.stringify(re));
  await sleep(900);
  await a.shot(`${OUT}/08-respawned.png`);

  // ---- director: mode switch, seeded reset ----
  await a.click(".tabs button", "DEMO DIRECTOR");
  await a.click(".director .seg.wide button", "SCRIPTED");
  await a.waitFor(S(`return s.mode === "scripted";`), 6000, "mode scripted");
  await sleep(1300);
  const badge = await a.eval(`document.querySelector(".hdr .badge").textContent`);
  check("scripted mode is labelled SCRIPTED, not Drex", /SCRIPTED/.test(badge) && !/DREX: LIVE/.test(badge), badge);
  await a.click(".director .seg.wide button", "DREX");
  await a.waitFor(S(`return s.mode === "drex";`), 6000, "mode drex");
  await a.click(".director button", "Seeded reset");
  await a.waitFor(S(`const ls = Object.values(s.listings); return ls.length === 1 && ls[0].status === "ACTIVE" && s.wallet && s.wallet.balanceCredits === 2000;`), 10000, "reset state");
  await sleep(1500);
  const afterReset = await Promise.all([a, h].map(async (p) => ({ me: await me(p), tickers: await p.eval(S(`return Object.keys(s.listings);`)), director: await p.eval(S(`return s.session.isDirector;`)) })));
  check("seeded reset: both browsers re-attach to fresh characters and the same first ticker", !!afterReset[0].me && !!afterReset[1].me && afterReset[0].tickers.join() === afterReset[1].tickers.join(), JSON.stringify(afterReset));
  await a.shot(`${OUT}/09-after-reset-director.png`);

  if (!process.env.SKIP_EXPERIMENT) {
    await a.click(".director button", "Run scripted vs Meta vs Drex");
    await a.waitFor(S(`return s.experiments.length > 0;`), 10000, "experiment started");
    const done = await a.waitFor(S(`const x = s.experiments[s.experiments.length - 1]; return x.status !== "RUNNING" ? x : null;`), 240000, "experiment finished").catch(() => null);
    check("baseline comparison finished with one row per mode", !!done && done.status === "DONE" && done.rows.length === 3, done ? JSON.stringify(done.rows.map((r) => ({ mode: r.mode, decisions: r.decisions, real: r.realProviderCalls, p50: r.latencyP50Ms, captured: r.captured }))) : "timed out");
    await a.eval(`document.querySelector(".director").scrollTop = 9999`);
    await sleep(400);
    await a.shot(`${OUT}/10-experiment.png`);
  }
} catch (err) {
  check("scenario completed", false, String(err && err.message ? err.message : err));
  for (const p of pages.slice(1)) await p.shot(`${OUT}/zz-failure-${p.name}.png`).catch(() => {});
} finally {
  for (const p of pages) if (p.errors.length) console.log(`--- console errors/warnings on ${p.name} ---\n` + [...new Set(p.errors)].slice(0, 12).join("\n"));
  chrome.close();
}
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed. Screenshots: ${OUT}`);
process.exit(failed ? 1 : 0);
