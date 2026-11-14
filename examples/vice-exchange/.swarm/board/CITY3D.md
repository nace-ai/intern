# CITY3D board

Status: DONE, viewable (13:12). Owns `apps/web/src/scene/three/**` only. Built to the FINAL `CityViewProps` in
`apps/web/src/scene/types.ts` (CITY2D's). Nothing outside my folder was edited.

## Exports
- `scene/three/index.tsx` and `scene/three/ThreeCity.tsx`: `export default function ThreeCity(props: CityViewProps)`.
  `scene/index.tsx` already lazy-loads it behind its error boundary; nothing else to wire.
- Throws during render when WebGL is unavailable, so the boundary falls back to the 2D canvas.
- Fills its parent (100% x 100%). Sends no commands; keyboard stays with WEB.
- No dependency on `@react-three/drei` any more (only `three` + `@react-three/fiber`).

## Behaviour
- Positions / heat / status / activity / pending come from `tick` when present, else from the views; motion is smoothed per frame.
- Effects are derived from `events` by `serverSequence` (history present at mount is not replayed; a lower top sequence = room reset):
  `AGENT_MESSAGE_SENT` -> arc sender->recipient coloured by kind; `BULLETIN_PUBLISHED` -> arcs issuer->each recipient;
  `CAPTURE_SUCCEEDED` -> burst + "CAPTURED · V$0" at the character.
- `youCharacterId` set and not captured -> third-person follow camera, fixed heading (north = away, so WASD matches 2D). It lifts over
  tall buildings, and a shader "keyhole" dissolves props/walls/vehicles standing between the lens and the player; anything about to
  touch the lens dissolves too. Otherwise (spectator / captured) -> orbit camera with a slow drift until the user drags.
- Click an NPC -> `onSelect(id, "npc")`; a human -> `onSelect(id, "character")`; empty space -> `onSelect(null, null)`.
- Labels (NPC role/name/thinking/pursuit, player name/ticker/price/WANTED, mission, capture) are ONE DOM overlay positioned per frame.
- QUALITY button (bottom-right, saved in localStorage `vice.city3d.quality`): HIGH = sun shadows + HDR bloom + up to 1.75x DPR;
  LOW = direct render. HIGH auto-drops to LOW after 5 s under 20 fps.
- Ambient life is cosmetic and client-only (never touches game state): 11 cars on right-hand circuits that brake for and steer
  around players/NPCs, 14 pedestrians on the sidewalks (no label, no ring: gameplay agents are the labelled ones), buzzing signs,
  swaying palms, sun glitter on the sea, a far-shore skyline.

## Startup (measured, headless Chrome on Metal, fresh profile each run)
- Before: shaders compiled synchronously (2.8 s main-thread block; 4.35 s total in program links), and the WebGL context was being
  evicted: `webglAvailable()` ran on every render and created a new context each time (151 contexts, 33 context losses in 17 s).
  Canvas blank at 6 s; city only visible by ~15 s. 23 React "synchronously unmount a root" errors + 1 uncaught `removeChild` error.
- After: in the real app first lit frame at 861 ms after navigation = 409 ms after the 3D view mounts (shader compile 350 ms, no
  main-thread task over 250 ms, 1 context, 0 losses, 0 console errors, ~60 fps at 1600x900 HIGH). A loading veil covers the gap.
- How: WebGL probed once and released; labels no longer use per-label React roots; materials grouped into 8 shader families and
  compiled in parallel with `compileAsync` while the loop is paused; facade textures stream in over the first frames; no dynamic
  lights (a light added mid-game recompiles every lit shader).
- `window.__city3d` = `{ mountMs, compiledMs, firstFrameMs, programs }` (ms since navigation) and a console.info line per boot.

## Known limits (not done / not verified)
- No SSAO. Shadows are PCF (three 0.187 dropped PCFSoft). One harmless console warning remains: "THREE.Clock deprecated" (R3F internal).
- Labels are not occluded by buildings (deliberate: they help find agents).
- Verified in headless Chrome only (Metal GPU), not yet on a visible browser window or the demo laptop.
- When a tall building sits directly south of the player the camera rises steeply and the player gets small.

## Dev tools (not imported by the app)
- `apps/web/src/scene/three/dev/index.html` + `harness.tsx`: mock props. Params: `spectate=1` (orbit), `at=x,z`, `still=1`,
  `q=high|low`, `noauto=1`, `capture=1`.
- `.swarm/repro/city3d/shot.mjs`: real-time capture driver, e.g.
  `OUT=<dir> PROBE=1 URL="http://localhost:5273/?nick=Sol&role=HUNTER&room=VICE-42&auto=1&view=3d" SHOTS=a:1000,b:7000 node .swarm/repro/city3d/shot.mjs`
  (`PROBE=1` prints WebGL timings, context losses, long tasks and `window.__city3d`; `TRACE=<text>` prints the stack of a console message).
