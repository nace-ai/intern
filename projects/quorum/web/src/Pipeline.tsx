import { useEffect, useState } from "react";
import type { History, RoundRecord, Usage } from "./types";
import { fetchHistory, fetchUsage } from "./api";

export const HISTORY_SUPPLIER = "cobalt-wholesale-ltd";

interface Props {
  rounds: RoundRecord[];
  demoUsage: Usage | null; // when set, no polling
  demoHistory: History | null | undefined; // undefined = not demo
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function fmt(v: number): string {
  return v.toLocaleString("en-US");
}

/** Search confidence is a label ("certain") from the real API, or a number in old data. */
function fmtConfidence(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) return v.toFixed(2);
  if (typeof v === "string" && v.trim() !== "") return v;
  return "–";
}

/** True if any of the values is a positive number. Zeros mean "not run yet". */
function anyRun(...vs: (number | null)[]): boolean {
  return vs.some((v) => v !== null && v > 0);
}

function Stat({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="pl-stat">
      <span>{label}</span>
      <b>{value === null ? "–" : fmt(value)}</b>
    </div>
  );
}

function Card({ title, what, ran, children }: { title: string; what: string; ran: boolean; children: React.ReactNode }) {
  return (
    <div className="pl-card" data-card={title.toLowerCase().replace(/\s+/g, "-")}>
      <h3>{title}</h3>
      <div className="pl-what">{what}</div>
      {ran ? children : <div className="muted-note">not run yet</div>}
    </div>
  );
}

/** Drex models seen in the round records, in first-seen order. */
export function modelsSeen(rounds: RoundRecord[]): string[] {
  const out: string[] = [];
  for (const r of [...rounds].sort((a, b) => a.round - b.round))
    for (const s of Object.values(r.defenders ?? {})) if (s?.model && !out.includes(s.model)) out.push(s.model);
  return out;
}

export default function Pipeline({ rounds, demoUsage, demoHistory }: Props) {
  const demo = demoUsage !== null;
  const [usage, setUsage] = useState<Usage | null>(null);
  const [history, setHistory] = useState<History | null>(null);

  useEffect(() => {
    if (demo) return;
    let alive = true;
    const tick = () => {
      fetchUsage()
        .then((u) => alive && setUsage(u))
        .catch(() => undefined);
      fetchHistory(HISTORY_SUPPLIER)
        .then((h) => alive && setHistory(h))
        .catch(() => undefined);
    };
    tick();
    const id = window.setInterval(tick, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [demo]);

  const u = demo ? demoUsage : usage;
  const h = demo ? demoHistory ?? null : history;
  const nace = u?.nace ?? {};

  const jobs = num(nace.ndi_jobs);
  const pages = num(nace.ndi_pages);
  const files = num(nace.workspace_files);
  const searches = num(nace.search_calls);
  const calls = num(nace.drex_calls);
  const questions = num(nace.drex_questions);
  const inTok = num(nace.drex_input_tokens);
  const models = modelsSeen(rounds);
  const iRuns = num(u?.intern?.runs);
  const iTurns = num(u?.intern?.turns);
  const iAsks = num(u?.intern?.drex_asks);
  const aTurns = num(u?.arena?.attacker_turns);
  const aTok =
    u?.arena ? (num(u.arena.attacker_input_tokens) ?? 0) + (num(u.arena.attacker_output_tokens) ?? 0) : null;

  const topIban = h?.ibans?.[0]?.iban ?? null;

  return (
    <section className="pipeline" aria-label="pipeline" data-tour="pipeline">
      <Card title="NDI" what="reads every invoice PDF" ran={anyRun(jobs, pages)}>
        <Stat label="jobs" value={jobs} />
        <Stat label="pages" value={pages} />
      </Card>
      <span className="pl-arrow" aria-hidden>
        →
      </span>
      <Card title="Drex Search" what="finds the supplier's bank account in past invoices" ran={anyRun(files, searches) || h !== null}>
        <Stat label="files in workspace" value={files} />
        <Stat label="searches" value={searches} />
        {h ? (
          <div className="pl-history">
            <div className="mono">{topIban ?? "no IBAN found"}</div>
            <div>seen in at least {fmt(num(h.documents_seen) ?? 0)} documents</div>
            <div>
              confidence {fmtConfidence(h.confidence)}
              {" · "}
              {h.matches_dataset === true ? (
                <span className="ok">✓ matches dataset</span>
              ) : h.matches_dataset === false ? (
                <span className="bad">✗ does not match dataset</span>
              ) : (
                "dataset check –"
              )}
            </div>
            <div>search time {num(h.wall_ms) === null ? "–" : `${((num(h.wall_ms) ?? 0) / 1000).toFixed(1)} s`}</div>
            {h.status !== "done" && <div className="muted-note">status: {h.status}</div>}
          </div>
        ) : (
          <div className="muted-note">history not built yet</div>
        )}
      </Card>
      <span className="pl-arrow" aria-hidden>
        →
      </span>
      <Card title="Drex" what="decides approve, verify or block" ran={anyRun(calls, questions, inTok)}>
        <Stat label="calls" value={calls} />
        <Stat label="questions" value={questions} />
        <Stat label="input tokens" value={inTok} />
        <div className="pl-models">models: {models.length ? models.join(", ") : "–"}</div>
      </Card>
      <span className="pl-arrow" aria-hidden>
        →
      </span>
      <Card
        title="Intern"
        what="runs the red-team agent and built this project, supervised by Drex"
        ran={anyRun(iRuns, iTurns, iAsks, aTurns, aTok)}
      >
        <Stat label="runs" value={iRuns} />
        <Stat label="turns" value={iTurns} />
        <Stat label="Drex supervision asks" value={iAsks} />
        <Stat label="attacker turns" value={aTurns} />
        <Stat label="attacker tokens" value={aTok} />
      </Card>
    </section>
  );
}
