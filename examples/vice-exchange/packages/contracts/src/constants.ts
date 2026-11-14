import type { MapView, ParkedCar, Rect, Vec2 } from "./types";

export const RULES_VERSION = "h0.2";
export const DEFAULT_ROOM_CODE = "VICE-42";
export const DEFAULT_SEED = 42;
export const CREDITS_NOTICE = "V$ are fictional, nonredeemable game credits. No real money, no cash-out.";

/** Every tunable game rule. Proposed defaults, not a financial model. */
export const RULES = {
  tickHz: 10,
  maxPlayersPerRoom: 8,
  playerSpeed: 9,              // m/s, server-integrated
  maxPoseSpeed: 45,            // m/s ceiling accepted from player.pose clients (vehicles); above = rejected
  npcWalkSpeed: 3,
  policePursuitSpeed: 7.5,     // slower than a player on foot so escape is possible
  // Demo pacing: an officer who decides to investigate a second-hand report sets off after a dispatch delay and
  // searches at a slower pace, so the rumor chain and the market have time to react before any capture.
  // A direct sighting is never delayed.
  policeDispatchDelayMs: 10000,
  policeSearchPaceFactor: 0.5,  // x npcWalkSpeed while investigating
  perceptionRadius: 14,        // metres, needs line of sight
  missionInteractRadius: 2.5,
  captureRadius: 2.5,
  captureCooldownMs: 1500,
  missionRespawnMs: 20000,
  reconnectGraceMs: 30000,
  heat: {
    max: 100,
    wantedThreshold: 30,
    perMission: 12,
    perPoliceReport: 30,       // a witness report reaching police, or police directly seeing an incident
    perPoliceSighting: 6,      // police re-acquire a wanted character
    sightingCooldownMs: 5000,
    decayPerSecond: 1,         // only while no police NPC sees the character
    decayGraceMs: 6000,
  },
  notoriety: { perMission: 2 },
  price: {
    base: 100, perNotorietyPoint: 15, perMission: 60,
    // "The longer you live, the more you're worth": +2 every 5 s (24/min). PRD proposed 8/min; raised so it is visible in a 5-minute demo.
    survivalStepSeconds: 5, perSurvivalStep: 2,
    survivalFactor: { base: 0.95, perHeat: 0.007, perEscape: 0.02, min: 0.15, max: 0.98 },
    sentiment: { perNetUnit: 0.03, min: 0.75, max: 1.25, windowMs: 60000 },
    minActiveSpot: 1,
    houseSpread: 0.03,         // buys fill at spot (ask); sells fill at bid = max(1, floor(spot * 0.97))
    historyMaxPoints: 400,
  },
  trade: { qtyPerOrder: 1, maxSharesPerTicker: 10, cooldownMs: 2000, startingWalletHuman: 2000, startingWalletTrader: 2000 },
  // "The later someone hunts you down, the more they get": bounty tracks reference value, which only grows with survival and notoriety.
  bounty: { fractionOfReference: 0.5, min: 50 },
  // Every agent profits: the capturing officer earns the bounty; the witness whose report led to it earns a tip.
  profit: { witnessTipShare: 0.1, witnessTipMin: 5 },
  // Stun pulse: no damage, nobody dies. A hit freezes a human character or a police NPC for stunMs.
  // An outlaw's shot adds heat and is an incident nearby NPCs can witness.
  // The hunted agent: an autonomous outlaw that decides with the room's decision engine. Humans join the hunt around it.
  fugitive: { enabled: true, thinkEveryMs: 6000, respawnDelayMs: 12000 },
  weapon: { rangeM: 16, cooldownMs: 2500, stunMs: 3000, heatPerShot: 8, ammo: 6, coneDeg: 25 },
  // Hunter crews: crewmates near the capture split the bounty equally; stunning a crewmate shortly before capturing is a double-cross.
  crew: { maxMembers: 4, assistRadiusM: 25, betrayalWindowMs: 12000, tipFee: 40 },
  memory: {
    maxEntriesPerNpc: 30,
    ttlMs: { SIGHTING: 60000, INCIDENT: 180000, RUMOR: 120000, WANTED_BULLETIN: 180000, LISTING: 600000, POLICY: 86400000 },
    confidence: { DIRECT: 0.95, REPORT: 0.8, GOSSIP: 0.7, RELAY: 0.5, BULLETIN: 0.9, DOCUMENT: 0.99 },
  },
  messages: { deliveryDelayMs: 900 },
  decisions: {
    deadlineMs: { drex: 3000, meta: 8000 },
    npcCooldownMs: 3500,
    // Drex "confidence" is the margin between its top two labels (observed live). Only near coin-flips
    // (margin under 10 points) fall back to the safe action; otherwise Drex's own choice is applied.
    lowConfidenceThreshold: 0.1,
    maxQueue: 24,
    circuitBreakerFailures: 3,
    circuitBreakerOpenMs: 15000,
  },
} as const;

