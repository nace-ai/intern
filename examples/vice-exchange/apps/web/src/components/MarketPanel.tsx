import { useEffect, useMemo, useState } from "react";
import { RULES } from "@vice/contracts";
import type { ListingView, PricePoint } from "@vice/contracts";
import { useGame, useMyCharacter } from "../state/gameStore";
import { newId, send } from "../net/socketClient";
import { pct, vd } from "../lib/format";

function Chart({ points, halted }: { points: PricePoint[]; halted: boolean }) {
  const W = 300, H = 70;
  if (points.length < 2) return <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Not enough price history yet" />;
  const t0 = points[0]!.t, t1 = points[points.length - 1]!.t || t0 + 1;
  const max = Math.max(...points.map((p) => Math.max(p.spot, p.reference)), 1);
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W;
  const y = (v: number) => H - 4 - (v / max) * (H - 10);
  const line = (key: "spot" | "reference") => points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const last = points[points.length - 1]!;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
      aria-label={`Price chart. Last price V$${last.spot}, reference value V$${last.reference}.`}>
      <path d={`${line("spot")} L${W},${H} L0,${H} Z`} className={halted ? "area halted" : "area"} />
      <path d={line("reference")} className="ref" />
      <path d={line("spot")} className={halted ? "spot halted" : "spot"} />
    </svg>
  );
}

function change(l: ListingView): number {
  const h = l.history;
  if (h.length < 2 || l.status !== "ACTIVE") return 0;
  const cutoff = h[h.length - 1]!.t - 60000;
  const base = h.find((p) => p.t >= cutoff) ?? h[0]!;
  return base.spot ? ((l.spotPrice - base.spot) / base.spot) * 100 : 0;
}

function TickerCard({ l, selected, onSelect }: { l: ListingView; selected: boolean; onSelect(): void }) {
  const wallet = useGame((s) => s.wallet);
  const role = useGame((s) => s.session?.role);
  const toast = useGame((s) => s.toast);
  const [cool, setCool] = useState(0);
  const [busy, setBusy] = useState(false);
  const halted = l.status !== "ACTIVE";
  const shares = wallet?.holdings.find((h) => h.ticker === l.ticker)?.shares ?? 0;
  useEffect(() => {
    if (cool <= Date.now()) return;
    const t = setTimeout(() => setCool(0), cool - Date.now());
    return () => clearTimeout(t);
  }, [cool]);
  const order = async (side: "buy" | "sell") => {
    setBusy(true);
    const ack = await send({ type: side === "buy" ? "market.buy" : "market.sell", ticker: l.ticker, qty: 1, commandId: newId() });
    setBusy(false);
    if (ack.ok) { setCool(Date.now() + RULES.trade.cooldownMs); toast(`${side === "buy" ? "Bought" : "Sold"} 1 ${l.ticker}`, "good"); }
    else {
      const reason = (ack.error?.reason ?? ack.error?.message ?? "rejected").toString().toLowerCase().replace(/_/g, " ");
      toast(`Order rejected: ${reason}`, "bad");
      if (ack.error?.reason === "COOLDOWN") setCool(Date.now() + RULES.trade.cooldownMs);
    }
  };
  const ch = change(l);
  const canTrade = role !== "SPECTATOR" && !!wallet && !halted;
  const cooling = cool > Date.now();
  return (
    <div className={`tcard ${halted ? "halted" : ""} ${selected ? "sel" : ""}`} onClick={onSelect}>
      <div className="tcard-top">
        <span className="tk">{l.ticker}</span>
        <span className="nm">{l.nickname}</span>
        <span className="px">{vd(l.spotPrice)}</span>
        {halted ? <span className="chg down">HALTED</span> : <span className={`chg ${ch >= 0 ? "up" : "down"}`}>{ch >= 0 ? "▲" : "▼"} {pct(ch)}</span>}
      </div>
      <Chart points={l.history} halted={halted} />
      {halted ? (
        <div className="tcard-meta">Captured · last traded {vd(l.finalSpotBeforeHalt)} → <b>V$0</b> · history kept, trading closed</div>
      ) : (
        <>
          <div className="tcard-meta">
            <span>bid {vd(l.bid)}</span><span>ask {vd(l.ask)}</span><span>ref {vd(l.referenceValue)}</span>
            <span title="What a captor earns right now; grows the longer this outlaw survives">bounty {vd(l.currentBounty)}</span>
            <span>net 60s {l.netBuyUnits >= 0 ? "+" : ""}{l.netBuyUnits}</span>
          </div>
          {canTrade && (
            <div className="tcard-btns" onClick={(e) => e.stopPropagation()}>
              <button className="buy" disabled={busy || cooling || shares >= RULES.trade.maxSharesPerTicker} onClick={() => void order("buy")}>BUY 1 @ {vd(l.ask)}</button>
              <button className="sell" disabled={busy || cooling || shares <= 0} onClick={() => void order("sell")}>SELL 1 @ {vd(l.bid)}</button>
              <span className="own">{cooling ? "cooldown 2 s…" : `you hold ${shares}/${RULES.trade.maxSharesPerTicker}`}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function MarketPanel() {
  const listings = useGame((s) => s.listings);
  const wallet = useGame((s) => s.wallet);
  const trades = useGame((s) => s.trades);
  const selectedTicker = useGame((s) => s.selectedTicker);
  const me = useMyCharacter();
  const sorted = useMemo(() => Object.values(listings).sort((a, b) =>
    (a.status === b.status ? b.openedAtMs - a.openedAtMs : a.status === "ACTIVE" ? -1 : 1)), [listings]);
  return (
    <section className="panel market" aria-label="Market">
      <h2>MARKET <span className="h2-sub" title="The server is the simulated house counterparty. Max 10 shares per ticker, one order every 2 s.">fictional V$ · 1 share per order</span></h2>
      {wallet ? (
        <div className="wallet">
          <div><span className="k">Wallet</span><b>{vd(wallet.balanceCredits)}</b></div>
          <div><span className="k">Net worth</span><b>{vd(wallet.netWorth)}</b></div>
          <div className="holds">{wallet.holdings.filter((h) => h.shares > 0).length === 0 ? <span className="dim">no holdings</span> :
            wallet.holdings.filter((h) => h.shares > 0).map((h) => <span key={h.ticker} className="hold">{h.ticker} ×{h.shares} = {vd(h.marketValue)}</span>)}</div>
        </div>
      ) : <div className="wallet dim">Spectators do not trade.</div>}
      <div className="tcards">
        {sorted.length === 0 && <div className="dim pad">No tickers yet. An outlaw has to join.</div>}
        {sorted.map((l) => <TickerCard key={l.ticker} l={l} selected={(selectedTicker ?? me?.ticker) === l.ticker} onSelect={() => useGame.setState({ selectedTicker: l.ticker })} />)}
      </div>
      <h3>TRADE FEED</h3>
      <ul className="feed">
        {trades.length === 0 && <li className="dim">No trades yet.</li>}
        {[...trades].slice(-12).reverse().map((t) => (
          <li key={t.tradeId}><span className={t.side === "BUY" ? "up" : "down"}>{t.side}</span> {t.qty} {t.ticker} @ {vd(t.executionPrice)} <span className="dim">· {t.actorName}{t.actorKind === "NPC" ? " (NPC)" : ""}</span></li>
        ))}
      </ul>
    </section>
  );
}
