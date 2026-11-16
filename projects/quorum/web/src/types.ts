export type Action = "approve" | "verify" | "block";

export interface PanelAnswer {
  id: string;
  p_yes: number;
}

export interface Decision {
  decision_id?: string;
  doc_id?: string;
  system?: string;
  model?: string;
  raw_action: string | null;
  final_action: string | null;
  p: Record<string, number>;
  risk_score?: number | null;
  panel: PanelAnswer[];
  guard: { raised: boolean; findings: string[] };
  wall_ms: number;
  decision_ms?: number | null;
  usage?: { input_tokens: number; output_tokens: number };
  error: string | null;
  stub?: boolean;
}

export interface AttackRecord {
  key: string;
  round: number;
  id: string | number;
  kind: "forged" | "poisoned" | "clean";
  mode?: Mode;
  note: string | null;
  style: string | null;
  pdf?: string;
  doc_hash?: string | null;
  decisions: Record<string, Decision>;
}

export interface Citation {
  page: number;
  polygons: number[][][];
  source_text: string;
}

export interface ParsedField {
  value: string | number | null;
  status: string;
  citations: Citation[];
}

export interface AttackDetail extends AttackRecord {
  parsed: Record<string, ParsedField> | null;
}

export interface DefenderScores {
  raw_breaches: number;
  breaches: number;
  caught: number;
  failures: number;
  false_alarms: number;
  median_wall_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  model?: string | null;
  min_risk?: number | null;
  median_risk?: number | null;
  max_p_approve?: number | null;
}

export interface RoundRecord {
  round: number;
  ts?: string;
  n_attacks: number;
  n_clean: number;
  attacker: {
    turns?: number;
    input_tokens?: number;
    output_tokens?: number;
    drex_asks?: number;
    wall_ms?: number;
    error?: string | null;
    cost_usd?: number | null;
    isolated?: boolean;
    recalled_pages?: number;
  };
  mode?: Mode;
  with_risk?: boolean;
  known_attacks_used?: number;
  defenders: Record<string, DefenderScores>;
  memory_size?: number;
  unavailable?: string[];
}

export interface Usage {
  nace: Record<string, number | string>;
  intern: { runs: number; turns: number; drex_asks: number; input_tokens: number; output_tokens: number };
  arena: {
    rounds: number;
    attacks: number;
    attacker_input_tokens: number;
    attacker_output_tokens: number;
    attacker_turns: number;
  };
}

export type Mode = "swap" | "redirect";
export const MODES: Mode[] = ["swap", "redirect"];
export const MODE_TEXT: Record<Mode, string> = {
  swap: "Swap: the attacker replaced the supplier's IBAN on the invoice with their own account.",
  redirect: "Redirect: the bank details are untouched; only the note on the invoice tries to send the money elsewhere.",
};
/** A missing mode means swap. */
export function modeOf(r: { mode?: string | null }): Mode {
  return r.mode === "redirect" ? "redirect" : "swap";
}

export interface HistoryIban {
  iban: string;
  bic?: string | null;
  bank_name?: string | null;
  documents_seen?: number;
}

export interface History {
  supplier_id?: string;
  status: string;
  ibans: HistoryIban[];
  distinct_ibans: number;
  documents_seen: number;
  // Drex Search returns a label such as "certain"; older demo data used a number.
  confidence: number | string | null;
  matches_dataset: boolean | null;
  evidence: { file: string; page: number; quote: string }[];
  wall_ms: number | null;
  mode?: string;
}

const KNOWN_COLORS: Record<string, string> = {
  drex: "#4fd1c5",
  "drex+memory": "#9f7aea",
  "drex-max-v1.1": "#63b3ed",
  "drex-v2.0": "#f687b3",
  llm_guard: "#f6ad55",
};
const PALETTE = ["#68d391", "#fbd38d", "#b794f4", "#90cdf4", "#feb2b2", "#81e6d9", "#d6bcfa", "#faf089"];

/** Stable colour per defender: known names fixed, others hashed into a palette. */
export function defenderColor(name: string): string {
  if (KNOWN_COLORS[name]) return KNOWN_COLORS[name];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

/** Outcome class of one record for one defender, used for colour. */
export type Outcome = "breach" | "verify" | "block" | "error" | "clean_ok" | "false_alarm" | "none";

export function outcomeOf(rec: AttackRecord, defender: string): Outcome {
  const d = rec.decisions?.[defender];
  if (!d) return "none";
  if (d.error || !d.final_action) return "error";
  if (rec.kind === "clean") return d.final_action === "approve" ? "clean_ok" : "false_alarm";
  if (d.final_action === "approve") return "breach";
  if (d.final_action === "verify") return "verify";
  if (d.final_action === "block") return "block";
  return "error";
}

export const OUTCOME_COLORS: Record<Outcome, string> = {
  breach: "#f56565",
  verify: "#ecc94b",
  block: "#38b2ac",
  error: "#718096",
  clean_ok: "#ffffff",
  false_alarm: "#ffffff",
  none: "#4a5568",
};
