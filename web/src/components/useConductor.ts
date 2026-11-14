// The UI's state layer for a conductor (real or fake): subscribe, keep everything in one reducer.
import { useEffect, useReducer } from "react";
import type { InstrumentId, MoodName } from "@/orchestra/musicians";
import type { ConductorApi, ConductorEvent, Det, SectionInfo, Stats } from "./conductorTypes";

export type PanelState = {
  play: string;
  probs: Record<string, number>;
  direction: string;
  lastNote: string | null;
  pulse: number;
  /** last 8 play values (incl. "rest"/"hold"/drum patterns), newest last */
  recent: string[];
  det?: Det;
  instrument?: InstrumentId;
};
export const EMPTY_PANEL: PanelState = { play: "", probs: {}, direction: "", lastNote: null, pulse: 0, recent: [] };

export type StageState = {
  panels: Record<string, PanelState>;
  stats: Stats | null;
  section: SectionInfo;
  mood: { preset: MoodName; probs?: Record<MoodName, number>; text?: string } | null;
  loading: { loaded: number; total: number } | null;
  /** true once c.init() resolved */
  ready: boolean;
  running: boolean;
  paused: boolean;
  mode: "improv" | "score" | null;
  /** last error message; cleared 6 s after it was set */
  error: string | null;
};

const DRUM_PATTERN = /^[xX.]+$/;

const initialState: StageState = {
  panels: {},
  stats: null,
  section: null,
  mood: null,
  loading: null,
  ready: false,
  running: false,
  paused: false,
  mode: null,
  error: null,
};

type Action =
  | { type: "event"; event: ConductorEvent }
  | { type: "ready" }
  | { type: "initError"; message: string }
  | { type: "clearError" };

function applyEvent(state: StageState, e: ConductorEvent): StageState {
  switch (e.type) {
    case "beat": {
      const panels = { ...state.panels };
      for (const u of e.updates) {
        const prev = panels[u.musician] ?? EMPTY_PANEL;
        const isNote = u.play !== "rest" && u.play !== "hold" && !DRUM_PATTERN.test(u.play);
        const direction = u.direction !== undefined && u.direction.length > 0 ? u.direction : prev.direction;
        panels[u.musician] = {
          play: u.play,
          probs: u.probs,
          direction,
          lastNote: isNote ? u.play : prev.lastNote,
          pulse: prev.pulse + (isNote || /[xX]/.test(u.play) ? 1 : 0),
          recent: [...prev.recent, u.play].slice(-8),
          det: u.det ?? prev.det,
          instrument: u.instrument ?? prev.instrument,
        };
      }
      return { ...state, panels, stats: e.stats };
    }
    case "section":
      return { ...state, section: e.section };
    case "mood":
      return { ...state, mood: { preset: e.preset, probs: e.probs, text: e.text } };
    case "loading":
      return { ...state, loading: { loaded: e.loaded, total: e.total } };
    case "state": {
      // the live conductor's state event may not carry paused yet; read it if it does
      const pausedField = (e as { paused?: unknown }).paused;
      const paused = e.running && typeof pausedField === "boolean" ? pausedField : false;
      return { ...state, running: e.running, paused, mode: e.mode };
    }
    case "error":
      return { ...state, error: e.message };
  }
}

function reducer(state: StageState, action: Action): StageState {
  switch (action.type) {
    case "event":
      return applyEvent(state, action.event);
    case "ready":
      return { ...state, ready: true };
    case "initError":
      return { ...state, error: action.message };
    case "clearError":
      return { ...state, error: null };
  }
}

export function useConductor(c: ConductorApi): StageState {
  const [state, dispatch] = useReducer(reducer, initialState);

  useEffect(() => {
    const unsubscribe = c.subscribe((event) => dispatch({ type: "event", event }));
    let disposed = false;
    c.init()
      .then(() => {
        if (!disposed) dispatch({ type: "ready" });
      })
      .catch((err: unknown) => {
        if (!disposed) dispatch({ type: "initError", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [c]);

  useEffect(() => {
    if (state.error === null) return;
    const timer = window.setTimeout(() => dispatch({ type: "clearError" }), 6000);
    return () => window.clearTimeout(timer);
  }, [state.error]);

  return state;
}

/** Look up a panel by member id, falling back to instrument id (the pre-roster conductor sends those). */
export function panelFor(
  panels: Record<string, PanelState>,
  member: { id: string; instrument: string },
): PanelState | undefined {
  return panels[member.id] ?? panels[member.instrument];
}
