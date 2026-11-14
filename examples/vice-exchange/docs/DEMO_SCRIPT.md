# VICE EXCHANGE: five-minute live demo

Track (say it once, verbatim): "Swarm Intelligence: multi-agent systems. Build systems where multiple agents
coordinate, compete, or negotiate, with Drex making every agent's calls."

This script follows the code as of 14:00 PST on 2026-11-14. It has not been rehearsed end to end by a person. Steps
marked (tests only) are covered by the server test suite and have not been seen in a browser. The pitch text is in
[`PITCH.md`](PITCH.md).

## Before you start (two minutes)

1. Start the server and the web app: `npm run dev` (or `npm run start:server` plus `npm run dev:web`). Server on 3001,
   web on 5173, UDP for the native client on 3002.
2. Open the presenter's browser at `http://localhost:5173`, not the LAN address. Only the first browser on the server's
   own machine gets the director controls; anyone else needs `DEMO_DIRECTOR_SECRET` (join screen, or `&director=...`).
3. Window A, spectator and director: `http://localhost:5173/?nick=Judge&role=SPECTATOR&room=VICE-42&auto=1`
4. Window B, hunter in a crew: `http://localhost:5173/?nick=Rio&role=HUNTER&room=VICE-42&auto=1&crew=WOLVES`
5. Optional window C, second hunter in the same crew: `...?nick=Vex&role=HUNTER&room=VICE-42&auto=1&crew=WOLVES`
6. Check the header badge reads `DREX: LIVE` once the first decisions have resolved.
7. In window A open **Demo Director** and press the seeded reset so the room is clean. Browser zoom 100%.
8. The 3D city is the default view. Camera: drag to orbit, scroll to zoom, **C** cycles chase / high angle / top-down /
   cinematic, **V** resets. WASD follows the camera. Buttons **2D** and **WORLD** switch views.

## The five minutes

| Time | Do | The audience sees | Say | Proves |
|---|---|---|---|---|
| 0:00 | Window A, spectator, press **C** for the high angle | The city with eight labelled NPCs and Ryder; market panel with ticker `RYD-nn`; swarm graph | "Nine agents decide with Drex. One of them, Ryder, is a fugitive, and he is a stock. No human is playing yet." | Multiple agents; traders in a simulated market |
| 0:30 | Watch Ryder walk to the job marker and take it | Price jumps; a witness's decision card: options, probability bars, chosen action, latency, `DREX LIVE` | "A witness saw it. That card is one Drex call: the options, the probabilities, the answer." | Drex makes the call; NPCs think for themselves |
| 1:00 | Point at the swarm graph and the ticker tape | Report travels to police; wanted bulletin; price drops; trader cards (buy / hold / sell) | "The witness told the police. The bulletin went public. The traders each decided on their own, and the price moved." | Coordinate; compete |
| 1:30 | Click a police officer, then a trader | Inspector: what it knows with source and hops, request id, round trip, the Meta comparison line, SFPD citations with page numbers | "Each agent only knows what it saw or was told. The police decision cites a real police order that NDI parsed." | Private memory; NDI bonus |
| 2:00 | Window B, hunter Rio, crew WOLVES (HUD chip `CREW WOLVES`) | Trader cards for "Hunter crew WOLVES is now after RYD-nn" | "Now a human joins the hunt, in a crew. The traders hear a crew is after Ryder and decide again." | Humans join; traders react (tests only until seen here) |
| 2:30 | Rio runs at Ryder; press **F** when within 16 m | Tracer, `STUNNED`, HUD `F pulse 5/6`; if Ryder fires back, witness cards pop | "Guns stun for three seconds. An outlaw's shot is seen by the agents nearby, and they decide what to do about it." | Compete; the swarm reacts to players |
| 3:00 | Press **E** within 2.5 m of the stunned, wanted Ryder | Full-screen crash: ticker to V$0, captor, bounty and split, time survived | "Captured. The stock is exactly zero, trading is halted, and the crew splits the bounty." | Atomic capture; shared bounty (split caption: tests only) |
| 3:20 | Optional, with window C: Vex stuns Rio with **F**, then captures within 12 s | Crash caption `DOUBLE-CROSS`; `CREW_BETRAYED` in the timeline | "Or one hunter turns on the other and keeps it all. The informants hear about that too." | Compete inside a team (tests only) |
| 3:40 | Wait about 12 s | A new fugitive with a new ticker; the old ticker still listed, halted at V$0 | "A new fugitive appears. The old stock stays on the board at zero." | Respawn with history |
| 4:00 | Window A, **Demo Director**: run the experiment | Table: scripted vs Meta vs Drex, calls, median and p95 latency; shadow line "Drex vs Meta" | "Same seed, three ways. These are measured in this room, one run each." | Measured comparison |
| 4:40 | Open `http://localhost:3001/api/rooms/VICE-42/replay/verify` | `ok: true`, event count, no mismatches | "The whole log replays with zero model calls and matches the live state." | Replay |

If Ryder has not been reported after about 40 seconds, press the director's **Trigger incident**. Police take about
26 seconds to reach an outlaw who stands still (`.swarm/BRIEF.md`), so a hunter usually gets there first.

## Optional: the 3D world

- In the browser: the **WORLD** button (or `&view=world`) embeds the Godot client as the same player. First load took
  38 s and it has not been run in a real Chrome window with the GPU, so open it once before the demo or skip it.
- Native window (smoother): commands and keys are on `.swarm/board/GODOT.md`. Left mouse fires, **F** takes a car or
  boards the helicopter. Its HUD shows `NET: UDP 30 Hz` when the UDP stream is live. It ignores crews.
- Fallback clips: `integrations/ota-bridge/frames/vice_exchange_3d_full_chain.mp4` (43 s) and
  `vice_exchange_3d_guns_cars_heli.mp4` (22 s).

## When things go wrong

- **Drex slow or down.** The badge reads `DREX: FALLBACK`, decisions are labeled `fallback` with the error type, and
  the room keeps running. Say so. Never describe a fallback or a guardrail decision as Drex.
- **Switch to the scripted baseline.** Demo Director, mode `scripted`. The badge reads `SCRIPTED` and no provider is called.
- **Ryder is not there.** He joins 3 s after the room starts and 12 s after each capture. `FUGITIVE=0` turns him off;
  then use window B as an outlaw (`role=OUTLAW`) and do the job yourself with **E**.
- **A page freezes or loses the room.** Reload it; the same session re-attaches. After a server restart the room is new.
- **No director buttons.** You joined from a LAN address or were not first. Rejoin with the director secret.
- **Recorded fallback.** `demo/vice-exchange-demo.mp4` and `demo/vice-exchange-npcs.mp4`. Say it is a recording.
