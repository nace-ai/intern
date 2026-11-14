// What we ask NDI Extract to find in the document. Only the QUESTIONS live here: every value, quote and page shown
// in the product comes out of a real NDI job, never from this file.
import { createHash } from "node:crypto";
import type { NpcRole } from "@vice/contracts";

export interface PolicyFieldSpec {
  /** JSON property sent to NDI. Becomes Citation.extractedField and the `rule` key in the police decision state. */
  name: string;
  /** Sent to NDI as the property description: what to find. */
  description: string;
  /** How the simulation uses the rule. Our reading of it, shown as ours, never as document text. */
  gameUse: string;
  /** NPC roles the rule governs. The order is written for officers, so only POLICE appears. */
  roles: NpcRole[];
}

const VERBATIM = "Copy the complete sentence or sentences word for word, exactly as printed in the document.";

/** Policy rules a police NPC weighs when it chooses investigate | patrol | pursue. */
export const POLICY_FIELDS: readonly PolicyFieldSpec[] = [
  {
    name: "pursuit_may_start_when",
    description: `When an officer may initiate a foot pursuit: the sentence stating what the officer must reasonably believe about the person. ${VERBATIM}`,
    gameUse: "pursue is only for a character the officer has grounds to arrest (a WANTED character it has seen).",
    roles: ["POLICE"],
  },
  {
    name: "keep_weighing_need_against_risk",
    description: `What officers must continually assess, and what they must weigh against the danger, while initiating or continuing a foot pursuit. ${VERBATIM}`,
    gameUse: "the officer re-decides on every new trigger instead of committing to a chase once.",
    roles: ["POLICE"],
  },
  {
    name: "decide_on_facts_known_at_the_time",
    description: `The standard by which an officer's decision to engage, not to engage or discontinue a foot pursuit is evaluated: which facts and circumstances count. ${VERBATIM}`,
    gameUse: "the officer decides only from its own memory: what it saw itself versus what a report told it.",
    roles: ["POLICE"],
  },
  {
    name: "if_sight_of_person_is_lost",
    description: `What happens to the danger, and what officers should consider doing, when they lose sight of the person they are pursuing. ${VERBATIM}`,
    gameUse: "without a fresh direct sighting pursue is not offered; the officer investigates the last known location.",
    roles: ["POLICE"],
  },
  {
    name: "unsure_of_location_is_a_safety_issue",
    description: `The listed officer safety issue about the officer becoming unsure where the person is or which way the person is travelling. ${VERBATIM}`,
    gameUse: "a second-hand report gives no certain location, so it justifies investigating, not a chase.",
    roles: ["POLICE"],
  },
  {
    name: "hold_position_where_last_seen",
    description: `The task, in the transition to containment mode, about taking cover or a position near where the person was last seen. ${VERBATIM}`,
    gameUse: "investigate sends the officer to the last known location of the suspect.",
    roles: ["POLICE"],
  },
  {
    name: "broadcast_description_and_direction",
    description: `The task, in the transition to containment mode, about broadcasting the person's description and direction of travel. ${VERBATIM}`,
    gameUse: "police publish a wanted bulletin with the last known location to every NPC.",
    roles: ["POLICE"],
  },
  {
    name: "safety_is_the_priority",
    description: `The stated priority for officers engaging in a foot pursuit. ${VERBATIM}`,
    gameUse: "patrol is the safe choice when the information is too weak to act on.",
    roles: ["POLICE"],
  },
  {
    name: "supervisor_must_monitor",
    description: `What supervisors shall make every effort to do during a foot pursuit. ${VERBATIM}`,
    gameUse: "every police decision is recorded with its trace, so a supervisor (the judge) can review it.",
    roles: ["POLICE"],
  },
];

/** Identity fields: used only to check that NDI read the document we pinned. They never become policy facts. */
export const IDENTITY_FIELDS: ReadonlyArray<{ name: string; description: string }> = [
  { name: "order_number", description: "The number of the general order as printed in its header, for example 5.25." },
  { name: "order_title", description: "The title of the general order as printed under its header." },
  { name: "revision_date", description: "The revision date printed after 'Rev.' in the general order header, exactly as printed." },
  { name: "effective_date", description: "The effective date printed after 'Eff.' in the general order header, exactly as printed." },
];

export const EXTRACT_INSTRUCTIONS =
  "The file is a police department notice (first page) followed by the general order it announces. Fill every field "
  + "from the general order itself, not from the notice. Every rule must be copied word for word from the document: "
  + "complete sentences, no paraphrase, no summary, no corrected or added words. If the document does not state "
  + "something, leave that field empty.";

/** Options sent with the Parse job (part of the cache identity). */
export const PARSE_OPTIONS = { output: { formats: ["markdown", "text", "blocks"], include_page_markers: true }, parse_mode: "low" } as const;
/** Options sent with the Extract job (part of the cache identity). */
export const EXTRACT_OPTIONS = { citations: { enabled: true, include_source_text: true } } as const;

export function buildExtractSchema(fields: readonly PolicyFieldSpec[] = POLICY_FIELDS): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  for (const f of IDENTITY_FIELDS) properties[f.name] = { type: "string", description: f.description };
  for (const f of fields) properties[f.name] = { type: "string", description: f.description };
  return { type: "object", properties, additionalProperties: false };
}

/** Changes whenever the questions or job options change, so a stale cached result is never reused for new questions. */
export function schemaHash(fields: readonly PolicyFieldSpec[] = POLICY_FIELDS): string {
  const identity = JSON.stringify({ schema: buildExtractSchema(fields), instructions: EXTRACT_INSTRUCTIONS, parse: PARSE_OPTIONS, extract: EXTRACT_OPTIONS });
  return createHash("sha256").update(identity).digest("hex").slice(0, 16);
}
