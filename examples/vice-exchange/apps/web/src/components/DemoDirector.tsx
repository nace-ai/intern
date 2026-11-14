import { useState } from "react";
import type { ClientCommand, DecisionMode, ExperimentResult, LatencyStats } from "@vice/contracts";
import { useGame } from "../state/gameStore";
import { newId, send } from "../net/socketClient";
import { ms, vd } from "../lib/format";

const MODES: Array<{ id: DecisionMode; label: string; hint: string }> = [
  { id: "drex", label: "DREX", hint: "Nace Drex decides for every NPC (live calls)" },
  { id: "meta", label: "META LLM", hint: "Meta Muse decides (LLM baseline, live calls)" },
  { id: "scripted", label: "SCRIPTED", hint: "Fixed rules, no model is called" },
];

function Lat({ name, l }: { name: string; l: LatencyStats | null }) {
  return <div className="stat"><span className="k">{name} round trip</span>{l && l.count ? <b>p50 {ms(l.p50Ms)} · p95 {ms(l.p95Ms)} · max {ms(l.maxMs)} <span className="dim">({l.count} calls{l.evalP50Ms != null ? `, model eval p50 ${ms(l.evalP50Ms)}` : ""})</span></b> : <span className="dim">no calls measured yet</span>}</div>;
}

function Experiment({ x }: { x: ExperimentResult }) {
  return (
    <div className="exp">
      <div className="exp-head">Experiment {x.id} · seed {x.seed} · {x.durationSec}s per mode · <b>{x.status}</b></div>
      <table>
        <thead><tr><th>Mode</th><th>Decisions</th><th>Real model calls</th><th>Fallbacks</th><th>p50</th><th>p95</th><th>Messages</th><th>Rumor hops</th><th>NPC trades</th><th>Reported</th><th>Captured</th><th>Time to capture</th><th>Peak</th><th>Final</th><th>Actions</th></tr></thead>
        <tbody>
          {x.rows.map((r) => (
            <tr key={r.mode}>
              <td><b>{r.mode.toUpperCase()}</b></td><td>{r.decisions}</td><td>{r.realProviderCalls}</td><td>{r.fallbacks}</td>
              <td>{ms(r.latencyP50Ms)}</td><td>{ms(r.latencyP95Ms)}</td><td>{r.messages}</td><td>{r.rumorHops}</td><td>{r.npcTrades}</td>
              <td>{r.reportedToPolice ? "yes" : "no"}</td><td>{r.captured ? "yes" : "no"}</td><td>{r.timeToCaptureSec != null ? `${r.timeToCaptureSec.toFixed(1)} s` : "—"}</td>
              <td>{vd(r.peakPrice)}</td><td>{vd(r.finalPrice)}</td>
              <td className="small">{Object.entries(r.actions).map(([a, n]) => `${a}×${n}`).join(" ")}</td>
            </tr>
          ))}
          {x.rows.length === 0 && <tr><td colSpan={15} className="dim">running…</td></tr>}
        </tbody>
      </table>
      <div className="caveat">{x.caveat}</div>
    </div>
  );
}

