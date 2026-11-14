# VICE EXCHANGE — Software Requirements Specification (SRS)

**Status:** DRAFT / review required | **Version:** 0.1 | **Date:** 2026-11-14  
**Source of product intent:** `VICE_EXCHANGE_PRD.md`  
**Priority legend:** **P0** = release blocker for hackathon MVP; **P1** = stretch; **P2** = post-hackathon.

## 1. Purpose and boundaries

This document turns the PRD into testable behavior for a **single-room, fictional economy multiplayer simulator**. It specifies externally observable behavior, game rules, integrity and demo acceptance conditions. The architecture and interfaces are in `VICE_EXCHANGE_TECHNICAL_DESIGN.md`.

### Terminology

- **Player:** a human connection/accountless session; can control one active character at a time.
- **Character:** a life in the game with its own ticker; a player may have multiple sequential characters after captures.
- **NPC:** autonomous non-player character with its own bounded memory, goals, role and decision policy.
- **Ticker/listing:** virtual market instrument assigned to one character; a captured listing is permanently halted at price 0.
- **Reference value:** deterministic score driven by fictional crime/missions, survival and notoriety.
- **Spot price:** executable fictional game quote derived from reference, risk and bounded demand sentiment.
- **Heat:** server-computed measure of pursuit/capture risk.
- **Observation:** an agent's direct perception of a nearby event, subject to visibility conditions.
- **Message:** a recorded communication from one agent to another containing an observation or rumor.
- **Drex decision:** a real external evaluation with traceable state, choices and chosen label, as opposed to scripted fallback.
- **Virtual credits (V$):** nonredeemable, nontransferable outside the game; **not actual securities or money**.

## 2. Actors and permissions

| Actor | Read | Initiate | May not do |
|---|---|---|---|
| Outlaw human | Public world/market, own wallet/holdings | Movement, interact/mission, trade, respawn | Set prices, edit NPC memories, self-capture other players remotely |
| Hunter human | Public world/market, own wallet/holdings | Movement, trade, attempted capture of wanted target | Capture without proximity/valid state |
| Spectator human | Public world, public event/decision trace | View/filter/replay | Move, trade, capture |
| Witness NPC | Local observed events + received messages | Report, ignore, message | Read arbitrary private NPC memories |
| Trader NPC | Public market + allowed messages + own memory | Buy/sell/hold | Bypass wallet/position limits |
| Police NPC | Public wanted bulletin + local sightings + allowed messages | Investigate, pursue, capture | Capture without validated conditions |
| Informant NPC | Local observations/messages | Relay/withhold a rumor | Fabricate a direct observation in the authoritative event ledger |
| Server/demo director | Complete state | Seed/reset, apply rules, admin demo triggers | Present simulated decisions as live Drex outcomes |

## 3. Functional requirements

### 3.1 Rooms, players and game world

| ID | Priority | Requirement | Verification |
|---|---|---|---|
| FR-001 | P0 | Player can create or join a room with a short room code, enter nickname and pick Outlaw/Hunter/Spectator. | Join via two browsers; room code joins same instance. |
| FR-002 | P0 | At least 2 human clients can interact simultaneously in one authoritative world. | Move player A; player B sees updated location and mission state. |
| FR-003 | P0 | Clients can move avatars within map bounds; server rejects impossible positions and speed. | Attempt teleport/speed hack; server rejects. |
| FR-004 | P0 | Room spawns 6–8 NPCs with stable IDs and at least four distinct roles. | Inspect startup state; IDs/roles distinct. |
| FR-005 | P0 | The room contains one completable fictional mission objective and an incident trigger. | Player interacts in range; exactly one completion event issued. |
| FR-006 | P0 | The UI shows synchronized player/NPC positions, wanted state, stock price and event feed. | Compare two tabs after an incident. |
| FR-007 | P0 | A disconnected browser does not halt room simulation. | Disconnect one browser; NPCs and second player continue. |
| FR-008 | P1 | Player may reconnect to same character within a brief grace window using an ephemeral token. | Reconnect tab without duplicate character. |
| FR-009 | P2 | Persistent identity, multiple rooms and matchmaking are supported. | V1 end-to-end. |

### 3.2 Notoriety, observation and memory

