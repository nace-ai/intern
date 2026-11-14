// Grid A* over the static city map, so officers walk around buildings instead of into them.
import { CITY_MAP, carRects, segmentHitsRect } from "@vice/contracts";
import type { Rect, Vec2 } from "@vice/contracts";

const CELL = 1; // metres per cell
const W = Math.ceil(CITY_MAP.width / CELL);
const H = Math.ceil(CITY_MAP.height / CELL);
const SQRT2 = Math.SQRT2;
const grids = new Map<number, Uint8Array>();

const paddedCache = new Map<number, Rect[]>();
function padded(pad: number): Rect[] {
  let r = paddedCache.get(pad);
  // Buildings and solid parked cars are obstacles for walking (cars never block line of sight; that is a separate test).
  if (!r) { r = [...CITY_MAP.buildings, ...carRects()].map((b) => ({ x0: b.x0 - pad, z0: b.z0 - pad, x1: b.x1 + pad, z1: b.z1 + pad })); paddedCache.set(pad, r); }
  return r;
}
/** 1 = blocked. A cell is free when its centre is inside the walkable area and outside every padded building. */
function grid(pad: number): Uint8Array {
  let g = grids.get(pad);
  if (g) return g;
  g = new Uint8Array(W * H);
  const rects = padded(pad); const w = CITY_MAP.walkable;
  for (let j = 0; j < H; j++) for (let i = 0; i < W; i++) {
    const x = (i + 0.5) * CELL, z = (j + 0.5) * CELL;
    const out = x < w.x0 || x > w.x1 || z < w.z0 || z > w.z1;
    g[j * W + i] = out || rects.some((r) => x > r.x0 && x < r.x1 && z > r.z0 && z < r.z1) ? 1 : 0;
  }
  grids.set(pad, g);
  return g;
}
const centre = (idx: number): Vec2 => ({ x: ((idx % W) + 0.5) * CELL, z: (Math.floor(idx / W) + 0.5) * CELL });

/** Nearest free cell to a point (spiral search), or -1. */
function nearestFree(g: Uint8Array, p: Vec2): number {
  const ci = Math.min(W - 1, Math.max(0, Math.floor(p.x / CELL))), cj = Math.min(H - 1, Math.max(0, Math.floor(p.z / CELL)));
  for (let r = 0; r < 12; r++) {
    let best = -1, bestD = Infinity;
    for (let j = cj - r; j <= cj + r; j++) for (let i = ci - r; i <= ci + r; i++) {
      if (i < 0 || j < 0 || i >= W || j >= H || Math.max(Math.abs(i - ci), Math.abs(j - cj)) !== r) continue;
      const idx = j * W + i;
      if (g[idx]) continue;
      const c = centre(idx); const d = Math.hypot(c.x - p.x, c.z - p.z);
      if (d < bestD) { bestD = d; best = idx; }
    }
    if (best >= 0) return best;
  }
  return -1;
}

/** True when the straight segment a->b stays clear of every building grown by `pad`. */
export function clearLine(a: Vec2, b: Vec2, pad = 0.6): boolean {
  return !padded(pad - 0.05).some((r) => segmentHitsRect(a, b, r));
}

/**
 * Shortest walkable route from `from` to `to` as a list of waypoints (excluding `from`).
 * Returns [] when already there, null when no route exists.
 */
export function findPath(from: Vec2, to: Vec2, pad = 0.6): Vec2[] | null {
  if (clearLine(from, to, pad)) return [{ x: to.x, z: to.z }];
  const g = grid(pad);
  const start = nearestFree(g, from), goal = nearestFree(g, to);
  if (start < 0 || goal < 0) return null;
  if (start === goal) return [{ x: to.x, z: to.z }];
  const gScore = new Float32Array(W * H).fill(Infinity);
  const came = new Int32Array(W * H).fill(-1);
  const closed = new Uint8Array(W * H);
  const gi = goal % W, gj = Math.floor(goal / W);
  const h = (idx: number) => { const dx = Math.abs((idx % W) - gi), dz = Math.abs(Math.floor(idx / W) - gj); return (dx + dz) + (SQRT2 - 2) * Math.min(dx, dz); };
  // Binary heap of [f, idx].
  const heapF: number[] = [], heapI: number[] = [];
  const push = (f: number, idx: number) => {
    let n = heapF.length; heapF.push(f); heapI.push(idx);
    while (n > 0) { const p = (n - 1) >> 1; if (heapF[p]! <= f) break; heapF[n] = heapF[p]!; heapI[n] = heapI[p]!; n = p; }
    heapF[n] = f; heapI[n] = idx;
  };
  const pop = (): number => {
    const top = heapI[0]!; const f = heapF.pop()!, idx = heapI.pop()!;
    const len = heapF.length;
    if (len > 0) {
      let n = 0;
      for (;;) {
        let c = 2 * n + 1; if (c >= len) break;
        if (c + 1 < len && heapF[c + 1]! < heapF[c]!) c++;
        if (heapF[c]! >= f) break;
        heapF[n] = heapF[c]!; heapI[n] = heapI[c]!; n = c;
      }
      heapF[n] = f; heapI[n] = idx;
    }
    return top;
  };
  gScore[start] = 0; push(h(start), start);
  let found = false;
  while (heapF.length) {
    const cur = pop();
    if (closed[cur]) continue;
    if (cur === goal) { found = true; break; }
    closed[cur] = 1;
    const ci = cur % W, cj = Math.floor(cur / W);
    for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
      if (!di && !dj) continue;
      const ni = ci + di, nj = cj + dj;
      if (ni < 0 || nj < 0 || ni >= W || nj >= H) continue;
      const nb = nj * W + ni;
      if (g[nb] || closed[nb]) continue;
      // No corner cutting: a diagonal step needs both orthogonal neighbours free.
      if (di && dj && (g[cj * W + ni] || g[nj * W + ci])) continue;
      const cost = gScore[cur]! + (di && dj ? SQRT2 : 1);
      if (cost < gScore[nb]!) { gScore[nb] = cost; came[nb] = cur; push(cost + h(nb), nb); }
    }
  }
  if (!found) return null;
  const cells: Vec2[] = [];
  for (let c = goal; c !== start && c >= 0; c = came[c]!) cells.push(centre(c));
  cells.reverse();
  // The real destination replaces the last cell centre when it is itself reachable in a straight line.
  const last = cells[cells.length - 1];
  if (last && clearLine(last, to, pad)) cells.push({ x: to.x, z: to.z });
  // String-pull: drop every waypoint that can be skipped with a clear straight line.
  const out: Vec2[] = [];
  let anchor = from, i = 0;
  while (i < cells.length) {
    let far = i;
    for (let k = cells.length - 1; k > i; k--) if (clearLine(anchor, cells[k]!, pad)) { far = k; break; }
    out.push(cells[far]!); anchor = cells[far]!; i = far + 1;
  }
  return out;
}