export function DemoDirector() {
  const session = useGame((s) => s.session);
  const mode = useGame((s) => s.mode);
  const metrics = useGame((s) => s.metrics);
  const experiments = useGame((s) => s.experiments);
  const ps = useGame((s) => s.providerStatus);
  const seed = useGame((s) => s.seed);
  const runId = useGame((s) => s.runId);
  const policy = useGame((s) => s.policy);
  const toast = useGame((s) => s.toast);
  const [busy, setBusy] = useState<string | null>(null);
  const isDirector = !!session?.isDirector;
  const run = async (key: string, cmd: ClientCommand, okText: string) => {
    setBusy(key);
    const ack = await send(cmd, 15000);
    setBusy(null);
    toast(ack.ok ? okText : `${key} failed: ${ack.error?.message ?? "rejected"}`, ack.ok ? "good" : "bad");
  };
  const m = metrics;
  return (
    <div className="director">
      <div className="dir-col">
        <h3>CONTROLS {isDirector ? "" : <span className="h2-sub">director only — the room creator, or join with the director secret</span>}</h3>
        <div className="row wrap">
          <button className="primary" disabled={!isDirector || !!busy} onClick={() => void run("Reset", { type: "demo.reset", commandId: newId() }, "Room reset to the seeded start.")}>⟲ Seeded reset</button>
          <button disabled={!isDirector || !!busy} onClick={() => void run("Incident", { type: "demo.incident", incidentType: "SIGHTING", commandId: newId() }, "Incident triggered at the outlaw's position.")}>⚡ Trigger incident</button>
          <button disabled={!isDirector || !!busy} onClick={() => void run("Experiment", { type: "demo.runExperiment", commandId: newId() }, "Experiment started: scripted vs Meta vs Drex on the same seed.")}>🧪 Run scripted vs Meta vs Drex</button>
        </div>
        <div className="seg wide" role="group" aria-label="Decision mode">
          {MODES.map((x) => <button key={x.id} title={x.hint} className={mode === x.id ? "on" : ""} disabled={!isDirector || !!busy}
            onClick={() => void run("Mode", { type: "demo.setMode", mode: x.id, commandId: newId() }, `Decision mode is now ${x.label}.`)}>{x.label}</button>)}
        </div>
        <div className="dim small">Run {runId || "—"} · seed {seed} · experiment mode is always shown in the header and on every decision.</div>
        {ps && (
          <div className="prov">
            {(["drex", "meta"] as const).map((k) => { const h = ps[k]; return (
              <div key={k} className="stat"><span className="k">{k.toUpperCase()}</span><b className={h.state === "LIVE" ? "up" : h.state === "UNCONFIGURED" ? "dim" : "down"}>{h.state}</b> <span className="dim">{h.model ?? ""} · {h.callsLastMinute}/{h.maxCallsPerMinute} calls/min · {h.inFlight} in flight{h.lastErrorType ? ` · last error ${h.lastErrorType}` : ""}{h.circuitOpenUntilMs ? " · circuit open" : ""}</span></div>
            ); })}
          </div>
        )}
        {policy && policy.facts.length > 0 && (
          <div className="prov"><div className="stat"><span className="k">Policy doc</span><b>{policy.documentTitle}</b> <span className="dim">{policy.ingestedVia === "ndi" ? `parsed by NDI${policy.ndiJobId ? ` (job ${policy.ndiJobId})` : ""}` : "not parsed by NDI"} · {policy.facts.length} facts</span></div></div>
        )}
      </div>
      <div className="dir-col">
        <h3>LIVE METRICS <span className="h2-sub">measured in this run, nothing estimated</span></h3>
        {m ? (
          <div className="stats">
            <div className="stat"><span className="k">Decisions</span><b>{m.decisions.total}</b> <span className="dim">{Object.entries(m.decisions.bySource).map(([k, v]) => `${k} ${v}`).join(" · ") || "none yet"}{m.decisions.rejected ? ` · rejected ${m.decisions.rejected}` : ""}{m.decisions.stale ? ` · stale ${m.decisions.stale}` : ""}</span></div>
            <div className="stat"><span className="k">By role</span><span>{Object.entries(m.decisions.byRole).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(" · ") || "—"}</span></div>
            <div className="stat"><span className="k">By action</span><span>{Object.entries(m.decisions.byAction).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</span></div>
            <Lat name="Drex" l={m.latency.drex} />
            <Lat name="Meta" l={m.latency.meta} />
            <div className="stat"><span className="k">Messages</span><b>{m.messages.sent} sent · {m.messages.delivered} delivered</b> <span className="dim">longest rumor chain {m.messages.maxHops} hops</span></div>
            <div className="stat"><span className="k">Trades</span><b>{m.trades.executed}</b> <span className="dim">NPC {m.trades.byNpc} · human {m.trades.byHuman} · rejected {m.trades.rejected} · price updates {m.priceUpdates} · captures {m.captures}</span></div>
            <div className="stat"><span className="k">Who talks to whom</span><span>{m.edges.length ? m.edges.map((e) => `${useGame.getState().npcs[e.from]?.name.split(" ").slice(-1)[0] ?? e.from}→${useGame.getState().npcs[e.to]?.name.split(" ").slice(-1)[0] ?? e.to} (${e.kind.toLowerCase()}×${e.count})`).join(" · ") : "no messages yet"}</span></div>
          </div>
        ) : <div className="dim">No metrics yet.</div>}
      </div>
      <div className="dir-wide">
        <h3>BASELINE COMPARISON</h3>
        {experiments.length === 0 ? <div className="dim">No experiment run yet. It plays the same seeded scenario under each decision mode and reports what was measured.</div>
          : [...experiments].reverse().slice(0, 3).map((x) => <Experiment key={x.id} x={x} />)}
      </div>
    </div>
  );
}
