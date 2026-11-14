// Offline replay: rebuild wallets, listings and character outcomes from the event log alone.
// No model calls, no clock, no engine. AT-08 / FR-063.
import type { CharacterStatus, GameEvent, ListingStatus } from "@vice/contracts";
import type { RoomState } from "../domain";

export interface ProjectedWallet { walletId: string; ownerId: string; ownerKind: string; balance: number; holdings: Record<string, number> }
export interface ProjectedListing { ticker: string; characterId: string; status: ListingStatus; spotPrice: number; referenceValue: number; finalSpotBeforeHalt: number | null; trades: number }
export interface ProjectedCharacter { characterId: string; playerId: string; ticker: string | null; status: CharacterStatus; capturedBy: string | null; bounty: number | null }
export interface Projection {
  runId: string | null; eventCount: number; lastSequence: number;
  wallets: Record<string, ProjectedWallet>;
  listings: Record<string, ProjectedListing>;
  characters: Record<string, ProjectedCharacter>;
  captures: number; trades: number;
}

const is = <T extends GameEvent["type"]>(e: GameEvent, type: T): e is GameEvent<T> => e.type === type;

/** Fold an ordered event log into final economic state. A ROOM_RESET starts a fresh run. */
export function projectEvents(events: readonly GameEvent[]): Projection {
  const ordered = [...events].sort((a, b) => a.serverSequence - b.serverSequence);
  let p: Projection = { runId: null, eventCount: 0, lastSequence: 0, wallets: {}, listings: {}, characters: {}, captures: 0, trades: 0 };
  for (const e of ordered) {
    if (is(e, "ROOM_RESET")) p = { runId: e.payload.newRunId, eventCount: 0, lastSequence: 0, wallets: {}, listings: {}, characters: {}, captures: 0, trades: 0 };
    p.runId = e.runId; p.eventCount++; p.lastSequence = e.serverSequence;
    if (is(e, "WALLET_OPENED")) {
      const w = e.payload;
      p.wallets[w.walletId] ??= { walletId: w.walletId, ownerId: w.ownerId, ownerKind: w.ownerKind, balance: w.balance, holdings: {} };
    } else if (is(e, "CHARACTER_SPAWNED")) {
      const c = e.payload;
      p.characters[c.characterId] = { characterId: c.characterId, playerId: c.playerId, ticker: c.ticker, status: "ACTIVE", capturedBy: null, bounty: null };
    } else if (is(e, "LISTING_OPENED")) {
      const l = e.payload;
      p.listings[l.ticker] = { ticker: l.ticker, characterId: l.characterId, status: "ACTIVE", spotPrice: l.spotPrice, referenceValue: l.referenceValue, finalSpotBeforeHalt: null, trades: 0 };
    } else if (is(e, "MARKET_PRICE_UPDATED")) {
      const l = p.listings[e.payload.ticker];
      // A halted listing stays at exactly 0 whatever arrives later.
      if (l && l.status === "ACTIVE") { l.spotPrice = e.payload.spotPrice; l.referenceValue = e.payload.referenceValue; }
    } else if (is(e, "TRADE_EXECUTED")) {
      const t = e.payload;
      const w = p.wallets[t.walletId];
      if (w) { w.balance = t.balanceAfter; if (t.sharesAfter > 0) w.holdings[t.ticker] = t.sharesAfter; else delete w.holdings[t.ticker]; }
      const l = p.listings[t.ticker]; if (l) l.trades++;
      p.trades++;
    } else if (is(e, "WALLET_CREDITED")) {
      const w = p.wallets[e.payload.walletId]; if (w) w.balance = e.payload.balanceAfter;
    } else if (is(e, "WANTED_STATUS_CHANGED")) {
      const c = p.characters[e.payload.characterId];
      if (c && (c.status === "ACTIVE" || c.status === "WANTED")) c.status = e.payload.wanted ? "WANTED" : "ACTIVE";
    } else if (is(e, "CAPTURE_SUCCEEDED")) {
      const cap = e.payload;
      const c = p.characters[cap.characterId];
      if (c) { c.status = "CAPTURED"; c.capturedBy = cap.captorId; c.bounty = cap.bounty; }
      if (cap.shares?.length) {
        // Crew capture: every credited wallet is listed with its balance after the credit.
        for (const sh of cap.shares) { const w = p.wallets[sh.walletId]; if (w) w.balance = sh.balanceAfter; }
      } else if (cap.captorWalletId && cap.captorBalanceAfter !== null) {
        const w = p.wallets[cap.captorWalletId]; if (w) w.balance = cap.captorBalanceAfter;
      }
      p.captures++;
    } else if (is(e, "LISTING_HALTED")) {
      const l = p.listings[e.payload.ticker];
      if (l) { l.status = "HALTED_CAPTURED"; l.spotPrice = 0; l.finalSpotBeforeHalt = e.payload.finalSpotBeforeHalt; }
    } else if (is(e, "CHARACTER_ARCHIVED")) {
      const c = p.characters[e.payload.characterId]; if (c) c.status = "ARCHIVED";
    }
  }
  return p;
}

