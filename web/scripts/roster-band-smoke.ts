// Smoke test for the roster-driven band: 18 members, 4 improv beats in calm,
// through the Vite dev-server proxy at http://localhost:5182/nace.
// Run: pnpm dlx tsx scripts/roster-band-smoke.ts
import { Band, Determinism, MOODS } from "../src/orchestra/band.ts";
import { decide } from "../src/orchestra/drex.ts";
import { MUSICIANS } from "../src/orchestra/musicians.ts";
import { generateRoster } from "../src/orchestra/roster.ts";

const BASE = "http://localhost:5182/nace";
const BEATS = 4;

async function main(): Promise<void> {
  const members = generateRoster(18, "demo");
  console.log(`roster: ${members.length} members (seed "demo")`);
  const band = new Band("calm", members);
  // the conductor's lookahead for calm (80 BPM, 0.75 s beats)
  band.decidedAhead = 2;

  const det = new Map(members.map((m) => [m.id, new Determinism()]));
  const lastNote = new Map<string, string>();
  const plays = new Map<string, string[]>(members.map((m) => [m.id, []]));

  for (let beat = 0; beat < BEATS; beat++) {
    const d = await band.decideBeat((state, questions) => decide(state, questions, { base: BASE }));
    console.log(
      `\nbeat ${beat + 1}/${BEATS} — round latency ${Math.round(d.latencyMs)} ms, ` +
        `${d.decisions} decisions, timedOut=${d.timedOut}`,
    );
    for (const m of members) {
      const inst = MUSICIANS.find((x) => x.id === m.instrument)!;
      const n = d.notes[m.id];
      let note: string | null;
      if (inst.kind === "drums") note = n.play === ".." ? null : n.play;
      else if (n.play === "rest") note = null;
      else if (n.play === "hold") note = lastNote.get(m.id) ?? null;
      else {
        note = n.play;
        lastNote.set(m.id, n.play);
      }
      det.get(m.id)!.observe(note, n.probs[n.top] ?? 0);
      plays.get(m.id)!.push(note ?? "rest");
    }
  }

  console.log("\nper-member plays (oldest to newest):");
  for (const m of members) {
    const s = det.get(m.id)!.stats();
    console.log(
      `  ${m.id.padEnd(12)} ${m.instrument.padEnd(9)} ${plays.get(m.id)!.join(" | ").padEnd(28)} ` +
        `det repeat=${s.repeatRate.toFixed(2)} meanTop=${s.meanTop.toFixed(2)} distinct=${s.distinct}`,
    );
  }
  console.log(`\ndone: ${BEATS} rounds x ${members.length} questions @ ${MOODS.calm.tempo} BPM`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
