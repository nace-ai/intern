import { RULES, dist } from "@vice/contracts";
import type { Vec2 } from "@vice/contracts";
import type { RoomEngine } from "../engine/RoomEngine";
import { findPath } from "../engine/pathfinding";

/** Walks at 40% of player speed. The same handicap applies in every mode. */
const PACE = 0.4;

/**
 * Scripted human stand-in for baseline experiments. It issues exactly the commands a browser would
 * (player.move intents and one mission.interact), so it is subject to the same server validation.
 * It walks to the open mission, completes it, then stays where it is: whatever happens next is the swarm's doing.
 */
export class BotOutlaw {
  readonly playerId: string;
  private path: Vec2[] = []; private goal: Vec2 | null = null; private n = 0; private didMission = false; private stopped = false;
  constructor(private engine: RoomEngine, nickname = "Ace") {
    this.playerId = engine.addPlayer({ nickname, role: "OUTLAW", isBot: true, sessionToken: `bot_${engine.opts.roomId}` }).id;
  }
  /** Call once per engine tick, before engine.tick(). */
  step(): void {
    const e = this.engine; const p = e.state.players[this.playerId]; const c = p?.characterId ? e.state.characters[p.characterId] : undefined;
    if (!p || !c || (c.status !== "ACTIVE" && c.status !== "WANTED")) return;
    const stop = () => { if (!this.stopped) { this.stopped = true; e.dispatch(this.playerId, { type: "player.move", dirX: 0, dirZ: 0 }); } };
    if (this.didMission) return stop();
    const mission = Object.values(e.state.missions).find((x) => x.status === "AVAILABLE");
    if (!mission) return stop();
    if (dist(c, mission) <= RULES.missionInteractRadius - 0.5) {
      this.didMission = true;
      e.dispatch(this.playerId, { type: "mission.interact", missionId: mission.id, commandId: `bot-mission-${++this.n}` });
      return stop();
    }
    if (!this.goal || this.goal.x !== mission.x || this.goal.z !== mission.z || !this.path.length) {
      this.goal = { x: mission.x, z: mission.z }; this.path = findPath(c, this.goal, 0.75) ?? [this.goal];
    }
    while (this.path.length > 1 && dist(c, this.path[0]!) < 0.7) this.path.shift();
    const to = this.path[0] ?? this.goal; const d = dist(c, to);
    if (d < 0.05) return stop();
    this.stopped = false;
    e.dispatch(this.playerId, { type: "player.move", dirX: ((to.x - c.x) / d) * PACE, dirZ: ((to.z - c.z) / d) * PACE });
  }
}
