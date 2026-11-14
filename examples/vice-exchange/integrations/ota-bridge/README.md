# VICE EXCHANGE 3D world client (Open Theft Auto bridge)

Runs the open-source Godot game [Open Theft Auto](https://github.com/mehulkapadia5/open-theft-auto) (MIT, Godot 4.7,
procedural city) as a 3D client of the VICE EXCHANGE server. The server stays authoritative for the swarm, the market
and capture; this client renders server state inside the city and sends the same validated commands as the browser.

The upstream game is not committed (437 MB of assets). This folder holds only what we add:

| File | Purpose |
|---|---|
| `vice_bridge.gd` | Autoload `ViceBridge`: WebSocket client, swarm NPCs and remote players, HUD, look, FX, verification modes |
| `apply.py` | Idempotent installer: copies the autoload and makes six small guarded edits to upstream files |
| `frames/` | Real captures from the running game, and the full-chain clip |

## Setup
```
brew install --cask godot
git clone --depth 1 https://github.com/mehulkapadia5/open-theft-auto.git integrations/open-theft-auto
python3 integrations/ota-bridge/apply.py
godot --headless --path integrations/open-theft-auto --import
```
Launch commands and keys: see `.swarm/board/GODOT.md`.

## What the edits to upstream do
All are no-ops unless the game is started with `-- --vice`: optional windowed mode, a generic civilian rig for the local
player, no suit summon, no single-player hint toasts, and no reading or writing of the single-player save. In bridge mode
the bridge also skips the upstream boot screen, hides the single-player HUD, pins the upstream wanted level to zero (the
only police are the swarm's), holds the clock at dusk and swaps unclothed pedestrian rigs for clothed ones.

## Coordinates
The server plane is 120 x 80 m. `city.x = 112 + (sim.x - 62) * 32/30`, `city.z = -16 + (sim.z - 28) * 32/26`, which puts
the server's road centres on the city's 32 m road grid. The client reports its position with `player.pose`; the server
checks bounds and speed. **Perception and line of sight are computed by the server on its own map**, not on the city's
buildings, so what an NPC "sees" follows the server's blocks, which only roughly match the city's.

## Honesty rules kept
Decision popups and the HUD feed print the real `decisionSource`: the word DREX appears only when Drex decided;
guardrail, fallback, scripted and meta are labelled as such and coloured differently. Confidence and latency are shown
only when the server supplied them.

## Verification modes
`--vice-check` (works headless): join, ticks, buy with `]`, walk to the job, press `E`, wait for capture, press `R`.
`--vice-hunt` with role HUNTER shadows the outlaw and captures with `E`. `--vice-shots=<dir>` saves PNG frames.
`--vice-record=<dir>` saves real-time JPEG frames plus `frames.txt`; build the clip with
`ffmpeg -f concat -safe 0 -i frames.txt -vf fps=24,format=yuv420p -c:v libx264 out.mp4`.

## Credits and limits
Upstream game: MIT, copyright mehulkapadia5. Human rigs used for our figures are upstream's CC-BY models (Renderpeople
"Nathan" and the two suit models; see each `license.txt` upstream). The bridge does not use upstream's boot-screen
branding, its superhero-suit content or its "peter" player model. Known limits are listed on the board.

