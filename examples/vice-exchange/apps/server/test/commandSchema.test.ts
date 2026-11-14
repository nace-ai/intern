import { describe, expect, it } from "vitest";
import { clientCommandSchema } from "@vice/contracts";

const cid = "cmd_12345678";
describe("client command schema", () => {
  it("accepts the ids and tickers the server really issues", () => {
    expect(clientCommandSchema.safeParse({ type: "market.buy", ticker: "ACE-01", qty: 1, commandId: cid }).success).toBe(true);
    expect(clientCommandSchema.safeParse({ type: "market.sell", ticker: "MAN-104", qty: 1, commandId: cid }).success).toBe(true);
    expect(clientCommandSchema.safeParse({ type: "mission.interact", missionId: "mis_0001", commandId: cid }).success).toBe(true);
    expect(clientCommandSchema.safeParse({ type: "capture.attempt", targetCharacterId: "chr_0012", commandId: cid }).success).toBe(true);
  });

  // Audit finding: {ticker: "__proto__"} passed the schema and poisoned the room's dirty set, stopping every push.
  it.each(["__proto__", "constructor", "toString", "hasOwnProperty", "", "ace-01", "ACE-1", "ACE-01 ", "A".repeat(65)])(
    "rejects %j as a ticker", (ticker) => {
      expect(clientCommandSchema.safeParse({ type: "market.buy", ticker, qty: 1, commandId: cid }).success).toBe(false);
      expect(clientCommandSchema.safeParse({ type: "market.sell", ticker, qty: 1, commandId: cid }).success).toBe(false);
    });

  it.each(["__proto__", "constructor", "toString", "valueOf", "", "chr", "_chr_1"])("rejects %j as an entity id", (id) => {
    expect(clientCommandSchema.safeParse({ type: "mission.interact", missionId: id, commandId: cid }).success).toBe(false);
    expect(clientCommandSchema.safeParse({ type: "capture.attempt", targetCharacterId: id, commandId: cid }).success).toBe(false);
    expect(clientCommandSchema.safeParse({ type: "demo.incident", incidentType: "SIGHTING", commandId: cid, targetCharacterId: id }).success).toBe(false);
  });

  it("never lets a client set a quantity other than one share, a price, or a wallet total", () => {
    expect(clientCommandSchema.safeParse({ type: "market.buy", ticker: "ACE-01", qty: 5, commandId: cid }).success).toBe(false);
    const parsed = clientCommandSchema.safeParse({ type: "market.buy", ticker: "ACE-01", qty: 1, commandId: cid, price: 1, balance: 999999 });
    expect(parsed.success && Object.keys(parsed.data).sort()).toEqual(["commandId", "qty", "ticker", "type"]);
  });
});
