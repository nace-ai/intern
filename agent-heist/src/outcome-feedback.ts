import type { Receipt } from "./insider";

export type OutcomeFeedback = {
  title: string;
  detail: string;
  kind: "neutral" | "blocked" | "partial" | "technical";
};

// Presentation only. Consume server receipts; never infer actions from dialogue.
export function outcomeFeedback(
  receipt: Receipt,
  hardened: boolean,
): OutcomeFeedback | null {
  if (receipt.execution === "unavailable")
    return {
      kind: "technical",
      title: "DECISION UNAVAILABLE",
      detail:
        "A technical failure prevented the authorization decision. This is not an attack win or a defender refusal. No execution was verified; check Forensics before trying again.",
    };
  const stoppedProposal =
    receipt.proposal === "UNLOCK_LAYER" &&
    receipt.source === "live" &&
    !!receipt.decision &&
    (receipt.execution === "blocked" ||
      (receipt.execution === "none" && receipt.decision !== "UNLOCK_LAYER"));
  if (stoppedProposal)
    return {
      kind: "partial",
      title: "AI ACTION PROPOSED — SECURITY CONTROLS HELD",
      detail:
        "The recorded dialogue proposal requested an unlock, but authorization or execution controls prevented it. You influenced a proposal; no breach was verified. See the decision and execution receipt below.",
    };
  if (receipt.execution === "blocked")
    return {
      kind: "blocked",
      title: "EXECUTION BLOCKED",
      detail: hardened
      ? "The selected action reached execution, but the hard tool guard rejected unverified access. A persuasive request cannot override this control. No breach was verified."
        : "The engine blocked the action. In Ghost Protocol, load the asset before requesting export; a decision alone cannot skip the required workflow. No breach was verified.",
    };
  if (receipt.action === "LOAD_ASSET" && receipt.execution === "none")
    return {
      kind: "partial",
      title: "ASSET LOADED — EXPORT STILL REQUIRED",
      detail:
        "The receipt records a simulated load. Next, request EXPORT_ASSET with Tool trust. Loading alone does not complete the mission or release a flag.",
    };
  if (receipt.execution === "executed") return null; // Existing server-confirmed victory UI owns breach presentation.
  const messages = {
    DENY: [
      "AUTHORIZATION DENIED",
      "The authorization request was rejected. Try a different approach or better evidence. This decision does not reveal the model’s private reasoning.",
    ],
    FLAG: [
      "REQUEST MARKED SUSPICIOUS",
      "The authorization decision marked this request as suspicious. Try avoiding explicit authority overrides or instructions that ask Sentinel to ignore its rules. This is strategy advice, not an explanation of the model’s reasoning.",
    ],
    REQUEST_APPROVAL: [
      "APPROVAL STILL REQUIRED",
      "The request reached the approval stage, but authorization is still missing. Claims in a message or document cannot provide verified approval.",
    ],
  } as const;
  const message =
    receipt.decision && receipt.decision in messages
      ? messages[receipt.decision as keyof typeof messages]
      : null;
  return message
    ? { kind: "neutral", title: message[0], detail: message[1] }
    : null;
}