## WORLD view in the browser UI (Godot Web export)
The browser app has a third city view, WORLD, that embeds this client as a Web export and plays as the page's own
player (it presents the page's session token on `/bridge`; the browser socket keeps the session).
Build it (needs the Godot 4.7.2 web export templates in `~/Library/Application Support/Godot/export_templates/4.7.2.stable/`):
```
python3 integrations/ota-bridge/apply.py          # also adds the "Web" export preset
cd integrations/open-theft-auto && godot --headless --path . --export-release "Web" ../../apps/web/public/world/index.html
```
Output goes to `apps/web/public/world/` (git-ignored, about 400 MB). Open `http://localhost:5173/?...&view=world`
or press WORLD in the header. The client connects to `ws://<page host>:3001/bridge`; override with `&bridge=ws://host:port/bridge`.
On the web it runs single-threaded on the Compatibility renderer with background life and real-time shadows off and 3D at 60% scale.

## Guns, cars and the helicopter (added 2026-11-14)

- **Gun.** You start with the pistol. **Left mouse** (the game's normal trigger, mouse captured) fires the server's stun
  pulse along your aim: 16 m range, 6 charges, 2.5 s recharge, a hit knocks a player or a police officer down for 3 s,
  nobody is hurt. The city's own bullets, damage and wanted stars never run in VICE mode (`apply.py` routes
  `_fire_weapon` to the bridge), so nothing is hit locally that the server does not know about. Every client sees the
  tracer, the muzzle flash, the stun ring and a "STUNNED" tag; the HUD shows `PULSE n/6` and the recharge. An outlaw's
  shot raises heat and is witnessed by the swarm NPCs, who then decide with Drex as for any incident.
- **Cars.** **F** next to a car takes it (the city's own enter/exit); WASD drives. Your position keeps going to the
  server, so other clients see you move at car speed and show you in a car with a "DRIVING" tag (inferred from speed,
  above about 13 m/s). Leaving the mapped district pins you at its edge on the server and the HUD says
  `OUT OF THE DISTRICT`; you stay joined.
- **Helicopter.** One flyable helicopter is parked inside the district (sim 62,54, a few metres north of the outlaw
  spawn). **F** boards, **Up / Down arrows** climb and descend, WASD flies. The server only tracks your ground position.
- Flags: `--vice-no-guns`, `--vice-no-heli`. The web (WORLD view) build has no helicopter.
- Verified with two real clients on a private server (`--vice-demo=gun|victim|car|watch|heli`, frames with
  `--vice-shots=<dir>`): pulse fired, STUNNED received, victim held at 0.00 m for the stun; car entered and driven
  108 sim m in 6 s; remote client flagged the driver as in a car; helicopter boarded and climbed to 110 m.
  Stills `frames/30_*.png`, `31_*.png`, `32_*.png`; clip `frames/vice_exchange_3d_guns_cars_heli.mp4` (22 s).
- Not verified / limits: the shots in those runs were sent by the test driver through the same `fire()` path the patched
  trigger calls, not by a physical mouse click; the knocked-down figure still looks upright on other clients (tag and
  ring show the stun); the remote car model was detected but not confirmed on a frame; altitude is not networked, so a
  flying player appears on the ground to others and can still be stunned or captured from below; the server does not
  check Godot buildings or the browser map's parked cars for bridge clients; a few poses are rejected while speeding
  out of the district (2 in the car run, 7 in the helicopter run) and are simply resent.

## UDP fast path (native client only)

The WebSocket at `/bridge` stays the reliable channel: join, commands and their acks, events, the room state, wallet.
High-rate, loss-tolerant traffic goes over UDP when it is available:

| Direction | What | Rate | Format |
|---|---|---|---|
| client -> server | your pose (x, z, heading, flags: 1 in a car, 2 airborne) | up to 30 Hz (one per rendered frame at most) | `<32 hex token>{"q":seq,"x":..,"z":..,"h":..,"f":..}` |
| server -> client | positions of characters and NPCs, heat, status, activity, pending, stunned | 30 Hz | `{"q":seq,"t":ms,"hz":30,"c":[...],"n":[...]}` |

- Port: `UDP_PORT` if set, otherwise the HTTP port + 1 (server on 3001 -> UDP 3002). `VICE_UDP=0` turns the server side off.
  If the port cannot be bound the server logs it and everyone stays on WebSocket.
- Handshake: after a WebSocket join the server sends `{ t: "udp", port, token, hz }`. The token (16 random bytes) only
  selects that already-joined session; the client's address is learned from its first datagram and re-learned if it changes.
- Trust: a UDP pose goes through the same `player.pose` validation as a WebSocket pose (command schema, then the engine's
  bounds and speed checks). Unknown tokens, oversized (> 1200 bytes) or malformed datagrams and out-of-order sequence
  numbers are dropped; each session is rate-limited (90 datagrams/s).
- 30 Hz from a 10 Hz engine: pose-driven players are sampled as they are (their poses land in room state at once);
  NPCs and server-integrated walkers are dead-reckoned between engine ticks from their last measured velocity, for at
  most 120 ms and never across a jump. The client's existing smoothing interpolates toward each new state.
- Fallback: no datagram from the server within 2 s of the offer, or 1.5 s of silence later, and the client uses the
  10 Hz WebSocket ticks again without disconnecting. `--vice-no-udp` forces that. The HUD title shows
  `NET: UDP 30 Hz  loss n%  age n ms` or `NET: WS 10 Hz`; `/health` reports `udp: { enabled, port, clients }`.
- The browser-embedded WORLD view cannot use UDP (browsers have no UDP sockets); it always uses WebSocket.
- Measured 2026-11-14, headless client on this Mac, same scripted check run: 290 world-state updates over UDP
  (29 Hz, 0% loss) against 105 over WebSocket.

## Stream mode (server and game side only; not used by the browser UI)

`--vice-stream` makes a native client publish its own rendered frames (960 px wide JPEG, encoded on a worker thread, up
to 30 fps, frames skipped while the socket is draining) on `ws /stream?role=pub&token=<session token>`; the server relay
(`apps/server/src/bridge/stream.ts`) sends the newest frame to subscribers (`/stream?role=sub&token=...` for the same
player, who may also send keyboard and mouse input back; `/stream?role=sub&room=CODE` is view-only). It is inert unless
a client is started with the flag. A browser view for it was built and then shelved at the founder's request
(`.swarm/shelved/WorldStream.tsx`, `.swarm/oneworld-stream-web.patch`): the UI keeps its own 3D city. In one headed
Chrome run before it was shelved it showed 23 to 25 fps at 17 to 18 ms on this Mac. `/health` reports
`stream: { publishers, viewers, frames }`.
