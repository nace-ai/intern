import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { parse } from "dotenv";
function git(...args) {
  const r = spawnSync("git", args, {
    encoding: null,
    maxBuffer: 32 * 1024 * 1024,
  });
  if (r.status !== 0)
    throw Error("Git audit command failed; inspect repository state.");
  return r.stdout;
}
const env = existsSync(".env") ? parse(readFileSync(".env")) : {};
const external =
  env.NACE_ENV_FILE && existsSync(env.NACE_ENV_FILE)
    ? parse(readFileSync(env.NACE_ENV_FILE))
    : {};
const credentials = Object.entries({ ...external, ...env, ...process.env })
  .filter(
    ([name, v]) =>
      /(?:API_KEY|ACCESS_TOKEN|SECRET_ACCESS_KEY|AUTH_TOKEN)$/.test(name) &&
      typeof v === "string" &&
      v.length > 15,
  )
  .map(([, v]) => v);
const dummyKeys = new Set(["nace_sk_UNIT_TEST_NOT_A_REAL_KEY"]);
function inspect(bytes, label) {
  const text = bytes.toString("utf8");
  if (credentials.some((value) => bytes.includes(Buffer.from(value))))
    throw Error(
      `Configured credential detected in ${label}; do not commit or push.`,
    );
  const patterns = [
    /nace_sk_[A-Za-z0-9_-]{12,}/g,
    /gsk_[A-Za-z0-9]{16,}/g,
    /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/g,
    /AKIA[A-Z0-9]{16}/g,
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g,
    /HEIST\{[a-f0-9]{32,}\}/g,
  ];
  for (const pattern of patterns)
    for (const match of text.matchAll(pattern))
      if (!dummyKeys.has(match[0]))
        throw Error(
          `Potential credential or session flag detected in ${label}; do not commit or push.`,
        );
}
const names = git("ls-files", "-z").toString().split("\0").filter(Boolean);
if (!names.length)
  throw Error("No indexed files. Stage the intended files before auditing.");
const forbidden =
  /(^|\/)(node_modules|dist|worker-dist|\.private|\.runtime|\.wrangler|\.cache|\.git|\.idea|\.vscode)(\/|$)|(^|\/)\.DS_Store$|\.(log|pid|pem|key)$|\.pid\.lock$|(^|\/)benchmark-results[^/]*\.json$/;
for (const path of names) {
  if (
    forbidden.test(path) ||
    (/(^|\/)\.env[^/]*$/.test(path) && path !== ".env.example") ||
    (/(^|\/)\.dev\.vars[^/]*$/.test(path) && path !== ".dev.vars.example")
  )
    throw Error(`Private/generated path is indexed: ${path}`);
  inspect(git("show", `:./${path}`), `index:${path}`);
}
let historyBlobs = 0;
for (const line of git("rev-list", "--objects", "--all")
  .toString()
  .trim()
  .split("\n")
  .filter(Boolean)) {
  const [oid, ...path] = line.split(" ");
  if (git("cat-file", "-t", oid).toString().trim() === "blob") {
    inspect(git("cat-file", "blob", oid), `history:${path.join(" ") || oid}`);
    historyBlobs++;
  }
}
console.log(
  `PASS: ${names.length} indexed files and ${historyBlobs} reachable history blobs audited. No forbidden paths, configured credentials, credential patterns or real session flags found. Dummy test credentials are explicitly allowlisted.`,
);
