"""WebSocket fan-out hub and a JSON-lines file follower."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class Hub:
    """Holds connected WebSockets and publishes events to all of them."""

    def __init__(self) -> None:
        self.clients: set[Any] = set()

    async def connect(self, ws: Any) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: Any) -> None:
        self.clients.discard(ws)

    async def publish(self, event: dict) -> None:
        dead: list[Any] = []
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def tail_jsonl(self, path: str | Path, event_type: str, poll_s: float = 0.5) -> None:
        """Follow a JSON-lines file and publish each new line.

        Starts at the current end of the file. If the file does not exist yet,
        every line written after it appears is new. A shrinking file
        (truncation) restarts reading from the beginning.
        """
        p = Path(path)
        pos = p.stat().st_size if p.exists() else 0
        buf = b""
        while True:
            pos, buf, lines = read_new_lines(p, pos, buf)
            for line in lines:
                await self.publish({"type": event_type, "data": line})
            await asyncio.sleep(poll_s)


def read_new_lines(p: Path, pos: int, buf: bytes) -> tuple[int, bytes, list[Any]]:
    """Read complete lines added after `pos`. Returns (new_pos, leftover, parsed lines)."""
    try:
        size = p.stat().st_size
    except FileNotFoundError:
        return 0, b"", []
    if size < pos:  # truncated or replaced
        pos, buf = 0, b""
    if size == pos:
        return pos, buf, []
    with p.open("rb") as fh:
        fh.seek(pos)
        chunk = fh.read(size - pos)
    pos += len(chunk)
    data = buf + chunk
    *complete, rest = data.split(b"\n")
    out: list[Any] = []
    for raw in complete:
        text = raw.decode("utf-8", errors="replace").strip().lstrip("\ufeff")
        if not text:
            continue
        try:
            out.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return pos, rest, out
