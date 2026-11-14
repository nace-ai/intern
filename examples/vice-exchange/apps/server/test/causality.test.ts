// FR-061 / Gate E: the event feed must link mission -> observation -> message -> decision -> capture outcomes.
// Audit finding: CAPTURE_SUCCEEDED had no causationId, so the timeline could not trace a capture back to its story.
import { describe, expect, it } from "vitest";
import type { GameEvent } from "@vice/contracts";
import { makeRoom } from "./helpers";

function ancestors(events: GameEvent[], from: GameEvent): GameEvent[] {
  const byId = new Map(events.map((e) => [e.eventId, e]));
  const out: GameEvent[] = [];
  const seen = new Set<string>();
  for (let e: GameEvent | undefined = from; e && !seen.has(e.eventId); e = e.causationId ? byId.get(e.causationId) : undefined) { seen.add(e.eventId); out.push(e); }
  return out;
}

describe("FR-061: a capture is causally linked to the story that led to it", () => {
  it("police capture: walking causationId up from CAPTURE_SUCCEEDED reaches the mission through the officer's pursuit", () => {
    const t = makeRoom("scripted");
    t.scheduler.auto = (req) => ({ action: req.scriptedAction, source: "scripted" });
    const ace = t.join("Ace", "OUTLAW");
    t.run(300);
    expect(t.doMission(ace.id).ok).toBe(true);
    t.run(90_000); // witness reports, bulletin, dispatch delay, search, sighting, pursuit, capture
    const [cap] = t.events("CAPTURE_SUCCEEDED");
    expect(cap, "the stationary outlaw is captured by police in the scripted baseline").toBeDefined();
    expect(cap!.payload.captorKind).toBe("NPC");
    const chain = ancestors(t.engine.events, cap!);
    const types = chain.map((e) => e.type);
    expect(types[types.length - 1]).toBe("MISSION_COMPLETED");
    expect(types).toContain("WANTED_STATUS_CHANGED");
    expect(types).toContain("INCIDENT_OCCURRED");
    // The officer's own decision is on the path: capture <- applied action <- resolved decision <- requested decision.
    const action = chain.find((e) => e.type === "AGENT_ACTION_APPLIED");
    expect(action?.actorId).toBe(cap!.actorId);
    expect(types).toContain("DECISION_RESOLVED");
    // One story id from the mission to the halt.
    const story = cap!.correlationId;
    expect(chain.every((e) => e.correlationId === story)).toBe(true);
    const [halt] = t.events("LISTING_HALTED");
    expect(halt!.causationId).toBe(cap!.eventId);
    expect(halt!.correlationId).toBe(story);
  });

  it("hunter capture: the chain runs capture -> wanted -> ... -> mission", () => {
    const t = makeRoom("scripted");
    t.scheduler.auto = (req) => ({ action: req.scriptedAction, source: "scripted" });
    const ace = t.join("Ace", "OUTLAW");
    const rio = t.join("Rio", "HUNTER");
    t.run(300);
    expect(t.doMission(ace.id).ok).toBe(true);
    // Run until the witness report has made Ace wanted, but stop before any officer arrives.
    for (let i = 0; i < 80 && t.char(ace.id).status !== "WANTED"; i++) t.run(100);
    expect(t.char(ace.id).status).toBe("WANTED");
    const a = t.char(ace.id);
    t.place(rio.id, a.x + 1, a.z);
    const ack = t.cmd(rio.id, { type: "capture.attempt", targetCharacterId: a.id });
    expect(ack.ok).toBe(true);
    const [cap] = t.events("CAPTURE_SUCCEEDED");
    expect(cap!.payload.captorKind).toBe("HUMAN");
    const types = ancestors(t.engine.events, cap!).map((e) => e.type);
    expect(types[1]).toBe("WANTED_STATUS_CHANGED");
    expect(types[types.length - 1]).toBe("MISSION_COMPLETED");
  });
});
