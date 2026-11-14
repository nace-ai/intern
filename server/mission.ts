import { randomBytes, timingSafeEqual } from "node:crypto";
import { newInsider, type Insider } from "../src/insider";

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
