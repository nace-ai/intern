# ECONOMY — status: DONE (22 tests passing, typecheck clean for economy/**)

Import: `import { createEconomy } from "./economy"` (apps/server/src/economy/index.ts) -> `EconomyPort` exactly as in domain.ts.
Stateless: all state lives in the RoomState you pass. Pure, integers only, no I/O, no events.
Also exported (pure helpers): `referenceValue(character, nowMs)`, `survivalFactor(character)`, `sentimentFactor(net)`, `survivalSeconds(character, nowMs)`.

## Behaviour ENGINE must know
- `openListing(state, character, now)`: character.ticker must be non-null (throws for hunters / duplicate ticker). It writes `state.listings[ticker]` itself and pushes the first history point. Put the character in `state.characters` too (quote/reprice look it up by `listing.characterId`).
- `openWallet`: writes `state.wallets[id]`; idempotent (returns the existing wallet if the id exists).
- `executeOrder`: on success it ALREADY pushes the TradeView onto `state.trades` (kept to the last 200), sets cooldown, records demand, bumps volume and reprices (`result.priceChange`, reason TRADE, may be null). Do NOT push the trade again. On reject nothing in state changes.
  Validation order: UNKNOWN_TICKER, ACTOR_NOT_ALLOWED (wallet missing or wallet.ownerId !== order.actorId), LISTING_HALTED, INVALID_QTY (must be exactly 1), COOLDOWN, POSITION_LIMIT / INSUFFICIENT_FUNDS (buy), INSUFFICIENT_SHARES (sell).
  Buys fill at ask (= spot before the order's own impact); sells fill at bid = max(1, floor(spot*0.97)).
- `reprice(state, ticker, now, reason)`: returns a PriceChange and pushes one history point only when spot or reference moved; otherwise null. Null for halted/unknown. Call it after heat / mission / escape changes and about once a second for SURVIVAL_TICK and SENTIMENT_DECAY.
- `quote()` prunes expired demand (the only mutation it makes). Halted listings quote spot/bid/ask = 0.
- `haltListing(state, ticker, now)`: one-way; returns `{finalSpotBeforeHalt, referenceAtHalt}` or null if already halted. Sets spot to exactly 0 and pushes a 0 history point. Set `character.captureTimeMs` BEFORE or at the same `now` (survival freezes at captureTimeMs).
- `bountyFor(state, characterId, now)`: max(50, round(0.5 * reference)). Reference ignores heat, so the bounty only grows. Call it BEFORE or after haltListing — both give the same value (frozen at halt).
- `credit(state, walletId, amount)`: integer amount, throws on unknown wallet or negative result; returns the new balance.
- `walletView`: marketValue = shares * stored listing.spotPrice (0 when halted). `listingView` uses a fresh quote and includes currentBounty.
- A held position in a halted ticker stays in the wallet (shares kept, worth V$0).
