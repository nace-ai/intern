# WEB board

Status: DONE. Typecheck + build pass. Protocol verified end to end with a scripted socket client through the Vite proxy (join, snapshot, trade, cooldown reject, incident, live Drex decisions, all push channels). NOT yet seen in a real browser: the Chrome extension was not connected. Test ports: `VICE_SERVER=http://localhost:PORT npx vite --port N` overrides the proxy target.

## What WEB expects from ENGINE (please confirm or tell me the difference)
- Client emits socket.io `command` (SOCKET.command) with a ClientCommand and an ack callback -> CommandAck.
- `room.create` / `room.join` ack: `{ ok:true, id, result: JoinResult }` (JoinResult incl. `snapshot` and `wallet`).
  Reconnect/resync = `room.join` with `sessionToken` (same character, no duplicate); also my gap-recovery path.
- Failed trade ack: `error.reason` = TradeRejectReason; failed capture ack: `error.reason` = CaptureRejectReason.
- Server pushes: room.snapshot (after demo.reset), world.tick (10 Hz), event (one GameEvent or an array), agent.update,
  listing.update, character.update (incl. new character after respawn), mission.update, wallet.update (owner only),
  provider.status, metrics.update, experiment.update.
- I find "my character" as the live CharacterView whose playerId is mine, so character.update after respawn is enough.
- PlayerView list is refreshed from PLAYER_JOINED / PLAYER_LEFT events between snapshots.

## Exports (apps/web/src/state/gameStore.ts)
- `useGame` zustand store: session, mode, map, players, characters, npcs, missions, listings, trades, events (ascending),
  metrics, experiments, policy, providerStatus, wallet, lastTick, selectedAgentId, selectedEventId, viewMode, crash.
- `useMyCharacter()`, `useSelectedAgent()`, `getPose(id, now?)` (interpolated position), `nameOf(state, id)`, `isLive(c)`.
- `apps/web/src/net/socketClient.ts`: `send(cmd)`, `sendNoAck(cmd)`, `joinRoom(...)`, `resync()`, `newId()`.

## City
- WEB mounts `<CityView renderer={viewMode} ...CityViewProps />` from `./scene` (CITY2D's props, unchanged). WEB owns
  keyboard: WASD/arrows -> player.move, E -> mission.interact or capture.attempt (hunter), R -> character.respawn.
