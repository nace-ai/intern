import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  renameSync,
  rmdirSync,
} from "node:fs";
export type Kind = "parse" | "extract" | "drex";
export const limits = { parse: 1, extract: 1, drex: 8 };
export const reserveCost = { parse: 0.02, extract: 0.03, drex: 0.01 };
export class Budget {
  data = { counts: { parse: 0, extract: 0, drex: 0 }, reserved: 0, limit: 4 };
  constructor(private path?: string) {
    if (path && existsSync(path))
      this.data = JSON.parse(readFileSync(path, "utf8"));
  }
  refresh() {
    if (this.path && existsSync(this.path))
      this.data = JSON.parse(readFileSync(this.path, "utf8"));
  }
  reserve(kind: Kind) {
    if (this.path) {
      mkdirSync(this.path.substring(0, this.path.lastIndexOf("/")), {
        recursive: true,
        mode: 0o700,
      });
      mkdirSync(this.path + ".lock");
    }
    try {
      this.refresh();
      if (
        this.data.counts[kind] >= limits[kind] ||
        this.data.reserved + reserveCost[kind] > Math.min(4, this.data.limit)
      )
        throw Error(
          `Paid-call safety limit reached (${kind}). No request sent.`,
        );
      this.data.counts[kind]++;
      this.data.reserved += reserveCost[kind];
      this.save();
    } finally {
      if (this.path) rmdirSync(this.path + ".lock");
    }
  }
  save() {
    if (this.path) {
      mkdirSync(this.path.substring(0, this.path.lastIndexOf("/")), {
        recursive: true,
        mode: 0o700,
      });
      writeFileSync(this.path + ".tmp", JSON.stringify(this.data), {
        mode: 0o600,
      });
      renameSync(this.path + ".tmp", this.path);
    }
  }
}
