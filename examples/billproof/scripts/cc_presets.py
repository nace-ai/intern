#!/usr/bin/env python3
"""Recompute hypothetical what-if presets from the cached Drex facts.

No model calls. Safe to run while cc_scale.py is still writing the cache;
the counts are whatever is cached, and partial is set when the queue is short.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.command_center.store import get_store, publish


def main() -> int:
    store = get_store(force=True)
    planned = int(store.counts.get("n_cases") or 0)
    status = "complete" if planned and store.n_cached >= planned else "partial"
    if store.n_cached == 0:
        status = "empty"
    summary = publish(
        status,
        note="Preset refresh. No new model calls. Counts follow the fact cache.",
        with_presets=True,
        with_doc=True,
    )
    for item in summary.get("presets") or []:
        print(
            f"{item.get('id')} flips={item.get('n_flips')} "
            f"decided={item.get('n_decided')} counts={item.get('flip_counts')}",
            flush=True,
        )
    print(f"status={summary['status']} cached={summary['n_cached']}/{summary['n_planned']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