/** Original city. Plane is (x, z) in metres; z grows south. */
export const CITY_MAP: MapView = {
  name: "Coral Bay",
  width: 120, height: 80,
  walkable: { x0: 1, z0: 1, x1: 119, z1: 73 },
  water: [{ x0: 0, z0: 74, x1: 120, z1: 80 }],
  buildings: [
    { id: "b_precinct", label: "Precinct 9", x0: 8, z0: 8, x1: 26, z1: 22, height: 9, tone: "aqua" },
    { id: "b_arcade", label: "Flamingo Arcade", x0: 38, z0: 8, x1: 56, z1: 22, height: 7, tone: "pink" },
    { id: "b_alley_w", label: "Tide Lofts", x0: 68, z0: 8, x1: 76, z1: 22, height: 12, tone: "purple" },
    { id: "b_alley_e", label: "Gull Lofts", x0: 80, z0: 8, x1: 86, z1: 22, height: 10, tone: "teal" },
    { id: "b_motel", label: "Sunset Motel", x0: 98, z0: 8, x1: 112, z1: 22, height: 6, tone: "sand" },
    { id: "b_exchange", label: "The Exchange", x0: 8, z0: 34, x1: 26, z1: 48, height: 14, tone: "purple" },
    { id: "b_kiosk", label: "Plaza Kiosk", x0: 45, z0: 39, x1: 49, z1: 43, height: 3, tone: "pink" },
    { id: "b_mall", label: "Mirage Mall", x0: 68, z0: 34, x1: 86, z1: 48, height: 8, tone: "aqua" },
    { id: "b_court", label: "Palm Court", x0: 98, z0: 34, x1: 112, z1: 48, height: 11, tone: "sand" },
    { id: "b_warehouse", label: "Dockside Warehouse", x0: 8, z0: 60, x1: 26, z1: 70, height: 7, tone: "teal" },
    { id: "b_hotel", label: "Bayfront Hotel", x0: 68, z0: 60, x1: 86, z1: 70, height: 16, tone: "pink" },
    { id: "b_diner", label: "Lighthouse Diner", x0: 98, z0: 60, x1: 112, z1: 70, height: 5, tone: "aqua" },
  ],
  // Solid parked cars along the kerbs (2.75 m off the building line). Never on a job spot, spawn, NPC post,
  // patrol waypoint or road centre line; every road keeps well over 3 m clear between them.
  cars: [
    { id: "car_01", x: 12, z: 24.75, length: 4.4, width: 1.9, heading: 0, tone: "#c0222d" },
    { id: "car_02", x: 20, z: 24.75, length: 4.4, width: 1.9, heading: 0, tone: "#1b1c21" },
    { id: "car_03", x: 43, z: 24.75, length: 4.4, width: 1.9, heading: 0, tone: "#8a2fa0" },
    { id: "car_04", x: 51, z: 24.75, length: 4.4, width: 1.9, heading: 0, tone: "#1f56b8" },
    { id: "car_05", x: 72, z: 24.75, length: 4.4, width: 1.9, heading: 0, tone: "#e09a1f" },
    { id: "car_06", x: 102, z: 24.75, length: 4.4, width: 1.9, heading: 0, tone: "#a7b0ba" },
    { id: "car_07", x: 11, z: 31.25, length: 4.4, width: 1.9, heading: 0, tone: "#ece8de" },
    { id: "car_08", x: 23, z: 31.25, length: 4.4, width: 1.9, heading: 0, tone: "#2a9f88" },
    { id: "car_09", x: 72, z: 31.25, length: 4.4, width: 1.9, heading: 0, tone: "#c0222d" },
    { id: "car_10", x: 105, z: 31.25, length: 4.4, width: 1.9, heading: 0, tone: "#1b1c21" },
    { id: "car_11", x: 14, z: 50.75, length: 4.4, width: 1.9, heading: 0, tone: "#8a2fa0" },
    { id: "car_12", x: 22, z: 50.75, length: 4.4, width: 1.9, heading: 0, tone: "#1f56b8" },
    { id: "car_13", x: 73, z: 50.75, length: 4.4, width: 1.9, heading: 0, tone: "#e09a1f" },
    { id: "car_14", x: 103, z: 50.75, length: 4.4, width: 1.9, heading: 0, tone: "#a7b0ba" },
    { id: "car_15", x: 13, z: 57.25, length: 4.4, width: 1.9, heading: 0, tone: "#ece8de" },
    { id: "car_16", x: 74, z: 57.25, length: 4.4, width: 1.9, heading: 0, tone: "#2a9f88" },
    { id: "car_17", x: 82, z: 57.25, length: 4.4, width: 1.9, heading: 0, tone: "#c0222d" },
    { id: "car_18", x: 104, z: 57.25, length: 4.4, width: 1.9, heading: 0, tone: "#1b1c21" },
    { id: "car_19", x: 28.75, z: 13, length: 4.4, width: 1.9, heading: 90, tone: "#8a2fa0" },
    { id: "car_20", x: 35.25, z: 16, length: 4.4, width: 1.9, heading: 90, tone: "#1f56b8" },
    { id: "car_21", x: 65.25, z: 41, length: 4.4, width: 1.9, heading: 90, tone: "#e09a1f" },
    { id: "car_22", x: 95.25, z: 40, length: 4.4, width: 1.9, heading: 90, tone: "#a7b0ba" },
    { id: "car_23", x: 28.75, z: 45.6, length: 4.4, width: 1.9, heading: 90, tone: "#ece8de" },
    { id: "car_24", x: 65.25, z: 65, length: 4.4, width: 1.9, heading: 90, tone: "#2a9f88" },
    { id: "car_25", x: 28.75, z: 65, length: 4.4, width: 1.9, heading: 90, tone: "#c0222d" },
    { id: "car_26", x: 95.25, z: 65, length: 4.4, width: 1.9, heading: 90, tone: "#1b1c21" },
  ],
  districts: [
    { id: "d_marina", label: "Marina", x: 47, z: 64 },
    { id: "d_plaza", label: "Neon Plaza", x: 52, z: 46 },
    { id: "d_strip", label: "The Strip", x: 62, z: 28 },
    { id: "d_east", label: "East Shore", x: 92, z: 54 },
  ],
};

