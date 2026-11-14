import { useMemo } from "react";
import type { AgentView, DecisionTrace, GameEvent, MemoryEntry } from "@vice/contracts";
import { nameOf, useGame, useSelectedAgent } from "../state/gameStore";
import { ms, sourceLabel, traceHasProviderData, vd } from "../lib/format";
import SwarmGraph from "./wow/SwarmGraph";
import AgentPnl from "./wow/AgentPnl";

const ROLE_ICON: Record<string, string> = { WITNESS: "👁", INFORMANT: "🗣", TRADER: "📈", POLICE: "🛡" };
const PROV: Record<string, string> = { DIRECT: "saw it first-hand", MESSAGE: "was told", BULLETIN: "public bulletin", DOCUMENT: "policy document (NDI)" };

function Decision({ t, full }: { t: DecisionTrace; full: boolean }) {
  const src = sourceLabel(t.decisionSource);
  const real = traceHasProviderData(t);
  const labels = Object.keys(t.offeredActions);
  return (
    <div className="dec">
      <div className="dec-top">
        <span className={`src ${src.cls}`}>{src.text}</span>
        <span className="dec-choice">{t.chosenAction ? `→ ${t.chosenAction}` : "thinking…"}</span>
        <span className="dim">{t.status}</span>
      </div>
      {full && <div className="dec-q">{t.question}</div>}
      <ul className="opts">
        {labels.map((l) => {
          const p = real && t.probabilities ? t.probabilities[l] : undefined;
          return (
            <li key={l} className={l === t.chosenAction ? "chosen" : ""}>
              <span className="opt-l">{l}</span>
              {p !== undefined ? (
                <><span className="bar" aria-hidden><i style={{ width: `${Math.round(p * 100)}%` }} /></span><span className="opt-p">{(p * 100).toFixed(1)}%</span></>
              ) : full ? <span className="opt-d">{t.offeredActions[l]}</span> : null}
            </li>
          );
        })}
      </ul>
      {real && !t.probabilities && full && <div className="dim small">This provider does not return probabilities, so none are shown.</div>}
      {t.providerChoice && t.providerChoice !== t.chosenAction && <div className="warnline">Provider answered “{t.providerChoice}”; applied “{t.chosenAction}” instead{t.rejectReason ? ` (${t.rejectReason})` : ""}.</div>}
      {(t.errorType || t.rejectReason) && !(t.providerChoice && t.providerChoice !== t.chosenAction) && <div className="warnline">{t.errorType ?? ""}{t.errorType && t.errorMessage ? ": " : ""}{t.errorMessage ?? ""}{t.rejectReason ? ` · ${t.rejectReason}` : ""}</div>}
      <div className="dec-meta">
        {real ? (
          <>
            <span>model <b>{t.model ?? "—"}</b></span>
            <span>confidence <b>{t.confidence != null ? t.confidence.toFixed(3) : "n/a"}</b></span>
            <span>round trip <b>{ms(t.latencyMs)}</b></span>
            <span>model eval <b>{ms(t.providerEvalMs)}</b></span>
            <span title={t.providerRequestId ?? ""}>request <b className="mono">{t.providerRequestId ?? "—"}</b></span>
            {t.usage && <span>tokens <b>{t.usage.inputTokens ?? "—"}/{t.usage.outputTokens ?? "—"}</b></span>}
          </>
        ) : t.decisionSource ? <span>No model was called for this decision{t.provider ? ` (${t.provider} was attempted)` : ""}.</span> : <span>waiting for the model…</span>}
      </div>
      {t.shadow && (
        <div className="dec-meta"><span>Shadow {t.shadow.provider} ({t.shadow.model ?? "—"}): <b>{t.shadow.chosenAction ?? t.shadow.errorType ?? "—"}</b> in {ms(t.shadow.latencyMs)}{t.shadow.agrees != null ? (t.shadow.agrees ? " · agrees" : " · disagrees") : ""} · not applied</span></div>
      )}
      {full && t.citations.length > 0 && (
        <div className="cites">{t.citations.map((c, i) => <div key={i} className="cite">📄 <b>{c.documentTitle}</b>{c.page != null ? ` p.${c.page}` : ""} — “{c.quote}” <span className="dim">[{c.extractedField}{c.ndiJobId ? ` · NDI job ${c.ndiJobId}` : ""}]</span></div>)}</div>
      )}
      {full && (
        <details className="state"><summary>Exactly what this NPC told the model (its own knowledge only)</summary><pre>{JSON.stringify(t.stateSnapshot, null, 2)}</pre></details>
      )}
    </div>
  );
}

