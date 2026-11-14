import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { CONTRACTS_PLACEHOLDER } from "@vice/contracts";
import { config, publicConfigSummary } from "./config";

describe("scaffold smoke", () => {
  it("loads a typed config with sane defaults", () => {
    expect(config.port).toBeGreaterThan(0);
    expect(config.tickHz).toBeGreaterThan(0);
    expect(config.decisionConcurrency).toBeGreaterThan(0);
    expect(config.naceModel.length).toBeGreaterThan(0);
    expect(config.metaBaseUrl.startsWith("https://")).toBe(true);
  });

  it("exposes only booleans in the public config summary", () => {
    const summary = publicConfigSummary();
    expect(Object.values(summary).every((v) => typeof v === "boolean")).toBe(true);
  });

  it("resolves the @vice/contracts workspace package from TypeScript source", () => {
    expect(CONTRACTS_PLACEHOLDER).toBe(true);
  });

  it("can use the built-in node:sqlite module (no native dependency)", () => {
    const db = new DatabaseSync(":memory:");
    db.exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER NOT NULL CHECK (v >= 0))");
    db.prepare("INSERT INTO t (v) VALUES (?)").run(7);
    const row = db.prepare("SELECT v FROM t WHERE id = 1").get() as { v: number };
    expect(row.v).toBe(7);
    db.close();
  });
});
