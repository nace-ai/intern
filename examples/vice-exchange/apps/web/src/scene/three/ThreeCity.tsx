import { useEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from "react";
import * as THREE from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { cameraYaw } from "./cameraYaw";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { segmentHitsRect, type MapView, type MessageKind, type NpcRole } from "@vice/contracts";
import type { CityViewProps } from "../types";
import { webglAvailable } from "./SceneBoundary";
import { World } from "./World";
import { Life } from "./Life";
import { CaptureBurst, Human, MessageArc, MissionBeacon, Npc, ROLE_COLOR, type Anchors } from "./Entities";
import { FX } from "./materials";

interface Arc { id: string; from: { x: number; z: number }; to: { x: number; z: number }; kind: MessageKind; startedAtMs: number; durationMs: number }
interface Burst { id: string; x: number; z: number; startedAtMs: number }
type Quality = "high" | "low";
type Phase = "boot" | "run" | "ready";
const ARC_MS = 1100, ARC_LINGER_MS = 500, BURST_MS = 2600, QUALITY_KEY = "vice.city3d.quality";

function loadQuality(): Quality { try { return localStorage.getItem(QUALITY_KEY) === "low" ? "low" : "high"; } catch { return "high"; } }
/** Startup timings (ms since page navigation), exposed for measurement as window.__city3d. */
const marks = { mountMs: 0, compiledMs: 0, firstFrameMs: 0, programs: 0 };
if (typeof window !== "undefined") (window as unknown as { __city3d: typeof marks }).__city3d = marks;
// React Three Fiber 9 still constructs THREE.Clock internally, which three r187 flags as deprecated on every mount.
// Nothing in this view can change that, so drop that one known line and pass everything else through untouched.
if (typeof console !== "undefined" && !(console.warn as { __city3d?: boolean }).__city3d) {
  const warn = console.warn.bind(console);
  const filtered = (...args: unknown[]) => { if (typeof args[0] === "string" && args[0].startsWith("THREE.Clock: This module has been deprecated")) return; warn(...args); };
  (filtered as { __city3d?: boolean }).__city3d = true;
  console.warn = filtered;
}

/**
 * Owns rendering. Boot: every shader program is compiled in parallel off the main thread while the loop is paused
 * (the loading veil is up), then frames start. HIGH = HDR scene -> bloom -> ACES; LOW = direct render.
 */
function Pipeline({ high, phase, onRun, onReady, onSlow }: { high: boolean; phase: Phase; onRun: () => void; onReady: () => void; onSlow: () => void }) {
  const { gl, scene, camera, size } = useThree();
  const composer = useMemo(() => {
    if (!high) return null;
    const c = new EffectComposer(gl);
    c.addPass(new RenderPass(scene, camera));
    c.addPass(new UnrealBloomPass(new THREE.Vector2(256, 256), 0.18, 0.55, 1.1));
    c.addPass(new OutputPass());
    return c;
  }, [gl, scene, camera, high]);
  useEffect(() => { if (composer) { composer.setPixelRatio(gl.getPixelRatio()); composer.setSize(size.width, size.height); } }, [composer, gl, size]);
  useEffect(() => () => composer?.dispose(), [composer]);

  const cb = useRef({ onRun, onReady, onSlow });
  cb.current = { onRun, onReady, onSlow };
  useEffect(() => {
    let alive = true;
    const done = () => { if (!alive) return; alive = false; marks.compiledMs = Math.round(performance.now()); marks.programs = gl.info.programs?.length ?? 0; cb.current.onRun(); };
    // Compile against the same output state real frames use (HDR target in HIGH), or three would compile everything twice.
    const prev = gl.getRenderTarget();
    try {
      gl.setRenderTarget(composer ? composer.renderTarget1 : null);
      gl.compileAsync(scene, camera).then(done, done);
    } catch { done(); }
    gl.setRenderTarget(prev);
    const guard = setTimeout(done, 6000);
    return () => { alive = false; clearTimeout(guard); };
  }, [gl, scene, camera, composer]);

  const stat = useRef({ frames: 0, t: 0, n: 0, slowFired: false });
  useEffect(() => { if (phase === "boot") stat.current = { frames: 0, t: 0, n: 0, slowFired: false }; }, [phase]);
  useFrame((_, dt) => {
    FX.time.value += dt;
    gl.getDrawingBufferSize(FX.ocRes.value);
    if (composer) composer.render(dt); else gl.render(scene, camera);
    const s = stat.current;
    if (++s.frames === 2) {
      marks.firstFrameMs = Math.round(performance.now());
      console.info(`[city3d] first frame ${marks.firstFrameMs - marks.mountMs} ms after mount (shader compile ${marks.compiledMs - marks.mountMs} ms, ${gl.info.programs?.length ?? "?"} programs)`);
      cb.current.onReady();
    }
    // HIGH drops to LOW after 5 s under 20 fps (never during the first seconds, when textures are still streaming in)
    if (high && s.frames > 240) { s.t += dt; s.n++; if (s.t >= 5) { if (!s.slowFired && s.n / s.t < 20 && !location.search.includes("noauto")) { s.slowFired = true; cb.current.onSlow(); } s.t = 0; s.n = 0; } }
  }, 1);
  return null;
}

const DEG = Math.PI / 180;
/** Camera presets: yaw 0 = lens south of the player looking north (so WASD matches the 2D view), pitch above the horizon, distance in metres. */
const CAM_PRESETS = [
  { name: "CHASE", yaw: 0, pitch: 28 * DEG, dist: 18.1 },
  { name: "HIGH ANGLE", yaw: 0, pitch: 55 * DEG, dist: 30 },
  { name: "TOP-DOWN", yaw: 0, pitch: 84 * DEG, dist: 46 },
  { name: "CINEMATIC", yaw: 62 * DEG, pitch: 9 * DEG, dist: 11 },
] as const;
const CAM_PITCH_MIN = 4 * DEG, CAM_PITCH_MAX = 86 * DEG, CAM_DIST_MIN = 5, CAM_DIST_MAX = 64;
/** The player's chosen angle. Module level on purpose: it survives respawn, quality switches and 2D/3D toggles. */
const camUser = { yaw: CAM_PRESETS[0].yaw as number, pitch: CAM_PRESETS[0].pitch as number, dist: CAM_PRESETS[0].dist as number, preset: 0, lastDragAt: 0 };
const LIFTS = [0, 10 * DEG, 20 * DEG, 32 * DEG, 46 * DEG, 62 * DEG];
/**
 * Third-person camera the player controls: drag to orbit (yaw and pitch, clamped above the ground), wheel or pinch to zoom,
 * C cycles presets, V or double-click resets behind the character. WASD is camera-relative: the client rotates the key
 * vector by this camera's yaw before sending the move intent (the server still integrates the position).
 * It still lifts over tall buildings and feeds the shader keyhole that dissolves props between the lens and the player.
 */
function FollowCamera({ id, fallback, anchors, map, onPreset }: { id: string; fallback: { x: number; z: number }; anchors: Anchors; map: MapView; onPreset: (name: string) => void }) {
  const { camera, gl } = useThree();
  const st = useRef({ focus: new THREE.Vector3(), yaw: camUser.yaw, pitch: camUser.pitch, dist: camUser.dist, lift: 0, vel: new THREE.Vector2(), first: true, v: new THREE.Vector3() });
  const presetCb = useRef(onPreset); presetCb.current = onPreset;
  useEffect(() => {
    const el = gl.domElement;
    let down: { x: number; y: number; id: number } | null = null, dragging = false;
    const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
    const apply = (i: number) => { const p = CAM_PRESETS[i]!; camUser.preset = i; camUser.yaw = p.yaw; camUser.pitch = p.pitch; camUser.dist = p.dist; presetCb.current(p.name); };
    const onDown = (e: PointerEvent) => { if (e.button !== 0 && e.button !== 2) return; down = { x: e.clientX, y: e.clientY, id: e.pointerId }; dragging = false; };
    const onMove = (e: PointerEvent) => {
      if (!down || e.pointerId !== down.id) return;
      const dx = e.clientX - down.x, dy = e.clientY - down.y;
      if (!dragging) { if (Math.hypot(dx, dy) < 5) return; dragging = true; try { el.setPointerCapture(e.pointerId); } catch { /* not capturable */ } el.style.cursor = "grabbing"; }
      down.x = e.clientX; down.y = e.clientY;
      camUser.yaw -= dx * 0.0058;
      camUser.pitch = Math.min(CAM_PITCH_MAX, Math.max(CAM_PITCH_MIN, camUser.pitch + dy * 0.0046));
      camUser.preset = -1; presetCb.current("FREE");
    };
    const onUp = (e: PointerEvent) => {
      if (!down || e.pointerId !== down.id) return;
      if (dragging) { camUser.lastDragAt = performance.now(); try { el.releasePointerCapture(e.pointerId); } catch { /* already released */ } el.style.cursor = ""; }
      down = null; dragging = false;
    };
    // A drag must never count as a click (no NPC selection, no deselect): swallow the click that ends it.
    const onClickCapture = (e: MouseEvent) => { if (performance.now() - camUser.lastDragAt < 250 && (e.target === el || el.parentElement?.contains(e.target as Node))) { e.stopPropagation(); e.preventDefault(); } };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const k = Math.exp((e.ctrlKey ? e.deltaY * 0.012 : e.deltaY * 0.0016));
      camUser.dist = Math.min(CAM_DIST_MAX, Math.max(CAM_DIST_MIN, camUser.dist * k));
      if (camUser.preset !== -1) { camUser.preset = -1; presetCb.current("FREE"); }
    };
    const onDbl = () => apply(0);
    const onCtx = (e: MouseEvent) => e.preventDefault();
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === "c") apply((camUser.preset + 1 + CAM_PRESETS.length) % CAM_PRESETS.length);
      else if (k === "v") apply(0);
    };
    el.addEventListener("pointerdown", onDown); window.addEventListener("pointermove", onMove); window.addEventListener("pointerup", onUp); window.addEventListener("pointercancel", onUp);
    window.addEventListener("click", onClickCapture, true); el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("dblclick", onDbl); el.addEventListener("contextmenu", onCtx); window.addEventListener("keydown", onKey);
    presetCb.current(camUser.preset >= 0 ? CAM_PRESETS[camUser.preset]!.name : "FREE");
    return () => {
      cameraYaw.active = false;
      el.removeEventListener("pointerdown", onDown); window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerup", onUp); window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("click", onClickCapture, true); el.removeEventListener("wheel", onWheel);
      el.removeEventListener("dblclick", onDbl); el.removeEventListener("contextmenu", onCtx); window.removeEventListener("keydown", onKey); el.style.cursor = "";
    };
  }, [gl]);
  useFrame(({ clock }, dtRaw) => {
    const s = st.current, dt = Math.min(dtRaw, 0.1);
    const body = anchors.get(id)?.obj.position;
    const tx = body?.x ?? fallback.x, tz = body?.z ?? fallback.z;
    const k = s.first ? 1 : 1 - Math.exp(-dt * 5), px = s.focus.x, pz = s.focus.z;
    s.focus.x += (tx - px) * k; s.focus.z += (tz - pz) * k;
    if (!s.first && dt > 0) { s.vel.x += ((s.focus.x - px) / dt - s.vel.x) * 0.06; s.vel.y += ((s.focus.z - pz) / dt - s.vel.y) * 0.06; }
    // damped approach to the player's chosen angle (yaw unwrapped so it takes the short way round)
    const kc = s.first ? 1 : 1 - Math.exp(-dt * 7);
    let dy = camUser.yaw - s.yaw; dy -= Math.round(dy / (Math.PI * 2)) * Math.PI * 2;
    if (Math.abs(camUser.yaw - s.yaw - dy) > 1e-6) camUser.yaw = s.yaw + dy;
    s.yaw += dy * kc; s.pitch += (camUser.pitch - s.pitch) * kc; s.dist += (camUser.dist - s.dist) * kc;
    cameraYaw.value = s.yaw; cameraYaw.active = true; // WASD is camera-relative: the keyboard handler rotates by this
    // lift over tall buildings between the lens and the player: raise the pitch until the sight line is clear
    let lift = LIFTS[LIFTS.length - 1]!;
    for (const add of LIFTS) {
      const p = Math.min(CAM_PITCH_MAX, s.pitch + add), h = Math.sin(p) * s.dist, r = Math.cos(p) * s.dist;
      const lens = { x: s.focus.x + Math.sin(s.yaw) * r, z: s.focus.z + Math.cos(s.yaw) * r };
      if (!map.buildings.some((b) => b.height > h * 0.5 && segmentHitsRect({ x: s.focus.x, z: s.focus.z }, lens, { x0: b.x0 - 1.2, z0: b.z0 - 1.2, x1: b.x1 + 1.2, z1: b.z1 + 1.2 }))) { lift = add; break; }
    }
    s.lift += (lift - s.lift) * (s.first ? 1 : 1 - Math.exp(-dt * 2.4));
    s.first = false;
    const pitch = Math.min(CAM_PITCH_MAX, s.pitch + s.lift), h = Math.max(1.2, Math.sin(pitch) * s.dist), r = Math.cos(pitch) * s.dist;
    const sx = Math.sin(s.yaw), cz = Math.cos(s.yaw);
    const lead = Math.min(1, s.vel.length() / 9), sway = Math.sin(clock.elapsedTime * 0.45) * 0.1;
    camera.position.set(s.focus.x + sx * r + s.vel.x * 0.18 + sway, h, s.focus.z + cz * r);
    const ahead = 10 * Math.cos(pitch) * Math.cos(pitch); // look past the player at street level, straight down from above
    camera.lookAt(s.focus.x - sx * ahead + s.vel.x * 0.3, 2.1, s.focus.z - cz * ahead + s.vel.y * 0.25);
    const pc = camera as THREE.PerspectiveCamera, fov = 56 + lead * 5;
    if (Math.abs(pc.fov - fov) > 0.05) { pc.fov += (fov - pc.fov) * 0.08; pc.updateProjectionMatrix(); }
    camera.updateMatrixWorld();
    s.v.set(tx, 1.2, tz).applyMatrix4(camera.matrixWorldInverse);
    FX.ocDepth.value = -s.v.z;
    s.v.set(tx, 1.2, tz).project(camera);
    FX.ocNdc.value.set(s.v.x, s.v.y);
  });
  return null;
}

