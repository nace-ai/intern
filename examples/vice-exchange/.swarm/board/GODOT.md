# GODOT board — 3D world client (Open Theft Auto, Godot 4.7)

Address: a0e7dbb5b6c9ab618. Status: working end to end; art pass in progress until 13:50.

## Launch (server on port 3001 must be running: `npm run dev`)
One-time setup (already done on this Mac): `brew install --cask godot`, clone
https://github.com/mehulkapadia5/open-theft-auto into `integrations/open-theft-auto`, then
`python3 integrations/ota-bridge/apply.py` and `godot --headless --path integrations/open-theft-auto --import`.

Outlaw window (default room VICE-42):
```
cd "/Users/manubaba/Documents/vice city/vice-exchange-claude/integrations/open-theft-auto"
godot --path . -- --vice --vice-windowed --vice-size=1600x900 --vice-name=ACE --vice-role=OUTLAW --vice-room=VICE-42 --vice-url=ws://127.0.0.1:3001/bridge
```
Hunter window (second player, same room):
```
cd "/Users/manubaba/Documents/vice city/vice-exchange-claude/integrations/open-theft-auto"
godot --path . -- --vice --vice-windowed --vice-size=1600x900 --vice-name=VEX --vice-role=HUNTER --vice-room=VICE-42 --vice-url=ws://127.0.0.1:3001/bridge
```
Second laptop: replace 127.0.0.1 with this Mac's Wi-Fi address. Drop `--vice-windowed` for fullscreen.
Other flags: `--vice-time=18.3` (clock, default neon dusk; 17.2 golden hour; -1 lets it run), `--vice-look=plain`,
`--vice-keep-hud`, `--vice-no-ambient`, `--vice-room=NEW` (private room), `--vice-mode=scripted` (room creator only).

## Keys
WASD move, mouse look, Shift sprint, F enter/exit a car. **E** grab the job when the prompt shows (Outlaw) or capture a
WANTED outlaw within 2.5 m (Hunter). **R** respawn as a new character and ticker after capture. **]** buy 1 share,
**[** sell 1 share (own ticker for an outlaw, first active ticker for a hunter). Click the window once to capture the mouse.

## What the bridge is
- Server: `apps/server/src/bridge/index.ts`, plain WebSocket at `/bridge`, mounted by ENGINE in `transport/app.ts`.
  Same `clientCommandSchema` validation and `RoomEngine.dispatch` path as the browser; position comes in as `player.pose`.
- Godot: `integrations/ota-bridge/vice_bridge.gd` (autoload ViceBridge) + `apply.py` (six small guarded edits to
  upstream files, all no-ops without `-- --vice`). Upstream checkout is git-ignored.
- Mapping: city.x = 112 + (sim.x - 62) * 32/30, city.z = -16 + (sim.z - 28) * 32/26. Sim roads land on the city's
  road grid. Server perception and line of sight use the server's own map, not the city's buildings.

## Verified (real runs, two Godot clients, real E / R / ] keys)
join, 8 NPCs + remote player rendered, ticks, pose (0 rejections), buy (2000 -> 1903), job, witness report, wanted
(heat 42, level 2), capture by hunter (+V$100 bounty) and by Drex-driven police, listing HALTED_CAPTURED at 0,
banner, respawn to a new ticker (ACE-02) with the old one still halted. Drex mode shows real decisions in the HUD feed.
Verification commands: add `--vice-check` (headless ok), `--vice-hunt`, `--vice-shots=<dir>`, `--vice-record=<dir>`.

## Not verified / limits
- Manual play with a human at the keyboard (all runs were scripted through the real key path).
- FALLBACK and META decision popups were not captured on a frame (colours exist in code).
- A bridge client that created a private room loses it when the watch-mode server restarts (it then creates a new one).
- About 40 fps at 1280x720 while recording on this Mac with the neon look and background life.
- Frames and clip: `integrations/ota-bridge/frames/`.

## GODOT handoff (a0e7dbb5b6c9ab618, 13:26)
- Clip: `integrations/ota-bridge/frames/vice_exchange_3d_full_chain.mp4`, 45 s, 1280x720, real-time capture of one live
  Drex-mode run: flyover, buy 1 share, job, witness REPORT [DREX 0.97], bulletin ring, price 175 -> 136 on the HUD,
  PURSUE [DREX 0.96], capture by Sgt. Imani Duarte, V$0 banner, respawn as ACE-02. Re-record with
  `--vice-check --vice-record=<dir> --vice-size=1280x720 --vice-room=NEW --vice-wait=5 --vice-linger=85`, then
  `ffmpeg -f concat -safe 0 -i <dir>/frames.txt -vf fps=24,format=yuv420p -c:v libx264 -crf 26 out.mp4`.
- Frames 20 to 27 in the same folder show the effects: side-on message arc + bulletin ring + police strobes (scripted
  room, badge says SCRIPTED), DREX decision popup, WANTED price drop, job prompt, pursuit two-shot, V$0 banner.
- Last end-to-end check of the script as it is on disk now: 13:25, headless, private room on port 3001: join, buy,
  job, wanted all confirmed, 0 pose rejections.
- Not verified: a human playing with WASD and mouse (checks move the player by script; E, R, ] go through the real
  key path); decision popups for `fallback` and `meta` sources (only drex, guardrail and scripted were seen);
  the Web export / browser embed added to `vice_bridge.gd` and `apply.py` by another contributor.
- Known limits: the message arc is often partly hidden by buildings or the HUD strip from the chase camera; a private
  room does not survive a server restart (the client makes a new one); about 40 fps while recording at 1280x720.
- `vice_bridge.gd` was edited by two contributors at once after 13:05 and broke twice from merged edits; before the
  demo run `godot --headless --path integrations/open-theft-auto --quit` and check for "Parse Error".

## Guns, cars, helicopter (GODOT2, 13:55)
Same launch commands as above. Keys added: **left mouse** fires the stun pulse (pistol in hand by default), **F** takes a
car or boards the helicopter parked at sim (62,54), **Up / Down** climb and descend. Flags `--vice-no-guns`, `--vice-no-heli`.
Verified with two real clients on a private server; details and limits in `integrations/ota-bridge/README.md`.
Clip: `integrations/ota-bridge/frames/vice_exchange_3d_guns_cars_heli.mp4` (22 s). No server change: port 3001 needs
no restart for this (it already has `weapon.fire`).

## UDP fast path (agent UDP, 13:56)
- Server `apps/server/src/bridge/udp.ts` (hooked from `bridge/index.ts`), Godot `integrations/ota-bridge/vice_udp.gd`
  (copied by `apply.py`, loaded by `vice_bridge.gd`). Tests: `apps/server/test/udp.test.ts` (8).
- Port = `UDP_PORT` or HTTP port + 1 (3001 -> 3002). Off switches: server `VICE_UDP=0`, client `--vice-no-udp`.
- Poses up and world state down at 30 Hz over UDP; everything reliable stays on the WebSocket. Details and wire format
  in `integrations/ota-bridge/README.md`. The web WORLD embed stays on WebSocket.
- Verified with a real headless Godot client: NET live at 29 Hz, 0% loss, 290 state updates vs 105 over WebSocket in the
  same check run; `--vice-no-udp` run completed on WebSocket. Not verified: a windowed two-client visual comparison,
  and the mid-session drop back to WebSocket (logic only).
