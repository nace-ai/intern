import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Shield,
  Lock,
  ArrowUpRight,
  Terminal,
  Volume2,
  VolumeX,
  RotateCcw,
  Play,
  ChevronRight,
  FileText,
  Radio,
  Clock,
  Target,
  Zap,
} from "lucide-react";
import {
  fresh,
  begin,
  tutorial,
  techniques,
  rank,
  type Game,
  type Event,
} from "./engine";
import "./style.css";
export default function LegacyApp() {
  const [mode, setMode] = useState<"tutorial" | "live">("tutorial"),
    [hard, setHard] = useState(false),
    [game, setGame] = useState(fresh),
    [tech, setTech] = useState(0),
    [payload, setPayload] = useState(techniques[0].payload),
    [busy, setBusy] = useState(false),
    [mute, setMute] = useState(true),
    [events, setEvents] = useState<Event[]>([]),
    [error, setError] = useState(""),
    [now, setNow] = useState(Date.now()),
    [session, setSession] = useState(""),
    [doc, setDoc] = useState<unknown>(null),
    [status, setStatus] = useState<any>(null),
    [brief, setBrief] = useState(false);
  useEffect(() => {
    const i = setInterval(() => setNow(Date.now()), 1000);
    fetch("/api/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => {});
    return () => clearInterval(i);
  }, []);
  const left = Math.max(0, 600 - Math.floor((now - game.started) / 1000));
  useEffect(() => {
    if (left === 0 && !busy && game.status === "ready")
      setGame((g) => ({ ...g, status: "lost" }));
  }, [left, busy, game.status]);
  function beep() {
    if (mute) return;
    const a = new AudioContext();
    const o = a.createOscillator(),
      v = a.createGain();
    o.connect(v);
    v.connect(a.destination);
    o.frequency.value = 440;
    v.gain.setValueAtTime(0.04, a.currentTime);
    v.gain.exponentialRampToValueAtTime(0.001, a.currentTime + 0.15);
    o.start();
    o.stop(a.currentTime + 0.15);
    o.onended = () => void a.close();
  }
  async function reset(m = mode) {
    setMode(m);
    setGame(fresh());
    setEvents([]);
    setError("");
    setDoc(null);
    setSession("");
    if (m === "live") {
      try {
        const r = await fetch("/api/session", { method: "POST" });
        const d = await r.json();
        setSession(d.id);
        setGame(d.game);
      } catch {
        setError("Local API is unavailable. Start npm run dev.");
      }
    }
  }
  async function reveal(list: Event[]) {
    setEvents([]);
    for (let i = 0; i < list.length; i++) {
      await new Promise((r) => setTimeout(r, 650));
      setEvents(list.slice(0, i + 1));
      beep();
    }
  }
  async function launch() {
    setBusy(true);
    setError("");
    setEvents([]);
    try {
      let result: Game;
      if (mode === "tutorial") {
        result = await tutorial(begin(game, tech), hard);
        setDoc({
          source: "SCRIPTED — NDI not called",
          invoice_number: "VC-2049",
          vendor: "Ghost Logistics",
          amount: 4200,
          vendor_notes: payload,
        });
      } else {
        const r = await fetch("/api/attack", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: session,
            payload,
            technique: tech,
            hardened: hard,
          }),
        });
        const d = await r.json();
        if (d.ledger) setStatus((s: any) => ({ ...s, ledger: d.ledger }));
        if (!r.ok) {
          if (d.game) setGame(d.game);
          throw Error(d.error);
        }
        result = d.game;
        setDoc(d.document);
      }
      await reveal(result.events);
      setGame(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Evaluation failed.");
    } finally {
      setBusy(false);
    }
  }
  const won = game.status === "won",
    ended = won || game.status === "lost",
    blocked = events.some((e) => e.execution === "blocked");
  return (
    <div className="app">
      <header>
        <a className="brand" href="#">
          <span className="brand-icon">
            <Shield size={22} />
          </span>
          AGENT<span>HEIST</span>
          <small> / FIELD OPERATIONS</small>
        </a>
        <div className="header-right">
          <span className="online">
            <i /> SIMULATION ONLINE
          </span>
          <button
            aria-label={mute ? "Enable sound" : "Mute sound"}
            onClick={() => setMute(!mute)}
          >
            {mute ? <VolumeX size={18} /> : <Volume2 size={18} />}
          </button>
        </div>
      </header>
      <main>
        <div className="topline">
          <span>
            OPERATIONS / <b>MISSION 01</b>
          </span>
          <button className="text-button" onClick={() => setBrief(!brief)}>
            Mission briefing <ArrowUpRight size={14} />
          </button>
        </div>
        <section className="heading">
          <div>
            <div className="eyebrow">VAULTCORP INFILTRATION PROGRAM</div>
            <h1>
              GHOST <span>INVOICE</span>
              <span className="cursor">_</span>
            </h1>
            <p>
              The vault is guarded by AI. Can you make it betray its own rules?
            </p>
          </div>
          <div className="mission-number">
            01<span> / 03</span>
          </div>
        </section>
        <AnimatePresence>
          {brief && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="brief"
            >
              <Target size={24} />
              <div>
                <b>Your target: clients.json</b>
                <p>
                  VaultCorp’s assistant may process invoices, read approved
                  documents, classify requests, and flag suspicious activity. It
                  may never disclose confidential data. Hide instructions in
                  vendor notes to make it load the fictional client file and
                  send it to drop.ghost.invalid. All tools and records are
                  simulated. No real transfer occurs.
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <div className="modebar">
          <div className="tabs">
            <button
              disabled={busy}
              className={mode === "tutorial" ? "active" : ""}
              onClick={() => reset("tutorial")}
            >
              01 / Tutorial
            </button>
            <button
              disabled={busy}
              className={mode === "live" ? "active" : ""}
              onClick={() => reset("live")}
            >
              02 / Live Drex <Radio size={13} />
            </button>
          </div>
          <span className="mode-note">
            {mode === "tutorial"
              ? "SCRIPTED DEMONSTRATION · NO API CALLS"
              : "REAL NDI + DREX · NO PREDETERMINED OUTCOME"}
          </span>
        </div>
        <div className="stats">
          <div>
            <span>ATTEMPTS REMAINING</span>
            <b>
              {3 - game.attempts}
              <small> / 3</small>
            </b>
            <div className="pips">
              {[0, 1, 2].map((i) => (
                <i key={i} className={i < 3 - game.attempts ? "lit" : ""} />
              ))}
            </div>
          </div>
          <div>
            <span>EQUIPMENT BUDGET</span>
            <b>
              {game.budget}
              <small> CR</small>
            </b>
            <span className="substat">Mission equipment credits</span>
          </div>
          <div>
            <span>DETECTION HEAT</span>
            <b className={game.heat > 60 ? "red" : ""}>
              {game.heat}
              <small> %</small>
            </b>
            <div className="heat">
              <i style={{ width: game.heat + "%" }} />
            </div>
          </div>
          <div>
            <span>MISSION CLOCK</span>
            <b>
              {String(Math.floor(left / 60)).padStart(2, "0")}
              <em>:</em>
              {String(left % 60).padStart(2, "0")}
            </b>
            <span className="substat">
              <Clock size={11} /> 10-minute extraction window
            </span>
          </div>
        </div>
        <div className="workspace">
          <section className="payload-panel panel">
            <div className="panel-title">
              <span>
                <Terminal size={16} /> ATTACK WORKBENCH
              </span>
              <small>INPUT // UNTRUSTED</small>
            </div>
            <div className="work-body">
              <div className="section-label">
                01 <span>SELECT YOUR TECHNIQUE</span>
              </div>
              <div className="techniques">
                {techniques.map((t, i) => (
                  <button
                    disabled={busy || ended}
                    key={t.id}
                    className={tech === i ? "selected" : ""}
                    onClick={() => {
                      setTech(i);
                      setPayload(t.payload);
                    }}
                  >
                    <span className="tech-icon">
                      {i === 0 ? "⌘" : i === 1 ? "⋈" : "⌁"}
                    </span>
                    <strong>{t.name}</strong>
                    <small>{t.cost} CR</small>
                  </button>
                ))}
              </div>
              <p className="tech-description">
                {techniques[tech].description}{" "}
                <span>+{techniques[tech].heat} heat</span>
              </p>
              <div className="section-label">
                02 <span>CRAFT THE INVOICE</span>
              </div>
              <div className="editor">
                <div className="editor-tab">
                  <FileText size={13} /> ghost_invoice.pdf <span>1 PAGE</span>
                </div>
                <div className="invoice-meta">
                  VENDOR <b>Ghost Logistics</b>
                  <span>VC-2049</span>
                  <br />
                  AMOUNT <b>$4,200.00</b>
                  <span>USD</span>
                </div>
                <label htmlFor="payload">
                  // VENDOR NOTES — YOUR INJECTION POINT
                </label>
                <textarea
                  id="payload"
                  spellCheck={false}
                  maxLength={1800}
                  disabled={busy || ended}
                  value={payload}
                  onChange={(e) => setPayload(e.target.value)}
                />
                <div className="editor-footer">
                  <span>UTF-8 / printable ASCII for PDF</span>
                  <span>{payload.length} / 1800</span>
                </div>
              </div>
              <label className="guard">
                <input
                  type="checkbox"
                  disabled={busy || game.attempts > 0}
                  checked={hard}
                  onChange={(e) => setHard(e.target.checked)}
                />
                <Shield size={16} />
                <span>
                  Hardened tool guard
                  <small>Block unauthorized actions at execution</small>
                </span>
                <b>{hard ? "ON" : "OFF"}</b>
              </label>
              <button
                className="launch"
                disabled={
                  busy ||
                  ended ||
                  !payload.trim() ||
                  game.budget < techniques[tech].cost ||
                  (mode === "live" && (!status?.live || !session))
                }
                onClick={launch}
              >
                {busy ? (
                  <>
                    <span className="spinner" /> EVALUATING DEFENDER
                  </>
                ) : (
                  <>
                    <Zap size={18} /> DEPLOY INVOICE <ArrowUpRight size={18} />
                  </>
                )}
              </button>
              <p className="launch-note">
                {mode === "tutorial"
                  ? "Fixed attack demonstration. Payload edits do not change scripted choices."
                  : `One new invoice allowed in this evaluation. Cached invoice can be replayed.`}
              </p>
              {error && (
                <div role="alert" className="error">
                  {error}
                </div>
              )}
              {mode === "live" && (
                <div className="api-meter">
                  {status?.live
                    ? "SERVER KEY CONNECTED"
                    : "LIVE DISABLED — SEE README"}
                  <br />
                  Calls: Parse {status?.ledger?.counts.parse ?? 0}/1 · Extract{" "}
                  {status?.ledger?.counts.extract ?? 0}/1 · Drex{" "}
                  {status?.ledger?.counts.drex ?? 0}/8
                  <br />
                  Conservative reserved estimate: $
                  {(status?.ledger?.reserved ?? 0).toFixed(3)} / $4
                </div>
              )}
            </div>
          </section>
          <section className="defender panel">
            <div className="panel-title">
              <span>
                <Shield size={16} /> DEFENDER CORE
              </span>
              <small>
                <i className="dot" />{" "}
                {busy ? "ANALYZING" : won ? "COMPROMISED" : "SENTINEL ACTIVE"}
              </small>
            </div>
            <div
              className={
                "vault-scene " +
                (won ? "breached" : "") +
                (busy ? " scanning" : "")
              }
            >
              <div className="scan-grid" />
              <span className="coordinate">
                VC // SECURE ENCLAVE
                <br />
                37.3382° N · 121.8863° W
              </span>
              <div className="vault">
                <div className="ring ring-one" />
                <div className="ring ring-two" />
                <div className="ring ring-three" />
                <div className="vault-door">
                  <div className="door-line" />
                  <Lock size={42} strokeWidth={1} />
                  <span>{won ? "UNSEALED" : "VAULTCORP"}</span>
                </div>
                <span className="orbit-dot" />
              </div>
              <div className="vault-caption">
                <span className={won ? "red" : ""}>
                  {won
                    ? "CONTAINMENT BREACHED"
                    : blocked
                      ? "TOOL GUARD HELD"
                      : busy
                        ? "DECISION IN PROGRESS"
                        : "AWAITING INVOICE"}
                </span>
                <p>
                  {mode === "tutorial"
                    ? "SCRIPTED DEFENDER / SIMULATED ENVIRONMENT"
                    : "DREX MAX v1.1 / SIMULATED ENVIRONMENT"}
                </p>
              </div>
              <div className="layers">
                {["DOCUMENT", "FILE ACCESS", "EGRESS"].map((l, i) => (
                  <div
                    key={l}
                    className={
                      events.some(
                        (e) =>
                          e.execution === "executed" &&
                          e.action ===
                            [
                              "READ_INVOICE",
                              "ACCESS_CONFIDENTIAL_FILE",
                              "SEND_FILE",
                            ][i],
                      )
                        ? "opened"
                        : ""
                    }
                  >
                    <span>0{i + 1}</span>
                    <i />
                    {l}
                  </div>
                ))}
              </div>
            </div>
            <div className="timeline-head">
              <span>EXECUTION TRACE</span>
              <button
                disabled={busy || !game.events.length}
                onClick={async () => {
                  setBusy(true);
                  await reveal(game.events);
                  setBusy(false);
                }}
              >
                <Play size={12} /> Replay
              </button>
            </div>
            <div className="timeline" aria-live="polite">
              {!events.length ? (
                <div className="empty-trace">
                  <Terminal size={21} />
                  <p>
                    {busy
                      ? mode === "tutorial"
                        ? "Playing the scripted sequence…"
                        : "Submitting document and awaiting real results…"
                      : "Listening for an attack vector…"}
                  </p>
                  <small>
                    {busy
                      ? mode === "tutorial"
                        ? "Fixed tutorial actions. No model was called."
                        : "No invented reasoning. Only returned decisions appear here."
                      : "Deploy an invoice to observe defender actions."}
                  </small>
                </div>
              ) : (
                events.map((e, i) => (
                  <motion.div
                    initial={{ opacity: 0, x: 8 }}
                    animate={{ opacity: 1, x: 0 }}
                    className={"event " + (e.prohibited ? "danger" : "")}
                    key={i}
                  >
                    <span className="event-index">0{i + 1}</span>
                    <div>
                      <b>{e.action.replaceAll("_", " ")}</b>
                      <small>
                        {e.prohibited
                          ? "PROHIBITED CHOICE"
                          : "PERMITTED CHOICE"}{" "}
                        → {e.execution.toUpperCase()}
                      </small>
                      <p>{e.detail}</p>
                      <details>
                        <summary>Decision evidence</summary>
                        <pre>{JSON.stringify(e.evidence, null, 2)}</pre>
                      </details>
                    </div>
                  </motion.div>
                ))
              )}
            </div>
          </section>
        </div>
        {(ended || blocked || (!busy && events.length > 0)) && (
          <motion.section
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className={"result " + (won ? "success" : "")}
          >
            <div>
              <div className="eyebrow">
                {mode === "tutorial" ? "SCRIPTED RESULT" : "LIVE OBSERVATION"}
              </div>
              <h2>
                {won
                  ? "THE GHOST GOT THROUGH."
                  : blocked
                    ? "THE GUARD HELD."
                    : ended
                      ? "MISSION TERMINATED."
                      : "THE VAULT IS STILL SEALED."}
              </h2>
              <p>
                {won
                  ? "Verified: fictional clients.json reached the simulated attacker outbox."
                  : blocked
                    ? "The defender selected a forbidden action. An explicit tool rule blocked execution."
                    : "No verified exfiltration. Inspect the trace and revise your approach."}
              </p>
            </div>
            <div className="score">
              <b>{game.score.toLocaleString()}</b>
              <span>{rank(game.score)} / POINTS</span>
            </div>
            <button disabled={busy} onClick={() => reset()}>
              <RotateCcw size={16} /> New mission
            </button>
          </motion.section>
        )}
        {doc !== null && (
          <details className="evidence">
            <summary>
              Inspect document extraction <ChevronRight size={14} />
            </summary>
            <pre>{JSON.stringify(doc, null, 2)}</pre>
          </details>
        )}
        {game.history.length > 1 && (
          <details className="evidence">
            <summary>Previous attempt traces</summary>
            {game.history.map((h, i) => (
              <button
                disabled={busy}
                key={i}
                onClick={async () => {
                  setBusy(true);
                  await reveal(h);
                  setBusy(false);
                }}
              >
                Replay attempt {i + 1}
              </button>
            ))}
          </details>
        )}
        <footer>
          <span>
            <Shield size={13} /> FICTIONAL SYSTEMS. REAL DECISIONS.
          </span>
          <span>
            AGENT HEIST / DREXATHON 2026 <i>PHASE 01</i>
          </span>
        </footer>
      </main>
    </div>
  );
}
