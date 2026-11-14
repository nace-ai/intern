// ECONOMY: deterministic pricing, house-counterparty trading, listing halt and bounty.
// Pure state mutation: no I/O, no events, integers only. Every number comes from RULES.
import { RULES, clamp } from "@vice/contracts";
import type { EntityId, ListingView, PriceReason, WalletView } from "@vice/contracts";
import type {
  CharacterState, EconomyPort, ListingState, OrderRequest, OrderResult, PriceChange, Quote, RoomState, WalletState,
} from "../domain";

const P = RULES.price;
const T = RULES.trade;

function assertCredits(n: number, what: string): void {
  if (!Number.isSafeInteger(n)) throw new Error(`${what} must be a safe integer, got ${n}`);
}

/** Whole seconds the character has been alive (frozen at capture). */
export function survivalSeconds(character: CharacterState, nowMs: number): number {
  const end = character.captureTimeMs ?? nowMs;
  return Math.floor(Math.max(0, end - character.spawnTimeMs) / 1000);
}

/** Reference value: grows with notoriety, missions and survival time. Never depends on heat. */
export function referenceValue(character: CharacterState, nowMs: number): number {
  const steps = Math.floor(survivalSeconds(character, nowMs) / P.survivalStepSeconds);
  return P.base + P.perNotorietyPoint * character.notorietyPoints + P.perMission * character.completedMissions + P.perSurvivalStep * steps;
}

export function survivalFactor(character: CharacterState): number {
  const f = P.survivalFactor;
  return clamp(f.base - f.perHeat * character.heat + f.perEscape * character.successfulEscapes, f.min, f.max);
}

export function sentimentFactor(netBuyUnits: number): number {
  const s = P.sentiment;
  return clamp(1 + s.perNetUnit * netBuyUnits, s.min, s.max);
}

/** Net signed units in the sentiment window; with `exceptWalletId`, as if that wallet's own orders had never happened. */
function netUnits(listing: ListingState, nowMs: number, exceptWalletId?: string): number {
  const cutoff = nowMs - P.sentiment.windowMs;
  let net = 0;
  for (const d of listing.demand) if (d.atMs > cutoff && (exceptWalletId === undefined || d.walletId !== exceptWalletId)) net += d.units;
  return net;
}

function pruneDemand(listing: ListingState, nowMs: number): void {
  const cutoff = nowMs - P.sentiment.windowMs;
  if (listing.demand.some((d) => d.atMs <= cutoff)) listing.demand = listing.demand.filter((d) => d.atMs > cutoff);
}

/** Non-mutating quote. */
function compute(state: RoomState, listing: ListingState, nowMs: number, exceptWalletId?: string): Quote {
  const character = state.characters[listing.characterId];
  const net = netUnits(listing, nowMs, exceptWalletId);
  if (listing.status === "HALTED_CAPTURED" || !character) {
    return {
      referenceValue: listing.referenceValue, spotPrice: 0, bid: 0, ask: 0,
      survivalFactor: character ? survivalFactor(character) : 0, sentimentFactor: sentimentFactor(net),
      netBuyUnits: net, survivalSeconds: character ? survivalSeconds(character, nowMs) : 0,
    };
  }
  const reference = referenceValue(character, nowMs);
  const sf = survivalFactor(character);
  const sent = sentimentFactor(net);
  const spot = Math.max(P.minActiveSpot, Math.round(reference * sf * sent));
  return {
    referenceValue: reference, spotPrice: spot, ask: spot,
    bid: Math.max(P.minActiveSpot, Math.floor(spot * (1 - P.houseSpread))),
    survivalFactor: sf, sentimentFactor: sent, netBuyUnits: net, survivalSeconds: survivalSeconds(character, nowMs),
  };
}

function pushHistory(listing: ListingState, t: number, spot: number, reference: number): void {
  listing.history.push({ t, spot, reference });
  if (listing.history.length > P.historyMaxPoints) listing.history.splice(0, listing.history.length - P.historyMaxPoints);
}

const MAX_TRADES_KEPT = 200;

