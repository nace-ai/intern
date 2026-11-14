import type { NpcRole, Vec2 } from "@vice/contracts";
import type { NpcState } from "../domain";

interface NpcSeed { id: string; name: string; role: NpcRole; at: Vec2; patrol?: Vec2[]; persona: string; goal: string; contacts: string[] }

/** Stable roster for every room and every reset. Original characters. */
export const NPC_SEEDS: readonly NpcSeed[] = [
  { id: "npc_w1", name: "Marisol Vega", role: "WITNESS", at: { x: 40, z: 64 }, persona: "Dock fisher, civic-minded, trusts the police.", goal: "Keep the marina safe without getting hurt.", contacts: ["npc_i1"] },
  { id: "npc_w2", name: "Otis Brandt", role: "WITNESS", at: { x: 55, z: 45 }, persona: "Plaza vendor, distrusts police, loves to gossip.", goal: "Stay out of trouble and trade stories for favours.", contacts: ["npc_i2"] },
  { id: "npc_i1", name: "Lena Okoye", role: "INFORMANT", at: { x: 33, z: 52 }, persona: "Sells tips to traders for a cut.", goal: "Monetise information before it becomes public.", contacts: ["npc_t1", "npc_t2"] },
  { id: "npc_i2", name: "Dex Marlowe", role: "INFORMANT", at: { x: 62, z: 30 }, persona: "Cautious, relays only what he believes.", goal: "Protect his reputation for accurate tips.", contacts: ["npc_t2"] },
  { id: "npc_t1", name: "Priya Castellan", role: "TRADER", at: { x: 17, z: 31 }, persona: "Momentum trader, risk-seeking.", goal: "Grow her fictional V$ portfolio by riding notoriety.", contacts: [] },
  { id: "npc_t2", name: "Rook Halvorsen", role: "TRADER", at: { x: 30, z: 41 }, persona: "Risk-averse, sells on bad news.", goal: "Protect capital; avoid holding a ticker that could go to zero.", contacts: [] },
  { id: "npc_p1", name: "Sgt. Imani Duarte", role: "POLICE", at: { x: 32, z: 28 },
    // Stays north of the marina (never within 14 m of the dock job), so police learn about that job through the witness, not by walking past it.
    patrol: [{ x: 32, z: 28 }, { x: 62, z: 28 }, { x: 62, z: 44 }, { x: 62, z: 28 }, { x: 32, z: 28 }, { x: 32, z: 44 }], persona: "By the book.", goal: "Capture wanted characters with solid evidence.", contacts: [] },
  { id: "npc_p2", name: "Ofc. Cole Renner", role: "POLICE", at: { x: 92, z: 28 },
    // Also keeps north of the marina, so the first job's pacing never depends on where he happens to be.
    patrol: [{ x: 92, z: 28 }, { x: 92, z: 44 }, { x: 92, z: 28 }, { x: 77, z: 28 }], persona: "Aggressive.", goal: "Make the arrest before anyone else does.", contacts: [] },
];

export function seedNpcs(): Record<string, NpcState> {
  const out: Record<string, NpcState> = {};
  for (const s of NPC_SEEDS) {
    out[s.id] = {
      id: s.id, name: s.name, role: s.role, persona: s.persona, goal: s.goal,
      x: s.at.x, z: s.at.z, home: { ...s.at }, patrol: (s.patrol ?? []).map((p) => ({ ...p })), patrolIndex: s.patrol ? 1 : 0,
      activity: s.role === "POLICE" ? "PATROL" : "IDLE", moveTarget: null, targetCharacterId: null,
      walletId: `wal_${s.id}`, contacts: [...s.contacts], cooldownUntilMs: 0, lastCaptureAttemptAtMs: 0,
    };
  }
  return out;
}
