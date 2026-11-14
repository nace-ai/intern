# CITY2D board

## CityView props (FINAL unless someone objects here) — `apps/web/src/scene/index.tsx`
Renderers are pure props-driven; they never import the store. WEB passes store slices in.

```ts
import type { AgentView, CharacterView, GameEvent, ListingView, MapView, MissionView, WorldTick } from "@vice/contracts";
export type SelectKind = "npc" | "character";
export interface CityViewProps {
  map: MapView;
  characters: CharacterView[];
  npcs: AgentView[];
  missions: MissionView[];
  listings: ListingView[];
  /** Latest 10 Hz tick; its positions override the (staler) view positions. */
  tick: WorldTick | null;
  /** Recent events, ascending serverSequence. Renderers diff by serverSequence to fire effects. */
  events: GameEvent[];
  youCharacterId: string | null;
  selectedId: string | null;
  onSelect(id: string | null, kind: SelectKind | null): void;
}
export function CityView(props: CityViewProps & { renderer?: "2d" | "3d" }): JSX.Element  // default "2d"
```
- WEB: `import { CityView } from "./scene"`; parent element must have a size (CityView fills 100%×100%).
- CITY3D: export `default function ThreeCity(props: CityViewProps)` from `apps/web/src/scene/three/ThreeCity.tsx`;
  `scene/index.tsx` already lazy-imports that path behind an error boundary and falls back to 2D if it throws or is missing.
- Keyboard movement / E-interact are WEB's job (commands); CITY2D only draws prompts.

## Status
- DONE 12:25. Files: scene/index.tsx (CityView, has3D), scene/types.ts (CityViewProps), scene/canvas/CanvasCity.tsx, scene/canvas/cityArt.ts.
- Verified visually with headless Chrome against a mock feed: scene/canvas/preview.html (+preview.tsx), dev-only, at /src/scene/canvas/preview.html[?spectator=1].
- Not yet run against the live server (it was not up); WEB mounts it with real store slices.
- 3D: index.tsx lazy-loads the default export of scene/three/index.tsx (or ThreeCity.tsx) and falls back to 2D on error.
- Effects are derived inside the renderer from `events` by serverSequence (history present at mount is not replayed).
