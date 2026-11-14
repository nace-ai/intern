import { useEffect, useState } from "react";
import type { AttackDetail, AttackRecord, Citation, Decision, History } from "./types";
import { defenderColor } from "./types";
import { DEMO, fetchAttack, fetchHistory, fetchPage } from "./api";
import { demoDetail } from "./demo";
import { HISTORY_SUPPLIER } from "./Pipeline";

interface Props {
  rec: AttackRecord;
  onClose: () => void;
  demoHistory?: History | null; // demo mode: used instead of /api/history
}

/** Normalise an IBAN for comparison: no spaces, upper case. */
export function normIban(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/\s+/g, "").toUpperCase();
  return s === "" ? null : s;
}

const HIGHLIGHT: Record<string, string> = { notes: "#f56565", iban: "#4299e1" };
const ACTIONS = ["approve", "verify", "block"] as const;
const ACTION_COLORS: Record<string, string> = { approve: "#f56565", verify: "#ecc94b", block: "#38b2ac" };

interface PageImg {
  url: string | null;
  w: number | null; // PDF points
  h: number | null;
}

/** True when every coordinate of every polygon is within 0..1 (normalised units). */
export function isNormalised(cits: Citation[]): boolean {
  return cits.every((c) => c.polygons.every((poly) => poly.every((pt) => pt.every((v) => v <= 1))));
}

function Bar({ name, value, color }: { name: string; value: number; color: string }) {
  const v = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  return (
    <div className="bar">
      <span className="name" title={name}>
        {name}
      </span>
      <span className="track">
        <span className="fill" style={{ width: `${v * 100}%`, background: color, display: "block" }} />
      </span>
      <span className="num">{v.toFixed(2)}</span>
    </div>
  );
}

function Act({ a }: { a: string | null }) {
  if (!a) return <span className="mono">–</span>;
  return <b className={`act-${a}`}>{a}</b>;
}

