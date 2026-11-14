// Read a crash / EMS report with NDI, reconstruct the crash, triage with Drex.
//   node scripts/ndi-intake.mjs samples/ems-report-sample.pdf
import "dotenv/config";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { upload, extractIntake, toTriageInput } from "../lib/ndi.js";
import { simulate, describe } from "../public/physics.js";
import { decisionQuestions, drex, summarize } from "../lib/triage.js";

const file = process.argv[2] ?? "samples/ems-report-sample.pdf";
const source = await upload(await readFile(file), path.basename(file));
const ndi = await extractIntake(source);
const { input, notes } = toTriageInput(ndi.data);
const sim = simulate(input);
const r = await drex({ state: [describe(sim), ...notes].join("\n"), questions: decisionQuestions() });
console.log(JSON.stringify({ ndi: { ms: Math.round(ndi.ms), data: ndi.data, fields: ndi.fields?.map((f) => `${f.path}:${f.status}`) }, input: sim.input, nhtsa_risk: sim.risk, drex: { ms: Math.round(r.roundTripMs), ...summarize(r.answers) } }, null, 1));
