// UI-facing conductor types. Event shapes are a superset of conductor.ts's, so both the live conductor
// (instrument ids, no det yet) and the roster-aware one (member ids + instrument + det) fit.
import type {
  ConductorEvent as LiveEvent,
  SectionInfo as LiveSectionInfo,
  Stats as LiveStats,
} from "@/orchestra/conductor";
import type { InstrumentId, MoodName } from "@/orchestra/musicians";
import type { Directions } from "@/orchestra/ndi";
import type { Member } from "@/orchestra/roster";
import type { ScoreData } from "@/orchestra/score";

export type Stats = LiveStats;
export type SectionInfo = LiveSectionInfo;
export type DirectionsLike = Directions;
export type Det = { repeatRate: number; meanTop: number; distinct: number };
export type PanelUpdate = {
  musician: string; // member id (or instrument id from the pre-roster conductor)
  instrument?: InstrumentId;
  play: string;
  probs: Record<string, number>;
  direction?: string;
  det?: Det;
};
export type ConductorEvent =
  | Exclude<LiveEvent, { type: "beat" }>
  | { type: "beat"; updates: PanelUpdate[]; stats: Stats };

export interface ConductorApi {
  init(): Promise<void>;
  start(): Promise<void>;
  perform(): Promise<void>;
  stop(): void;
  pause?(): void; // optional until the live conductor has it
  resume?(): void;
  setMood(preset: MoodName): void;
  moodFromText(text: string): Promise<{ preset: MoodName; probs: Record<MoodName, number> }>;
  setDirections(d: Directions): void;
  setRoster?(members: Member[]): void; // optional until the live conductor has it
  setScore?(score: ScoreData | null): void;
  subscribe(fn: (e: ConductorEvent) => void): () => void;
}
