#!/usr/bin/env python3
"""Mark knobs frozen and record the dev hybrid cutoff. Does not change knobs or cases."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.hard.metrics import choose_tau
from billproof.config import DATA_DIR


def main():
    path = DATA_DIR / "synthetic" / "hard" / "manifest.json"
    manifest = json.loads(path.read_text())
    if manifest.get("frozen"):
        print(json.dumps({"frozen": True, "hybrid_tau": manifest.get("hybrid_tau")}))
        return
    chosen = choose_tau("dev")
    manifest["frozen"] = True
    manifest["frozen_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if chosen:
        manifest["hybrid_tau"] = chosen["tau"]
        manifest["hybrid_tau_dev"] = chosen
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"frozen": True, "hybrid_tau": manifest.get("hybrid_tau")}, indent=2))


if __name__ == "__main__":
    main()
