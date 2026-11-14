// Reproduce the staged teammate checkout without copying local credentials or data.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const temp = mkdtempSync(join(tmpdir(), "agent-heist-onboarding-"));
const clean = join(temp, "Agent Heist");
mkdirSync(clean);
const env = {
  ...process.env,
  LIVE_ENABLED: "false",
  NACE_API_KEY: "",
  GROQ_API_KEY: "",
  NACE_ENV_FILE: "",
  RUN_GROQ_EVAL: "false",
};
delete env.HEIST_OFFLINE;
function run(cmd, args, cwd = clean) {
  const r = spawnSync(cmd, args, {
    cwd,
    env,
    stdio: "inherit",
    timeout: 240000,
  });
  if (r.error || r.status !== 0)
    throw Error(`Clean-checkout check failed: ${cmd} ${args.join(" ")}`);
}
try {
  run("git", ["checkout-index", "--all", `--prefix=${clean}/`], root);
  for (const path of [".env", ".private", "node_modules", "dist", ".runtime"])
    assert.equal(
      existsSync(join(clean, path)),
      false,
      path + " must not be copied",
    );
  run(join(clean, "start.sh"), [], temp);
  run(process.execPath, ["scripts/smoke-api.mjs"]);
  run(join(clean, "stop.sh"), [], temp);
  run("npm", ["run", "test:launcher"]);
  run("npm", ["test"]);
  run("npm", ["run", "check:worker"]);
  run("npm", ["run", "audit:secrets"]);
  console.log(
    `PASS: credential-free staged checkout installed, started, played three Training missions, stopped, and passed all checks. Location: ${clean}`,
  );
} finally {
  if (existsSync(join(clean, "stop.sh")))
    spawnSync(join(clean, "stop.sh"), [], { cwd: temp, env, stdio: "inherit" });
}