export function createEconomy(): EconomyPort {
  function reprice(state: RoomState, ticker: string, nowMs: number, reason: PriceReason): PriceChange | null {
    const listing = state.listings[ticker];
    if (!listing || listing.status !== "ACTIVE") return null;
    pruneDemand(listing, nowMs);
    const q = compute(state, listing, nowMs);
    if (q.spotPrice === listing.spotPrice && q.referenceValue === listing.referenceValue) return null;
    const previousSpotPrice = listing.spotPrice;
    listing.spotPrice = q.spotPrice;
    listing.referenceValue = q.referenceValue;
    pushHistory(listing, nowMs, q.spotPrice, q.referenceValue);
    return {
      ticker, referenceValue: q.referenceValue, spotPrice: q.spotPrice, previousSpotPrice, reason,
      heat: state.characters[listing.characterId]?.heat ?? 0, netBuyUnits: q.netBuyUnits,
    };
  }

  function bountyFor(state: RoomState, characterId: EntityId, nowMs: number): number {
    const character = state.characters[characterId];
    const listing = character?.ticker ? state.listings[character.ticker] : undefined;
    if (!character || !listing) return RULES.bounty.min;
    const reference = listing.status === "ACTIVE" ? referenceValue(character, nowMs) : listing.referenceValue;
    return Math.max(RULES.bounty.min, Math.round(reference * RULES.bounty.fractionOfReference));
  }

  return {
    openWallet(state, w): WalletState {
      const existing = state.wallets[w.id];
      if (existing) return existing;
      assertCredits(w.balance, "wallet balance");
      if (w.balance < 0) throw new Error("wallet balance cannot be negative");
      const wallet: WalletState = {
        id: w.id, ownerId: w.ownerId, ownerKind: w.ownerKind, ownerName: w.ownerName,
        balanceCredits: w.balance, holdings: {}, lastOrderAtMs: {},
      };
      state.wallets[w.id] = wallet;
      return wallet;
    },

    openListing(state, character, nowMs): ListingState {
      if (!character.ticker) throw new Error(`character ${character.id} has no ticker and cannot be listed`);
      if (state.listings[character.ticker]) throw new Error(`ticker ${character.ticker} is already listed`);
      const listing: ListingState = {
        ticker: character.ticker, characterId: character.id, nickname: character.nickname, status: "ACTIVE",
        referenceValue: 0, spotPrice: 0, openedAtMs: nowMs, haltedAtMs: null, finalSpotBeforeHalt: null,
        history: [], demand: [], volume: 0,
      };
      state.listings[listing.ticker] = listing;
      // The character is normally already in state; fall back to the passed object for the opening quote.
      const reference = referenceValue(character, nowMs);
      const spot = Math.max(P.minActiveSpot, Math.round(reference * survivalFactor(character) * sentimentFactor(0)));
      listing.referenceValue = reference;
      listing.spotPrice = spot;
      pushHistory(listing, nowMs, spot, reference);
      return listing;
    },

    quote(state, ticker, nowMs): Quote | null {
      const listing = state.listings[ticker];
      if (!listing) return null;
      pruneDemand(listing, nowMs);
      return compute(state, listing, nowMs);
    },

    reprice,

    executeOrder(state: RoomState, order: OrderRequest, nowMs: number): OrderResult {
      // ---- validate everything; no mutation before this block ends ----
      const listing = state.listings[order.ticker];
      if (!listing) return { ok: false, reason: "UNKNOWN_TICKER", message: `No listing ${order.ticker}` };
      const wallet = state.wallets[order.walletId];
      if (!wallet || wallet.ownerId !== order.actorId) return { ok: false, reason: "ACTOR_NOT_ALLOWED", message: "Actor has no wallet for this order" };
      if (listing.status !== "ACTIVE") return { ok: false, reason: "LISTING_HALTED", message: `${order.ticker} is halted at V$0` };
      if (!Number.isInteger(order.qty) || order.qty !== T.qtyPerOrder) return { ok: false, reason: "INVALID_QTY", message: `Orders are ${T.qtyPerOrder} share each` };
      if (order.side !== "BUY" && order.side !== "SELL") return { ok: false, reason: "ACTOR_NOT_ALLOWED", message: "Unknown side" };
      const last = wallet.lastOrderAtMs[order.ticker];
      if (last !== undefined && nowMs - last < T.cooldownMs) return { ok: false, reason: "COOLDOWN", message: `One order per ${T.cooldownMs / 1000}s per ticker` };
      const q = compute(state, listing, nowMs);
      const held = wallet.holdings[order.ticker]?.shares ?? 0;
      // Anti-echo fill: a wallet never gets a better price than the market would quote without its own recent orders.
      // It still pays its own impact (the public quote), but it cannot buy into the dip its own sells made or sell into
      // the bump its own buys made. Without this a lone wallet could farm the house by trading against the expiry of
      // its own sentiment entries.
      const own = compute(state, listing, nowMs, wallet.id);
      const price = order.side === "BUY" ? Math.max(q.ask, own.ask) : Math.min(q.bid, own.bid);
      const total = price * order.qty;
      if (order.side === "BUY") {
        if (held + order.qty > T.maxSharesPerTicker) return { ok: false, reason: "POSITION_LIMIT", message: `Max ${T.maxSharesPerTicker} shares per ticker` };
        if (wallet.balanceCredits < total) return { ok: false, reason: "INSUFFICIENT_FUNDS", message: `Needs V$${total}` };
      } else if (held < order.qty) {
        return { ok: false, reason: "INSUFFICIENT_SHARES", message: "Not enough shares to sell" };
      }
      assertCredits(total, "order total");

      // ---- apply: credits and shares move together ----
      pruneDemand(listing, nowMs);
      const holding = wallet.holdings[order.ticker] ?? { shares: 0, costBasis: 0 };
      if (order.side === "BUY") {
        wallet.balanceCredits -= total;
        holding.shares += order.qty;
        holding.costBasis += total;
      } else {
        const released = Math.round((holding.costBasis * order.qty) / holding.shares);
        wallet.balanceCredits += total;
        holding.shares -= order.qty;
        holding.costBasis = holding.shares === 0 ? 0 : holding.costBasis - released;
      }
      if (holding.shares === 0) delete wallet.holdings[order.ticker];
      else wallet.holdings[order.ticker] = holding;
      wallet.lastOrderAtMs[order.ticker] = nowMs;
      listing.demand.push({ atMs: nowMs, units: order.side === "BUY" ? order.qty : -order.qty, walletId: wallet.id });
      listing.volume += order.qty;
      const trade = {
        tradeId: order.tradeId, atMs: nowMs, actorId: order.actorId, actorName: order.actorName, actorKind: order.actorKind,
        ticker: order.ticker, side: order.side, qty: order.qty, executionPrice: price,
      };
      state.trades.push(trade);
      if (state.trades.length > MAX_TRADES_KEPT) state.trades.splice(0, state.trades.length - MAX_TRADES_KEPT);
      const priceChange = reprice(state, order.ticker, nowMs, "TRADE");
      return { ok: true, trade, walletId: wallet.id, balanceAfter: wallet.balanceCredits, sharesAfter: holding.shares, priceChange };
    },

    haltListing(state, ticker, nowMs) {
      const listing = state.listings[ticker];
      if (!listing || listing.status !== "ACTIVE") return null;
      const q = compute(state, listing, nowMs);
      listing.status = "HALTED_CAPTURED";
      listing.finalSpotBeforeHalt = q.spotPrice;
      listing.referenceValue = q.referenceValue;
      listing.spotPrice = 0;
      listing.haltedAtMs = nowMs;
      pushHistory(listing, nowMs, 0, q.referenceValue);
      return { finalSpotBeforeHalt: q.spotPrice, referenceAtHalt: q.referenceValue };
    },

    bountyFor,

    credit(state, walletId, amount): number {
      const wallet = state.wallets[walletId];
      if (!wallet) throw new Error(`Unknown wallet ${walletId}`);
      assertCredits(amount, "credit amount");
      if (wallet.balanceCredits + amount < 0) throw new Error("credit would make the wallet negative");
      wallet.balanceCredits += amount;
      return wallet.balanceCredits;
    },

    walletView(state, walletId): WalletView | null {
      const wallet = state.wallets[walletId];
      if (!wallet) return null;
      const holdings = Object.entries(wallet.holdings).map(([ticker, h]) => {
        const listing = state.listings[ticker];
        const spot = listing && listing.status === "ACTIVE" ? listing.spotPrice : 0;
        return { ticker, shares: h.shares, costBasis: h.costBasis, marketValue: h.shares * spot };
      });
      return {
        walletId: wallet.id, ownerId: wallet.ownerId, balanceCredits: wallet.balanceCredits, holdings,
        netWorth: wallet.balanceCredits + holdings.reduce((s, h) => s + h.marketValue, 0),
      };
    },

    listingView(state, ticker, nowMs): ListingView | null {
      const listing = state.listings[ticker];
      if (!listing) return null;
      const q = compute(state, listing, nowMs);
      return {
        ticker: listing.ticker, characterId: listing.characterId, nickname: listing.nickname, status: listing.status,
        referenceValue: q.referenceValue, spotPrice: q.spotPrice, bid: q.bid, ask: q.ask,
        history: listing.history.slice(), volume: listing.volume, netBuyUnits: q.netBuyUnits,
        openedAtMs: listing.openedAtMs, haltedAtMs: listing.haltedAtMs, finalSpotBeforeHalt: listing.finalSpotBeforeHalt,
        currentBounty: bountyFor(state, listing.characterId, nowMs),
      };
    },
  };
}
