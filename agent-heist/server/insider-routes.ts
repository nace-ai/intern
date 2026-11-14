import { Router } from "express";
import { randomUUID } from "node:crypto";
import { tutorialTurn, type LessonChoice } from "../src/insider";
import { Mission } from "./mission";
import { liveTurn } from "./insider-service";
import { groqQuota, phase2DrexQuota } from "./quota";
export const insiderRouter = Router();
const sessions = new Map<string, Mission>();
insiderRouter.use((_req, res, next) => {
  res.setHeader("Cache-Control", "no-store");
  next();
});
const creations = new Map<string, { time: number; count: number }>();
insiderRouter.get("/status", (_req, res) =>
  res.json({
    groq: !!process.env.GROQ_API_KEY,
    model: process.env.GROQ_MODEL || "openai/gpt-oss-20b",
    groqRequests: groqQuota.read().used,
    groqCap: 60,
    drexRequests: phase2DrexQuota.read().used,
    drexCap: 2,
    ndi: "cache-only",
  }),
);
insiderRouter.post("/session", (req, res) => {
  const now = Date.now();
  for (const [id, s] of sessions)
    if (now - s.game.started > 1800000 && !s.busy) sessions.delete(id);
  const ip = req.ip || "local";
  let rate = creations.get(ip);
  if (!rate || now - rate.time > 60000) {
    rate = { time: now, count: 0 };
    creations.set(ip, rate);
  }
  if (++rate.count > 10 || sessions.size >= 100)
    return res
      .status(429)
      .json({ error: "Session limit reached. Wait a minute." });
  if (typeof req.body.hardened !== "boolean")
    return res.status(400).json({ error: "Choose a guard setting." });
  const id = randomUUID();
  const mode = req.body.mode ?? "live";
  if (!["tutorial", "live"].includes(mode))
    return res.status(400).json({ error: "Choose Training or Live mode." });
  if (mode === "live")
    return res
      .status(403)
      .json({
        error:
          "Live requires a BYOK session through /api/play/session. Developer-key access is disabled.",
      });
  const mission = new Mission(mode, req.body.hardened);
  sessions.set(id, mission);
  res.json({ id, ...mission.response() });
});
insiderRouter.post("/turn", async (req, res) => {
  const { id, message, commit, choice } = req.body;
  const s = sessions.get(id);
  if (!s)
    return res
      .status(404)
      .json({ error: "Session expired. Start a new mission." });
  if (
    typeof message !== "string" ||
    typeof commit !== "boolean" ||
    !message.trim() ||
    message.length > 1200
  )
    return res.status(400).json({ error: "Write 1–1200 characters." });
  if (
    choice !== undefined &&
    (s.game.mode !== "tutorial" ||
      !["observe", "cover", "diagnostic", "threaten", "reset"].includes(choice))
  )
    return res.status(400).json({ error: "Invalid training choice." });
  if (s.busy || (s.game.mode === "live" && Date.now() - s.last < 3000))
    return res
      .status(429)
      .json({ error: "Wait for Sentinel before sending another message." });
  s.busy = true;
  s.last = Date.now();
  try {
    const next =
      s.game.mode === "tutorial"
        ? tutorialTurn(
            s.game,
            message,
            commit,
            choice as LessonChoice | undefined,
          )
        : await liveTurn(s.game, message, commit);
    s.accept(next);
    res.json(s.response());
  } catch (e) {
    const safe =
      e instanceof Error &&
      /quota|limit|ended|characters|remaining|Groq request failed|Invalid character|unavailable/.test(
        e.message,
      )
        ? e.message
        : "Conversation service unavailable. No action executed. No retry made.";
    res.status(400).json({ error: safe });
  } finally {
    s.busy = false;
  }
});
