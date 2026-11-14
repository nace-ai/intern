import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { extractDirections } from "../src/orchestra/ndi.ts";

const USAGE =
  "usage: node scripts/cache-directions.ts --base <proxy base, e.g. http://localhost:5173/nace> " +
  "(--url <https url> [--file-name name] | --file <local path>) --out <path>";

function fail(message: string): never {
  console.error(message);
  process.exit(1);
}

const args = process.argv.slice(2);
function value(flag: string): string | undefined {
  const i = args.indexOf(flag);
  if (i === -1) return undefined;
  const next = args[i + 1];
  if (next === undefined) fail(USAGE);
  return next;
}

const base = value("--base");
const url = value("--url");
const fileName = value("--file-name");
const filePath = value("--file");
const out = value("--out");
if (base === undefined || out === undefined) fail(USAGE);
if (url === undefined && filePath === undefined) fail(USAGE);
if (url !== undefined && filePath !== undefined) fail(USAGE);

async function main(): Promise<void> {
  let source: File | { url: string; fileName?: string };
  if (url !== undefined) {
    source = { url, fileName };
  } else {
    const file = filePath ?? "";
    source = new File([await readFile(file)], path.basename(file));
  }
  const directions = await extractDirections(
    source,
    (status) => console.log(`[ndi] ${status.phase} ${status.message}`),
    { base },
  );
  await writeFile(out, `${JSON.stringify(directions, null, 2)}\n`);
  console.log(`wrote ${out}`);
  for (const section of directions.sections) {
    console.log(`- ${section.name}: ${section.mood ?? "no mood"}`);
  }
}

main().catch((err: unknown) => fail(err instanceof Error ? err.message : String(err)));