const ended = (s: CharacterStatus) => s === "CAPTURED" || s === "ARCHIVED";

/** Compare a replayed projection against the live authoritative state. */
export function verifyReplay(events: readonly GameEvent[], liveState: RoomState): { ok: boolean; mismatches: string[]; projection: Projection } {
  const projection = projectEvents(events.filter((e) => e.runId === liveState.runId));
  const m: string[] = [];

  for (const w of Object.values(liveState.wallets)) {
    const pw = projection.wallets[w.id];
    if (!pw) { m.push(`wallet ${w.id}: missing from replay`); continue; }
    if (pw.balance !== w.balanceCredits) m.push(`wallet ${w.id}: balance replay=${pw.balance} live=${w.balanceCredits}`);
    const tickers = new Set([...Object.keys(pw.holdings), ...Object.keys(w.holdings)]);
    for (const t of tickers) {
      const a = pw.holdings[t] ?? 0, b = w.holdings[t]?.shares ?? 0;
      if (a !== b) m.push(`wallet ${w.id}: ${t} shares replay=${a} live=${b}`);
    }
  }
  for (const id of Object.keys(projection.wallets)) if (!liveState.wallets[id]) m.push(`wallet ${id}: in replay but not live`);

  for (const l of Object.values(liveState.listings)) {
    const pl = projection.listings[l.ticker];
    if (!pl) { m.push(`listing ${l.ticker}: missing from replay`); continue; }
    if (pl.status !== l.status) m.push(`listing ${l.ticker}: status replay=${pl.status} live=${l.status}`);
    if (pl.spotPrice !== l.spotPrice) m.push(`listing ${l.ticker}: spot replay=${pl.spotPrice} live=${l.spotPrice}`);
    if (l.status === "ACTIVE" && pl.referenceValue !== l.referenceValue) m.push(`listing ${l.ticker}: reference replay=${pl.referenceValue} live=${l.referenceValue}`);
    if (l.status === "HALTED_CAPTURED" && l.spotPrice !== 0) m.push(`listing ${l.ticker}: halted but live spot is ${l.spotPrice}`);
  }
  for (const t of Object.keys(projection.listings)) if (!liveState.listings[t]) m.push(`listing ${t}: in replay but not live`);

  for (const c of Object.values(liveState.characters)) {
    const pc = projection.characters[c.id];
    if (!pc) { m.push(`character ${c.id}: missing from replay`); continue; }
    const same = pc.status === c.status || (ended(pc.status) && ended(c.status));
    if (!same) m.push(`character ${c.id}: status replay=${pc.status} live=${c.status}`);
    if (pc.ticker !== c.ticker) m.push(`character ${c.id}: ticker replay=${pc.ticker} live=${c.ticker}`);
  }
  return { ok: m.length === 0, mismatches: m, projection };
}
