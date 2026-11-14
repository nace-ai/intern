import {
  existsSync,
  readFileSync,
  mkdirSync,
  writeFileSync,
  renameSync,
  rmdirSync,
} from "node:fs";
export class Quota {
  constructor(
    readonly path: string,
    readonly cap: number,
  ) {}
  read(): { used: number; tokens: number } {
    return existsSync(this.path)
      ? JSON.parse(readFileSync(this.path, "utf8"))
      : { used: 0, tokens: 0 };
  }
  reserve(tokens = 0) {
    mkdirSync(".private", { recursive: true, mode: 0o700 });
    mkdirSync(this.path + ".lock");
    try {
      const d = this.read();
      if (d.used >= this.cap || d.tokens + tokens > 100000)
        throw Error("Server request quota reached. No request sent.");
      d.used++;
      d.tokens += tokens;
      writeFileSync(this.path + ".tmp", JSON.stringify(d), { mode: 0o600 });
      renameSync(this.path + ".tmp", this.path);
      return d.used;
    } finally {
      rmdirSync(this.path + ".lock");
    }
  }
}
export const groqQuota = new Quota(".private/groq-quota.json", 60);
export const phase2DrexQuota = new Quota(".private/phase2-drex-quota.json", 2);
