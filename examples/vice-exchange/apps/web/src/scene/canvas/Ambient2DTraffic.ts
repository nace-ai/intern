// Ambient2D helper: the road graph and the decorative traffic simulation. No canvas, no DOM, no game state.
// Everything is derived from the MapView and a seed, and advanced in fixed steps, so the state after N steps
// is identical on every client.
import type { MapView } from "@vice/contracts";

/** Fixed simulation step in seconds. */
export const TRAFFIC_STEP = 1 / 30;
export const SIG_GREEN = 0, SIG_AMBER = 1, SIG_RED = 2;
/** Vehicle kinds. */
export const KIND_SEDAN = 0, KIND_VAN = 1, KIND_SCOOTER = 2, KIND_SPORT = 3;

const PAD = 2;                       // pavement ring cityArt draws around every building
const BOX = 1.6;                     // stop line distance beyond the cross street's kerb
const ACC = 2.8, DECEL = 4.2, DECEL_MAX = 9, V_TURN = 3.5;
const DIR_X = [1, 0, -1, 0], DIR_Z = [0, 1, 0, -1]; // 0 east, 1 south, 2 west, 3 north (z grows south)

export interface Traffic {
  /** Road centre lines (metres), the same ones cityArt paints. */
  readonly xs: number[]; readonly zs: number[];
  /** Distance from each centre line to the nearest kerb on its low / high side. */
  readonly xNeg: number[]; readonly xPos: number[]; readonly zNeg: number[]; readonly zPos: number[];
  readonly land: number;
  readonly nodeCount: number;
  readonly nodeX: Float32Array; readonly nodeZ: Float32Array; readonly nodeSignal: Uint8Array;
  readonly linkCount: number;
  /** Per directed lane: end point (the stop line), travel direction 0..3, the node it arrives at, signal lamp spot. */
  readonly linkX1: Float32Array; readonly linkZ1: Float32Array; readonly linkDir: Uint8Array; readonly linkNode: Int16Array;
  /** 0 for a lane closed to traffic (it gets no stop line and no lamp). */
  readonly linkAlive: Uint8Array;
  readonly lampX: Float32Array; readonly lampZ: Float32Array;
  /** Static road paint, 4 numbers each (x0, z0, x1, z1). */
  readonly stopLines: Float32Array; readonly crosswalks: Float32Array;
  readonly carCount: number;
  /** Interpolated pose written by sample(). ang is the heading in radians (0 = east). */
  readonly x: Float32Array; readonly z: Float32Array; readonly ang: Float32Array;
  readonly speed: Float32Array;
  /** 0..1 brake light intensity. */
  readonly brake: Float32Array;
  /** -1 left indicator, 0 none, 1 right indicator. */
  readonly blink: Int8Array;
  readonly kind: Uint8Array; readonly style: Uint8Array; readonly halfLen: Float32Array; readonly halfWid: Float32Array;
  /** Seconds simulated so far. */
  time(): number;
  /** Advance exactly TRAFFIC_STEP seconds. */
  step(): void;
  /** Write x/z/ang for a point between the previous step (0) and the current one (1). */
  sample(alpha: number): void;
  /** Signal lamp shown to each lane (SIG_GREEN / SIG_AMBER / SIG_RED). The junctions are demand-actuated. */
  readonly lamp: Uint8Array;
}

function mulberry32(seed: number): () => number {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Road centre lines from the gaps between building blocks (the derivation cityArt uses for its lane paint). */
function centres(spans: Array<[number, number]>, max: number): number[] {
  spans.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const [a, b] of spans) {
    const last = merged[merged.length - 1];
    if (last && a <= last[1] + 5) last[1] = Math.max(last[1], b); else merged.push([a, b]);
  }
  const out: number[] = [];
  let prev = 0;
  for (const [a, b] of merged) { if (a - prev > 5) out.push((prev + a) / 2); prev = b; }
  if (max - prev > 5) out.push((prev + max) / 2);
  return out;
}

/**
 * `keepClear` is optional static geometry (for example fixed stall positions): a lane or junction movement
 * passing within 1.6 m of one of those spots is closed to traffic. It is evaluated once, here, and never again.
 */