| ID | Priority | Requirement | Verification |
|---|---|---|---|
| FR-010 | P0 | Valid mission completion increases character `notorietyPoints`, `completedMissions` and heat as configured. | Assert changes and event payload. |
| FR-011 | P0 | Witness directly observes an incident only if its position and line-of-sight/perception predicate allow it. | Place witness outside radius/blocked zone; memory unchanged. |
| FR-012 | P0 | Every NPC has separate bounded memory entries with origin, timestamp, confidence, expiry and source actor. | Query two NPCs before gossip; only observer has incident. |
| FR-013 | P0 | Other NPCs learn observations only via direct witnessing, explicit messages or public bulletin publication. | Inspect message chain and recipient memory. |
| FR-014 | P0 | A message records sender, recipient, payload, delivery event and source memory/event link. | Trace rumor from witness to informant to trader. |
| FR-015 | P0 | Decisions use each NPC's own snapshot, not omniscient server state. | Hide an incident from NPC B; B's Drex state must not contain it. |
| FR-016 | P1 | NPCs track source reliability; contradicted reports can reduce belief confidence. | Deliver conflicting rumors; confidence changes and is logged. |
| FR-017 | P2 | Long-lived agent relationships and memory summarization survive sessions. | V1 state restore. |

### 3.3 Agent decisions and observability

| ID | Priority | Requirement | Verification |
|---|---|---|---|
| FR-020 | P0 | Meaningful NPC actions (report/ignore, relay/withhold, investigate/patrol, buy/sell/hold) are selected through real Drex typed `choice` calls when available. | Instrument tests inspect request and response traces. |
| FR-021 | P0 | Drex state contains role, goal, allowed local information, recent messages and relevant world/market summary. | Check logged redacted decision input. |
| FR-022 | P0 | Server maps a model `choice` to one of the explicitly allowlisted actions for that agent and state. | Inject invalid choice; no effect and error logged. |
| FR-023 | P0 | Every agent decision trace includes time, agent ID, model ID, offered options, chosen option, response confidence/probabilities, provider request ID, latency, trigger event and effect event(s), where available. | Open trace; verify fields. |
| FR-024 | P0 | Model unavailability or low confidence never grants an illegal action; use a **clearly labeled scripted fallback**. | Mock 429/529/timeout; world proceeds and trace reads `fallback`, not `Drex`. |
| FR-025 | P0 | Simulation advances while an AI decision is pending; stale decisions are rejected or reconsidered against current state. | Delay a response past capture; no stale trade accepted. |
| FR-026 | P0 | Decision calls are rate-limited, event-driven and bounded in concurrency. | Stress burst; queue drains without unlimited parallel requests. |
| FR-027 | P1 | The decision inspector shows probability bar or list for each candidate action. | Test from real API result. |
| FR-028 | P1 | Moderator can switch to fully scripted baseline; dashboard clearly labels experiment mode. | A/B run shows mode and numbers. |
| FR-029 | P2 | Agents may create structured contracts/alliances via server-approved protocols. | V1 negotiated actions. |

### 3.4 Market and virtual portfolio

| ID | Priority | Requirement | Verification |
|---|---|---|---|
| FR-030 | P0 | Each spawned character gets a new unique ticker and initial reference price. | Spawn two characters; tickers differ. |
| FR-031 | P0 | Reference value responds to completed missions, notoriety and survival according to documented server formula. | Fixed seed/clock input returns fixed expected price. |
| FR-032 | P0 | Spot price incorporates reference value, risk and bounded sentiment, unless listing is halted. | Increase heat vs. demand; verify controlled change. |
| FR-033 | P0 | Captured ticker's spot price becomes **exactly zero** and status is `HALTED_CAPTURED`; further orders are rejected. | Capture, query ticker, submit order. |
| FR-034 | P0 | Buy/sell operations transfer V$ and integer share units atomically, under a per-order/position cap. | Verify credits, shares, recorded trade and invariant. |
| FR-035 | P0 | Orders fail if wallet insufficient, selling exceeds holdings, ticker halted, command duplicated or actor disallowed. | Attempt each failure; no partial mutation. |
| FR-036 | P0 | NPC trades go through same validation and settlement path as human trades. | Trader NPC with empty wallet cannot buy. |
| FR-037 | P0 | UI displays watchlist/ticker graph, current wallet, holdings, trade feed and recent changes. | Watch after mission, buy and capture. |
| FR-038 | P0 | Each new character after capture has a **new ticker**; all old orders remain history but cannot trade. | Capture then respawn; compare listing records. |
| FR-039 | P0 | Trading uses **only fictional nonredeemable credits**; user sees that label. | Inspect UI and API for absence of payments. |
| FR-040 | P1 | Leaderboard ranks active tickers by market cap or valuation with transparent metric. | Active ticker shows rank; halted not misrepresented. |
| FR-041 | P2 | Market depth/limit order book, complex trading, factions and lending. | V1 only. |

