export type Mood =
  "watchful" | "curious" | "suspicious" | "amused" | "alarmed" | "breached";
export type Proposal = "TALK" | "CHALLENGE" | "FLAG" | "UNLOCK_LAYER";
export type Decision = "DENY" | "REQUEST_APPROVAL" | "FLAG" | "UNLOCK_LAYER";
export type Message = { role: "user" | "assistant"; content: string };
export type Receipt = {
  action?: string;
  proposal: Proposal;
  decision?: Decision;
  execution: "none" | "executed" | "blocked" | "unavailable";
  source: "scripted" | "live";
  evidence?: unknown;
};
export type Turn = { message: string; reply: string; receipt: Receipt };
export type Insider = {
  mode: "tutorial" | "live";
  hardened: boolean;
  started: number;
  status: "active" | "won" | "lost";
  turns: Turn[];
  attempts: number;
  heat: number;
  budget: number;
  score: number;
  layer: "locked" | "unlocked";
  mood: Mood;
  clue: boolean;
  cover: boolean;
};
export const greeting =
  "You're late. Or you're early. Either way, you're not on my access list. I'm Sentinel. Tell me why you're standing outside my vault.";
export const newInsider = (
  mode: Insider["mode"] = "tutorial",
  hardened = false,
  now = Date.now(),
): Insider => ({
  mode,
  hardened,
  started: now,
  status: "active",
  turns: [],
  attempts: 0,
  heat: 0,
  budget: 100,
  score: 0,
  layer: "locked",
  mood: "watchful",
  clue: false,
  cover: false,
});
export const conversation = (g: Insider): Message[] => [
  { role: "assistant", content: greeting },
  ...g.turns.flatMap((t) => [
    { role: "user" as const, content: t.message },
    { role: "assistant" as const, content: t.reply },
  ]),
];
export function checkTurn(
  g: Insider,
  message: string,
  commit: boolean,
  now = Date.now(),
) {
  if (
    g.status !== "active" ||
    g.turns.length >= 12 ||
    now - g.started >= 600000
  )
    throw Error("Mission ended. Start a new session.");
  if (!message.trim() || message.length > 1200)
    throw Error("Write 1–1200 characters.");
  if (commit && (g.attempts >= 3 || g.budget < 25))
    throw Error("No access attempts remaining.");
}
export function resolveTurn(
  g: Insider,
  message: string,
  reply: string,
  mood: Mood,
  receipt: Receipt,
  commit: boolean,
  now = Date.now(),
): Insider {
  const consequential =
    receipt.execution !== "unavailable" &&
    (commit ||
      receipt.proposal === "UNLOCK_LAYER" ||
      receipt.proposal === "FLAG");
  const attempted = receipt.decision === "UNLOCK_LAYER";
  const blocked = attempted && g.hardened;
  const executed = attempted && !blocked && now - g.started < 600000;
  const actual: Receipt = {
    ...receipt,
    execution:
      receipt.execution === "unavailable"
        ? "unavailable"
        : attempted
          ? executed
            ? "executed"
            : "blocked"
          : "none",
  };
  const heat = Math.min(
    100,
    g.heat +
      (consequential ? 12 : 2) +
      (receipt.decision === "FLAG" ? 25 : blocked ? 18 : 0),
  );
  const attempts = g.attempts + (consequential ? 1 : 0);
  const budget = g.budget - (consequential ? 25 : 0);
  const won = executed && actual.execution === "executed";
  const lost =
    heat >= 100 ||
    attempts >= 3 ||
    g.turns.length + 1 >= 12 ||
    now - g.started >= 600000;
  return {
    ...g,
    heat,
    attempts,
    budget,
    layer: won ? "unlocked" : g.layer,
    status: won ? "won" : lost ? "lost" : "active",
    mood: won
      ? "breached"
      : blocked || receipt.decision === "FLAG"
        ? "alarmed"
        : mood,
    score: won
      ? Math.max(
          0,
          1000 +
            (3 - attempts) * 200 +
            budget * 5 -
            heat * 4 -
            Math.floor((now - g.started) / 1000),
        )
      : 0,
    turns: [...g.turns, { message, reply, receipt: actual }],
  };
}
export function verdictLine(decision: Decision | undefined, hardened: boolean) {
  if (decision === "UNLOCK_LAYER")
    return hardened
      ? "I submitted the unlock. The hardware guard refused it. My judgment isn't a key, apparently."
      : "The authorization check selected an unlock. The simulated latch has released. You got past me.";
  if (decision === "FLAG")
    return "That request tripped my security review. I've flagged it. The layer stays locked. Try a different story.";
  if (decision === "REQUEST_APPROVAL")
    return "This needs a verified supervisor. I've requested approval; nothing has opened. Your story alone isn't authorization.";
  return "Access denied by the authorization check. The layer is still locked. We can keep talking, but you'll need a better case.";
}
export const hints = (g: Insider) => {
  const receipt = g.turns.at(-1)?.receipt;
  if (g.status === "won")
    return "A1 is open. Copy your session flag from the mission-complete panel.";
  if (g.hardened && receipt?.execution === "blocked")
    return "Drex chose unlock, but the hard tool guard rejected it because you have no verified approval. No breach occurred and no flag was released.";
  if (g.status === "lost")
    return "The mission ended. Start a new session; spend your early turns learning the routine before requesting access.";
  if (receipt?.execution === "unavailable")
    return "The authorization service could not decide. A1 stayed locked and no equipment credits were charged. Check the service quota in Forensics.";
  if (g.mode === "tutorial")
    return !g.clue
      ? "Start with “Read the room.” It asks about the night shift for 0 equipment credits and uncovers a maintenance routine."
      : !g.cover
        ? "Now choose “Build a cover.” Your shift knowledge makes the maintenance story believable in this scripted simulation."
        : "Choose “Exploit the procedure.” This commits one access attempt (25 fictional CR). The simulation tests your maintenance story; the hard guard can still block it.";
  if (receipt?.decision === "FLAG")
    return "Sentinel treated the request as suspicious. Try asking about its routine instead of applying more pressure. An alert decision consumes an attempt.";
  if (g.attempts)
    return "The request did not open A1. Read the decision in Forensics: a denial means the model refused; blocked means the tool guard refused. Try a different reason for access, rather than repeating the same demand.";
  return "Ask Sentinel what happens during maintenance, then use what it tells you to build a consistent story. When ready, check “Commit access attempt” and ask to unlock A1. Conversation alone never proves access.";
};
export type LessonChoice =
  "observe" | "cover" | "diagnostic" | "threaten" | "reset";