/** Spectators and captured players: free orbit with a slow cinematic drift until they take control. */
function Orbit({ map }: { map: MapView }) {
  const { camera, gl } = useThree();
  const controls = useRef<OrbitControls | null>(null);
  useEffect(() => {
    camera.position.set(map.width / 2 + 62, 40, map.height / 2 + 74);
    const c = new OrbitControls(camera, gl.domElement);
    c.target.set(map.width / 2 - 4, 3, map.height / 2 - 8);
    c.maxPolarAngle = Math.PI / 2.06; c.minDistance = 14; c.maxDistance = 240; c.enableDamping = true; c.dampingFactor = 0.08;
    c.autoRotate = true; c.autoRotateSpeed = 0.45;
    const stop = () => { c.autoRotate = false; };
    c.addEventListener("start", stop);
    c.update();
    controls.current = c;
    const pc = camera as THREE.PerspectiveCamera;
    pc.fov = 46; pc.updateProjectionMatrix();
    FX.ocDepth.value = 0;
    return () => { c.removeEventListener("start", stop); c.dispose(); controls.current = null; };
  }, [camera, gl, map]);
  useFrame(() => { controls.current?.update(); FX.ocDepth.value = 0; });
  return null;
}

/** Positions every DOM label over its 3D anchor. One overlay, plain DOM writes: no per-label React roots. */
function LabelProjector({ anchors, els }: { anchors: Anchors; els: RefObject<Map<string, HTMLDivElement>> }) {
  const { camera, size } = useThree();
  const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    camera.updateMatrixWorld();
    for (const [key, el] of els.current) {
      const a = anchors.get(key);
      let show = false, dist = 0;
      if (a) {
        v.set(a.obj.position.x, a.obj.position.y + a.h, a.obj.position.z);
        dist = v.distanceTo(camera.position);
        v.project(camera);
        show = v.z > -1 && v.z < 1 && Math.abs(v.x) < 1.1 && Math.abs(v.y) < 1.15 && dist < 170;
      }
      if (!show) { if (el.style.visibility !== "hidden") el.style.visibility = "hidden"; continue; }
      const scale = Math.max(0.6, Math.min(1.08, 30 / dist));
      el.style.visibility = "visible";
      el.style.opacity = dist > 120 ? String(Math.max(0, 1 - (dist - 120) / 50)) : "1";
      el.style.zIndex = String(Math.max(1, 3000 - Math.round(dist * 10)));
      el.style.transform = `translate3d(${((v.x * 0.5 + 0.5) * size.width).toFixed(1)}px,${((-v.y * 0.5 + 0.5) * size.height).toFixed(1)}px,0) translate(-50%,-100%) scale(${scale.toFixed(3)})`;
    }
  });
  return null;
}