**Proposed H0 trading guardrails:** integer shares; 1 share per click by default; max 10 shares per character per wallet; 1 order per actor per ticker every 2 seconds; configurable virtual starting wallet V$2,000; no short selling, leverage, credit transfers or real-money prizes. The server is the simulated market counterparty; this is *not* a realistic exchange-clearing implementation.

### 3.5 Pursuit, capture and respawn

| ID | Priority | Requirement | Verification |
|---|---|---|---|
| FR-050 | P0 | Server determines whether character is wanted using defined heat/incident thresholds; UI shows it. | Generate incident; wanted flag updates. |
| FR-051 | P0 | Police or hunter capture requires wanted target, alive/uncaptured state, same room, in-range positions and cooldown/interaction conditions. | Remote/duplicate capture attempt rejected. |
| FR-052 | P0 | Capture transition is single-use and atomic: set captured, halt ticker, zero price, announce event, lock mission interaction. | Two simultaneous captures produce one settlement. |
| FR-053 | P0 | Character can respawn after capture with clean active-character state and a new ticker. | Respawn; identity reused but character ID differs. |
| FR-054 | P0 | Event history and old listings persist after respawn. | Open prior character's history. |
| FR-055 | P1 | Valid captor earns virtual bounty; reward cannot be claimed twice. | Simultaneous hunters get one payout. |

### 3.6 Demo / analytics / optional NDI

| ID | Priority | Requirement | Verification |
|---|---|---|---|
| FR-060 | P0 | Demo director can reset one room to a seeded initial state. | Reset twice; initial positions, tickers and portfolios match. |
| FR-061 | P0 | Event feed links mission -> observation -> message -> decision -> market/capture outcomes. | Judge follows links through a live chain. |
| FR-062 | P0 | Server records actual observed Drex evaluation latency/usage and counts of agent decisions by type. | Dashboard reflects logged requests; no invented figures. |
| FR-063 | P0 | An identical logged decision sequence can be replayed without fresh model calls. | Replay reproduces market and capture state. |
| FR-064 | P1 | Parse at least one **fictional policy/incident PDF** with NDI; expose extracted data/citation in an agent state and the demo panel. | Live or cached parsing artifact contains NDI source and extracted field. |
| FR-065 | P1 | Demonstrate scripted baseline vs Drex using comparable event seeds and clearly distinguish non-deterministic model decisions. | Comparison table reports seed, mode, metric and caveat. |
| FR-066 | P2 | Long-term analysis across rooms and sessions. | V1 dashboards. |

## 4. Core state transitions

### Character state machine

`SPAWNED -> ACTIVE -> WANTED -> CAPTURED -> ARCHIVED`  
`ACTIVE -> CAPTURED` permitted if a legal capture condition is met. `CAPTURED` is terminal for that character; **respawn creates a new character** rather than resetting the old one.

### Ticker state machine

`ACTIVE -> HALTED_CAPTURED` is one-way. On transition, `price=0`; buy/sell must reject. No reopening a halted ticker.

### Decision lifecycle

`REQUESTED -> RESOLVED -> VALIDATED -> APPLIED` or `REQUESTED -> TIMEOUT/ERROR -> FALLBACK_APPLIED`. A stale or illegal action follows `RESOLVED -> REJECTED`; trace remains inspectable.

### Information flow

`World Event -> Agent Perception -> Local Memory -> Drex Decision -> Message or World Action -> Recipient Memory / Market Event`. The private-memory boundary applies at every step.

## 5. Event schema and commands (contractual requirements)

All authoritative events MUST have `eventId`, `roomId`, `serverSequence`, `time`, `type`, `actorId`, `causationId`, `correlationId`, `payload` and `schemaVersion`. Events must be ordered **per room** and replayable with the same deterministic rule version.

Minimum event names:

