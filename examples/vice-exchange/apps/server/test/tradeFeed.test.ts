// Audit finding (engine and economy reviewers): once the trade list reached its cap of 200, every new trade was stored
// twice, so snapshots carried duplicate rows (and duplicate React keys) and only half the recent history.
import { describe, expect, it } from "vitest";
import { makeRoom } from "./helpers";

describe("trade feed", () => {
  it("never stores a trade twice, before or after the list reaches its cap", () => {
    const t = makeRoom("scripted");
    t.scheduler.auto = (req) => ({ action: "hold", source: "scripted" });
    const ace = t.join("Ace", "OUTLAW");
    t.run(300);
    const ticker = t.char(ace.id).ticker!;
    let executed = 0;
    for (let i = 0; i < 230; i++) {
      const ack = t.cmd(ace.id, { type: i % 2 === 0 ? "market.buy" : "market.sell", ticker, qty: 1 });
      if (ack.ok) executed += 1;
      t.run(2100); // past the per-wallet cooldown
    }
    expect(executed).toBe(230);
    const ids = t.engine.state.trades.map((x) => x.tradeId);
    expect(ids.length).toBeLessThanOrEqual(200);
    expect(new Set(ids).size).toBe(ids.length);
    const snap = t.engine.snapshot().trades.map((x) => x.tradeId);
    expect(new Set(snap).size).toBe(snap.length);
    expect(t.events("TRADE_EXECUTED")).toHaveLength(230);
  });
});
