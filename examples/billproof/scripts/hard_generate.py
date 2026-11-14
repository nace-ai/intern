#!/usr/bin/env python3
"""Write a BillProof-Hard split. Dev first; test only after knobs are frozen."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.hard.policy import GPT_SYSTEM
from billproof.config import DATA_DIR
from billproof.synth.hard.generate import generate_split, summarize, write_split
from billproof.synth.hard.knobs import DEFAULT_KNOBS, Knobs
from billproof.synth.hard.questions import questions_for, visible_state
from billproof.synth.hard.rulespec import RULES

ROOT = DATA_DIR / "synthetic" / "hard"
SRC = Path(__file__).resolve().parents[1] / "src" / "billproof" / "synth" / "hard"


def _sha_files(paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--force", action="store_true", help="Rewrite an existing split file")
    args = parser.parse_args()
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    knobs = Knobs.from_dict(manifest.get("knobs") or {}) if manifest.get("knobs") else DEFAULT_KNOBS
    if args.split == "test" and not manifest.get("frozen"):
        raise SystemExit("refusing to write the test split before knobs are frozen in manifest.json")
    out = ROOT / f"{args.split}.jsonl"
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists; pass --force to rewrite a whole split")
    cases = generate_split(args.split, knobs)
    digest = write_split(out, cases)
    summary = summarize(cases)
    manifest.update({
        "seed": knobs.seed,
        "knobs": knobs.to_dict(),
        "frozen": bool(manifest.get("frozen")),
        f"{args.split}_sha256": digest,
        f"{args.split}_summary": summary,
        "generator_sha256": _sha_files(SRC.glob("*.py")),
        "rules_sha256": hashlib.sha256(RULES.encode()).hexdigest(),
        "gpt_system_sha256": hashlib.sha256(GPT_SYSTEM.encode()).hexdigest(),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"split": args.split, "sha256": digest, **summary}, indent=2))


if __name__ == "__main__":
    main()