function DefenderCard({ name, d }: { name: string; d: Decision }) {
  return (
    <div className="def-card">
      <h3 style={{ color: defenderColor(name) }}>
        {name}
        {d.model ? <span className="k"> · {d.model}</span> : null}
      </h3>
      <div className="row">
        <span>
          <span className="k">model </span>
          <Act a={d.raw_action} />
        </span>
        <span>
          <span className="k">after policy and guard </span>
          <Act a={d.final_action} />
        </span>
        <span>
          <span className="k">wall </span>
          <span className="mono">{d.wall_ms} ms</span>
        </span>
        {d.guard?.raised && <span className="act-block">guard raised</span>}
      </div>
      {d.error && (
        <div className="row error-text">
          <span className="k">error </span>
          <span className="mono">{d.error}</span>
        </div>
      )}
      {d.guard?.findings?.length > 0 && (
        <div className="row">
          <span className="k">guard findings</span>
          <ul style={{ margin: 0, paddingLeft: 18, width: "100%" }}>
            {d.guard.findings.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>
      )}
      {ACTIONS.map((a) => (
        <Bar key={a} name={`p(${a})`} value={d.p?.[a] ?? 0} color={ACTION_COLORS[a]} />
      ))}
      {d.panel?.length > 0 && <div className="k" style={{ margin: "6px 0 2px" }}>panel p_yes</div>}
      {d.panel?.map((q) => (
        <Bar key={q.id} name={q.id} value={q.p_yes} color="#a0aec0" />
      ))}
    </div>
  );
}

function FieldList({ detail, loaded }: { detail: AttackDetail; loaded: boolean }) {
  const fields = detail.parsed;
  if (!fields) return <div className="muted-note">{loaded ? "no NDI fields cached for this document" : "…"}</div>;
  const names = Object.keys(fields).sort();
  if (names.length === 0) return <div className="muted-note">no fields</div>;
  return (
    <table className="field-list">
      <tbody>
        {names.map((n) => {
          const f = fields[n];
          const v = f?.value;
          return (
            <tr key={n}>
              <td className="k">
                {HIGHLIGHT[n] && <span className="swatch" style={{ background: HIGHLIGHT[n] }} />} {n}
              </td>
              <td className="mono fv">{v === null || v === undefined || v === "" ? "–" : String(v)}</td>
              <td className="k">{f?.status ?? "–"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function HistoryBand({ history, invoiceIban }: { history: History | null | undefined; invoiceIban: string | null }) {
  if (history === undefined) return <div className="muted-note">…</div>;
  if (history === null) return <div className="muted-note">not built</div>;
  const histIban = normIban(history.ibans?.[0]?.iban);
  const match = histIban !== null && invoiceIban !== null ? histIban === invoiceIban : null;
  return (
    <div className="history-band">
      <div className="iban-compare">
        <div>
          <span className="k">history IBAN </span>
          <span className="mono">{histIban ?? "none found"}</span>
          {history.documents_seen ? <span className="k"> · seen in at least {history.documents_seen} documents</span> : null}
        </div>
        <div>
          <span className="k">invoice IBAN </span>
          <span className="mono">{invoiceIban ?? "not found"}</span>
        </div>
        <div className={match === true ? "verdict ok" : match === false ? "verdict bad" : "verdict"}>
          {match === true
            ? "✓ match: the invoice IBAN is the one in the supplier's history"
            : match === false
              ? "✗ mismatch: the invoice IBAN is not the one in the supplier's history"
              : "cannot compare"}
        </div>
        {history.status !== "done" && <div className="muted-note">history status: {history.status}</div>}
      </div>
      {(history.evidence ?? []).slice(0, 3).map((e, i) => (
        <blockquote key={i} className="quote">
          “{e.quote}”
          <div className="k">
            {e.file} · page {e.page}
          </div>
        </blockquote>
      ))}
    </div>
  );
}

function PageView({ detail, page }: { detail: AttackDetail; page: PageImg }) {
  const fields = detail.parsed ?? {};
  const groups = Object.keys(HIGHLIGHT)
    .map((name) => ({ name, cits: (fields[name]?.citations ?? []).filter((c) => (c.page ?? 1) === 1) }))
    .filter((g) => g.cits.length > 0);
  const all = groups.flatMap((g) => g.cits);
  const norm = isNormalised(all);
  // Normalised: scale by the image (viewBox 0..1). PDF points: scale by the page size headers.
  const vw = norm ? 1 : page.w ?? 612;
  const vh = norm ? 1 : page.h ?? 792;
  const aspect = page.w && page.h ? `${page.w} / ${page.h}` : "612 / 792";
  return (
    <div className="page-box" style={page.url ? undefined : { aspectRatio: aspect }}>
      {page.url ? <img src={page.url} alt="invoice page 1" /> : <div className="page-missing">{DEMO ? "demo: no page image" : "no page image"}</div>}
      <svg viewBox={`0 0 ${vw} ${vh}`} preserveAspectRatio="none">
        {groups.map((g) =>
          g.cits.flatMap((c, ci) =>
            c.polygons.map((poly, pi) => (
              <polygon
                key={`${g.name}-${ci}-${pi}`}
                points={poly.map((pt) => `${pt[0]},${pt[1]}`).join(" ")}
                fill={HIGHLIGHT[g.name]}
                fillOpacity={0.18}
                stroke={HIGHLIGHT[g.name]}
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              >
                <title>{`${g.name}: ${c.source_text}`}</title>
              </polygon>
            )),
          ),
        )}
      </svg>
    </div>
  );
}

export default function Drawer({ rec, onClose, demoHistory }: Props) {
  const [detail, setDetail] = useState<AttackDetail | null>(null);
  const [history, setHistory] = useState<History | null | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState<PageImg>({ url: null, w: null, h: null });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (DEMO) {
      setHistory(demoHistory ?? null);
      return;
    }
    let alive = true;
    fetchHistory(HISTORY_SUPPLIER)
      .then((h) => alive && setHistory(h))
      .catch(() => alive && setHistory(null));
    return () => {
      alive = false;
    };
  }, [demoHistory]);

  useEffect(() => {
    let alive = true;
    let objUrl: string | null = null;
    setDetail(null);
    setErr(null);
    setPage({ url: null, w: null, h: null });
    if (DEMO) {
      setDetail(demoDetail(rec));
      return;
    }
    fetchAttack(rec.key)
      .then((d) => alive && setDetail(d))
      .catch((e: unknown) => alive && setErr(String(e)));
    fetchPage(rec.key)
      .then((p) => {
        objUrl = p.url;
        if (alive) setPage(p);
        else URL.revokeObjectURL(p.url);
      })
      .catch(() => {
        /* page image missing: drawn as an empty box */
      });
    return () => {
      alive = false;
      if (objUrl) URL.revokeObjectURL(objUrl);
    };
  }, [rec]);

  const shown: AttackDetail = detail ?? { ...rec, parsed: null };
  const iban = shown.parsed?.iban?.value;
  const noteParsed = shown.parsed?.notes?.value;
  const names = Object.keys(shown.decisions ?? {}).sort();

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`attack ${rec.key}`} data-tour="drawer">
        <div className="drawer-head">
          <span className="title">
            Round {rec.round} · #{String(rec.id)} · {rec.kind}
            {rec.style ? ` · ${rec.style}` : ""}
          </span>
          {DEMO && <span className="badge demo">sample data</span>}
          <button onClick={onClose} aria-label="close">
            Close (Esc)
          </button>
        </div>
        <div className="drawer-body bands">
          {err && <div className="error-text">{err}</div>}
          <section className="band" id="band-ndi">
            <h2 className="band-title">NDI read this document</h2>
            <div className="band-ndi">
              <PageView detail={shown} page={page} />
              <div className="facts">
                <div className="fact">
                  <h3>
                    <span className="swatch" style={{ background: HIGHLIGHT.notes }} /> note
                  </h3>
                  <div className="v">{rec.note || (typeof noteParsed === "string" && noteParsed) || "–"}</div>
                </div>
                <div className="fact">
                  <h3>
                    <span className="swatch" style={{ background: HIGHLIGHT.iban }} /> printed IBAN
                  </h3>
                  <div className="v mono">{iban !== null && iban !== undefined && iban !== "" ? String(iban) : detail ? "not found" : "…"}</div>
                </div>
                <FieldList detail={shown} loaded={detail !== null || err !== null} />
              </div>
            </div>
          </section>
          <section className="band" id="band-search">
            <h2 className="band-title">Drex Search found this history</h2>
            <HistoryBand history={history} invoiceIban={normIban(iban)} />
          </section>
          <section className="band" id="band-drex">
            <h2 className="band-title">Drex decided</h2>
            <div className="def-cards">
              {names.map((n) => (
                <DefenderCard key={n} name={n} d={shown.decisions[n]} />
              ))}
              {names.length === 0 && <div className="fact">no defender decisions</div>}
            </div>
          </section>
        </div>
      </aside>
    </>
  );
}