```text
ROOM_CREATED, PLAYER_JOINED, CHARACTER_SPAWNED,
MISSION_COMPLETED, INCIDENT_OCCURRED, NPC_OBSERVED,
AGENT_MESSAGE_SENT, AGENT_MESSAGE_DELIVERED,
DREX_DECISION_REQUESTED, DREX_DECISION_RESOLVED, AGENT_ACTION_APPLIED,
MARKET_PRICE_UPDATED, TRADE_EXECUTED, TRADE_REJECTED,
WANTED_STATUS_CHANGED, CAPTURE_SUCCEEDED, CAPTURE_REJECTED,
LISTING_HALTED, CHARACTER_ARCHIVED, CHARACTER_RESPAWNED
```

Minimum client commands:

`room.join`, `player.move`, `mission.interact`, `market.buy`, `market.sell`, `capture.attempt`, `character.respawn`, `demo.reset`, `demo.incident`.

All commands that mutate money, capture, missions or life state MUST include an idempotency key. Server state is authoritative; clients must not provide prices, coordinates for instant teleports, wallet totals or claimed Drex outputs.

## 6. Nonfunctional requirements

| ID | Priority | Requirement / target | Validation |
|---|---|---|---|
| NFR-001 | P0 | Browser app usable on modern Chrome on a laptop at demo resolution. | Manual demo smoke test. |
| NFR-002 | P0 | Two concurrent human clients + 8 NPCs, with no unbounded queue growth. | Small local soak/stress test. |
| NFR-003 | P0 | 10Hz authoritative world tick (configurable), UI interpolation; event-driven NPC decisions. | Network/state counters. |
| NFR-004 | P0 | World continues if Drex calls are slow; visible model-pending status and labeled fallbacks. | Inject 5s delayed provider call. |
| NFR-005 | P0 | Persist capture, transaction and decision logs across server restart where database available. | Restart and inspect state/history. |
| NFR-006 | P0 | Server-only `NACE_API_KEY`; never expose it in browser, logs, source control or screenshots. | Source/network scan. |
| NFR-007 | P0 | Orders, capture and mission completion are validated and idempotent under reconnect/retry. | Parallel command tests. |
| NFR-008 | P0 | On API 401/402/422, do not blindly retry; on 429/529/transient errors use bounded retries respecting server guidance. | Fault-injection unit tests. |
| NFR-009 | P0 | Provider decisions and scripted fallbacks are explicitly distinguished in telemetry. | Inspector badge and event field. |
| NFR-010 | P0 | Virtual credits remain fictional and never connect to external payment rails. | Integration scan. |
| NFR-011 | P1 | P95 client command acknowledgement under 250ms on local/dev demo network **excluding model calls**, as an engineering target, not a measured claim. | Load test and report actual values. |
| NFR-012 | P1 | Keyboard-only flows for join, trading and agent inspector; readable chart labels and reduced-motion mode. | Manual accessibility audit. |
| NFR-013 | P1 | Configurable cap on API spend/requests; trace recent inputs without secrets. | Verify budget circuit breaker. |
| NFR-014 | P2 | Horizontal scaling, ranked matchmaking, durable accounts, admin tools. | V1. |

### Security and abuse requirements

- Reject unauthorized `demo.reset` or event-injection commands in public rooms; H0 may restrict them to a protected demo-director token.
- All client inputs parsed against schemas (type, length, enums, range); room code alone is not admin authorization.
- Use opaque player session token and rate-limited socket events; do not trust client timestamps for settlement.
- Separate public events from **private memory** and confidential provider credentials.
- Prevent duplicate capture bounties, duplicate order execution, trading halted listings and negative wallets.
- Since this is a game depicting crime, actions remain fictional and stylized; no real-world targeting or actionable harmful instructions are needed.

## 7. Test scenarios / acceptance stories

### AT-01 — A mission raises reference value

**Given** Player A has an active ticker at price P0 and a nearby uncompleted objective  
**When** Player A interacts within allowed distance  
**Then** exactly one `MISSION_COMPLETED` event occurs, notoriety increases and the reference value increases. Attempting the same objective again must have no effect.

### AT-02 — Private memory does not magically spread

**Given** Witness W sees an incident while Trader T is out of observation range  
**When** W records the observation but no message has been delivered to T  
**Then** W has a sourced memory entry and T has **no** memory of the incident.  
**When** W's message is delivered to T  
**Then** T may update memory, with W named as the source, and T may make a Drex trade decision.

### AT-03 — Real agent decision trace

