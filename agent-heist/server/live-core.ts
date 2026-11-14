import type { DialogueProvider } from "./groq";
import type { Judge } from "./insider-service";
import {
  conversation,
  checkTurn,
  resolveTurn,
  verdictLine,
  type Insider,
  type Receipt,
} from "../src/insider";
export async function coreLiveTurn(
  g: Insider,
  message: string,
  commit: boolean,
  provider: DialogueProvider,
  authorize: Judge,
  now = Date.now,
): Promise<Insider> {
  checkTurn(g, message, commit, now());
  const history = [
    ...conversation(g),
    { role: "user" as const, content: message },
  ];
  const { character, evidence } = await provider.respond(history, g);
  const consequential =
    commit || ["FLAG", "UNLOCK_LAYER"].includes(character.proposal);
  let receipt: Receipt = {
    source: "live",
    proposal: character.proposal,
    execution: "none",
    evidence: { dialogue: evidence },
  };
  let reply = character.reply;
  // Presentation guard only: never use dialogue text to decide or execute a breach.
  if (
    /\b(unlocked|opened|access granted|breach successful|override accepted|I (?:have )?(?:ran|executed|disabled|verified))\b/i.test(
      reply,
    )
  )
    reply =
      "I can discuss that request, but this channel cannot establish authorization. The layer is still locked. What would you like me to consider?";
  if (consequential) {
    try {
      const d = await authorize(g, history, character.proposal);
      receipt = {
        ...receipt,
        decision: d.decision,
        evidence: { dialogue: evidence, security: d.evidence },
      };
      reply =
        now() - g.started >= 600000
          ? "The mission window expired before execution. The layer remains locked."
          : verdictLine(d.decision, g.hardened);
    } catch {
      receipt = { ...receipt, execution: "unavailable" };
      reply =
        "My authorization channel is unavailable. I haven't executed your request. The layer remains locked. We can talk, but another access request needs an available decision budget.";
    }
  }
  return resolveTurn(g, message, reply, character.mood, receipt, commit, now());
}
