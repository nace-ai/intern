#!/usr/bin/env node
// Crashzilla as an MCP server (stdio, JSON-RPC 2.0, one message per line), so Intern, or any
// MCP client, can reconstruct a crash and triage the occupant as tool calls.
//
//   simulate_crash  pure physics + NHTSA injury curves (no network)
//   triage_crash    the same, then Drex scores 14 organs and the ER calls (network)
import "dotenv/config";
import { createInterface } from "node:readline";
import { simulate, describe } from "../public/physics.js";
import { decisionQuestions, drex, summarize } from "../lib/triage.js";

const INPUT = {
  type: "object",
  properties: {
    speedKmh: { type: "number", minimum: 5, maximum: 160, description: "Impact speed into a rigid barrier, km/h" },
    belted: { type: "boolean", description: "Driver wearing a three-point belt" },
    airbag: { type: "boolean", description: "Frontal airbag deployed" },
    age: { type: "number", minimum: 1, maximum: 110, description: "Occupant age in years" },
    pulseMs: { type: "number", minimum: 50, maximum: 160, description: "Crash pulse duration in ms (stiffer car = shorter). Default 95" },
  },
  required: ["speedKmh"],
  additionalProperties: false,
};
const TOOLS = [
  { name: "simulate_crash", description: "Reconstruct a frontal crash for one driver. Returns what a crash-test dummy would record (HIC15, chest deflection, neck Nij, femur load) and the NHTSA NCAP injury risks. Pure computation.", inputSchema: INPUT },
  { name: "triage_crash", description: "Reconstruct the crash, then ask Drex for injury odds for 14 organs and the ER calls (destination, trauma team, CT head, C-spine, FAST, blood bank). Calls the Drex API.", inputSchema: INPUT },
];

const round = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, typeof v === "number" ? +v.toFixed(3) : v]));

async function call(name, args) {
  const sim = simulate(args ?? {});
  const base = { input: sim.input, readings: round(sim.metrics), nhtsa_risk: round(sim.risk) };
  if (name === "simulate_crash") return base;
  if (name === "triage_crash") {
    const r = await drex({ state: describe(sim), questions: decisionQuestions() });
    return { ...base, drex: { model: r.model, evaluation_time_ms: r.evaluation_time_ms, ...summarize(r.answers) } };
  }
  throw new Error(`unknown tool ${name}`);
}

const send = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");
createInterface({ input: process.stdin }).on("line", async (line) => {
  if (!line.trim()) return;
  let m;
  try { m = JSON.parse(line); } catch { return send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "parse error" } }); }
  if (m.id === undefined) return; // notification
  try {
    if (m.method === "initialize") return send({ jsonrpc: "2.0", id: m.id, result: { protocolVersion: m.params?.protocolVersion ?? "2025-06-18", capabilities: { tools: {} }, serverInfo: { name: "crashzilla", version: "1.0.0" } } });
    if (m.method === "ping") return send({ jsonrpc: "2.0", id: m.id, result: {} });
    if (m.method === "tools/list") return send({ jsonrpc: "2.0", id: m.id, result: { tools: TOOLS } });
    if (m.method === "tools/call") {
      try {
        const out = await call(m.params?.name, m.params?.arguments);
        return send({ jsonrpc: "2.0", id: m.id, result: { content: [{ type: "text", text: JSON.stringify(out, null, 1) }] } });
      } catch (e) {
        return send({ jsonrpc: "2.0", id: m.id, result: { isError: true, content: [{ type: "text", text: String(e.message) }] } });
      }
    }
    send({ jsonrpc: "2.0", id: m.id, error: { code: -32601, message: `method not found: ${m.method}` } });
  } catch (e) {
    send({ jsonrpc: "2.0", id: m.id, error: { code: -32603, message: String(e.message) } });
  }
});
