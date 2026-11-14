#!/usr/bin/env node
// Writes cases/cases.json from mocks/tad-sim.js so the engine and the mocks share one case definition.
//   node tools/export_cases.js
const fs = require("fs"), path = require("path"), vm = require("vm");
const root = path.resolve(__dirname, "..");
const ctx = { console }; ctx.globalThis = ctx; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(root, "mocks/lawwiki-data.js"), "utf8").replace(/window\./g, "globalThis."), ctx);
vm.runInContext(fs.readFileSync(path.join(root, "mocks/tad-sim.js"), "utf8"), ctx);
const T = ctx.TAD;
const out = {
  note: "Invented disputes and people. Statute citations come from LawWiki; a ruling marked real is a published opinion (data/rulings). 'base' and facts[].w are weights for the simulated fallback only.",
  phases: T.PHASES, jurors: T.JURORS, cases: T.CASES,
};
fs.writeFileSync(path.join(root, "cases/cases.json"), JSON.stringify(out, null, 1));
console.log(out.cases.length, "cases,", out.jurors.length, "jurors -> cases/cases.json");
