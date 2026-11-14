import { RULES } from "@vice/contracts";
import type { EntityId, MemoryEntry } from "@vice/contracts";
import type { MemoryPort } from "../domain";

/** Private, bounded, per-NPC memory. Nothing here is ever shared between NPCs except through recorded messages. */
export function createMemoryStore(maxPerNpc: number = RULES.memory.maxEntriesPerNpc): MemoryPort {
  const byAgent = new Map<EntityId, MemoryEntry[]>();
  const byId = new Map<string, MemoryEntry>();
  return {
    remember(entry, id) {
      const full: MemoryEntry = { ...entry, id, confidence: Math.max(0, Math.min(1, entry.confidence)) };
      const list = byAgent.get(full.agentId) ?? [];
      list.push(full);
      while (list.length > maxPerNpc) { const gone = list.shift()!; byId.delete(gone.id); }
      byAgent.set(full.agentId, list);
      byId.set(id, full);
      return full;
    },
    recall(agentId, nowMs) {
      return (byAgent.get(agentId) ?? [])
        .filter((m) => m.expiresAtMs > nowMs)
        .sort((a, b) => b.observedAtMs - a.observedAtMs || (a.id < b.id ? 1 : -1));
    },
    get: (memoryId) => byId.get(memoryId),
    all: () => [...byAgent.values()].flat(),
    clear() { byAgent.clear(); byId.clear(); },
  };
}
