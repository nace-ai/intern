import { useEffect, useRef } from "react";
import { RULES } from "@vice/contracts";
import type { GameEvent, MessageKind, NpcRole, Vec2 } from "@vice/contracts";
import type { CityViewProps } from "../types";
import { createAmbient } from "./ambient";
import type { Ambient } from "./ambient";
import { drawStaticCity, PALETTE } from "./cityArt";

interface Smooth { x: number; z: number; tx: number; tz: number; seen: number }
type Fx =
  | { t: "pulse"; from: string; to: string; kind: MessageKind; start: number; dur: number; done?: boolean }
  | { t: "ring"; x: number; z: number; color: string; start: number; dur: number; max: number }
  | { t: "label"; x: number; z: number; text: string; color: string; start: number; dur: number; big?: boolean }
  | { t: "beam"; x: number; z: number; tx: number; tz: number; color: string; start: number; dur: number }
  | { t: "flash"; start: number; dur: number };

const ROLE_COLOR: Record<NpcRole, string> = { WITNESS: "#ffd166", INFORMANT: "#c77dff", TRADER: "#5dfdcb", POLICE: "#4cc9f0" };
const KIND_COLOR: Record<MessageKind, string> = { REPORT: "#4cc9f0", GOSSIP: "#ff5fa2", RELAY: "#c77dff", BULLETIN: "#ffd166" };
const FONT = "600 11px ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif";

