import type { JSX } from "react";

const WOOD = "#b5651d";
const WOOD_D = "#8b4513";
const WOOD_L = "#d2873c";
const IVORY = "#f5efe9";
const BRASS = "#e0a43a";
const AMBER = "#ffb547";
const CORAL = "#ff6b4a";
const SILVER = "#c9c3bd";
const DARK = "#1e1512";
const COPPER = "#c8743a";
const EBONY = "#3b2a22";

function StringArt({ body, endpin, bow }: { body: string; endpin?: boolean; bow?: boolean }) {
  return (
    <svg viewBox="0 0 40 100" width="100%" height="100%" aria-hidden="true">
      <rect x="18" y="4" width="4" height="38" rx="2" fill={DARK} />
      <circle cx="20" cy="5" r="4" fill={WOOD_D} />
      <path d="M20 38 C6 38 4 52 12 58 C4 64 4 92 20 94 C36 92 36 64 28 58 C36 52 34 38 20 38 Z" fill={body} />
      <path d="M14 62 q2 6 0 12 M26 62 q-2 6 0 12" stroke={DARK} strokeWidth={1.5} fill="none" />
      <line x1="20" y1="8" x2="20" y2="86" stroke={IVORY} strokeWidth={0.8} />
      <rect x="15" y="78" width="10" height="3" fill={DARK} />
      {endpin ? <line x1="20" y1="94" x2="20" y2="100" stroke={SILVER} strokeWidth={2} /> : null}
      {bow ? <line x1="2" y1="74" x2="38" y2="34" stroke={IVORY} strokeWidth={1.5} strokeLinecap="round" /> : null}
    </svg>
  );
}

function Flute() {
  return (
    <svg viewBox="0 0 100 10" width="100%" height="100%" aria-hidden="true">
      <rect x="0" y="3" width="100" height="4" rx="2" fill={SILVER} />
      <ellipse cx="12" cy="5" rx="2" ry="1.2" fill={DARK} />
      {[40, 50, 60, 70, 80].map((cx) => (
        <circle key={cx} cx={cx} cy="5" r="1.4" fill={DARK} />
      ))}
    </svg>
  );
}

function Kazoo() {
  return (
    <svg viewBox="0 0 40 14" width="100%" height="100%" aria-hidden="true">
      <path d="M2 7 L30 3 Q38 7 30 11 Z" fill={BRASS} />
      <circle cx="22" cy="4" r="3" fill={AMBER} />
    </svg>
  );
}

function Harp() {
  return (
    <svg viewBox="0 0 50 100" width="100%" height="100%" aria-hidden="true">
      <path d="M8 96 L8 10 Q30 0 46 30 L14 96 Z" fill="none" stroke={WOOD_L} strokeWidth={4} strokeLinejoin="round" />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <line
          key={i}
          x1={14 + i * 5}
          y1={14 + i * 3}
          x2={14 + i * 5}
          y2={88 - i * 11}
          stroke={IVORY}
          strokeWidth={0.8}
        />
      ))}
    </svg>
  );
}

function WindTube({ bell }: { bell?: boolean }) {
  return (
    <svg viewBox="0 0 14 100" width="100%" height="100%" aria-hidden="true">
      <rect x="5" y="0" width="4" height="92" rx="2" fill={EBONY} stroke={SILVER} strokeWidth={0.6} />
      <rect x="6" y="0" width="2" height="6" fill={WOOD_L} />
      {[30, 42, 54, 66, 78].map((cy) => (
        <circle key={cy} cx="7" cy={cy} r="1.3" fill={SILVER} />
      ))}
      {bell ? (
        <path d="M3 86 L11 86 L14 100 L0 100 Z" fill={EBONY} stroke={SILVER} strokeWidth={0.6} />
      ) : (
        <path d="M4 90 L10 90 L11 100 L3 100 Z" fill={EBONY} />
      )}
    </svg>
  );
}

function BassDrum() {
  return (
    <svg viewBox="0 0 60 60" width="100%" height="100%" aria-hidden="true">
      <circle cx="30" cy="30" r="27" fill={DARK} stroke={WOOD} strokeWidth={4} />
      <circle cx="30" cy="30" r="21" fill={IVORY} opacity={0.9} />
      <circle cx="30" cy="30" r="8" fill={CORAL} />
    </svg>
  );
}

