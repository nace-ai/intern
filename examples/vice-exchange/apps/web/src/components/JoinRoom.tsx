import { useEffect, useState } from "react";
import { CREDITS_NOTICE, DEFAULT_ROOM_CODE } from "@vice/contracts";
import type { HumanRole } from "@vice/contracts";
import { joinRoom, savedSession } from "../net/socketClient";
import { useGame } from "../state/gameStore";

const ROLES: Array<{ id: HumanRole; title: string; blurb: string }> = [
  { id: "OUTLAW", title: "Outlaw", blurb: "Run jobs, build notoriety, stay free. You are a ticker." },
  { id: "HUNTER", title: "Hunter", blurb: "Track wanted outlaws. The longer they lasted, the bigger your bounty." },
  { id: "SPECTATOR", title: "Spectator", blurb: "Watch the swarm think. Inspect every decision." },
];

/** Deep link for demos: /?nick=Ace&role=OUTLAW&room=VICE-42&auto=1 (add &create=1 to create, &view=3d for the 3D city, &director=<secret> for demo-director controls). */
function readLink(): { nick: string; role: HumanRole | null; room: string; auto: boolean; create: boolean; director: string } {
  const q = new URLSearchParams(window.location.search);
  const r = (q.get("role") ?? "").toUpperCase();
  const view = (q.get("view") ?? "").toLowerCase();
  if (view === "3d" || view === "2d" || view === "world") useGame.setState({ viewMode: view });
  return {
    nick: (q.get("nick") ?? "").slice(0, 16), role: r === "OUTLAW" || r === "HUNTER" || r === "SPECTATOR" ? r : null,
    room: (q.get("room") ?? "").toUpperCase().slice(0, 12), auto: q.get("auto") === "1", create: q.get("create") === "1",
    director: (q.get("director") ?? "").slice(0, 128),
  };
}
// React StrictMode runs mount effects twice in development; an automatic join must happen once per page load.
let autoJoinStarted = false;

export function JoinRoom() {
  const saved = savedSession();
  const [link] = useState(readLink);
  const [nickname, setNickname] = useState(link.nick || saved?.nickname || "");
  const [roomCode, setRoomCode] = useState(link.room || saved?.roomCode || DEFAULT_ROOM_CODE);
  const [role, setRole] = useState<HumanRole>(link.role ?? saved?.role ?? "OUTLAW");
  const [secret, setSecret] = useState(link.director);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const go = async (create: boolean, token?: string) => {
    const nick = nickname.trim();
    if (!nick) { setError("Enter a nickname (letters, numbers, spaces, . _ -)."); return; }
    setBusy(true); setError(null);
    let ack = await joinRoom({ create, roomCode: roomCode.trim().toUpperCase(), nickname: nick, role, directorSecret: secret.trim() || undefined, sessionToken: token });
    // Joining a code that does not exist yet: offer the obvious next step instead of a dead end.
    if (!ack.ok && !create && ack.error?.code === "ROOM_NOT_FOUND") {
      setError(`Room ${roomCode.toUpperCase()} does not exist. Use “Create new room”.`);
    } else if (!ack.ok) {
      setError(ack.error?.message ?? "Could not join.");
    }
    setBusy(false);
    return ack;
  };

  // Same tab reload: quietly re-attach to the same character. A demo deep link joins straight away.
  useEffect(() => {
    if (autoJoinStarted) return;
    if (saved?.sessionToken && (!link.nick || link.nick === saved.nickname)) { autoJoinStarted = true; void go(false, saved.sessionToken); }
    else if (link.auto && link.nick) { autoJoinStarted = true; void go(link.create); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="join">
      <form className="join-card" onSubmit={(e) => { e.preventDefault(); void go(false, saved && saved.roomCode === roomCode && saved.nickname === nickname.trim() && saved.role === role ? saved.sessionToken : undefined); }}>
        <div className="brand big"><span className="brand-a">VICE</span><span className="brand-b">EXCHANGE</span></div>
        <p className="tag">Every outlaw is a stock. Every NPC decides for itself. Get caught and your ticker goes to V$0.</p>
        <label>Nickname
          <input autoFocus value={nickname} maxLength={16} onChange={(e) => setNickname(e.target.value)} placeholder="e.g. Ace" />
        </label>
        <label>Room code
          <input value={roomCode} maxLength={12} onChange={(e) => setRoomCode(e.target.value.toUpperCase())} />
        </label>
        <fieldset className="roles">
          <legend>Role</legend>
          {ROLES.map((r) => (
            <label key={r.id} className={`role ${role === r.id ? "on" : ""}`}>
              <input type="radio" name="role" checked={role === r.id} onChange={() => setRole(r.id)} />
              <b>{r.title}</b><span>{r.blurb}</span>
            </label>
          ))}
        </fieldset>
        <details>
          <summary>Demo director secret (optional)</summary>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="only needed if you did not create the room" />
        </details>
        {error && <div className="err" role="alert">{error}</div>}
        <div className="row">
          <button type="submit" className="primary" disabled={busy}>{busy ? "Joining…" : "Join room"}</button>
          <button type="button" className="ghost" disabled={busy} onClick={() => void go(true)}>Create new room</button>
        </div>
        <p className="fine">{CREDITS_NOTICE}</p>
      </form>
    </div>
  );
}
