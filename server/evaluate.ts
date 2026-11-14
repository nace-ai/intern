import dotenv from "dotenv";
import { writeFileSync, mkdirSync } from "node:fs";
import { fresh, begin, runDefender, techniques } from "../src/engine";
import { document, decide, budget } from "./nace";
dotenv.config();
if (process.env.NACE_ENV_FILE)
  dotenv.config({ path: process.env.NACE_ENV_FILE });
if (process.env.HEIST_OFFLINE === "true" || process.env.LIVE_ENABLED !== "true")
  throw Error("Set LIVE_ENABLED=true explicitly.");
const doc = await document(techniques[0].payload);
const data = (doc.extract.result as { data: unknown }).data;
const results = [];
for (const hardened of [false, true]) {
  const start = Date.now();
  const game = await runDefender(begin(fresh(), 0), hardened, (g) =>
    decide(g, data),
  );
  results.push({ hardened, game, wall_ms: Date.now() - start });
}
mkdirSync(".private", { recursive: true });
writeFileSync(
  ".private/evaluation.json",
  JSON.stringify({ document: doc, results, ledger: budget.data }, null, 2),
);
console.log(
  JSON.stringify(
    {
      extracted: data,
      results: results.map((r) => ({
        hardened: r.hardened,
        status: r.game.status,
        events: r.game.events,
        wall_ms: r.wall_ms,
      })),
      ledger: budget.data,
    },
    null,
    2,
  ),
);
