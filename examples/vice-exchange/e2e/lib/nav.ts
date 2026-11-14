// Routes an avatar along the open roads between CITY_MAP buildings and drives it with real key presses.
// The position is always read back from the server snapshot API, never assumed.
import type { Page } from "playwright-core";
import { CITY_MAP, dist, insideRect, segmentHitsRect } from "@vice/contracts";
import type { CharacterView, Rect, RoomSnapshot, Vec2 } from "@vice/contracts";
import { sleep, type Api } from "./api";

const inflate = (r: Rect, pad: number): Rect => ({ x0: r.x0 - pad, z0: r.z0 - pad, x1: r.x1 + pad, z1: r.z1 + pad });
const W = CITY_MAP.walkable;
const inMap = (p: Vec2, pad = 0.3): boolean => p.x >= W.x0 + pad && p.x <= W.x1 - pad && p.z >= W.z0 + pad && p.z <= W.z1 - pad;

export function segmentFree(a: Vec2, b: Vec2, pad: number): boolean {
  return !CITY_MAP.buildings.some((r) => segmentHitsRect(a, b, inflate(r, pad)));
}

/** Shortest route over a visibility graph whose nodes are the building corners pushed out by `clearance` metres. */
export function planPath(from: Vec2, to: Vec2, clearance = 1.7): Vec2[] {
  if (segmentFree(from, to, clearance - 0.4)) return [to];
  const nodes: Vec2[] = [from, to];
  for (const b of CITY_MAP.buildings) {
    const o = clearance;
    for (const c of [{ x: b.x0 - o, z: b.z0 - o }, { x: b.x1 + o, z: b.z0 - o }, { x: b.x1 + o, z: b.z1 + o }, { x: b.x0 - o, z: b.z1 + o }]) {
      if (inMap(c) && !CITY_MAP.buildings.some((r) => insideRect(c, r, 0.8))) nodes.push(c);
    }
  }
  const n = nodes.length;
  // Legs that start or end at the avatar/goal may hug a wall (the server keeps avatars 0.6 m off walls).
  const pad = (i: number, j: number): number => (i < 2 || j < 2 ? 0.5 : clearance - 0.4);
  const cost = new Array<number>(n).fill(Infinity), prev = new Array<number>(n).fill(-1), done = new Array<boolean>(n).fill(false);
  cost[0] = 0;
  for (;;) {
    let u = -1;
    for (let i = 0; i < n; i++) if (!done[i] && cost[i]! < Infinity && (u < 0 || cost[i]! < cost[u]!)) u = i;
    if (u < 0 || u === 1) break;
    done[u] = true;
    for (let v = 0; v < n; v++) {
      if (done[v] || v === u || !segmentFree(nodes[u]!, nodes[v]!, pad(u, v))) continue;
      const c = cost[u]! + dist(nodes[u]!, nodes[v]!);
      if (c < cost[v]!) { cost[v] = c; prev[v] = u; }
    }
  }
  if (cost[1] === Infinity) return [to]; // no route found: head straight for it and let the caller's stuck detection report it
  const out: Vec2[] = [];
  for (let v = 1; v > 0; v = prev[v]!) out.unshift(nodes[v]!);
  return out;
}
export function pathLength(from: Vec2, path: Vec2[]): number {
  let d = 0, p = from;
  for (const q of path) { d += dist(p, q); p = q; }
  return d;
}

export interface Walker {
  page: Page; api: Api; charId: string; label: string;
  /** Keys the harness is currently holding down in this page. */
  held: Set<string>;
  /** Called with every server sample taken while walking (used for the bounds/buildings invariant). */
  onSample?: (snap: RoomSnapshot) => void;
}
export interface WalkResult { ok: boolean; reason?: string; pos: Vec2; ms: number; samples: number; route: Vec2[] }

export async function setKeys(w: Walker, want: string[]): Promise<void> {
  for (const k of [...w.held]) if (!want.includes(k)) { await w.page.keyboard.up(k); w.held.delete(k); }
  for (const k of want) if (!w.held.has(k)) { await w.page.keyboard.down(k); w.held.add(k); }
}

