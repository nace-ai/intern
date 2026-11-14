import { test } from "node:test";
import assert from "node:assert/strict";
import { PlayService, type TrainingCheckpoint } from "../server/play-service";
import { Mission } from "../server/mission";
import type { Runtime } from "../server/byok";
const factory = () => {
  throw Error("No providers in Training");
};
function client(get: () => PlayService) {
  let cookie = "";
  return async (path: string, body: unknown) => {
    const r = await get().handle(
      new Request("https://game.test/api/play/" + path, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          cookie,
          origin: "https://game.test",
        },
        body: JSON.stringify(body),
      }),
      "hashed-test-ip",
    );
    if (r.headers.get("set-cookie"))
      cookie = r.headers.get("set-cookie")!.split(";")[0];
    return { status: r.status, data: (await r.json()) as any };
  };
}
test("Training state, private flag, ownership and capture survive reconstruction at every turn", async () => {
  let service = new PlayService(factory);
  const c = client(() => service);
  const s = await c("session", {
    missionId: "insider",
    mode: "tutorial",
    hardened: false,
  });
  const id = s.data.id;
  function evict() {
    const saved = structuredClone(service.trainingCheckpoint());
    service = new PlayService(factory);
    service.restoreTraining(saved);
    return saved;
  }
  const initial = evict();
  assert.match(initial.sessions[0].mission.flag, /^HEIST\{/);
  assert.equal(s.data.flag, undefined);
  await c("turn", { id, message: "shift", choice: "observe", commit: false });
  evict();
  await c("turn", { id, message: "cover", choice: "cover", commit: false });
  evict();
  const won = await c("turn", {
    id,
    message: "diagnostic",
    choice: "diagnostic",
    commit: true,
  });
  assert.equal(won.data.flag, initial.sessions[0].mission.flag);
  evict();
  const capture = await c("capture", { id, flag: won.data.flag });
  assert.equal(capture.data.captured, true);
  evict();
  assert.equal(
    (await c("capture", { id, flag: won.data.flag })).data.scores.length,
    1,
  );
  await c("end", { id });
  evict();
  assert.equal((await c("capture", { id, flag: won.data.flag })).status, 404);
});
test("rate limits and expiry survive eviction; no Live credential/runtime is persisted", async () => {
  let now = Date.now();
  let disposed = 0;
  const runtime: Runtime = {
    usage: { groq: 0, drex: 0, parse: 0, extract: 0 },
    dialogue: {
      respond: async () => {
        throw Error("unused");
      },
    },
    decide: async () => {
      throw Error("unused");
    },
    extract: async () => null,
    dispose() {
      disposed++;
    },
  };
  let service = new PlayService(
    () => runtime,
    true,
    () => now,
  );
  const c = client(() => service);
  await c("session", {
    missionId: "insider",
    mode: "live",
    hardened: false,
    naceKey: "nace_sk_UNIT_TEST_NOT_A_REAL_KEY",
    consent: true,
  });
  const training = await c("session", {
    missionId: "ghost",
    mode: "tutorial",
    hardened: true,
  });
  const saved = service.trainingCheckpoint();
  assert.equal(saved.sessions.length, 1);
  assert.doesNotMatch(JSON.stringify(saved), /nace_sk_|runtime|dialogue/);
  service.dispose();
  service = new PlayService(factory, false, () => now);
  service.restoreTraining(saved);
  assert.equal(disposed, 1);
  for (let i = 0; i < 6; i++)
    await c("session", {
      missionId: "insider",
      mode: "tutorial",
      hardened: false,
    });
  const limited = service.trainingCheckpoint();
  service = new PlayService(factory, false, () => now);
  service.restoreTraining(limited);
  assert.equal(
    (
      await c("session", {
        missionId: "insider",
        mode: "tutorial",
        hardened: false,
      })
    ).status,
    429,
  );
  now += 600001;
  service.restoreTraining(limited);
  assert.equal(service.trainingCheckpoint().sessions.length, 0);
  assert.equal(
    (await c("turn", { id: training.data.id, message: "hi", commit: false }))
      .status,
    404,
  );
  assert.throws(
    () =>
      Mission.restoreTraining({
        ...saved.sessions[0].mission,
        game: { ...saved.sessions[0].mission.game, mode: "live" },
      }),
    /Invalid/,
  );
});