export function createTraffic(map: MapView, seed: number, wantedCars: number, keepClear: ReadonlyArray<{ x: number; z: number }> = []): Traffic {
  const land = map.water.length ? Math.min(...map.water.map((r) => r.z0)) : map.height;
  const blocks = map.buildings.filter((b) => b.x1 - b.x0 > 6);
  let xs = centres(blocks.map((b) => [b.x0, b.x1] as [number, number]), map.width);
  let zs = centres(blocks.map((b) => [b.z0, b.z1] as [number, number]), land);

  // Kerb clearance on both sides of a centre line, measured over the stretch the traffic uses.
  const sideX = (x: number, za: number, zb: number): [number, number] => {
    let neg = x, pos = map.width - x;
    for (const b of map.buildings) {
      if (b.z1 + PAD <= za || b.z0 - PAD >= zb) continue;
      if (b.x1 + PAD <= x) neg = Math.min(neg, x - b.x1 - PAD);
      else if (b.x0 - PAD >= x) pos = Math.min(pos, b.x0 - PAD - x);
      else neg = pos = -1;
    }
    return [neg, pos];
  };
  const sideZ = (z: number, xa: number, xb: number): [number, number] => {
    let neg = z, pos = land - z;
    for (const b of map.buildings) {
      if (b.x1 + PAD <= xa || b.x0 - PAD >= xb) continue;
      if (b.z1 + PAD <= z) neg = Math.min(neg, z - b.z1 - PAD);
      else if (b.z0 - PAD >= z) pos = Math.min(pos, b.z0 - PAD - z);
      else neg = pos = -1;
    }
    return [neg, pos];
  };
  // Solid parked cars from the shared map: lanes squeeze in beside them and never touch their footprints.
  const solids = (map.cars ?? []).map((c) => {
    const hx = (c.heading === 90 ? c.width : c.length) / 2, hz = (c.heading === 90 ? c.length : c.width) / 2;
    return { x0: c.x - hx, z0: c.z - hz, x1: c.x + hx, z1: c.z + hz };
  });
  /** Room for a lane on either side of a centre line: the kerb clearance, narrowed by parked cars. */
  const laneX = (x: number, za: number, zb: number): number => {
    const s = sideX(x, za, zb);
    let c = Math.min(s[0], s[1]);
    for (const r of solids) { if (r.z1 <= za || r.z0 >= zb) continue; c = Math.min(c, r.x1 <= x ? x - r.x1 : r.x0 >= x ? r.x0 - x : -1); }
    return c;
  };
  const laneZ = (z: number, xa: number, xb: number): number => {
    const s = sideZ(z, xa, xb);
    let c = Math.min(s[0], s[1]);
    for (const r of solids) { if (r.x1 <= xa || r.x0 >= xb) continue; c = Math.min(c, r.z1 <= z ? z - r.z1 : r.z0 >= z ? r.z0 - z : -1); }
    return c;
  };
  const zLo = zs.length ? zs[0]! : 0, zHi = zs.length ? zs[zs.length - 1]! : 0;
  const xLo = xs.length ? xs[0]! : 0, xHi = xs.length ? xs[xs.length - 1]! : 0;
  // A road needs room for two lanes; anything narrower (or blocked outright) carries no traffic.
  xs = xs.filter((x) => laneX(x, zLo, zHi) >= 1.95);
  zs = zs.filter((z) => laneZ(z, xLo, xHi) >= 1.95);
  const nx = xs.length, nz = zs.length;
  const xNeg: number[] = [], xPos: number[] = [], zNeg: number[] = [], zPos: number[] = [];
  const xOff: number[] = [], zOff: number[] = [], hx: number[] = [], hz: number[] = [];
  for (const x of xs) {
    const s = sideX(x, zLo, zHi); const c = Math.min(s[0], s[1]);
    xNeg.push(s[0]); xPos.push(s[1]); xOff.push(Math.max(0.95, Math.min(1.8, laneX(x, zLo, zHi) - 1.0))); hx.push(c + BOX);
  }
  for (const z of zs) {
    const s = sideZ(z, xLo, xHi); const c = Math.min(s[0], s[1]);
    zNeg.push(s[0]); zPos.push(s[1]); zOff.push(Math.max(0.95, Math.min(1.8, laneZ(z, xLo, xHi) - 1.0))); hz.push(c + BOX);
  }

  // ---- nodes and directed lanes ----
  const nodeCount = nx * nz;
  const nodeX = new Float32Array(nodeCount), nodeZ = new Float32Array(nodeCount);
  const nodeSignal = new Uint8Array(nodeCount);
  const rand = mulberry32(seed ^ 0x51f15e);
  const maxLinks = Math.max(1, nodeCount * 4);
  const outLink = new Int16Array(maxLinks).fill(-1);   // [node * 4 + dir] -> lane leaving the node
  const lx0 = new Float32Array(maxLinks), lz0 = new Float32Array(maxLinks);
  const linkX1 = new Float32Array(maxLinks), linkZ1 = new Float32Array(maxLinks);
  const llen = new Float32Array(maxLinks);
  const linkDir = new Uint8Array(maxLinks), linkNode = new Int16Array(maxLinks);
  const lampX = new Float32Array(maxLinks), lampZ = new Float32Array(maxLinks);
  let linkCount = 0;
  const stop: number[] = [], walks: number[] = [];
  // 1. every candidate lane between neighbouring junctions
  interface Lane { n: number; n2: number; d: number; i: number; j: number; x0: number; z0: number; x1: number; z1: number; span: number; kerb: number; off: number; alive: boolean }
  const lanes: Lane[] = [];
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
    const n = j * nx + i;
    nodeX[n] = xs[i]!; nodeZ[n] = zs[j]!;
    for (let d = 0; d < 4; d++) {
      const i2 = i + DIR_X[d]!, j2 = j + DIR_Z[d]!;
      if (i2 < 0 || i2 >= nx || j2 < 0 || j2 >= nz) continue;
      const horiz = d % 2 === 0;
      const off = horiz ? zOff[j]! : xOff[i]!;
      const hA = horiz ? hx[i]! : hz[j]!, hB = horiz ? hx[i2]! : hz[j2]!;
      const span = Math.abs(horiz ? xs[i2]! - xs[i]! : zs[j2]! - zs[j]!) - hA - hB;
      if (span < 3) continue;
      const dx = DIR_X[d]!, dz = DIR_Z[d]!, rx = -dz, rz = dx;   // right-hand side of travel
      const x0 = xs[i]! + dx * hA + rx * off, z0 = zs[j]! + dz * hA + rz * off;
      const x1 = xs[i2]! - dx * hB + rx * off, z1 = zs[j2]! - dz * hB + rz * off;
      // a lane that would run over a keep-clear spot carries no traffic at all
      let alive = true;
      for (const p of keepClear) {
        const u = Math.max(0, Math.min(1, ((p.x - x0) * (x1 - x0) + (p.z - z0) * (z1 - z0)) / (span * span)));
        if (Math.hypot(p.x - (x0 + (x1 - x0) * u), p.z - (z0 + (z1 - z0) * u)) < 1.6) alive = false;
      }
      const side = horiz ? (rz > 0 ? zPos[j]! : zNeg[j]!) : (rx > 0 ? xPos[i]! : xNeg[i]!);
      lanes.push({ n, n2: j2 * nx + i2, d, i, j, x0, z0, x1, z1, span, kerb: side - off, off, alive });
    }
  }
  // 2. drop lanes that lead nowhere but straight back, until nothing changes
  for (let changed = true; changed;) {
    changed = false;
    for (const c of lanes) {
      if (!c.alive) continue;
      if (!lanes.some((o) => o.alive && o.n === c.n2 && o.d !== (c.d + 2) % 4)) { c.alive = false; changed = true; }
    }
  }
  // 3. the surviving lanes, their paint and their signal lamps
  const armCount = new Uint8Array(nodeCount);
  for (let n = 0; n < nodeCount; n++) for (let d = 0; d < 4; d++) {
    if (!lanes.some((c) => c.alive && ((c.n === n && c.d === d) || (c.n2 === n && (c.d + 2) % 4 === d)))) continue;
    armCount[n] = armCount[n]! + 1;
    // zebra crossing on this arm, just outside the junction
    const i = n % nx, j = Math.floor(n / nx), horiz = d % 2 === 0;
    const at = (horiz ? hx[i]! : hz[j]!) - BOX + 0.6;
    const cx = xs[i]! + DIR_X[d]! * at, cz = zs[j]! + DIR_Z[d]! * at;
    const lo = horiz ? zNeg[j]! : xNeg[i]!, hi = horiz ? zPos[j]! : xPos[i]!;
    if (horiz) walks.push(cx, cz - lo + 0.25, cx, cz + hi - 0.25); else walks.push(cx - lo + 0.25, cz, cx + hi - 0.25, cz);
  }
  for (const c of lanes) {
    if (!c.alive) continue;
    const L = linkCount++;
    const rx = -DIR_Z[c.d]!, rz = DIR_X[c.d]!;
    lx0[L] = c.x0; lz0[L] = c.z0; linkX1[L] = c.x1; linkZ1[L] = c.z1;
    llen[L] = c.span; linkDir[L] = c.d; linkNode[L] = c.n2;
    outLink[c.n * 4 + c.d] = L;
    stop.push(c.x1 - rx * (c.off - 0.2), c.z1 - rz * (c.off - 0.2), c.x1 + rx * (c.kerb - 0.2), c.z1 + rz * (c.kerb - 0.2));
    lampX[L] = Math.max(0.7, Math.min(map.width - 0.7, c.x1 + rx * (c.kerb + 0.5)));
    lampZ[L] = Math.max(0.7, Math.min(land - 0.7, c.z1 + rz * (c.kerb + 0.5)));
  }
  for (let n = 0; n < nodeCount; n++) nodeSignal[n] = armCount[n]! >= 3 ? 1 : 0;

  // ---- junction movements: straight on, right or left (never straight back) ----
  const succ = new Int16Array(maxLinks * 3).fill(-1);
  const ckind = new Uint8Array(maxLinks * 3);          // 0 straight, 1 right, 2 left
  const ccx = new Float32Array(maxLinks * 3), ccz = new Float32Array(maxLinks * 3), clen = new Float32Array(maxLinks * 3);
  for (let L = 0; L < linkCount; L++) {
    const n = linkNode[L]!, d = linkDir[L]!;
    const options = [outLink[n * 4 + d]!, outLink[n * 4 + ((d + 1) % 4)]!, outLink[n * 4 + ((d + 3) % 4)]!];
    for (let k = 0; k < 3; k++) {
      const M = options[k]!;
      if (M < 0) continue;
      const ax = linkX1[L]!, az = linkZ1[L]!, bx = lx0[M]!, bz = lz0[M]!;
      // straight across uses the midpoint as its control point, so one curve formula covers every movement
      const qx = k === 0 ? (ax + bx) / 2 : d % 2 === 0 ? bx : ax, qz = k === 0 ? (az + bz) / 2 : d % 2 === 0 ? az : bz;
      let len = 0, px = ax, pz = az, blocked = false;
      for (let s = 1; s <= 16; s++) {
        const u = s / 16, w = 1 - u;
        const x = w * w * ax + 2 * w * u * qx + u * u * bx, z = w * w * az + 2 * w * u * qz + u * u * bz;
        len += Math.hypot(x - px, z - pz); px = x; pz = z;
        for (const p of keepClear) if (Math.hypot(p.x - x, p.z - z) < 1.6) blocked = true;
        for (const r of solids) if (x > r.x0 - 1 && x < r.x1 + 1 && z > r.z0 - 1 && z < r.z1 + 1) blocked = true;
      }
      if (blocked) continue;                               // this movement would cross a keep-clear spot or a parked car
      succ[L * 3 + k] = M; ckind[L * 3 + k] = k;
      ccx[L * 3 + k] = qx; ccz[L * 3 + k] = qz; clen[L * 3 + k] = len;
    }
  }
  // lanes left without any way out are closed, and so is every movement into them
  const linkAlive = new Uint8Array(maxLinks);
  for (let L = 0; L < linkCount; L++) linkAlive[L] = 1;
  for (let changed = true; changed;) {
    changed = false;
    for (let L = 0; L < linkCount; L++) {
      if (!linkAlive[L]) continue;
      let any = false;
      for (let k = 0; k < 3; k++) {
        const M = succ[L * 3 + k]!;
        if (M >= 0 && !linkAlive[M]) succ[L * 3 + k] = -1;
        if (succ[L * 3 + k]! >= 0) any = true;
      }
      if (!any) { linkAlive[L] = 0; changed = true; }
    }
  }
  const open: number[] = [];
  for (let L = 0; L < linkCount; L++) if (linkAlive[L]) open.push(L);

  // ---- vehicles ----
  const carCount = open.length ? Math.max(0, Math.min(wantedCars, Math.floor(open.length * 1.2))) : 0;
  const link = new Int16Array(carCount), nextK = new Uint8Array(carCount), rngState = new Uint32Array(carCount);
  const s = new Float32Array(carCount), v = new Float32Array(carCount), v0 = new Float32Array(carCount);
  const cx = new Float32Array(carCount), cz = new Float32Array(carCount), cang = new Float32Array(carCount);
  const px = new Float32Array(carCount), pz = new Float32Array(carCount), pang = new Float32Array(carCount);
  const x = new Float32Array(carCount), z = new Float32Array(carCount), ang = new Float32Array(carCount);
  const brake = new Float32Array(carCount), blink = new Int8Array(carCount);
  const kind = new Uint8Array(carCount), style = new Uint8Array(carCount);
  const halfLen = new Float32Array(carCount), halfWid = new Float32Array(carCount);
  const held = new Float32Array(carCount), noRoom = new Uint8Array(carCount), go = new Uint8Array(carCount), holdNow = new Uint8Array(carCount);
  const lamp = new Uint8Array(maxLinks).fill(SIG_RED), lampSeen = new Float32Array(maxLinks).fill(-10);
  let time = 0;

  const pickNext = (a: number): void => {
    let r = rngState[a]!;
    r ^= r << 13; r ^= r >>> 17; r ^= r << 5;
    rngState[a] = r;
    const u = rngState[a]! / 4294967296, L = link[a]!;
    const w0 = succ[L * 3]! >= 0 ? 0.56 : 0, w1 = succ[L * 3 + 1]! >= 0 ? 0.3 : 0, w2 = succ[L * 3 + 2]! >= 0 ? 0.28 : 0;
    const pick = u * (w0 + w1 + w2);
    nextK[a] = pick < w0 ? 0 : pick < w0 + w1 ? 1 : 2;
    if (succ[L * 3 + nextK[a]!]! < 0) nextK[a] = w0 ? 0 : w1 ? 1 : 2;
  };
  const pose = (a: number): void => {
    const L = link[a]!, sa = s[a]!, len = llen[L]!, d = linkDir[L]!;
    if (sa <= len) {
      cx[a] = lx0[L]! + DIR_X[d]! * sa; cz[a] = lz0[L]! + DIR_Z[d]! * sa; cang[a] = d * (Math.PI / 2);
      return;
    }
    const kk = L * 3 + nextK[a]!, M = succ[kk]!;
    const u = Math.min(1, (sa - len) / Math.max(0.01, clen[kk]!)), w = 1 - u;
    const ax = linkX1[L]!, az = linkZ1[L]!, bx = lx0[M]!, bz = lz0[M]!;
    const qx = ccx[kk]!, qz = ccz[kk]!;
    cx[a] = w * w * ax + 2 * w * u * qx + u * u * bx;
    cz[a] = w * w * az + 2 * w * u * qz + u * u * bz;
    cang[a] = Math.atan2(w * (qz - az) + u * (bz - qz), w * (qx - ax) + u * (bx - qx));
  };
  const step = (): void => {
    const DT = TRAFFIC_STEP;
    for (let a = 0; a < carCount; a++) { px[a] = cx[a]!; pz[a] = cz[a]!; pang[a] = cang[a]!; }

    // 1. Is there room to clear the junction on the far side? (All decisions in 1 and 2 read one snapshot.)
    for (let a = 0; a < carCount; a++) {
      const L = link[a]!, n = linkNode[L]!, hl = halfLen[a]!;
      const toLine = llen[L]! - s[a]! - hl;                 // front bumper to the stop line
      noRoom[a] = 0;
      if (toLine <= -0.4 || !nodeSignal[n]) continue;
      const nl = succ[L * 3 + nextK[a]!]!;
      let tail = 1e9, need = hl * 2 + 1.8;
      for (let b = 0; b < carCount; b++) {
        const Lb = link[b]!;
        if (Lb === nl) { if (s[b]! - halfLen[b]! < tail) tail = s[b]! - halfLen[b]!; }
        else if (b !== a && linkNode[Lb] === n && succ[Lb * 3 + nextK[b]!] === nl && llen[Lb]! - s[b]! - halfLen[b]! <= -0.4) need += halfLen[b]! * 2 + 1.8;   // already crossing towards the same lane
      }
      if (tail < need) noRoom[a] = 1;
    }

    // 2. Demand-actuated junctions: a lane is released only when nothing that crosses its path is inside the
    //    junction, is already past its point of no return, or will reach the junction sooner.
    for (let a = 0; a < carCount; a++) {
      const L = link[a]!, n = linkNode[L]!, va = v[a]!;
      const toLine = llen[L]! - s[a]! - halfLen[a]!;
      let hold = false;
      const approaching = toLine > -0.4 && nodeSignal[n] === 1;
      // released last step and too close to stop: it keeps its right of way whatever happens
      const committed = approaching && go[a] === 1 && toLine < (va * va) / (2 * DECEL_MAX) + 0.6;
      if (approaching && !committed) {
        const impatient = held[a]! > 120;                   // two minutes: stop queueing politely
        if (noRoom[a] && !impatient) {
          hold = true;
          // boxed in for a while: take another way out instead of waiting for a jam to clear
          if (held[a]! > 6 && va < 0.3) { pickNext(a); held[a] = 3; }
        } else {
          const turn = ckind[L * 3 + nextK[a]!]!, dir = linkDir[L]!;
          const eta = Math.max(0, toLine) / Math.max(va, 1.5);
          for (let b = 0; b < carCount; b++) {
            const Lb = link[b]!;
            if (b === a || Lb === L || linkNode[Lb] !== n) continue;
            // head-on lanes can run together unless one of them turns left across the other
            if ((linkDir[Lb]! + dir) % 2 === 0 && turn !== 2 && ckind[Lb * 3 + nextK[b]!] !== 2) continue;
            const toLineB = llen[Lb]! - s[b]! - halfLen[b]!;
            if (toLineB <= -0.4) { hold = true; break; }                                             // inside the junction
            if (go[b] === 1 && toLineB < (v[b]! * v[b]!) / (2 * DECEL_MAX) + 0.6) { hold = true; break; }   // cannot stop any more
            if (noRoom[b] || impatient) continue;
            const etaB = Math.max(0, toLineB) / Math.max(v[b]!, 1.5);
            if (etaB < eta || (etaB === eta && b < a)) { hold = true; break; }
          }
        }
      }
      holdNow[a] = hold ? 1 : 0;
      held[a] = (hold || noRoom[a]) && va < 0.3 ? held[a]! + DT : 0;
      if (nodeSignal[n] && (toLine <= -0.4 || (!hold && toLine < 15))) lampSeen[L] = time;
    }
    for (let a = 0; a < carCount; a++) {
      const L = link[a]!;
      go[a] = !holdNow[a] && nodeSignal[linkNode[L]!] && llen[L]! - s[a]! - halfLen[a]! > -0.4 ? 1 : 0;
    }

    // 3. Move.
    for (let a = 0; a < carCount; a++) {
      const L = link[a]!, kk = L * 3 + nextK[a]!, len = llen[L]!, total = len + clen[kk]!;
      const sa = s[a]!, va = v[a]!, hl = halfLen[a]!, turn = ckind[kk]!;
      let vmax = v0[a]!;
      if (turn !== 0) {
        // ease down into a turn, hold the turning speed through it
        const ve = sa < len ? Math.sqrt(V_TURN * V_TURN + 2 * DECEL * (len - sa)) : V_TURN;
        if (ve < vmax) vmax = ve;
      }
      if (holdNow[a]) { const ve = Math.sqrt(2 * DECEL * Math.max(0, len - sa - hl - 0.3)); if (ve < vmax) vmax = ve; }
      // keep a gap to whoever is ahead on this lane or the next one
      const nl = succ[kk]!;
      let gap = 1e9, vl = 0;
      for (let b = 0; b < carCount; b++) {
        if (b === a) continue;
        const Lb = link[b]!;
        let g: number;
        if (Lb === L) { g = s[b]! - sa; if (g < 0 || (g === 0 && b < a)) continue; }
        else if (Lb === nl) g = total - sa + s[b]!;
        else continue;
        g -= hl + halfLen[b]!;
        if (g < gap) { gap = g; vl = v[b]!; }
      }
      if (gap < 1e8) {
        const free = gap - 1.4;
        const ve = free <= 0 ? 0 : Math.sqrt(vl * vl * 0.7 + 2 * DECEL * free);
        if (ve < vmax) vmax = ve;
      }
      const dv = vmax - va;
      let nv = va + (dv > 0 ? Math.min(dv, ACC * DT) : Math.max(dv, -DECEL_MAX * DT));
      if (nv < 0.02) nv = 0;
      const braking = dv < -0.3 || (nv < 0.3 && vmax < 0.6);
      brake[a] = brake[a]! + ((braking ? 1 : 0) - brake[a]!) * 0.35;
      v[a] = nv;
      let ns = sa + nv * DT;
      if (ns >= total) { ns -= total; link[a] = nl; pickNext(a); }
      s[a] = ns;
      const L2 = link[a]!, t2 = ckind[L2 * 3 + nextK[a]!]!;
      blink[a] = t2 !== 0 && ns > llen[L2]! - 14 ? (t2 === 1 ? 1 : -1) : 0;
      pose(a);
    }
    time += DT;
    for (let L = 0; L < linkCount; L++) { const age = time - lampSeen[L]!; lamp[L] = age < 0.12 ? SIG_GREEN : age < 1 ? SIG_AMBER : SIG_RED; }
  };

  const sample = (alpha: number): void => {
    for (let a = 0; a < carCount; a++) {
      x[a] = px[a]! + (cx[a]! - px[a]!) * alpha;
      z[a] = pz[a]! + (cz[a]! - pz[a]!) * alpha;
      let da = cang[a]! - pang[a]!;
      if (da > Math.PI) da -= Math.PI * 2; else if (da < -Math.PI) da += Math.PI * 2;
      ang[a] = pang[a]! + da * alpha;
    }
  };

  // ---- deterministic placement, then a short warm-up so queues and gaps look lived-in from the first frame ----
  for (let a = 0; a < carCount; a++) {
    const kr = rand();
    const k = kr < 0.58 ? KIND_SEDAN : kr < 0.72 ? KIND_VAN : kr < 0.85 ? KIND_SCOOTER : KIND_SPORT;
    kind[a] = k; style[a] = Math.floor(rand() * 256);
    halfLen[a] = k === KIND_VAN ? 2.55 : k === KIND_SCOOTER ? 1.0 : k === KIND_SPORT ? 2.2 : 2.12;
    halfWid[a] = k === KIND_VAN ? 0.95 : k === KIND_SCOOTER ? 0.36 : 0.9;
    v0[a] = (k === KIND_VAN ? 6.2 : k === KIND_SCOOTER ? 8.4 : k === KIND_SPORT ? 9.2 : 7.4) + rand() * 1.6;
    rngState[a] = (Math.floor(rand() * 4294967295) | 1) >>> 0;
    let L = 0, at = 0;
    for (let tries = 0; tries < 40; tries++) {
      L = open[Math.floor(rand() * open.length)]!; at = 2.4 + rand() * Math.max(0.1, llen[L]! - 4.8);
      let clear = true;
      for (let b = 0; b < a; b++) if (link[b] === L && Math.abs(s[b]! - at) < 7.5) clear = false;
      if (clear) break;
    }
    link[a] = L; s[a] = at; v[a] = v0[a]! * 0.6;
    pickNext(a); pose(a);
    px[a] = cx[a]!; pz[a] = cz[a]!; pang[a] = cang[a]!;
  }
  for (let i = 0; i < 360; i++) step();
  sample(1);

  return {
    xs, zs, xNeg, xPos, zNeg, zPos, land, nodeCount, nodeX, nodeZ, nodeSignal,
    linkCount, linkX1, linkZ1, linkDir, linkNode, linkAlive, lampX, lampZ,
    stopLines: new Float32Array(stop), crosswalks: new Float32Array(walks),
    carCount, x, z, ang, speed: v, brake, blink, kind, style, halfLen, halfWid,
    time: () => time, step, sample, lamp,
  };
}
