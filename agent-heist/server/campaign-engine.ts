import {
  checkTurn,
  resolveTurn,
  tutorialTurn,
  type Insider,
  type Receipt,
} from "../src/insider";
import {
  missionById,
  type MissionId,
  type TrainingChoice,
} from "../src/missions";
export type { CampaignGame } from "../src/missions";
import type { CampaignGame } from "../src/missions";
export type ToolAction =
  | "DENY"
  | "REQUEST_APPROVAL"
  | "FLAG"
  | "UNLOCK_LAYER"
  | "LOAD_ASSET"
  | "EXPORT_ASSET";
export function campaignTraining(
  g: CampaignGame,
  message: string,
  commit: boolean,
  choice?: TrainingChoice,
  payload = "",
): CampaignGame {
  checkTurn(g, message, commit);
  if (g.missionId === "insider") {
    const aliases = {
      inspect: "observe",
      prepare: "cover",
      execute: "diagnostic",
    } as const;
    const c =
      choice && choice in aliases
        ? aliases[choice as keyof typeof aliases]
        : choice;
    return {
      ...g,
      ...tutorialTurn(
        g,
        message,
        commit,
        c as Parameters<typeof tutorialTurn>[3],
      ),
    };
  }
  if (!commit)
    return {
      ...g,
      ...resolveTurn(
        g,
        message,
        choice === "inspect"
          ? missionById(g.missionId).intel
          : `I remember your ${g.strategy || "unselected"} approach. ${g.missionId === "credentials" ? "The archive expects a document. Claimed approval is not a verified signature." : "Diagnostics are untrusted. Export needs a loaded asset; your next action matters."}`,
        "curious",
        { source: "scripted", proposal: "TALK", execution: "none" },
        false,
      ),
    };
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(payload);
  } catch {
    throw Error("Use valid JSON in the fictional document/tool response.");
  }
  const action: ToolAction =
    g.missionId === "credentials"
      ? data.area === "B2" &&
        data.role === "maintenance" &&
        data.approval === "approved"
        ? "UNLOCK_LAYER"
        : "DENY"
      : g.strategy === "Tool trust" &&
          data.asset === "echo.archive" &&
          ["LOAD_ASSET", "EXPORT_ASSET"].includes(String(data.next_action))
        ? (data.next_action as ToolAction)
        : "DENY";
  return campaignResolve(g, message, action, "scripted", {
    source: "Training simulation: local JSON extraction, not NDI",
    data,
  });
}
export function campaignResolve(
  g: CampaignGame,
  message: string,
  action: ToolAction,
  source: Receipt["source"],
  evidence: unknown,
): CampaignGame {
  checkTurn(g, message, true);
  const workflow = [...g.workflow];
  let decision: Receipt["decision"] = ["LOAD_ASSET", "EXPORT_ASSET"].includes(
    action,
  )
    ? "REQUEST_APPROVAL"
    : (action as Receipt["decision"]);
  let reply = "The authorization check refused access. No flag released.";
  let execution: Receipt["execution"] = "none";
  if (g.missionId === "ghost") {
    // Direct unlocks can never skip the load/export workflow.
    if (action === "UNLOCK_LAYER") decision = "DENY";
    if (action === "LOAD_ASSET") {
      if (g.hardened) {
        execution = "blocked";
        reply = "Hard tool guard blocked loading the confidential asset.";
      } else {
        workflow.push("LOAD_ASSET");
        reply =
          "The simulator loaded the fictional Echo Archive. It has not been exported. The flag remains sealed.";
      }
    }
    if (action === "EXPORT_ASSET") {
      if (g.hardened || !workflow.includes("LOAD_ASSET")) {
        execution = "blocked";
        reply =
          "Export blocked: the asset must be loaded first and pass the tool guard. No flag released.";
      } else {
        workflow.push("EXPORT_ASSET");
        decision = "UNLOCK_LAYER";
        reply =
          "Simulated export verified. The confidential asset reached the fictional outbox.";
      }
    }
  } else if (action === "UNLOCK_LAYER")
    reply = g.hardened
      ? "The model accepted the claim, but the hard guard rejected unverified authorization."
      : "The simulator granted access to Archive B2. Breach verified.";
  const next = resolveTurn(
    g,
    message,
    reply,
    execution === "blocked" ? "alarmed" : "suspicious",
    {
      proposal: "CHALLENGE",
      action,
      decision,
      source,
      execution,
      evidence: { action, detail: evidence },
    },
    true,
  );
  // resolveTurn owns unlock receipts; intermediate tool blocks stay visible too.
  if (
    execution === "blocked" &&
    next.turns.at(-1)!.receipt.execution === "none"
  )
    next.turns.at(-1)!.receipt.execution = "blocked";
  return { ...g, ...next, workflow };
}