export const MISSION_SPOTS: ReadonlyArray<Vec2 & { label: string }> = [
  // Spots sit on open ground in BOTH worlds (browser map and the 3D city the bridge maps onto).
  { x: 47, z: 60, label: "Marked package at the dock" },
  { x: 52, z: 48, label: "Drop bag in Neon Plaza" },
  { x: 78, z: 23, label: "Stash at the loft alley mouth" },
  { x: 92, z: 54, label: "Courier case on East Shore" },
  { x: 32, z: 28, label: "Envelope at the Strip corner" },
];
export const SPAWN_POINTS: { OUTLAW: Vec2[]; HUNTER: Vec2[] } = {
  OUTLAW: [{ x: 60, z: 68 }, { x: 34, z: 68 }, { x: 90, z: 68 }, { x: 60, z: 4 }],
  HUNTER: [{ x: 92, z: 28 }, { x: 4, z: 28 }, { x: 116, z: 54 }, { x: 4, z: 54 }],
};

export function clamp(v: number, lo: number, hi: number): number { return Math.max(lo, Math.min(hi, v)); }
export function dist(a: Vec2, b: Vec2): number { return Math.hypot(a.x - b.x, a.z - b.z); }
export function insideRect(p: Vec2, r: Rect, pad = 0): boolean {
  return p.x > r.x0 - pad && p.x < r.x1 + pad && p.z > r.z0 - pad && p.z < r.z1 + pad;
}
/** Liang–Barsky: does segment a->b pass through the rectangle's interior? */
export function segmentHitsRect(a: Vec2, b: Vec2, r: Rect): boolean {
  let t0 = 0, t1 = 1;
  const dx = b.x - a.x, dz = b.z - a.z;
  const p = [-dx, dx, -dz, dz];
  const q = [a.x - r.x0, r.x1 - a.x, a.z - r.z0, r.z1 - a.z];
  for (let i = 0; i < 4; i++) {
    const pi = p[i]!, qi = q[i]!;
    if (pi === 0) { if (qi <= 0) return false; continue; }
    const t = qi / pi;
    if (pi < 0) { if (t > t1) return false; if (t > t0) t0 = t; }
    else { if (t < t0) return false; if (t < t1) t1 = t; }
  }
  return t0 < t1;
}
export function hasLineOfSight(a: Vec2, b: Vec2, map: MapView = CITY_MAP): boolean {
  return !map.buildings.some((r) => segmentHitsRect(a, b, r));
}
export function wantedLevel(heat: number): number { return Math.min(5, Math.floor(heat / 20)); }

/** Axis-aligned footprint of a parked car. */
export function carRect(c: ParkedCar): Rect {
  const hx = (c.heading === 0 ? c.length : c.width) / 2, hz = (c.heading === 0 ? c.width : c.length) / 2;
  return { x0: c.x - hx, z0: c.z - hz, x1: c.x + hx, z1: c.z + hz };
}
/** Footprints of every solid parked car on a map (empty for maps without cars). */
export function carRects(map: MapView = CITY_MAP): Rect[] { return (map.cars ?? []).map(carRect); }