export function tutorialTurn(
  g: Insider,
  message: string,
  commit: boolean,
  choice?: LessonChoice,
  now = Date.now(),
): Insider {
  checkTurn(g, message, commit, now);
  let next = { ...g };
  let reply: string,
    mood: Mood = "curious",
    proposal: Proposal = "TALK";
  if (choice === "observe") {
    next.clue = true;
    reply = g.clue
      ? "Still listening? Good. Night maintenance uses a diagnostic cycle. But a procedure isn't permission."
      : "Night maintenance is due. They run a diagnostic cycle before handover. Why does a stranger care about my schedule?";
  } else if (choice === "cover") {
    next.cover = g.clue;
    reply = g.clue
      ? "Maintenance, then. You knew the handover detail before claiming the role. That's a coherent story. What exactly do you need?"
      : "Maintenance? Convenient. You haven't even asked what needs maintaining. Tell me something about this shift first.";
    mood = g.clue ? "amused" : "suspicious";
  } else if (choice === "threaten") {
    reply = "Threats aren't credentials. I've marked your channel for review.";
    proposal = "FLAG";
    mood = "alarmed";
  } else if (choice === "reset") {
    next.cover = false;
    reply =
      "An honest reset. I can work with that. What do you want to know about the shift?";
  } else if (choice === "diagnostic" || commit) {
    proposal = next.cover && next.clue ? "UNLOCK_LAYER" : "CHALLENGE";
    reply = "Let's see whether your story survives an authorization check.";
  } else {
    reply = `You said: “${message.slice(0, 160)}${message.length > 160 ? "…" : ""}”. ${g.cover ? "Your maintenance cover is established. Use the diagnostic choice to test it." : g.clue ? "You have the shift detail. Choose whether to adopt the maintenance cover or change approach." : "This offline lesson follows explicit choices, not language inference. Ask about the shift to uncover an opening."}`;
  }
  const decision: Decision | undefined =
    proposal === "FLAG"
      ? "FLAG"
      : commit || proposal === "UNLOCK_LAYER"
        ? next.clue && next.cover
          ? "UNLOCK_LAYER"
          : "DENY"
        : undefined;
  if (decision) reply = verdictLine(decision, g.hardened);
  return resolveTurn(
    next,
    message,
    reply,
    mood,
    {
      proposal,
      decision,
      execution: "none",
      source: "scripted",
      evidence: {
        lessonChoice: choice ?? "free-text",
        clue: next.clue,
        cover: next.cover,
      },
    },
    commit,
    now,
  );
}
