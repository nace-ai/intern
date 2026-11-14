import type { InstrumentId } from "@/orchestra/musicians";
import { INSTRUMENT_ART } from "./instrumentArt";

export type GlyphPlacement = { x: number; y: number; w: number; h: number; rotate: number };

// fractions of the face size, from the face box's top-left; rotate in degrees, clockwise
export const GLYPH_PLACEMENT: Record<InstrumentId, GlyphPlacement> & Partial<Record<string, GlyphPlacement>> = {
  violin: { x: 0.45, y: 0.45, w: 0.3, h: 0.85, rotate: 60 },
  pad_low: { x: -0.05, y: 0.5, w: 0.38, h: 0.95, rotate: 10 },
  bass: { x: -0.12, y: 0.35, w: 0.45, h: 1.15, rotate: 8 },
  flute: { x: 0.42, y: 0.6, w: 1.0, h: 0.1, rotate: -8 },
  kazoo: { x: 0.36, y: 0.58, w: 0.5, h: 0.18, rotate: -6 },
  kick: { x: 0.15, y: 0.8, w: 0.7, h: 0.7, rotate: 0 },
  snare: { x: 0.1, y: 0.88, w: 0.8, h: 0.53, rotate: 0 },
  hats: { x: -0.45, y: 0.6, w: 1.9, h: 0.45, rotate: 0 },
  bells: { x: 0.02, y: 0.9, w: 0.96, h: 0.4, rotate: 0 },
  pad_high: { x: 0.04, y: 0.92, w: 0.92, h: 0.27, rotate: 0 },
  synth: { x: 0.04, y: 0.92, w: 0.92, h: 0.27, rotate: 0 },
  viola: { x: 0.4, y: 0.42, w: 0.34, h: 0.95, rotate: 60 },
  harp: { x: -0.5, y: -0.1, w: 0.5, h: 1.3, rotate: 0 },
  oboe: { x: 0.44, y: 0.62, w: 0.12, h: 0.85, rotate: 0 },
  clarinet: { x: 0.43, y: 0.62, w: 0.14, h: 0.88, rotate: 4 },
  bassoon: { x: 0.2, y: -0.15, w: 0.16, h: 1.4, rotate: -25 },
  horn: { x: 0.45, y: 0.6, w: 0.75, h: 0.62, rotate: 0 },
  trumpet: { x: 0.45, y: 0.55, w: 1.0, h: 0.24, rotate: -4 },
  trombone: { x: 0.4, y: 0.6, w: 1.1, h: 0.22, rotate: 0 },
  tuba: { x: 0.1, y: 0.75, w: 0.8, h: 0.75, rotate: 0 },
  timpani: { x: 0.0, y: 0.85, w: 1.0, h: 0.65, rotate: 0 },
  piano: { x: -0.25, y: 0.92, w: 1.5, h: 0.32, rotate: 0 },
};

export function InstrumentGlyph({ instrument, size }: { instrument: string; size: number }) {
  const p = GLYPH_PLACEMENT[instrument];
  const Art = INSTRUMENT_ART[instrument];
  if (!p || !Art) {
    return (
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          left: 0.62 * size,
          top: 0.6 * size,
          fontSize: 0.34 * size,
          lineHeight: 1,
          pointerEvents: "none",
          zIndex: 10,
        }}
      >
        🎵
      </div>
    );
  }
  return (
    <div
      aria-hidden="true"
      style={{
        position: "absolute",
        left: p.x * size,
        top: p.y * size,
        width: p.w * size,
        height: p.h * size,
        transform: `rotate(${p.rotate}deg)`,
        transformOrigin: "center",
        pointerEvents: "none",
        zIndex: 10,
      }}
    >
      <Art />
    </div>
  );
}
