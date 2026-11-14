"""JSONL result storage. Mock and live never share a file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import RESULTS_DIR


class ResultStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or RESULTS_DIR

    def path(self, mode: str, track: str) -> Path:
        if mode not in ("mock", "live"):
            raise ValueError(mode)
        if track not in ("decisions", "documents", "hard_cases"):
            raise ValueError(track)
        return self.root / mode / track / "records.jsonl"

    def append(self, mode: str, track: str, record: dict[str, Any]) -> None:
        path = self.path(mode, track)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    def read(self, mode: str, track: str) -> list[dict[str, Any]]:
        path = self.path(mode, track)
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