function Snare() {
  return (
    <svg viewBox="0 0 60 40" width="100%" height="100%" aria-hidden="true">
      <rect x="6" y="14" width="48" height="18" fill={CORAL} />
      <ellipse cx="30" cy="32" rx="24" ry="6" fill={CORAL} />
      <ellipse cx="30" cy="14" rx="24" ry="6" fill={IVORY} />
      <path d="M6 18 L14 30 L22 18 L30 30 L38 18 L46 30 L54 18" stroke={SILVER} strokeWidth={1.2} fill="none" />
      <line x1="8" y1="2" x2="26" y2="12" stroke={WOOD_L} strokeWidth={2.5} strokeLinecap="round" />
      <line x1="52" y1="2" x2="34" y2="12" stroke={WOOD_L} strokeWidth={2.5} strokeLinecap="round" />
    </svg>
  );
}

function Cymbals() {
  return (
    <svg viewBox="0 0 160 30" width="100%" height="100%" aria-hidden="true">
      {[22, 138].map((cx) => (
        <g key={cx}>
          <ellipse cx={cx} cy="18" rx="20" ry="5" fill={BRASS} />
          <ellipse cx={cx} cy="15" rx="5" ry="3" fill={AMBER} />
        </g>
      ))}
    </svg>
  );
}

function Xylophone() {
  return (
    <svg viewBox="0 0 90 40" width="100%" height="100%" aria-hidden="true">
      <line x1="2" y1="10" x2="88" y2="14" stroke={DARK} strokeWidth={2} />
      <line x1="2" y1="30" x2="88" y2="26" stroke={DARK} strokeWidth={2} />
      {[0, 1, 2, 3, 4, 5, 6].map((i) => (
        <rect
          key={i}
          x={6 + i * 12}
          y={4 + i * 1.5}
          width="9"
          height={32 - i * 3}
          rx="1.5"
          fill={i % 2 ? WOOD : WOOD_L}
        />
      ))}
      <line x1="78" y1="6" x2="88" y2="26" stroke={IVORY} strokeWidth={1.5} />
      <circle cx="78" cy="6" r="3" fill={CORAL} />
    </svg>
  );
}

function Bassoon() {
  return (
    <svg viewBox="0 0 16 100" width="100%" height="100%" aria-hidden="true">
      <rect x="5" y="6" width="7" height="92" rx="3" fill={WOOD_D} stroke={WOOD_L} strokeWidth={0.8} />
      <path d="M8 6 Q8 1 2 3" stroke={SILVER} strokeWidth={1.2} fill="none" />
      {[40, 52, 64].map((cy) => (
        <circle key={cy} cx="8.5" cy={cy} r="1.2" fill={SILVER} />
      ))}
    </svg>
  );
}

function Horn() {
  return (
    <svg viewBox="0 0 60 50" width="100%" height="100%" aria-hidden="true">
      <circle cx="24" cy="25" r="18" fill="none" stroke={BRASS} strokeWidth={4} />
      <circle cx="24" cy="25" r="10" fill="none" stroke={BRASS} strokeWidth={2} />
      <path d="M38 20 L56 6 L58 28 Z" fill={AMBER} />
      <line x1="10" y1="12" x2="4" y2="6" stroke={SILVER} strokeWidth={2} />
    </svg>
  );
}

function Trumpet() {
  return (
    <svg viewBox="0 0 100 24" width="100%" height="100%" aria-hidden="true">
      <rect x="0" y="10" width="8" height="4" fill={SILVER} />
      <rect x="8" y="10" width="70" height="4" fill={BRASS} />
      <rect x="20" y="14" width="40" height="8" rx="4" fill="none" stroke={BRASS} strokeWidth={2.5} />
      {[34, 42, 50].map((x) => (
        <rect key={x} x={x} y="3" width="5" height="9" fill={BRASS} />
      ))}
      <path d="M78 12 L100 1 L100 23 Z" fill={AMBER} />
    </svg>
  );
}