/** Holds WASD in the page until the server reports the avatar within `tol` metres of `target`. */
export async function walkTo(w: Walker, target: Vec2, o: { tol?: number; timeoutMs?: number; clearance?: number } = {}): Promise<WalkResult> {
  const tol = o.tol ?? 1.0, timeoutMs = o.timeoutMs ?? 30000, t0 = Date.now();
  let path: Vec2[] | null = null, route: Vec2[] = [], samples = 0, stuck = 0;
  let ref: Vec2 | null = null, refAt = Date.now(), pos: Vec2 = { x: NaN, z: NaN };
  const done = async (ok: boolean, reason?: string): Promise<WalkResult> => { await setKeys(w, []); return { ok, reason, pos, ms: Date.now() - t0, samples, route }; };
  // Keyboard events only reach the game when no form control has focus.
  await w.page.evaluate("document.activeElement && document.activeElement !== document.body && document.activeElement.blur && document.activeElement.blur()").catch(() => {});
  for (;;) {
    let snap: RoomSnapshot;
    try { snap = await w.api.snapshot(); } catch (e) { return done(false, `snapshot API failed: ${(e as Error).message}`); }
    samples++;
    w.onSample?.(snap);
    const c: CharacterView | undefined = snap.characters.find((x) => x.id === w.charId);
    if (!c) return done(false, `character ${w.charId} is not in the snapshot`);
    pos = { x: c.x, z: c.z };
    if (c.status !== "ACTIVE" && c.status !== "WANTED") return done(false, `character is ${c.status}`);
    const d = dist(c, target);
    if (d <= tol) {
      await setKeys(w, []);
      await sleep(180); // let the last move intent settle, then confirm from the server again
      const again = (await w.api.snapshot()).characters.find((x) => x.id === w.charId);
      if (again) pos = { x: again.x, z: again.z };
      if (again && dist(again, target) <= tol + 0.7) return done(true);
      continue;
    }
    if (Date.now() - t0 > timeoutMs) return done(false, `timed out ${d.toFixed(1)} m from the waypoint`);
    if (!path) { path = planPath(c, target, o.clearance); route = [...path]; }
    while (path.length > 1 && dist(c, path[0]!) < 1.2) path.shift();
    const wp = path[0]!;
    const dx = wp.x - c.x, dz = wp.z - c.z, ax = Math.abs(dx), az = Math.abs(dz);
    const keys: string[] = [];
    if (ax > 0.3 && ax > 0.41 * az) keys.push(dx > 0 ? "d" : "a");
    if (az > 0.3 && az > 0.41 * ax) keys.push(dz > 0 ? "s" : "w");
    await setKeys(w, keys);
    if (path.length === 1 && d < 2.6) { await sleep(85); await setKeys(w, []); await sleep(140); } // short taps for the last metres
    else await sleep(45);
    // Stuck detection: no real progress for 1.6 s while keys are held.
    if (!ref || dist(ref, c) > 0.3) { ref = { x: c.x, z: c.z }; refAt = Date.now(); }
    else if (Date.now() - refAt > 1600) {
      stuck++;
      if (stuck > 5) return done(false, `stuck at (${c.x.toFixed(1)}, ${c.z.toFixed(1)}) heading for (${wp.x.toFixed(1)}, ${wp.z.toFixed(1)})`);
      // Slide off whatever is in the way, then plan again with more clearance.
      const side = stuck % 2 ? (ax >= az ? ["s"] : ["d"]) : (ax >= az ? ["w"] : ["a"]);
      await setKeys(w, side); await sleep(350); await setKeys(w, []);
      path = planPath(c, target, (o.clearance ?? 1.7) + 0.5 * stuck); route = [...path];
      ref = null; refAt = Date.now();
    }
  }
}

/** True when the point is inside the walkable rectangle and not inside any building footprint. */
export function positionLegal(p: Vec2): { ok: boolean; why?: string } {
  if (p.x < W.x0 - 1e-6 || p.x > W.x1 + 1e-6 || p.z < W.z0 - 1e-6 || p.z > W.z1 + 1e-6) return { ok: false, why: `outside the walkable area (${p.x}, ${p.z})` };
  const b = CITY_MAP.buildings.find((r) => insideRect(p, r, 0));
  return b ? { ok: false, why: `inside building ${b.label} at (${p.x}, ${p.z})` } : { ok: true };
}
