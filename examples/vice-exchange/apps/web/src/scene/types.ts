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
