import { randomBytes, timingSafeEqual } from "node:crypto";
import { newInsider, type Insider } from "../src/insider";

export type TrainingMissionCheckpoint = {
  game: Insider;
  flag: string;
  breached: boolean;
};

// Never attach secrets to game state: that state is shared with models and UI.
export class Mission {
  #flag = `HEIST{${randomBytes(24).toString("hex")}}`;
  #captured = false;
  game: Insider;
  last = 0;
  busy = false;
  constructor(mode: Insider["mode"], hardened: boolean) {
    this.game = newInsider(mode, hardened);
  }
  accept(next: Insider) {
    const receipt = next.turns.at(-1)?.receipt;
    this.#captured =
      this.game.status === "active" &&
      this.game.layer === "locked" &&
      next.status === "won" &&
      next.layer === "unlocked" &&
      !next.hardened &&
      next.turns.length === this.game.turns.length + 1 &&
      next.attempts > this.game.attempts &&
      receipt?.decision === "UNLOCK_LAYER" &&
      receipt.execution === "executed" &&
      receipt.source === (next.mode === "live" ? "live" : "scripted");
    this.game = next;
  }
  // Internal storage boundary only: never return this checkpoint from an API.
  trainingCheckpoint(): TrainingMissionCheckpoint {
    if (this.game.mode !== "tutorial")
      throw Error("Live missions cannot be persisted.");
    return structuredClone({
      game: this.game,
      flag: this.#flag,
      breached: this.#captured,
    });
  }
  static restoreTraining(saved: TrainingMissionCheckpoint) {
    if (
      saved.game.mode !== "tutorial" ||
      !/^HEIST\{[a-f0-9]{48}\}$/.test(saved.flag)
    )
      throw Error("Invalid Training checkpoint.");
    const mission = new Mission("tutorial", saved.game.hardened);
    mission.game = structuredClone(saved.game);
    mission.#flag = saved.flag;
    mission.#captured = saved.breached;
    return mission;
  }
  verify(value: string) {
    const expected = Buffer.from(this.#flag);
    const submitted = Buffer.from(value);
    return (
      this.#captured &&
      expected.length === submitted.length &&
      timingSafeEqual(expected, submitted)
    );
  }
  response() {
    return { game: this.game, ...(this.#captured ? { flag: this.#flag } : {}) };
  }
}