function Trombone() {
  return (
    <svg viewBox="0 0 120 24" width="100%" height="100%" aria-hidden="true">
      <rect x="0" y="14" width="80" height="2.5" fill={BRASS} />
      <rect x="0" y="20" width="80" height="2.5" fill={BRASS} />
      <rect x="0" y="14" width="3" height="8.5" fill={BRASS} />
      <rect x="40" y="6" width="52" height="2.5" fill={BRASS} />
      <rect x="62" y="15" width="6" height="2" fill={SILVER} />
      <path d="M90 7 L120 0 L120 15 Z" fill={AMBER} />
    </svg>
  );
}

function Tuba() {
  return (
    <svg viewBox="0 0 60 70" width="100%" height="100%" aria-hidden="true">
      <rect x="34" y="10" width="12" height="30" fill={BRASS} />
      <path d="M28 12 L52 12 L58 1 L22 1 Z" fill={AMBER} />
      <ellipse cx="28" cy="48" rx="20" ry="18" fill={BRASS} />
      <ellipse cx="28" cy="48" rx="11" ry="9" fill="none" stroke={AMBER} strokeWidth={3} />
      {[14, 20].map((x) => (
        <rect key={x} x={x} y="26" width="4" height="12" fill={SILVER} />
      ))}
    </svg>
  );
}

function Timpani() {
  return (
    <svg viewBox="0 0 70 52" width="100%" height="100%" aria-hidden="true">
      <path d="M3 14 Q8 46 35 48 Q62 46 67 14 Z" fill={COPPER} />
      <ellipse cx="35" cy="14" rx="32" ry="8" fill={IVORY} stroke={SILVER} strokeWidth={1.5} />
      <line x1="18" y1="44" x2="14" y2="52" stroke={SILVER} strokeWidth={2} />
      <line x1="52" y1="44" x2="56" y2="52" stroke={SILVER} strokeWidth={2} />
      <line x1="12" y1="4" x2="26" y2="11" stroke={WOOD_L} strokeWidth={2} />
      <circle cx="12" cy="4" r="3" fill={IVORY} />
    </svg>
  );
}

function Keyboard({ box, knobs }: { box: string; knobs?: boolean }) {
  return (
    <svg viewBox="0 0 90 26" width="100%" height="100%" aria-hidden="true">
      <rect x="0" y="0" width="90" height="26" rx="3" fill={box} />
      <rect x="4" y="12" width="82" height="11" fill={IVORY} />
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
        <rect key={i} x={9 + i * 10} y="12" width="4" height="6" fill={DARK} />
      ))}
      {knobs ? [14, 26, 38].map((cx) => <circle key={cx} cx={cx} cy="6" r="3" fill={AMBER} />) : null}
    </svg>
  );
}

function Piano() {
  return (
    <svg viewBox="0 0 140 26" width="100%" height="100%" aria-hidden="true">
      <rect x="0" y="0" width="140" height="26" rx="3" fill={EBONY} stroke={IVORY} strokeWidth={0.5} />
      <rect x="4" y="10" width="132" height="13" fill={IVORY} />
      {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map((i) => (
        <rect key={i} x={9 + i * 10} y="10" width="4" height="7" fill={DARK} />
      ))}
    </svg>
  );
}

export const INSTRUMENT_ART: Partial<Record<string, () => JSX.Element>> = {
  violin: () => <StringArt body={WOOD_L} bow />,
  pad_low: () => <StringArt body={WOOD} endpin />,
  bass: () => <StringArt body={WOOD_D} endpin />,
  flute: () => <Flute />,
  kazoo: () => <Kazoo />,
  kick: () => <BassDrum />,
  snare: () => <Snare />,
  hats: () => <Cymbals />,
  bells: () => <Xylophone />,
  pad_high: () => <Keyboard box={WOOD} />,
  synth: () => <Keyboard box={DARK} knobs />,
  viola: () => <StringArt body={WOOD} bow />,
  harp: () => <Harp />,
  oboe: () => <WindTube />,
  clarinet: () => <WindTube bell />,
  bassoon: () => <Bassoon />,
  horn: () => <Horn />,
  trumpet: () => <Trumpet />,
  trombone: () => <Trombone />,
  tuba: () => <Tuba />,
  timpani: () => <Timpani />,
  piano: () => <Piano />,
};