**Given** a valid `NACE_API_KEY` and a queued witness decision  
**When** the server calls Drex with `choice: report|ignore|gossip`  
**Then** it records model/request IDs, offered labels, returned label/probabilities/confidence and latency; the applied action is visible in the event feed. No value may be labeled live Drex if the provider was not called.

### AT-04 — Shared market integrity

**Given** Player A and Player B both watch ticker X  
**When** A buys one share  
**Then** A's wallet decreases, holdings increase, both clients see the same trade and price state, and the server records a single order settlement.  
**When** A replays the same command id  
**Then** the trade is not executed again.

### AT-05 — Capture crash is atomic

**Given** a wanted character and two eligible hunters within capture range  
**When** both send `capture.attempt` concurrently  
**Then** **one** capture succeeds; ticker price becomes exactly V$0; no further trades or mission completions are allowed on that character; any bounty is issued once.

### AT-06 — A new life, an old history

**Given** Player A's character ticker X was captured  
**When** Player A respawns  
**Then** the new character has a new ticker Y with a baseline price, while X remains visible as halted, including its trade and incident history.

### AT-07 — Provider failure is transparent

**Given** Drex times out or responds 429  
**When** a trader NPC must choose what to do  
**Then** the room still runs, a safe scripted fallback may run, and the trace explicitly marks `decisionSource=fallback` with error/context. The UI must never display fabricated confidence as a real API result.

### AT-08 — Replay is causally consistent

**Given** a recorded event and model-decision log from a seeded demo run  
**When** the system replays the log offline without calling Drex  
**Then** character/ticker/portfolio outcomes match the original ruleset, subject to the same rules version and deterministic timing inputs.

## 8. Acceptance gates (definition of done)

**Gate A — Can enter and interact:** Join, second player, movement, NPC scene and one mission work.  
**Gate B — Can reason:** At least four NPC roles and one complete *real* Drex-driven observation-to-action chain work.  
**Gate C — Can trade:** Wallet/holdings update and market reference+spot respond to events.  
**Gate D — Can end a listing:** Capture transitions are atomic and zero the stock; respawn archives old character.  
**Gate E — Can prove it:** Event chronology, decision traces, baseline comparison and a reproducible demo path available.

**Release block:** Any failed P0 gate means **do not** claim a completed hackathon MVP. A credible narrower demo with working real Drex and an honest list of missing features is preferable to a fake or unstable full demo.

## 9. Implementation order and de-scope rules

1. **Critical path 1:** monorepo/server/room/auth-light + basic join/movement.
2. **Critical path 2:** world events/missions/observation/memory + real Drex adapter and logs.
3. **Critical path 3:** deterministic price engine + trades + capture/respawn (single transaction boundary).
4. **Critical path 4:** spectator/debug timeline + seeded live demo + basic comparison.
5. **Polish:** 3D assets, smooth movement, neon look, optional NDI.

**Cut first under deadline:** advanced graphics, second map, extra missions, 4-player stretch, sophisticated investor behavior, NDI, fancy dialogue.  
**Never cut silently:** real vs simulated decision labeling; atomic stock-zero capture rule; separate NPC memories; server-trusted money.

## 10. Open requirements decisions

| ID | Open question | Recommended default | Blocking? |
|---|---|---|---|
| Q-01 | Must H0 run inside literal GTA Vice City? | No; browser-native original assets, future engine adapter | Yes |
| Q-02 | Can price drop before capture? | Yes, due to heat/market sentiment | Yes |
| Q-03 | Do other players get unlimited trading money? | No; fixed fictional V$2,000 per new human session | No |
| Q-04 | Who can capture? | Wanted police NPCs and in-range humans in Hunter role | Yes |
| Q-05 | Is NDI required on submission day? | Stretch; only include if meaningful, cited and working | No |
| Q-06 | 3D or 2D? | 3D preferred, 2D fallback allowed to protect core mechanics | Yes |

## 11. References

- Product intent and proposed economics: `VICE_EXCHANGE_PRD.md`
- Technical ownership, data model and APIs: `VICE_EXCHANGE_TECHNICAL_DESIGN.md`
- Drex decision types: https://console.nace.ai/docs/drex/guides/questions
- Drex error and retry guidance: https://console.nace.ai/docs/drex/guides/errors-and-retries
- Official hackathon tracks/rules: https://luma.com/nace-drexathon

---
**Approval status:** Pending. Requirements are proposed, not a claim that software already exists.
