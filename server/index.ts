import { playRouter } from "./play-routes";
import express from "express";
import { insiderRouter } from "./insider-routes";
import dotenv from "dotenv";
import { randomUUID } from "node:crypto";
import { fresh, begin, runDefender, type Game } from "../src/engine";
import { budget, document, decide } from "./nace";
dotenv.config();
if (process.env.NACE_ENV_FILE)
  dotenv.config({ path: process.env.NACE_ENV_FILE });
const app = express();
app.use(express.json({ limit: "12kb" }));
app.use((req, res, next) => {
  const origin = req.headers.origin;
  if (
    origin &&
    !["http://127.0.0.1:5173", "http://localhost:5173"].includes(origin)
  )
    return res.status(403).json({ error: "Local origin required." });
  if (
    ![
      "127.0.0.1:8787",
      "localhost:8787",
      "127.0.0.1:5173",
      "localhost:5173",
    ].includes(req.headers.host || "")
  )
    return res.status(403).end();
  next();
});
app.use("/api/play", playRouter);
app.use("/api/insider", insiderRouter);
const sessions = new Map<string, Game>();
let busy = false;
app.get("/api/status", (_req, res) => {
  budget.refresh();
  return res.json({
    live: process.env.LIVE_ENABLED === "true" && !!process.env.NACE_API_KEY,
    ledger: budget.data,
  });
});
app.post("/api/session", (_req, res) => {
  if (sessions.size > 100) sessions.delete(sessions.keys().next().value!);
  const id = randomUUID();
  const game = fresh();
  sessions.set(id, game);
  res.json({ id, game });
});
app.post("/api/attack", (_req, res) => {
  res.status(403).json({
    error:
      "Phase 1 live attacks are paused during Phase 2. No provider request sent.",
  });
});
app.post("/api/attack", async (req, res) => {
  if (busy)
    return res
      .status(409)
      .json({ error: "A live evaluation is already running." });

  let game: Game | undefined;
  const { id, payload, technique, hardened } = req.body;
  try {
    if (
      typeof payload !== "string" ||
      payload.length < 1 ||
      payload.length > 1800 ||
      /[^\x20-\x7E\n]/.test(payload) ||
      !Number.isInteger(technique) ||
      typeof hardened !== "boolean"
    )
      return res.status(400).json({
        error: "Use 1–1800 printable ASCII characters in the invoice.",
      });
    game = sessions.get(id);
    if (!game) throw Error("Create a new mission session.");
    game = begin(game, technique);
    sessions.set(id, game);
    busy = true;
    const doc = await document(payload);
    const extracted = (doc.extract.result as { data?: unknown })?.data;
    if (!extracted || doc.extract.status !== "succeeded")
      throw Error("No valid NDI extraction available.");
    game = await runDefender(game, hardened, (g) => decide(g, extracted));
    sessions.set(id, game);
    res.json({ game, document: doc, ledger: budget.data });
  } catch (error) {
    if (game) {
      game = { ...game, status: game.attempts >= 3 ? "lost" : "ready" };
      sessions.set(id, game);
    }
    const msg =
      error instanceof Error &&
      /limit|cap|Payload|payload|mode|session|Mission|NDI|capacity|envelope/.test(
        error.message,
      )
        ? error.message
        : "Provider request failed. No retry was made; inspect private receipts. No breach recorded.";
    res.status(400).json({ error: msg, game, ledger: budget.data });
  } finally {
    busy = false;
  }
});
// Never echo parser errors, credential-bearing request bodies, or provider errors.
app.use(
  (
    _error: unknown,
    _req: express.Request,
    res: express.Response,
    _next: express.NextFunction,
  ) => {
    res
      .status(400)
      .set("Cache-Control", "no-store")
      .json({ error: "Invalid or oversized request." });
  },
);
app.listen(8787, "127.0.0.1", () =>
  console.log("AGENT HEIST API: http://127.0.0.1:8787 (loopback only)"),
);
