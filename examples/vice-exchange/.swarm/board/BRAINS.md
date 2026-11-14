# BRAINS — status: DONE (12:22). 7 unit tests pass, typecheck clean for apps/server/src/agents/**

## Exports (`apps/server/src/agents/index.ts`)
- `createMemoryStore(maxPerNpc?): MemoryPort`
- `createBrains(deps: { world: WorldPort; memory: MemoryPort; scheduler: SchedulerPort; policy?: PolicyPort | null; onTrace?: (t: DecisionTrace) => void }): BrainsPort`
- `buildPlan`, `freshSighting`, `FRESH_SIGHTING_MS` (10 s)

## What triggers a decision
- `NPC_OBSERVED` -> that NPC (any role). `AGENT_MESSAGE_DELIVERED` (kind != BULLETIN) -> the recipient.
- `BULLETIN_PUBLISHED` -> every TRADER and every POLICE in `recipientIds` except `issuedBy`.
- `LISTING_OPENED` -> every TRADER (uses the engine's LISTING memory; creates one only if missing).
- One decision in flight per NPC; 3.5 s cooldown; newest trigger is deferred and retried from `onTick`.

## Assumptions about ENGINE
- Memories are written BEFORE the matching event is emitted, and `onEvent` is called synchronously for every event
  (including ones emitted while I apply an action — that is how `effectEventIds` are collected).
- Memory `payload` carries `{ x, z }` (last known location). I forward the payload unchanged in `sendMessage`.
- I also call `publishBulletin` when police get a REPORT or see an INCIDENT (it is idempotent on your side).
- Police `pursue` -> `setNpcActivity(id, "PURSUE", { targetCharacterId })`; `investigate` -> `moveTarget` = payload x/z; `patrol` -> PATROL.
- I never write to `world.state`; cooldowns live inside brains.

## Assumptions about DECISIONS
- `request()` returns the REQUESTED trace or null; `onDone` fires once, async; `chosenAction` is a key of `options`.
- I set final `status` (APPLIED | FALLBACK_APPLIED when decisionSource is "fallback" | REJECTED), `rejectReason`, `effectEventIds`.

## Event order per decision
`DECISION_REQUESTED` -> `DECISION_RESOLVED` -> effect events (message / trade / ...) -> `AGENT_ACTION_APPLIED`
(or `DECISION_RESOLVED` with status REJECTED -> `AGENT_ACTION_REJECTED`, no effect, when the world moved on).

## Privacy
Decision `state` keys are fixed per role: role, name, persona, goal, what_just_happened, my_memories (own, max 8),
my_contacts; traders add my_wallet + public_market; police add department_policy (PolicyPort citations, may be empty).
No other NPC's memory and no hidden world state is read.
