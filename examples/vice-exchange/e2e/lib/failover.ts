// Step 11 (AT-07) and the socket-level integrity checks, run against a SECOND server instance whose
// NACE_API_KEY is deliberately invalid. Everything here is driven by scripted Socket.IO clients.
// Because every NPC decision on this instance falls back to the safe action (police keep patrolling), the
// world is predictable enough for the concurrency checks: two hunters capturing at the same instant, order
// rejections, and position validation.
import { MISSION_SPOTS, RULES, dist, hasLineOfSight } from "@vice/contracts";
import type { DecisionTrace, GameEvent, RoomSnapshot, Vec2 } from "@vice/contracts";
import { Api, Bot, EventLog, id, sleep } from "./api";
import { until, type Recorder } from "./harness";

export interface FailoverResult { traces: DecisionTrace[]; events: GameEvent[]; finalBadge: string | null; verifyOk: boolean | null }

export async function providerFailureScenario(serverBase: string, room: string, rec: Recorder): Promise<FailoverResult> {
  const S = "S11";
  const api = new Api(serverBase, room);
  const log = new EventLog(api, 150);
  log.start();
  const bots: Bot[] = [];
  const out: FailoverResult = { traces: [], events: [], finalBadge: null, verifyOk: null };
  const pose = (b: Bot, p: Vec2) => b.cmd({ type: "player.pose", x: p.x, z: p.z, requestId: id("pose") });
  const charOf = async (b: Bot) => (await api.snapshot()).characters.find((c) => c.id === b.join?.characterId);
  try {
    const health = await api.health();
    rec.rec(S, "S11.boot", "second instance boots with an invalid NACE_API_KEY and stays healthy", health.ok === true && health.drexConfigured === true,
      { ok: health.ok, drexConfigured: health.drexConfigured, mode: health.mode, badgeAtBoot: health.badge }, ["AT-07"]);

    // ---- an outlaw joins: the listing makes both traders decide, so Drex is really called with the bad key ----
    const O = await Bot.connect(serverBase, "VEX"); bots.push(O);
    const jo = await O.joinRoom({ roomCode: room, nickname: "VEX", role: "OUTLAW" });
    if (!jo.ok || !jo.result?.characterId) { rec.rec(S, "S11.join", "scripted outlaw joins the second instance", false, jo.error ?? "no character"); return out; }
    const oChar = jo.result.characterId, oTicker = jo.result.snapshot.characters.find((c) => c.id === oChar)?.ticker ?? "";
    const firstResolved = await until(() => (log.of("DECISION_RESOLVED").length >= 2 ? log.of("DECISION_RESOLVED") : null), 12000, 100);
    const dec = (firstResolved ?? log.of("DECISION_RESOLVED")).map((e) => ({ agent: e.payload.agentId, role: e.payload.role, source: e.payload.decisionSource, status: e.payload.status, action: e.payload.chosenAction, errorType: e.payload.errorType, confidence: e.payload.confidence, probabilities: e.payload.probabilities, requestId: e.payload.providerRequestId }));
    rec.rec(S, "S11.fallback-labeled", "trader decisions are labeled fallback, carry an error type, and have no confidence or probabilities",
      dec.length >= 2 && dec.every((d) => d.source === "fallback" && !!d.errorType && d.confidence === null && d.probabilities === null), dec, ["AT-07", "FR-024", "NFR-009"]);

    const snap1 = await api.snapshot();
    const ps = snap1.providerStatus;
    rec.rec(S, "S11.badge", "provider badge reports the fallback honestly (never DREX: LIVE)", ps.badge !== "DREX: LIVE" && /FALLBACK/.test(ps.badge),
      { badge: ps.badge, drexState: ps.drex.state, lastErrorType: ps.drex.lastErrorType, circuitOpen: ps.drex.circuitOpenUntilMs !== null }, ["AT-07"]);

    // ---- the room keeps running ----
    const a = await api.snapshot(); await sleep(2000); const b = await api.snapshot();
    const moved = a.npcs.filter((n) => n.role === "POLICE").map((n) => { const m = b.npcs.find((x) => x.id === n.id)!; return Math.round(dist(n, m) * 10) / 10; });
    const buy1 = await O.cmd<{ trade: { tradeId: string; executionPrice: number }; balanceAfter: number; sharesAfter: number }>({ type: "market.buy", ticker: oTicker, qty: 1, commandId: id("buy") });
    rec.rec(S, "S11.room-runs", "the room keeps simulating and settling trades while Drex is failing",
      b.tick - a.tick >= 15 && moved.every((m) => m >= 3) && buy1.ok === true,
      { ticksIn2s: b.tick - a.tick, policeMovedMetres: moved, tradeOk: buy1.ok, price: buy1.result?.trade.executionPrice, balanceAfter: buy1.result?.balanceAfter }, ["AT-07", "FR-007"]);

    // ---- mission through the socket: exactly once (AT-01 at the protocol level) + a witness decision under failure ----
    const spot = MISSION_SPOTS[0]!;
    const mission = b.missions.find((m) => m.status === "AVAILABLE");
    const pm = await pose(O, { x: spot.x + 0.6, z: spot.z });
    const mid = id("mission");
    const m1 = mission ? await O.cmd({ type: "mission.interact", missionId: mission.id, commandId: mid }) : null;
    const m2 = mission ? await O.cmd({ type: "mission.interact", missionId: mission.id, commandId: mid }) : null;
    const m3 = mission ? await O.cmd({ type: "mission.interact", missionId: mission.id, commandId: id("mission") }) : null;
    await sleep(700); await log.pull();
    const completed = log.of("MISSION_COMPLETED").filter((e) => e.payload.characterId === oChar);
    rec.rec("S03", "S03.socket-idempotent", "socket level: the same mission command id is acknowledged as a duplicate and a new attempt is refused; one MISSION_COMPLETED",
      !!m1?.ok && m2?.duplicate === true && m3?.ok === false && completed.length === 1,
      { poseAck: pm.ok, first: m1?.ok, replay: { ok: m2?.ok, duplicate: m2?.duplicate }, secondAttempt: m3?.error?.code, missionCompletedEvents: completed.length, instance: "provider-failure server" }, ["AT-01", "NFR-007"]);

    // ---- supporting cast ----
    const Z = await Bot.connect(serverBase, "ZED"); bots.push(Z);
    const jz = await Z.joinRoom({ roomCode: room, nickname: "ZED", role: "OUTLAW" });
    const zTicker = jz.result?.snapshot.characters.find((c) => c.id === jz.result?.characterId)?.ticker ?? "";
    const H1 = await Bot.connect(serverBase, "HUN1"); bots.push(H1);
    const H2 = await Bot.connect(serverBase, "HUN2"); bots.push(H2);
    const SP = await Bot.connect(serverBase, "SPEC"); bots.push(SP);
    const j1 = await H1.joinRoom({ roomCode: room, nickname: "HUN1", role: "HUNTER" });
    const j2 = await H2.joinRoom({ roomCode: room, nickname: "HUN2", role: "HUNTER" });
    await SP.joinRoom({ roomCode: room, nickname: "SPEC", role: "SPECTATOR" });
    rec.rec(S, "S11.tickers-unique", "each outlaw gets its own ticker; hunters get a wallet and no ticker", !!oTicker && !!zTicker && oTicker !== zTicker && j1.ok && j2.ok && j1.result?.wallet?.balanceCredits === 2000,
      { tickers: [oTicker, zTicker], hunterWallet: j1.result?.wallet?.balanceCredits, hunterTicker: j1.result?.snapshot.characters.find((c) => c.id === j1.result?.characterId)?.ticker ?? null }, ["FR-030"]);

    // ---- FR-003: impossible positions ----
    await sleep(1100); // let enough time pass since spawn for a legal pose
    const h2Char = await charOf(H2);
    const far = await pose(H2, { x: 500, z: 500 });
    const okPose = await pose(H2, { x: (h2Char?.x ?? 4) + 2, z: (h2Char?.z ?? 28) });
    const tooFast = await pose(H2, { x: (h2Char?.x ?? 4) + 80, z: 28 });
    const badDir = await H2.cmd({ type: "player.move", dirX: 50, dirZ: 0 });
    rec.rec("S02", "S02.socket-teleport", "socket level: a position outside the map, a teleport and an out-of-range move vector are all rejected",
      far.ok === false && okPose.ok === true && tooFast.ok === false && badDir.ok === false,
      { outsideMap: far.error, legalStep: okPose.ok, teleport80m: tooFast.error, moveVector50: badDir.error?.code }, ["FR-003"]);
    await sleep(900);
    const inside = { x: 17, z: 15 }; // middle of "Precinct 9" (x 8..26, z 8..22)
    const intoBuilding = await pose(H2, inside);
    const h2After = await charOf(H2);
    const standsInside = !!h2After && Math.abs(h2After.x - inside.x) < 0.01 && Math.abs(h2After.z - inside.z) < 0.01;
    rec.rec("SX", "SX.pose-into-building", "socket level: a reported pose (player.pose) in the middle of a building is rejected, so the server position stays outside buildings",
      intoBuilding.ok === false && !standsInside,
      { command: "player.pose", target: inside, building: "Precinct 9 (x 8..26, z 8..22)", ack: intoBuilding.ok ? "accepted" : intoBuilding.error, serverPositionAfter: h2After ? { x: h2After.x, z: h2After.z } : null }, ["FR-003"]);

    // ---- FR-035: every rejection has a reason and mutates nothing ----
    const bal0 = H1.wallet?.balanceCredits ?? null;
    const sellNone = await H1.cmd({ type: "market.sell", ticker: oTicker, qty: 1, commandId: id("sell") });
    const unknown = await H1.cmd({ type: "market.buy", ticker: "NOPE-99", qty: 1, commandId: id("buy") });
    const spec = await SP.cmd({ type: "market.buy", ticker: oTicker, qty: 1, commandId: id("buy") });
    const qty2 = await H1.cmd({ type: "market.buy", ticker: oTicker, qty: 2, commandId: id("buy") });
    await sleep(300); await log.pull();
    const rej = log.of("TRADE_REJECTED").filter((e) => e.payload.actorId === j1.result?.playerId).map((e) => e.payload.reason);
    rec.rec("S06", "S06.sell-no-shares", "socket level: a sell with no shares is rejected with reason INSUFFICIENT_SHARES and the wallet is untouched",
      sellNone.ok === false && sellNone.error?.reason === "INSUFFICIENT_SHARES" && H1.wallet?.balanceCredits === bal0,
      { ack: sellNone.error, walletBefore: bal0, walletAfter: H1.wallet?.balanceCredits, rejectionEvents: rej, instance: "provider-failure server" }, ["AT-04", "FR-035"]);
    rec.rec("S06", "S06.other-rejections", "socket level: unknown ticker, spectator order and quantity 2 are rejected",
      unknown.ok === false && unknown.error?.reason === "UNKNOWN_TICKER" && spec.ok === false && qty2.ok === false,
      { unknownTicker: unknown.error?.reason, spectator: spec.error?.code, qty2: qty2.error?.code }, ["FR-035"]);

    // Drain a wallet: alternate two tickers (each has its own 2 s cooldown) until the house says no.
    const fills: Array<{ t: string; price: number; bal: number }> = [];
    let broke: { ticker: string; reason: string | undefined; message: string | undefined; balance: number | null } | null = null;
    let cooldownSeen: string | undefined;
    let dupOk: boolean | null = null;
    for (let round = 0; round < 14 && !broke; round++) {
      for (const t of [oTicker, zTicker]) {
        const cid = id("buy");
        const r = await H1.cmd<{ trade: { executionPrice: number }; balanceAfter: number }>({ type: "market.buy", ticker: t, qty: 1, commandId: cid });
        if (r.ok && r.result) {
          fills.push({ t, price: r.result.trade.executionPrice, bal: r.result.balanceAfter });
          if (round === 0 && t === oTicker) {
            const again = await H1.cmd({ type: "market.buy", ticker: t, qty: 1, commandId: cid });            // same id: must not execute twice
            const quick = await H1.cmd({ type: "market.buy", ticker: t, qty: 1, commandId: id("buy") });     // new id inside the cooldown
            dupOk = again.duplicate === true; cooldownSeen = quick.error?.reason;
          }
        } else if (r.error?.reason === "INSUFFICIENT_FUNDS") { broke = { ticker: t, reason: r.error.reason, message: r.error.message, balance: H1.wallet?.balanceCredits ?? null }; break; }
        else if (r.error?.reason === "POSITION_LIMIT" || r.error?.reason === "COOLDOWN") { /* keep going with the other ticker */ }
        else { broke = { ticker: t, reason: r.error?.reason ?? r.error?.code, message: r.error?.message, balance: H1.wallet?.balanceCredits ?? null }; break; }
      }
      if (!broke) await sleep(RULES.trade.cooldownMs + 80);
    }
    await sleep(300); await log.pull();
    const lastBal = fills.length ? fills[fills.length - 1]!.bal : 2000;
    const execByH1 = log.of("TRADE_EXECUTED").filter((e) => e.payload.actorId === j1.result?.playerId).length;
    rec.rec("S06", "S06.insufficient-funds", "socket level: a buy the wallet cannot afford is rejected with reason INSUFFICIENT_FUNDS; balance never goes negative and is unchanged by the rejection",
      broke?.reason === "INSUFFICIENT_FUNDS" && (broke.balance ?? -1) === lastBal && lastBal >= 0,
      { fills: fills.length, lastFill: fills[fills.length - 1], rejected: broke, balanceAfterRejection: broke?.balance, instance: "provider-failure server" }, ["AT-04", "FR-035"]);
    rec.rec("S06", "S06.socket-dup-cooldown", "socket level: replayed command id is a duplicate, a second order inside 2 s is rejected with COOLDOWN, one settlement per accepted order",
      dupOk === true && cooldownSeen === "COOLDOWN" && execByH1 === fills.length,
      { duplicateAck: dupOk, quickOrderReason: cooldownSeen, acceptedOrders: fills.length, tradeExecutedEvents: execByH1 }, ["AT-04", "FR-035", "NFR-007"]);

    // ---- AT-05: two hunters, one wanted outlaw, the same instant ----
    const stage: Vec2 = { x: 59, z: 31 };
    await pose(O, stage);
    let oc = await charOf(O);
    if (!oc || dist(oc, stage) > 1) { await sleep(1200); await pose(O, stage); oc = await charOf(O); }
    const p1 = await pose(H1, { x: stage.x + 0.9, z: stage.z + 0.6 });
    await sleep(600);
    const p2 = await pose(H2, { x: stage.x - 0.9, z: stage.z + 0.6 });
    const pos = async () => { const s = await api.snapshot(); const g = (b: Bot) => s.characters.find((c) => c.id === b.join?.characterId); return { s, o: g(O), h1: g(H1), h2: g(H2) }; };
    let at = await pos();
    if (at.h2 && at.o && dist(at.h2, at.o) > RULES.captureRadius) { await sleep(1500); await pose(H2, { x: stage.x - 0.9, z: stage.z + 0.6 }); at = await pos(); }
    const inRange = !!at.o && !!at.h1 && !!at.h2 && dist(at.h1, at.o) <= RULES.captureRadius && dist(at.h2, at.o) <= RULES.captureRadius;
    if (!inRange) {
      rec.rec("S07", "S07.socket-concurrent", "socket level: two hunters capture the same wanted outlaw at the same instant", "blocked",
        { reason: "could not place both scripted hunters within 2.5 m of the outlaw", poseAcks: [p1.error ?? p1.ok, p2.error ?? p2.ok], o: at.o && { x: at.o.x, z: at.o.z }, h1: at.h1 && { x: at.h1.x, z: at.h1.z }, h2: at.h2 && { x: at.h2.x, z: at.h2.z } }, ["AT-05"]);
    } else {
      let wanted: boolean | null = at.o?.status === "WANTED" ? true : null;
      let seen: { cop: string; d: number } | null = null;
      let inc: Awaited<ReturnType<Bot["cmd"]>> | null = null;
      if (!wanted) {
        // Not wanted yet: an attempt must be refused.
        const early = await H1.cmd({ type: "capture.attempt", targetCharacterId: oChar, commandId: id("cap") });
        rec.rec("S07", "S07.socket-not-wanted", "socket level: capturing a character that is not wanted is rejected", early.ok === false && early.error?.reason === "TARGET_NOT_WANTED", early.error ?? "accepted", ["FR-051"]);
        // A police NPC that sees an incident publishes a bulletin (+30 heat) with no model involved.
        seen = await until(async () => {
          const s: RoomSnapshot = await api.snapshot(); const o = s.characters.find((c) => c.id === oChar);
          const cop = o && s.npcs.find((n) => n.role === "POLICE" && dist(n, o) <= 11.5 && hasLineOfSight(n, o));
          return cop ? { cop: cop.id, d: Math.round(dist(cop, o!) * 10) / 10 } : null;
        }, 50000, 120);
        inc = seen ? await O.cmd({ type: "demo.incident", incidentType: "SIGHTING", commandId: id("inc") }) : null;
        wanted = inc?.ok ? await until(async () => ((await charOf(O))?.status === "WANTED" ? true : null), 4000, 60) : null;
      }
      if (!wanted) {
        rec.rec("S07", "S07.socket-concurrent", "socket level: two hunters capture the same wanted outlaw at the same instant", "blocked",
          { reason: "the scripted outlaw did not become wanted", policeInSight: seen, incidentAck: inc?.ok ? inc.result : inc?.error }, ["AT-05"]);
      } else {
        await sleep(1600); // captor cooldown from the early attempt
        const w1 = H1.wallet?.balanceCredits ?? null, w2 = H2.wallet?.balanceCredits ?? null;
        const [c1, c2] = await Promise.all([
          H1.cmd<{ bounty: number }>({ type: "capture.attempt", targetCharacterId: oChar, commandId: id("cap") }),
          H2.cmd<{ bounty: number }>({ type: "capture.attempt", targetCharacterId: oChar, commandId: id("cap") }),
        ]);
        await sleep(500); await log.pull();
        const ok = [c1, c2].filter((c) => c.ok).length;
        const caps = log.of("CAPTURE_SUCCEEDED").filter((e) => e.payload.characterId === oChar);
        const rejs = log.of("CAPTURE_REJECTED").filter((e) => e.payload.targetCharacterId === oChar && e.payload.reason !== "TARGET_NOT_WANTED");
        const bounty = caps[0]?.payload.bounty ?? 0;
        const gain1 = (H1.wallet?.balanceCredits ?? 0) - (w1 ?? 0), gain2 = (H2.wallet?.balanceCredits ?? 0) - (w2 ?? 0);
        const snapC = await api.snapshot();
        const listing = snapC.listings.find((l) => l.ticker === oTicker);
        rec.rec("S07", "S07.socket-concurrent", "socket level: two hunters fire capture.attempt at the same instant; exactly one succeeds and the bounty is paid once",
          ok === 1 && caps.length === 1 && bounty > 0 && gain1 + gain2 === bounty && (gain1 === 0 || gain2 === 0),
          { acks: [c1.ok ? { ok: true, bounty: c1.result?.bounty } : c1.error, c2.ok ? { ok: true, bounty: c2.result?.bounty } : c2.error], captureEvents: caps.map((e) => e.eventId), captor: caps[0]?.payload.captorName, bounty, walletGain: { HUN1: gain1, HUN2: gain2 }, rejectedReasons: rejs.map((e) => e.payload.reason), instance: "provider-failure server" }, ["AT-05", "FR-052", "FR-055"]);
        rec.rec("S07", "S07.socket-halted", "socket level: after the capture the listing is HALTED_CAPTURED at exactly 0",
          listing?.status === "HALTED_CAPTURED" && listing.spotPrice === 0 && listing.bid === 0 && listing.ask === 0,
          { ticker: oTicker, status: listing?.status, spot: listing?.spotPrice, bid: listing?.bid, ask: listing?.ask, finalSpotBeforeHalt: listing?.finalSpotBeforeHalt }, ["AT-05", "FR-033"]);
        await sleep(RULES.trade.cooldownMs + 100);
        const after = await H2.cmd({ type: "market.buy", ticker: oTicker, qty: 1, commandId: id("buy") });
        const sellAfter = await H1.cmd({ type: "market.sell", ticker: oTicker, qty: 1, commandId: id("sell") });
        const mAfter = (await api.snapshot()).missions.find((m) => m.status === "AVAILABLE");
        const mis = mAfter ? await O.cmd({ type: "mission.interact", missionId: mAfter.id, commandId: id("mission") }) : null;
        rec.rec("S07", "S07.socket-post-capture", "socket level: buy and sell on the halted ticker are rejected with LISTING_HALTED and the captured character cannot run a mission",
          after.ok === false && after.error?.reason === "LISTING_HALTED" && sellAfter.ok === false && sellAfter.error?.reason === "LISTING_HALTED" && (mis ? mis.ok === false : true),
          { buy: after.error, sell: sellAfter.error, mission: mis ? mis.error?.code : "no mission available to try" }, ["AT-05", "FR-033", "FR-035"]);
      }
    }

    // ---- final honesty sweep over every decision this instance made ----
    await sleep(600); await log.pull();
    out.traces = await api.allTraces();
    const resolved = out.traces.filter((t) => t.decisionSource !== null);
    const bad = resolved.filter((t) => t.decisionSource !== "fallback" || !t.errorType || t.confidence !== null || t.probabilities !== null);
    const byErr: Record<string, number> = {};
    for (const t of resolved) byErr[t.errorType ?? "none"] = (byErr[t.errorType ?? "none"] ?? 0) + 1;
    const byRole: Record<string, number> = {};
    for (const t of resolved) byRole[t.role] = (byRole[t.role] ?? 0) + 1;
    rec.rec(S, "S11.all-traces", "every decision trace on the failing instance is fallback with an error type and null confidence; none is labeled drex",
      resolved.length >= 2 && bad.length === 0,
      { decisions: resolved.length, byErrorType: byErr, byRole, actions: [...new Set(resolved.map((t) => `${t.role}:${t.chosenAction}`))], offending: bad.slice(0, 3).map((t) => ({ id: t.id, source: t.decisionSource, errorType: t.errorType, confidence: t.confidence })) }, ["AT-07", "FR-024"]);
    const v = await api.verify();
    out.verifyOk = v.ok;
    rec.rec("S10", "S10.verify-failover", "replay verify is consistent on the provider-failure instance as well", v.ok && v.mismatches.length === 0, { ok: v.ok, source: v.source, events: v.eventCount, mismatches: v.mismatches.slice(0, 5) }, ["AT-08"]);
    out.finalBadge = (await api.snapshot()).providerStatus.badge;
  } catch (e) {
    rec.rec(S, "S11.error", "provider-failure scenario ran to the end", false, `harness error: ${(e as Error).stack?.slice(0, 600) ?? String(e)}`);
  } finally {
    await log.close().catch(() => {});
    out.events = log.all;
    for (const b of bots) b.close();
  }
  return out;
}