function Mem({ m }: { m: MemoryEntry }) {
  const who = useGame((s) => nameOf(s, m.sourceActorId));
  return (
    <li className={`mem k-${m.sourceKind}`}>
      <span className="mem-type">{m.factType.replace(/_/g, " ")}</span> {m.summary}
      <div className="mem-prov">{PROV[m.sourceKind] ?? m.sourceKind}{m.sourceKind !== "DIRECT" ? ` · from ${who}` : ""} · hop {m.hop} · belief {(m.confidence * 100).toFixed(0)}%
        {m.citation ? ` · 📄 ${m.citation.documentTitle}` : ""}</div>
    </li>
  );
}

function AgentDetail({ a }: { a: AgentView }) {
  const events = useGame((s) => s.events);
  const state = useGame.getState;
  const msgs = useMemo(() => events.filter((e): e is GameEvent<"AGENT_MESSAGE_SENT"> => e.type === "AGENT_MESSAGE_SENT" &&
    ((e as GameEvent<"AGENT_MESSAGE_SENT">).payload.senderId === a.id || (e as GameEvent<"AGENT_MESSAGE_SENT">).payload.recipientId === a.id)).slice(-8).reverse(), [events, a.id]);
  const holdings = Object.entries(a.holdings).filter(([, n]) => n > 0);
  return (
    <div className="agent">
      <div className="agent-head">
        <span className="icon" aria-hidden>{ROLE_ICON[a.role]}</span>
        <div><div className="agent-name">{a.name}</div><div className="dim">{a.role} · {a.activity}{a.pendingDecisionId ? " · deciding…" : ""}</div></div>
      </div>
      <div className="agent-bio"><span className="k">Persona</span>{a.persona} <span className="k gap">Goal</span>{a.goal}</div>
      {(a.walletCredits != null || a.contacts.length > 0) && (
        <div className="agent-bio">
          {a.walletCredits != null && <><span className="k">Wallet</span>{vd(a.walletCredits)}{holdings.length ? ` · holds ${holdings.map(([t, n]) => `${t}×${n}`).join(", ")}` : " · no holdings"} </>}
          {a.contacts.length > 0 && <><span className="k gap">Tells</span>{a.contacts.map((c) => nameOf(state(), c)).join(", ")}</>}
        </div>
      )}
      <h3>LAST DECISION <span className="h2-sub">what it was offered, what it chose, and who actually decided</span></h3>
      {a.lastDecision ? <Decision t={a.lastDecision} full /> : <div className="dim">No decision yet.</div>}
      <h3>KNOWS ({a.memories.length}) <span className="h2-sub">private memory — nobody else has this unless told</span></h3>
      <ul className="mems">{a.memories.length === 0 ? <li className="dim">Knows nothing yet.</li> : a.memories.slice(0, 8).map((m) => <Mem key={m.id} m={m} />)}</ul>
      <h3>MESSAGES</h3>
      <ul className="feed">{msgs.length === 0 ? <li className="dim">No messages sent or received.</li> : msgs.map((e) => (
        <li key={e.eventId}>{e.payload.senderId === a.id ? "→ to" : "← from"} <b>{nameOf(state(), e.payload.senderId === a.id ? e.payload.recipientId : e.payload.senderId)}</b> [{e.payload.kind}, hop {e.payload.hop}] {e.payload.summary}</li>))}</ul>
      {a.recentDecisions.length > 1 && (<><h3>EARLIER DECISIONS</h3>{a.recentDecisions.filter((d) => d.id !== a.lastDecision?.id).slice(0, 5).map((d) => <Decision key={d.id} t={d} full={false} />)}</>)}
    </div>
  );
}

export function AgentInspector() {
  const agent = useSelectedAgent();
  const npcs = useGame((s) => s.npcs);
  const selectAgent = useGame((s) => s.selectAgent);
  const list = useMemo(() => Object.values(npcs).sort((a, b) => a.id.localeCompare(b.id)), [npcs]);
  return (
    <section className="panel inspector" aria-label="Agent inspector">
      <h2>AGENT INTELLIGENCE <span className="h2-sub">click an NPC in the city or below</span></h2>
      <div className="chips">
        {list.map((n) => {
          const src = n.lastDecision?.decisionSource ?? null;
          return (
            <button key={n.id} className={`chip ${agent?.id === n.id ? "on" : ""} ${n.pendingDecisionId ? "pending" : ""}`} onClick={() => selectAgent(agent?.id === n.id ? null : n.id)}
              title={`${n.role}${n.lastDecision ? ` · last: ${n.lastDecision.chosenAction ?? "…"} (${sourceLabel(src).text})` : ""}`}>
              <span aria-hidden>{ROLE_ICON[n.role]}</span> {n.name.split(" ").slice(-1)[0]}
              {n.lastDecision?.chosenAction && <i className={`dot ${sourceLabel(src).cls}`} />}
            </button>
          );
        })}
      </div>
      {agent ? null : <AgentPnl />}
      {agent ? <AgentDetail a={agent} /> : <div className="swarm-idle"><SwarmGraph /></div>}
    </section>
  );
}
