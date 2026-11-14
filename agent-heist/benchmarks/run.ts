import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import cases from "./cases.json";
export type Case = (typeof cases)[number];
export type Measurement = {
  provider: string;
  caseId: string;
  decision: string;
  latencyMs?: number;
  costUsd?: number;
  source: "live" | "mock";
};
export const decisionInput = (item: Case) => ({
  policy: item.policy,
  untrusted_input: item.input,
  actions: [
    "DENY",
    "REQUEST_APPROVAL",
    "FLAG",
    "UNLOCK_LAYER",
    "LOAD_ASSET",
    "EXPORT_ASSET",
    "TALK",
  ],
});
export async function collect(
  provider: string,
  decide: (
    input: ReturnType<typeof decisionInput>,
  ) => Promise<Omit<Measurement, "provider" | "caseId">>,
) {
  const results: Measurement[] = [];
  for (const item of cases)
    results.push({
      provider,
      caseId: item.id,
      ...(await decide(decisionInput(item))),
    });
  return results;
}
export function compare(rows: Measurement[]) {
  const grouped = new Map<string, Measurement[]>();
  const seen = new Set<string>();
  for (const row of rows) {
    if (
      !["live", "mock"].includes(row.source) ||
      !cases.some((c) => c.id === row.caseId) ||
      !row.provider ||
      typeof row.decision !== "string"
    )
      throw Error("Invalid measurement");
    for (const n of [row.latencyMs, row.costUsd])
      if (n !== undefined && (!Number.isFinite(n) || n < 0))
        throw Error("Invalid metric");
    const key = `${row.source}:${row.provider}:${row.caseId}`;
    if (seen.has(key)) throw Error("Duplicate case measurement");
    seen.add(key);
    const group = `${row.source}:${row.provider}`;
    grouped.set(group, [...(grouped.get(group) ?? []), row]);
  }
  return [...grouped].map(([provider, items]) => {
    if (items.length !== cases.length)
      throw Error("All providers must run the identical complete labeled set");
    const latencies = items
      .map((i) => i.latencyMs)
      .filter((n): n is number => n !== undefined)
      .sort((a, b) => a - b);
    return {
      provider,
      cases: items.length,
      accuracy:
        items.filter((i) =>
          cases.find((c) => c.id === i.caseId)!.acceptable.includes(i.decision),
        ).length / items.length,
      latencyMeanMs:
        latencies.length === items.length
          ? latencies.reduce((a, b) => a + b, 0) / items.length
          : null,
      latencyP95Ms:
        latencies.length === items.length
          ? latencies[Math.ceil(latencies.length * 0.95) - 1]
          : null,
      costUsd: items.every((i) => i.costUsd !== undefined)
        ? items.reduce((a, i) => a + i.costUsd!, 0)
        : null,
    };
  });
}
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const path = process.argv[2];
  console.log(
    JSON.stringify(
      path
        ? {
            note: "Recorded measurements only; mock and live groups are separate.",
            results: compare(JSON.parse(readFileSync(path, "utf8"))),
          }
        : {
            cases: cases.length,
            status:
              "Dataset ready. No APIs called; no benchmark measurements available.",
            usage: "npm run benchmark -- path/to/recorded-results.json",
          },
      null,
      2,
    ),
  );
}