export function CanvasCity(props: CityViewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const pos = new Map<string, Smooth>();
    const fx: Fx[] = [];
    let lastSeq = -1;
    let primed = false;
    let cam = { x: 60, z: 40, scale: 6 };
    let camInit = false;
    let w = 0, h = 0, dpr = 1;
    let staticLayer: HTMLCanvasElement | null = null;
    let staticKey = "";
    // Decorative city life (traffic, strollers, neon, water). Never reads or writes game state.
    let ambient: Ambient | null = null;
    const ambientView = { x: 0, z: 0, scale: 1, width: 0, height: 0 };
    let raf = 0;
    let last = performance.now();

    const resize = () => {
      const r = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = Math.max(1, Math.floor(r.width)); h = Math.max(1, Math.floor(r.height));
      canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    resize();

    const sx = (x: number) => (x - cam.x) * cam.scale + w / 2;
    const sz = (z: number) => (z - cam.z) * cam.scale + h / 2;

    const target = (id: string, x: number, z: number, now: number) => {
      const s = pos.get(id);
      if (!s || Math.hypot(s.x - x, s.z - z) > 25) pos.set(id, { x, z, tx: x, tz: z, seen: now });
      else { s.tx = x; s.tz = z; s.seen = now; }
    };
    const at = (id: string): Vec2 | null => pos.get(id) ?? null;

    const onEvents = (events: GameEvent[], now: number) => {
      if (!events.length) return;
      const top = events[events.length - 1]!.serverSequence;
      if (!primed) { primed = true; lastSeq = top; return; } // don't replay history as effects
      if (top < lastSeq) { lastSeq = -1; fx.length = 0; }    // room reset
      for (const e of events) {
        if (e.serverSequence <= lastSeq) continue;
        const p = e.payload as any;
        switch (e.type) {
          case "AGENT_MESSAGE_SENT":
            fx.push({ t: "pulse", from: p.senderId, to: p.recipientId, kind: p.kind, start: now, dur: RULES.messages.deliveryDelayMs + 150 });
            break;
          case "BULLETIN_PUBLISHED": {
            const o = at(p.issuedBy) ?? p.lastKnown;
            fx.push({ t: "ring", x: o.x, z: o.z, color: KIND_COLOR.BULLETIN, start: now, dur: 1600, max: 60 });
            fx.push({ t: "label", x: p.lastKnown.x, z: p.lastKnown.z, text: `WANTED ${p.ticker ?? ""}`.trim(), color: "#ff4d6d", start: now, dur: 2200 });
            break;
          }
          case "MISSION_COMPLETED":
            fx.push({ t: "ring", x: p.x, z: p.z, color: "#5dfdcb", start: now, dur: 900, max: 12 });
            fx.push({ t: "label", x: p.x, z: p.z, text: "JOB DONE +NOTORIETY", color: "#5dfdcb", start: now, dur: 1800 });
            break;
          case "INCIDENT_OCCURRED":
            fx.push({ t: "ring", x: p.x, z: p.z, color: "#ff5fa2", start: now, dur: 1200, max: RULES.perceptionRadius });
            break;
          case "TRADE_EXECUTED": {
            const o = at(p.actorId);
            if (o) fx.push({ t: "label", x: o.x, z: o.z, text: `${p.side} ${p.ticker} @V$${p.executionPrice}`, color: p.side === "BUY" ? "#5dfdcb" : "#ff5fa2", start: now, dur: 1800 });
            break;
          }
          case "AGENT_ACTION_APPLIED": {
            const o = at(p.agentId);
            if (o) fx.push({ t: "label", x: o.x, z: o.z - 1.5, text: String(p.action).toUpperCase(), color: ROLE_COLOR[p.role as NpcRole] ?? "#fff", start: now, dur: 1500 });
            break;
          }
          case "CAPTURE_SUCCEEDED": {
            const o = at(p.characterId) ?? { x: cam.x, z: cam.z };
            fx.push({ t: "flash", start: now, dur: 700 });
            fx.push({ t: "ring", x: o.x, z: o.z, color: "#ff4d6d", start: now, dur: 1400, max: 30 });
            fx.push({ t: "label", x: o.x, z: o.z, text: `${p.ticker ?? "CAPTURED"}  V$${p.spotBeforeCapture} → V$0`, color: "#ff4d6d", start: now, dur: 3500, big: true });
            break;
          }
          case "WEAPON_FIRED":
            fx.push({ t: "beam", x: p.x, z: p.z, tx: p.toX, tz: p.toZ, color: "#b8f7ff", start: now, dur: 320 });
            break;
          case "STUNNED": {
            const o = at(p.targetId);
            if (o) { fx.push({ t: "ring", x: o.x, z: o.z, color: "#b8f7ff", start: now, dur: 900, max: 5 }); fx.push({ t: "label", x: o.x, z: o.z, text: "STUNNED", color: "#b8f7ff", start: now, dur: RULES.weapon.stunMs }); }
            break;
          }
          case "ESCAPE_SUCCEEDED": {
            const o = at(p.characterId);
            if (o) fx.push({ t: "label", x: o.x, z: o.z, text: "ESCAPED", color: "#5dfdcb", start: now, dur: 2000 });
            break;
          }
        }
      }
      lastSeq = top;
    };

    const text = (s: string, x: number, y: number, color: string, font = FONT) => {
      ctx.font = font; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.lineJoin = "round"; ctx.lineWidth = 3; ctx.strokeStyle = "rgba(6,4,18,0.85)"; ctx.strokeText(s, x, y);
      ctx.fillStyle = color; ctx.fillText(s, x, y);
    };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      const P = propsRef.current;
      const map = P.map;

      // --- targets (tick overrides view positions) ---
      const tickChar = new Map<string, { heat: number; status: string }>();
      const tickNpc = new Map<string, { activity: string; pending: boolean }>();
      if (P.tick) {
        for (const c of P.tick.characters) { target(c.id, c.x, c.z, now); tickChar.set(c.id, c); }
        for (const n of P.tick.npcs) { target(n.id, n.x, n.z, now); tickNpc.set(n.id, n); }
      }
      // View positions go stale between snapshots: use them only for entities the latest tick does not carry.
      for (const c of P.characters) if (!tickChar.has(c.id)) target(c.id, c.x, c.z, now);
      for (const n of P.npcs) if (!tickNpc.has(n.id)) target(n.id, n.x, n.z, now);
      const k = reduced ? 1 : 1 - Math.exp(-dt * 14);
      for (const [id, s] of pos) {
        s.x += (s.tx - s.x) * k; s.z += (s.tz - s.z) * k;
        if (now - s.seen > 5000) pos.delete(id);
      }
      onEvents(P.events, now);

      // --- camera ---
      const fit = Math.min(w / map.width, h / map.height);
      const you = P.youCharacterId ? P.characters.find((c) => c.id === P.youCharacterId) : undefined;
      const youPos = you && you.status !== "CAPTURED" && you.status !== "ARCHIVED" ? at(you.id) : null;
      let goal = { x: map.width / 2, z: map.height / 2, scale: fit };
      if (youPos) {
        const scale = Math.max(fit, Math.min(w, h) / 46);
        const vw = w / scale / 2, vh = h / scale / 2;
        goal = {
          scale,
          x: vw * 2 >= map.width ? map.width / 2 : Math.max(vw, Math.min(map.width - vw, youPos.x)),
          z: vh * 2 >= map.height ? map.height / 2 : Math.max(vh, Math.min(map.height - vh, youPos.z)),
        };
      }
      if (!camInit || reduced) { cam = goal; camInit = true; }
      else {
        const ck = 1 - Math.exp(-dt * 5);
        cam.x += (goal.x - cam.x) * ck; cam.z += (goal.z - cam.z) * ck; cam.scale += (goal.scale - cam.scale) * ck;
      }

      // --- static layer (cached at map resolution) ---
      const key = `${map.name}:${map.buildings.length}`;
      if (!staticLayer || staticKey !== key) {
        staticLayer = document.createElement("canvas");
        const S = 12; // px per metre
        staticLayer.width = map.width * S; staticLayer.height = map.height * S;
        drawStaticCity(staticLayer.getContext("2d")!, map, S);
        staticKey = key;
        try { ambient = createAmbient(map, 42, { reducedMotion: reduced }); } catch (e) { ambient = null; console.error("[scene] ambient layer disabled", e); }
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = PALETTE.void; ctx.fillRect(0, 0, w, h);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(staticLayer, sx(0), sz(0), map.width * cam.scale, map.height * cam.scale);

      // ambient life underneath every real entity
      ambientView.x = cam.x; ambientView.z = cam.z; ambientView.scale = cam.scale; ambientView.width = w; ambientView.height = h;
      if (ambient) {
        try { ambient.update(dt * 1000); ambient.drawUnder(ctx, ambientView); }
        catch (e) { ambient = null; console.error("[scene] ambient layer disabled", e); }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }

      const m = cam.scale; // px per metre
      const selected = P.selectedId;

      // --- missions ---
      for (const ms of P.missions) {
        if (ms.status !== "AVAILABLE") continue;
        const x = sx(ms.x), y = sz(ms.z);
        const pulse = reduced ? 0.5 : (Math.sin(now / 280) + 1) / 2;
        ctx.save();
        ctx.strokeStyle = "#5dfdcb"; ctx.lineWidth = 2; ctx.shadowColor = "#5dfdcb"; ctx.shadowBlur = 14;
        ctx.beginPath(); ctx.arc(x, y, RULES.missionInteractRadius * m * (0.8 + pulse * 0.25), 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = "#5dfdcb"; ctx.beginPath();
        ctx.moveTo(x, y - 7); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 7); ctx.lineTo(x - 6, y); ctx.closePath(); ctx.fill();
        ctx.restore();
        text(ms.label, x, y - RULES.missionInteractRadius * m - 10, "#b9fff0");
        if (youPos && you?.role === "OUTLAW" && Math.hypot(youPos.x - ms.x, youPos.z - ms.z) <= RULES.missionInteractRadius)
          text("[E] GRAB", x, y + RULES.missionInteractRadius * m + 12, "#ffffff", "700 13px ui-sans-serif, system-ui, sans-serif");
      }

      // --- pursuit lines ---
      for (const n of P.npcs) {
        const act = tickNpc.get(n.id)?.activity ?? n.activity;
        if (act !== "PURSUE" || !n.targetCharacterId) continue;
        const a = at(n.id), b = at(n.targetCharacterId);
        if (!a || !b) continue;
        ctx.save(); ctx.strokeStyle = "rgba(255,77,109,0.85)"; ctx.lineWidth = 2; ctx.setLineDash([6, 6]);
        ctx.lineDashOffset = reduced ? 0 : -now / 30;
        ctx.beginPath(); ctx.moveTo(sx(a.x), sz(a.z)); ctx.lineTo(sx(b.x), sz(b.z)); ctx.stroke(); ctx.restore();
      }

      // --- NPCs ---
      for (const n of P.npcs) {
        const p = at(n.id); if (!p) continue;
        const x = sx(p.x), y = sz(p.z), col = ROLE_COLOR[n.role], r = Math.max(6, 0.9 * m);
        const live = tickNpc.get(n.id);
        const pending = live?.pending ?? n.pendingDecisionId !== null;
        if (selected === n.id) {
          ctx.save(); ctx.fillStyle = col + "14"; ctx.strokeStyle = col + "aa"; ctx.setLineDash([4, 5]); ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(x, y, RULES.perceptionRadius * m, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); ctx.restore();
        }
        ctx.save();
        ctx.shadowColor = col; ctx.shadowBlur = selected === n.id ? 22 : 10;
        ctx.fillStyle = col; ctx.strokeStyle = "#0b0720"; ctx.lineWidth = 2;
        ctx.beginPath();
        if (n.role === "WITNESS") ctx.arc(x, y, r, 0, Math.PI * 2);
        else if (n.role === "TRADER") ctx.rect(x - r, y - r, r * 2, r * 2);
        else if (n.role === "INFORMANT") { ctx.moveTo(x, y - r * 1.25); ctx.lineTo(x + r * 1.25, y); ctx.lineTo(x, y + r * 1.25); ctx.lineTo(x - r * 1.25, y); ctx.closePath(); }
        else { ctx.moveTo(x, y - r * 1.3); ctx.lineTo(x + r * 1.15, y - r * 0.5); ctx.lineTo(x + r * 0.8, y + r); ctx.lineTo(x, y + r * 1.35); ctx.lineTo(x - r * 0.8, y + r); ctx.lineTo(x - r * 1.15, y - r * 0.5); ctx.closePath(); }
        ctx.fill(); ctx.shadowBlur = 0; ctx.stroke();
        ctx.restore();
        text(n.role[0]!, x, y + 0.5, "#0b0720", "800 10px ui-sans-serif, system-ui, sans-serif");
        text(n.name, x, y + r + 11, selected === n.id ? "#ffffff" : col);
        if (pending) {
          const by = y - r - 13;
          ctx.save(); ctx.fillStyle = "rgba(11,7,32,0.9)"; ctx.strokeStyle = col; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.roundRect(x - 15, by - 8, 30, 16, 8); ctx.fill(); ctx.stroke();
          ctx.fillStyle = col;
          for (let i = 0; i < 3; i++) {
            const o = reduced ? 0 : Math.sin(now / 160 + i * 0.9) * 2.2;
            ctx.beginPath(); ctx.arc(x - 7 + i * 7, by + o, 2, 0, Math.PI * 2); ctx.fill();
          }
          ctx.restore();
        }
      }

      // --- human characters ---
      let capturePrompt: { x: number; y: number } | null = null;
      for (const c of P.characters) {
        if (c.status === "ARCHIVED") continue;
        const p = at(c.id); if (!p) continue;
        const live = tickChar.get(c.id);
        const status = live?.status ?? c.status, heat = live?.heat ?? c.heat;
        const x = sx(p.x), y = sz(p.z), r = Math.max(7, 1.0 * m);
        const isYou = c.id === P.youCharacterId, captured = status === "CAPTURED";
        const col = captured ? "#6b6880" : c.role === "OUTLAW" ? "#ff5fa2" : "#ff9f1c";
        ctx.save();
        if (!captured && heat > 0) { // heat ring
          const wanted = status === "WANTED";
          ctx.strokeStyle = wanted ? "#ff4d6d" : "#ffb703"; ctx.lineWidth = wanted ? 3.5 : 2.5;
          if (wanted) { ctx.shadowColor = "#ff4d6d"; ctx.shadowBlur = reduced ? 8 : 8 + Math.sin(now / 150) * 6; }
          ctx.beginPath(); ctx.arc(x, y, r + 5, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * Math.min(100, heat)) / 100); ctx.stroke();
        }
        ctx.shadowColor = col; ctx.shadowBlur = isYou ? 20 : 10;
        ctx.fillStyle = col; ctx.strokeStyle = isYou ? "#ffffff" : "#0b0720"; ctx.lineWidth = isYou ? 2.5 : 2;
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0; ctx.stroke();
        if (selected === c.id) { ctx.strokeStyle = "#ffffff"; ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.arc(x, y, r + 10, 0, Math.PI * 2); ctx.stroke(); }
        ctx.restore();
        text(c.role === "OUTLAW" ? "O" : "H", x, y + 0.5, "#0b0720", "800 10px ui-sans-serif, system-ui, sans-serif");
        const tag = captured ? `${c.nickname} · CAPTURED` : `${c.agent ? `${c.nickname.toUpperCase()} · AGENT` : c.nickname}${c.crewName ? ` [${c.crewName}]` : ""}${isYou ? " (you)" : ""}`;
        text(tag, x, y - r - 20, captured ? "#8d8aa6" : "#ffffff");
        if (c.ticker) text(captured ? `${c.ticker} V$0` : status === "WANTED" ? `${c.ticker} · WANTED` : c.ticker, x, y - r - 8, status === "WANTED" ? "#ff4d6d" : "#5dfdcb");
        if (!isYou && youPos && you?.role === "HUNTER" && status === "WANTED" && Math.hypot(youPos.x - p.x, youPos.z - p.z) <= RULES.captureRadius)
          capturePrompt = { x, y: y + r + 14 };
      }
      if (capturePrompt) text("[E] CAPTURE", capturePrompt.x, capturePrompt.y, "#ffffff", "700 13px ui-sans-serif, system-ui, sans-serif");

      // light in the air (haze, beams): after the agents, never opaque
      if (ambient) {
        try { ambient.drawOver(ctx, ambientView); } catch (e) { ambient = null; console.error("[scene] ambient layer disabled", e); }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }

      // --- effects ---
      for (let i = fx.length - 1; i >= 0; i--) {
        const f = fx[i]!;
        const t = (now - f.start) / f.dur;
        if (t >= 1) { fx.splice(i, 1); continue; }
        if (f.t === "pulse") {
          const a = at(f.from), b = at(f.to); if (!a || !b) continue;
          const col = KIND_COLOR[f.kind];
          const ax = sx(a.x), ay = sz(a.z), bx = sx(b.x), by = sz(b.z);
          ctx.save();
          ctx.strokeStyle = col + "66"; ctx.lineWidth = 1.5; ctx.setLineDash([3, 5]);
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke(); ctx.setLineDash([]);
          const e = reduced ? 1 : t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
          const px = ax + (bx - ax) * e, py = ay + (by - ay) * e;
          ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 16;
          ctx.beginPath(); ctx.arc(px, py, 5, 0, Math.PI * 2); ctx.fill();
          ctx.restore();
          text(f.kind, (ax + bx) / 2, (ay + by) / 2 - 9, col, "700 10px ui-sans-serif, system-ui, sans-serif");
        } else if (f.t === "beam") {
          ctx.save(); ctx.globalAlpha = 1 - t; ctx.strokeStyle = f.color; ctx.lineWidth = 3; ctx.shadowColor = f.color; ctx.shadowBlur = 14; ctx.lineCap = "round";
          ctx.beginPath(); ctx.moveTo(sx(f.x), sz(f.z)); ctx.lineTo(sx(f.tx), sz(f.tz)); ctx.stroke(); ctx.restore();
        } else if (f.t === "ring") {
          ctx.save(); ctx.globalAlpha = 1 - t; ctx.strokeStyle = f.color; ctx.lineWidth = 2.5; ctx.shadowColor = f.color; ctx.shadowBlur = 12;
          ctx.beginPath(); ctx.arc(sx(f.x), sz(f.z), f.max * m * (reduced ? 1 : t), 0, Math.PI * 2); ctx.stroke(); ctx.restore();
        } else if (f.t === "label") {
          ctx.save(); ctx.globalAlpha = Math.min(1, (1 - t) * 2.5);
          const rise = reduced ? 22 : 16 + t * 30;
          text(f.text, sx(f.x), sz(f.z) - rise - (f.big ? 22 : 12), f.color, f.big ? "800 20px ui-sans-serif, system-ui, sans-serif" : "700 12px ui-sans-serif, system-ui, sans-serif");
          ctx.restore();
        } else if (f.t === "flash" && !reduced) {
          ctx.save(); ctx.fillStyle = `rgba(255,77,109,${0.35 * (1 - t)})`; ctx.fillRect(0, 0, w, h); ctx.restore();
        }
      }
      if (fx.length > 80) fx.splice(0, fx.length - 80);
    };
    raf = requestAnimationFrame(frame);

    const click = (ev: MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      const px = ev.clientX - r.left, py = ev.clientY - r.top;
      const P = propsRef.current;
      let best: { id: string; kind: "npc" | "character"; d: number } | null = null;
      const consider = (id: string, kind: "npc" | "character") => {
        const p = pos.get(id); if (!p) return;
        const d = Math.hypot(sx(p.x) - px, sz(p.z) - py);
        if (d < Math.max(18, 2 * cam.scale) && (!best || d < best.d)) best = { id, kind, d };
      };
      for (const n of P.npcs) consider(n.id, "npc");
      for (const c of P.characters) if (c.status !== "ARCHIVED") consider(c.id, "character");
      const hit = best as { id: string; kind: "npc" | "character" } | null;
      P.onSelect(hit ? hit.id : null, hit ? hit.kind : null);
    };
    canvas.addEventListener("click", click);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); canvas.removeEventListener("click", click); };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label="Live map of Coral Bay showing players, NPC agents, missions and messages"
      style={{ width: "100%", height: "100%", display: "block", background: PALETTE.void, cursor: "crosshair" }}
    />
  );
}
