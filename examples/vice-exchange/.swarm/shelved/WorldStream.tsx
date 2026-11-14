import { useEffect, useRef, useState } from "react";
import { useGame } from "../state/gameStore";

/** True while the stream pane has keyboard focus: the page's own key handler stands down so nothing is handled twice. */
export const worldInput = { focused: false };

/**
 * The city pane: live video from ONE running game (the native Open Theft Auto client started with --vice-stream),
 * relayed by the server. The browser of the same player also plays it: keys and mouse go back to that client.
 * Nothing is rendered or simulated in the browser.
 */
export function WorldStream() {
  const session = useGame((s) => s.session);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [status, setStatus] = useState<{ connected: boolean; publishing: boolean; control: boolean }>({ connected: false, publishing: false, control: false });
  const [stats, setStats] = useState<{ fps: number; lagMs: number | null }>({ fps: 0, lagMs: null });
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!session) return;
    const page = new URLSearchParams(window.location.search);
    const base = page.get("stream") ?? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:3001/stream`;
    const url = `${base}?role=sub&token=${encodeURIComponent(session.sessionToken)}&room=${encodeURIComponent(session.roomCode)}`;
    let ws: WebSocket | null = null, alive = true, retry: ReturnType<typeof setTimeout> | null = null;
    let frames = 0, lastLag: number | null = null, drawing = false, pending: ArrayBuffer | null = null, gotFrameAt = 0;
    let dx = 0, dy = 0;
    const cv = canvas.current!;
    const ctx = cv.getContext("2d")!;
    const send = (m: unknown) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };

    const draw = async (buf: ArrayBuffer) => {
      if (drawing) { pending = buf; return; } // always the newest frame only
      drawing = true;
      try {
        const view = new DataView(buf);
        const sentAt = view.getFloat64(4, true);
        const bmp = await createImageBitmap(new Blob([buf.slice(12)], { type: "image/jpeg" }));
        const box = cv.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
        const W = Math.max(2, Math.round(box.width * dpr)), H = Math.max(2, Math.round(box.height * dpr));
        if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
        const k = Math.min(W / bmp.width, H / bmp.height), w = bmp.width * k, h = bmp.height * k;
        ctx.fillStyle = "#05060e"; ctx.fillRect(0, 0, W, H);
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(bmp, (W - w) / 2, (H - h) / 2, w, h);
        bmp.close();
        frames += 1; lastLag = Math.max(0, Date.now() - sentAt); gotFrameAt = performance.now();
      } catch { /* a broken frame is skipped */ }
      drawing = false;
      if (pending) { const next = pending; pending = null; void draw(next); }
    };
    const connect = () => {
      if (!alive) return;
      ws = new WebSocket(url); ws.binaryType = "arraybuffer";
      ws.onopen = () => setStatus((s) => ({ ...s, connected: true }));
      ws.onmessage = (e) => {
        if (typeof e.data === "string") {
          try { const m = JSON.parse(e.data) as { t?: string; publishing?: boolean; control?: boolean }; if (m.t === "status") setStatus({ connected: true, publishing: !!m.publishing, control: !!m.control }); } catch { /* ignore */ }
        } else void draw(e.data as ArrayBuffer);
      };
      ws.onclose = () => { setStatus({ connected: false, publishing: false, control: false }); if (alive) retry = setTimeout(connect, 1500); };
      ws.onerror = () => { /* close follows */ };
    };
    connect();
    const meter = setInterval(() => { setStats({ fps: frames, lagMs: performance.now() - gotFrameAt < 1500 ? lastLag : null }); frames = 0; }, 1000);

    // Input: only while the pane is focused. Keys and mouse go to the game, never to the page's own handlers.
    const isFocused = () => document.activeElement === cv;
    const onKey = (down: boolean) => (e: KeyboardEvent) => {
      if (!isFocused() || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.code === "Escape") { if (down) { document.exitPointerLock?.(); cv.blur(); } return; }
      e.preventDefault(); e.stopPropagation();
      if (down && e.repeat) return;
      send({ t: "k", code: e.code, down });
    };
    const kd = onKey(true), ku = onKey(false);
    // Pointer lock when the browser grants it; otherwise the mouse still looks while it is over the focused pane.
    const locked = () => document.pointerLockElement === cv;
    const onMove = (e: MouseEvent) => { if (locked() || (isFocused() && e.target === cv)) { dx += e.movementX; dy += e.movementY; } };
    const flush = setInterval(() => { if (dx || dy) { send({ t: "m", dx, dy }); dx = 0; dy = 0; } }, 33);
    const onBtn = (down: boolean) => (e: MouseEvent) => {
      if (down && !isFocused()) { // first click only takes control
        cv.focus();
        try { const p = cv.requestPointerLock?.() as unknown as Promise<void> | undefined; p?.catch?.(() => { /* no lock: mouse-over look still works */ }); } catch { /* same */ }
        return;
      }
      if (isFocused()) { e.preventDefault(); send({ t: "b", button: e.button, down }); }
    };
    const md = onBtn(true), mu = onBtn(false);
    const onFocus = () => { worldInput.focused = isFocused(); setFocused(worldInput.focused); };
    const releaseAll = () => { for (const code of ["KeyW", "KeyA", "KeyS", "KeyD", "ShiftLeft", "Space"]) send({ t: "k", code, down: false }); onFocus(); };
    window.addEventListener("keydown", kd, true); window.addEventListener("keyup", ku, true);
    document.addEventListener("mousemove", onMove);
    cv.addEventListener("mousedown", md); cv.addEventListener("mouseup", mu);
    cv.addEventListener("focus", onFocus); cv.addEventListener("blur", releaseAll);
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    return () => {
      alive = false; worldInput.focused = false;
      if (retry) clearTimeout(retry);
      clearInterval(meter); clearInterval(flush);
      window.removeEventListener("keydown", kd, true); window.removeEventListener("keyup", ku, true);
      document.removeEventListener("mousemove", onMove);
      cv.removeEventListener("mousedown", md); cv.removeEventListener("mouseup", mu);
      cv.removeEventListener("focus", onFocus); cv.removeEventListener("blur", releaseAll);
      try { ws?.close(); } catch { /* closed */ }
    };
  }, [session]);

  const cmd = session
    ? `cd integrations/open-theft-auto && godot --path . -- --vice --vice-windowed --vice-size=1600x900 --vice-stream --vice-name=${session.nickname} --vice-role=${session.role === "HUNTER" ? "HUNTER" : "OUTLAW"} --vice-room=${session.roomCode} --vice-token=${session.sessionToken} --vice-url=ws://127.0.0.1:3001/bridge`
    : "";
  const live = status.publishing && stats.fps > 0;
  return (
    <div className={`world-frame ${focused ? "is-focused" : ""}`}>
      <canvas ref={canvas} className="world-stream" tabIndex={0} aria-label="Live view of the Open Theft Auto world" />
      {status.publishing && (
        <div className="world-live">
          <i className={live ? "on" : ""} /> LIVE from Open Theft Auto · {stats.fps} fps{stats.lagMs !== null ? ` · ${Math.round(stats.lagMs)} ms` : ""} · {status.control ? "you are playing" : "watching"}
        </div>
      )}
      {status.publishing && status.control && !focused && (
        <div className="world-hint" aria-hidden>Click the city to play · WASD move · mouse look · left mouse fires · E job / capture · R respawn · F car · Esc frees the mouse</div>
      )}
      {!status.publishing && (
        <div className="world-msg world-nostream">
          <b>{status.connected ? "No game is streaming yet." : "Connecting to the game server…"}</b>
          <span>One game engine runs the city and streams it here. Start it with this command (it joins as you):</span>
          <code className="world-cmd">{cmd}</code>
        </div>
      )}
    </div>
  );
}
