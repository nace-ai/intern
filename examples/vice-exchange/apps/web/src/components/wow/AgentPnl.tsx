// AGENT P&L: every agent is out for its own profit. Ranks the NPC agents by net worth (wallet + holdings at spot)
// with the change since the room started, and shows each outlaw's ticker price and the bounty on its head.
import { useMemo } from "react";
import { RULES } from "@vice/contracts";
import { useGame } from "../../state/gameStore";

const vd = (n: number): string => `V$${Math.round(n).toLocaleString("en-US")}`;
const ROLE_ICON: Record<string, string> = { WITNESS: "👁", INFORMANT: "💬", TRADER: "📈", POLICE: "🛡" };

export default function AgentPnl() {
  const npcs = useGame((s) => s.npcs);
  const listings = useGame((s) => s.listings);
  const characters = useGame((s) => s.characters);
  const select = (id: string) => useGame.setState({ selectedAgentId: id });

  const rows = useMemo(() => Object.values(npcs)
    .filter((n) => n.walletCredits !== null)
    .map((n) => {
      const held = Object.entries(n.holdings ?? {}).reduce((sum, [ticker, shares]) => sum + shares * (listings[ticker]?.spotPrice ?? 0), 0);
      const start = n.role === "TRADER" ? RULES.trade.startingWalletTrader : 0;
      const net = (n.walletCredits ?? 0) + held;
      return { id: n.id, name: n.name, role: n.role, net, delta: net - start };
    })
    .sort((a, b) => b.delta - a.delta || b.net - a.net), [npcs, listings]);

  const hunted = useMemo(() => Object.values(characters)
    .filter((c) => c.role === "OUTLAW" && c.ticker && (c.status === "ACTIVE" || c.status === "WANTED"))
    .map((c) => ({ id: c.id, name: c.nickname, ticker: c.ticker!, price: listings[c.ticker!]?.spotPrice ?? 0, bounty: listings[c.ticker!]?.currentBounty ?? 0, wanted: c.wanted })),
  [characters, listings]);

  if (!rows.length && !hunted.length) return null;
  const chip = { display: "inline-flex", alignItems: "center", gap: 6, padding: "2px 8px", borderRadius: 999, border: "1px solid rgba(120,140,200,.28)", background: "rgba(16,20,44,.7)", fontSize: 11, lineHeight: "18px", cursor: "pointer", whiteSpace: "nowrap" } as const;
  return (
    <div style={{ flex: "none", padding: "6px 8px 4px", display: "flex", flexDirection: "column", gap: 4 }} aria-label="Agent profit and loss">
      <div style={{ fontSize: 10, letterSpacing: ".14em", fontWeight: 700, color: "#39f0d0" }}>
        AGENT P&amp;L <span style={{ color: "#8f9ac4", fontWeight: 400, letterSpacing: 0 }}>· every agent is out for its own V$ · net worth and change since start</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {hunted.map((h) => (
          <span key={h.id} style={{ ...chip, borderColor: h.wanted ? "rgba(255,90,120,.7)" : "rgba(255,90,170,.4)", cursor: "default" }} title="Outlaw: worth more the longer it survives">
            <b style={{ color: "#ff5aa8" }}>{h.ticker}</b> {h.name} · {vd(h.price)} · bounty <b style={{ color: "#ffd166" }}>{vd(h.bounty)}</b>{h.wanted ? " · WANTED" : ""}
          </span>
        ))}
        {rows.map((r, i) => (
          <span key={r.id} style={chip} onClick={() => select(r.id)} title={`${r.role.toLowerCase()} · click to inspect`}>
            <span style={{ color: "#8f9ac4" }}>{i + 1}</span> {ROLE_ICON[r.role] ?? ""} {r.name.split(" ").slice(-1)[0]} <b>{vd(r.net)}</b>
            <b style={{ color: r.delta > 0 ? "#4ade80" : r.delta < 0 ? "#ff6b81" : "#8f9ac4" }}>{r.delta > 0 ? "+" : ""}{r.delta === 0 ? "±0" : Math.round(r.delta).toLocaleString("en-US")}</b>
          </span>
        ))}
      </div>
    </div>
  );
}