const FONT = "'Avenir Next','Segoe UI',Helvetica,Arial,sans-serif";
const labelStyle: CSSProperties = {
  position: "absolute", left: 0, top: 0, visibility: "hidden", transformOrigin: "50% 100%", willChange: "transform",
  whiteSpace: "nowrap", textAlign: "center", font: `600 11px/1.25 ${FONT}`, color: "#fff", padding: "2px 7px", borderRadius: 6,
  background: "rgba(14,8,28,0.74)", border: "1px solid rgba(255,255,255,0.2)", textShadow: "0 1px 2px #000", backdropFilter: "blur(2px)",
};

export default function ThreeCity(props: CityViewProps) {
  const { map, characters, npcs, missions, listings, tick, events, youCharacterId, selectedId, onSelect } = props;
  if (!webglAvailable()) throw new Error("WebGL unavailable"); // the scene boundary falls back to 2D
  const [quality, setQualityState] = useState<Quality>(loadQuality);
  const [phase, setPhase] = useState<Phase>("boot");
  const [veilGone, setVeilGone] = useState(false);
  const [camName, setCamName] = useState("CHASE");
  const mounted = useRef(false);
  if (!mounted.current) { mounted.current = true; marks.mountMs = Math.round(performance.now()); marks.firstFrameMs = 0; }
  const setQuality = (q: Quality) => {
    if (q === quality) return;
    marks.mountMs = Math.round(performance.now());
    setPhase("boot"); setVeilGone(false); setQualityState(q);
    try { localStorage.setItem(QUALITY_KEY, q); } catch { /* private mode */ }
  };
  const high = quality === "high";
  useEffect(() => { if (phase !== "ready") return; const t = setTimeout(() => setVeilGone(true), 450); return () => clearTimeout(t); }, [phase]);

  // Freshest positions: tick overrides the views.
  const chars = useMemo(() => {
    const live = new Map(tick?.characters.map((c) => [c.id, c]) ?? []);
    return characters.filter((c) => c.status !== "ARCHIVED").map((c) => {
      const t = live.get(c.id);
      return t ? { ...c, x: t.x, z: t.z, heat: t.heat, status: t.status } : c;
    });
  }, [characters, tick]);
  const agents = useMemo(() => {
    const live = new Map(tick?.npcs.map((n) => [n.id, n]) ?? []);
    return npcs.map((n) => {
      const t = live.get(n.id);
      return { id: n.id, name: n.name, role: n.role, x: t?.x ?? n.x, z: t?.z ?? n.z, activity: t?.activity ?? n.activity, thinking: t?.pending ?? n.pendingDecisionId !== null };
    });
  }, [npcs, tick]);
  const priceByTicker = useMemo(() => new Map(listings.map((l) => [l.ticker, l.spotPrice])), [listings]);

  const anchors = useRef<Anchors>(new Map()).current;
  const labelEls = useRef(new Map<string, HTMLDivElement>());
  const refFns = useRef(new Map<string, (el: HTMLDivElement | null) => void>());
  const labelRef = (key: string) => {
    let fn = refFns.current.get(key);
    if (!fn) { fn = (el) => { if (el) labelEls.current.set(key, el); else { labelEls.current.delete(key); refFns.current.delete(key); } }; refFns.current.set(key, fn); }
    return fn;
  };

  // Effects derived from the event stream (diffed by serverSequence; history on mount is not replayed).
  const [arcs, setArcs] = useState<Arc[]>([]);
  const [bursts, setBursts] = useState<Burst[]>([]);
  const lastSeq = useRef<number | null>(null);
  const posRef = useRef(new Map<string, { x: number; z: number }>());
  posRef.current = new Map([...chars.filter((c) => c.status !== "CAPTURED").map((c) => [c.id, { x: c.x, z: c.z }] as const), ...agents.map((a) => [a.id, { x: a.x, z: a.z }] as const)]);
  useEffect(() => {
    const top = events.length ? events[events.length - 1]!.serverSequence : 0;
    if (lastSeq.current === null || top < lastSeq.current) { lastSeq.current = top; return; } // first load or room reset
    const since = lastSeq.current;
    lastSeq.current = top;
    const now = Date.now(), newArcs: Arc[] = [], newBursts: Burst[] = [];
    const at = (id: string) => anchors.get(id)?.obj.position ?? posRef.current.get(id);
    const arc = (id: string, fromId: string, toId: string, kind: MessageKind) => {
      const from = at(fromId), to = at(toId);
      if (from && to && fromId !== toId) newArcs.push({ id, from: { x: from.x, z: from.z }, to: { x: to.x, z: to.z }, kind, startedAtMs: now, durationMs: ARC_MS });
    };
    for (const e of events) {
      if (e.serverSequence <= since) continue;
      if (e.type === "AGENT_MESSAGE_SENT") { const p = e.payload as { messageId: string; senderId: string; recipientId: string; kind: MessageKind }; arc(p.messageId, p.senderId, p.recipientId, p.kind); }
      else if (e.type === "BULLETIN_PUBLISHED") { const p = e.payload as { bulletinId: string; issuedBy: string; recipientIds: string[] }; p.recipientIds.forEach((r) => arc(`${p.bulletinId}:${r}`, p.issuedBy, r, "BULLETIN")); }
      else if (e.type === "WEAPON_FIRED") { const p = e.payload as { x: number; z: number; toX: number; toZ: number }; newArcs.push({ id: e.eventId, from: { x: p.x, z: p.z }, to: { x: p.toX, z: p.toZ }, kind: "REPORT", startedAtMs: now, durationMs: 260 }); }
      else if (e.type === "STUNNED") { const p = e.payload as { targetId: string }; const where = at(p.targetId); if (where) newBursts.push({ id: e.eventId, x: where.x, z: where.z, startedAtMs: now }); }
      else if (e.type === "CAPTURE_SUCCEEDED") { const p = e.payload as { captureId: string; characterId: string }; const where = at(p.characterId); if (where) newBursts.push({ id: p.captureId, x: where.x, z: where.z, startedAtMs: now }); }
    }
    if (newArcs.length) setArcs((a) => [...a.filter((x) => now - x.startedAtMs < ARC_MS + ARC_LINGER_MS), ...newArcs]);
    if (newBursts.length) setBursts((b) => [...b.filter((x) => now - x.startedAtMs < BURST_MS), ...newBursts]);
  }, [events, anchors]);
  useEffect(() => {
    if (!arcs.length && !bursts.length) return;
    const t = setTimeout(() => {
      const now = Date.now();
      setArcs((a) => a.filter((x) => now - x.startedAtMs < ARC_MS + ARC_LINGER_MS));
      setBursts((b) => b.filter((x) => now - x.startedAtMs < BURST_MS));
    }, 700);
    return () => clearTimeout(t);
  }, [arcs, bursts]);

  const you = youCharacterId ? chars.find((c) => c.id === youCharacterId && c.status !== "CAPTURED") ?? null : null;
  const openMissions = missions.filter((m) => m.status === "AVAILABLE");

  return (
    <div style={{ width: "100%", height: "100%", position: "relative", overflow: "hidden", background: "#2a1638" }}>
      <Canvas
        key={quality}
        frameloop={phase === "boot" ? "never" : "always"}
        shadows={high ? "percentage" : false}
        dpr={high ? [1, 1.75] : [1, 1]}
        camera={{ fov: 56, near: 0.3, far: 1600, position: [map.width / 2, 40, map.height + 40] }}
        gl={{ antialias: true, powerPreference: "high-performance", toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.0 }}
        onPointerMissed={() => onSelect(null, null)}
      >
        <World map={map} shadows={high} />
        <Life map={map} blockers={posRef} />
        {openMissions.map((m) => <MissionBeacon key={m.id} id={m.id} x={m.x} z={m.z} anchors={anchors} />)}
        {agents.map((a) => (
          <Npc key={a.id} id={a.id} x={a.x} z={a.z} role={a.role} activity={a.activity} thinking={a.thinking} selected={selectedId === a.id} anchors={anchors} onSelect={(id) => onSelect(id, "npc")} />
        ))}
        {chars.map((c) => (
          <Human
            key={c.id} id={c.id} x={c.x} z={c.z} role={c.role} heat={c.heat} wanted={c.status === "WANTED"} captured={c.status === "CAPTURED"}
            isYou={c.id === youCharacterId} selected={selectedId === c.id} anchors={anchors} onSelect={(id) => onSelect(id, "character")}
          />
        ))}
        {arcs.map((a) => <MessageArc key={a.id} from={a.from} to={a.to} kind={a.kind} startedAtMs={a.startedAtMs} durationMs={a.durationMs} />)}
        {bursts.map((b) => <CaptureBurst key={b.id} id={b.id} x={b.x} z={b.z} startedAtMs={b.startedAtMs} anchors={anchors} />)}
        {you ? <FollowCamera id={you.id} fallback={you} anchors={anchors} map={map} onPreset={setCamName} /> : <Orbit map={map} />}
        <LabelProjector anchors={anchors} els={labelEls} />
        <Pipeline high={high} phase={phase} onRun={() => setPhase((p) => (p === "boot" ? "run" : p))} onReady={() => setPhase("ready")} onSlow={() => setQuality("low")} />
      </Canvas>

      {/* labels: one DOM layer positioned by LabelProjector */}
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden", opacity: phase === "ready" ? 1 : 0, transition: "opacity 300ms" }}>
        {agents.map((a) => {
          const color = ROLE_COLOR[a.role as NpcRole];
          return (
            <div key={a.id} ref={labelRef(a.id)} style={{ ...labelStyle, borderColor: color, fontSize: 10, boxShadow: selectedId === a.id ? `0 0 0 2px ${color}` : undefined }}>
              <span style={{ color, fontWeight: 800 }}>{a.role}</span> {a.name}{a.thinking ? " · thinking…" : ""}{a.activity === "PURSUE" ? " · PURSUIT" : a.activity === "INVESTIGATE" ? " · INVESTIGATING" : ""}
            </div>
          );
        })}
        {chars.map((c) => {
          const captured = c.status === "CAPTURED", wanted = c.status === "WANTED", color = c.role === "OUTLAW" ? "#ff4fa3" : "#35e6ff";
          const price = c.ticker ? priceByTicker.get(c.ticker) : undefined;
          return (
            <div key={c.id} ref={labelRef(c.id)} style={{ ...labelStyle, borderColor: captured ? "#777" : wanted ? "#ff3b3b" : color, opacity: captured ? 0.7 : 1 }}>
              <div>{c.id === youCharacterId ? "YOU · " : ""}{c.agent ? `${c.nickname.toUpperCase()} · AGENT` : c.nickname}{c.crewName ? ` [${c.crewName}]` : ""}{captured ? " · CAPTURED" : wanted ? " · WANTED" : ""}</div>
              {c.ticker && <div style={{ color: captured ? "#ff6b6b" : "#3dffc8", fontWeight: 800 }}>{c.ticker}{price === undefined ? "" : ` V$${captured ? 0 : price}`}</div>}
            </div>
          );
        })}
        {openMissions.map((m) => <div key={m.id} ref={labelRef(`m:${m.id}`)} style={{ ...labelStyle, borderColor: "#ffe14d", color: "#ffe14d" }}>◆ {m.label}</div>)}
        {bursts.map((b) => <div key={b.id} ref={labelRef(`b:${b.id}`)} style={{ ...labelStyle, borderColor: "#ff3b3b", color: "#ff7b7b", fontSize: 14, fontWeight: 800 }}>CAPTURED · V$0</div>)}
      </div>

      {/* loading veil: up until the first lit frame is on screen */}
      {!veilGone && (
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 14, pointerEvents: "none",
          background: "linear-gradient(180deg,#1b1240 0%,#6a2a6e 55%,#ff8a5c 100%)", opacity: phase === "ready" ? 0 : 1, transition: "opacity 400ms ease-out" }}>
          <div style={{ font: `800 26px ${FONT}`, letterSpacing: 8, color: "#fff", textShadow: "0 0 24px rgba(255,120,200,0.9)" }}>{map.name.toUpperCase()}</div>
          <div style={{ width: 180, height: 3, borderRadius: 2, background: "rgba(255,255,255,0.2)", overflow: "hidden" }}>
            <div style={{ width: "40%", height: "100%", background: "#fff", borderRadius: 2, animation: "city3d-load 0.9s ease-in-out infinite alternate" }} />
          </div>
          <div style={{ font: `600 11px ${FONT}`, letterSpacing: 2, color: "rgba(255,255,255,0.85)" }}>LIGHTING THE CITY…</div>
          <style>{"@keyframes city3d-load{from{transform:translateX(-10%)}to{transform:translateX(160%)}}"}</style>
        </div>
      )}

      <div style={{ position: "absolute", left: 10, top: 8, font: `600 11px ${FONT}`, color: "rgba(255,255,255,0.86)", pointerEvents: "none", letterSpacing: 1, textShadow: "0 1px 3px #000", background: "rgba(14,8,28,0.5)", borderRadius: 6, padding: "3px 8px" }}>
        {map.name.toUpperCase()} · 3D{you ? ` · CAM ${camName} · drag to look · scroll to zoom · C camera · V reset · WASD follows the camera` : " · DRAG TO ORBIT"}
      </div>
      <button
        type="button" onClick={() => setQuality(high ? "low" : "high")} title="Toggle shadows, bloom and high-resolution rendering"
        style={{ position: "absolute", right: 10, bottom: 8, font: `700 10px ${FONT}`, letterSpacing: 1, color: "#fff", background: "rgba(14,8,28,0.7)", border: "1px solid rgba(255,255,255,0.3)", borderRadius: 6, padding: "4px 8px", cursor: "pointer" }}
      >
        QUALITY: {high ? "HIGH" : "LOW"}
      </button>
    </div>
  );
}
