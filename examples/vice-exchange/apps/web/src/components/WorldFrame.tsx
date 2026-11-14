import { useEffect, useMemo, useState } from "react";
import { useGame } from "../state/gameStore";

/**
 * WORLD view: the Godot Web export of the 3D city client (built from integrations/open-theft-auto with our bridge),
 * embedded in the city pane. It joins the same room AS this page's player by presenting the page's session token to
 * the server's /bridge socket, so the character you see there is the one in the market panel.
 */
export function WorldFrame() {
  const session = useGame((s) => s.session);
  const [state, setState] = useState<"checking" | "ready" | "missing" | "nowebgl">("checking");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let alive = true;
    const gl = document.createElement("canvas").getContext("webgl2");
    if (!gl) { setState("nowebgl"); return; }
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    fetch("/world/index.pck", { method: "HEAD" })
      .then((r) => { if (alive) setState(r.ok && Number(r.headers.get("content-length") ?? 1) > 1000 ? "ready" : "missing"); })
      .catch(() => { if (alive) setState("missing"); });
    return () => { alive = false; };
  }, []);
  const src = useMemo(() => {
    if (!session) return null;
    const page = new URLSearchParams(window.location.search);
    // The 3D client talks to the game server's plain WebSocket bridge. Override with ?bridge=ws://host:port/bridge.
    const bridge = page.get("bridge") ?? (import.meta.env.VITE_BRIDGE_URL as string | undefined)
      ?? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:3001/bridge`;
    const q = new URLSearchParams({ name: session.nickname, role: session.role, room: session.roomCode, token: session.sessionToken, url: bridge });
    return `/world/index.html?${q.toString()}`;
  }, [session]);
  const back = (
    <button className="ghost" onClick={() => useGame.setState({ viewMode: "3d" })}>Back to the 3D city</button>
  );
  if (state === "missing" || state === "nowebgl" || !src) {
    return (
      <div className="world-frame world-msg">
        <b>{state === "nowebgl" ? "This browser has no WebGL 2, which the 3D world needs." : "The 3D world build is not on this machine."}</b>
        <span>{state === "nowebgl" ? "" : "Build it with: python3 integrations/ota-bridge/apply.py, then export the Web preset (see integrations/ota-bridge/README.md)."}</span>
        {back}
      </div>
    );
  }
  return (
    <div className="world-frame">
      {state === "ready" && (
        <iframe title="Coral Bay 3D world" src={src} allow="fullscreen; gamepad; autoplay; pointer-lock" onLoad={() => setLoaded(true)} />
      )}
      {!loaded && <div className="world-msg world-loading"><b>Loading the 3D world…</b><span>about 400 MB on first load; click inside it to take control (WASD, E, R).</span></div>}
    </div>
  );
}
