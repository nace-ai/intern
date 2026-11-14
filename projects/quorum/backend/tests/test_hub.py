import asyncio
import json
from pathlib import Path

from backend.app.hub import Hub, read_new_lines


class FakeWS:
    def __init__(self, dead: bool = False) -> None:
        self.sent: list[dict] = []
        self.dead = dead
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, event: dict) -> None:
        if self.dead:
            raise RuntimeError("closed")
        self.sent.append(event)


def test_publish_drops_dead_sockets() -> None:
    async def run() -> None:
        hub = Hub()
        good, bad = FakeWS(), FakeWS(dead=True)
        await hub.connect(good)
        await hub.connect(bad)
        assert good.accepted and bad.accepted
        await hub.publish({"type": "x"})
        assert good.sent == [{"type": "x"}]
        assert bad not in hub.clients and good in hub.clients
        hub.disconnect(good)
        assert not hub.clients

    asyncio.run(run())


def test_read_new_lines_partial_and_truncation(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    pos, buf, lines = read_new_lines(p, 0, b"")  # missing file
    assert (pos, buf, lines) == (0, b"", [])
    p.write_text('{"a": 1}\n{"b"')
    pos, buf, lines = read_new_lines(p, 0, b"")
    assert lines == [{"a": 1}] and buf == b'{"b"'
    with p.open("a") as fh:
        fh.write(': 2}\nnot json\n')
    pos, buf, lines = read_new_lines(p, pos, buf)
    assert lines == [{"b": 2}] and buf == b""
    p.write_text('{"c": 3}\n')  # truncated
    pos, buf, lines = read_new_lines(p, pos, buf)
    assert lines == [{"c": 3}]


def test_tail_jsonl_picks_up_appended_line(tmp_path: Path) -> None:
    p = tmp_path / "rounds.jsonl"
    p.write_text(json.dumps({"round": 0}) + "\n")  # existing content is skipped

    async def run() -> list[dict]:
        hub = Hub()
        ws = FakeWS()
        await hub.connect(ws)
        task = asyncio.create_task(hub.tail_jsonl(p, "round", poll_s=0.01))
        await asyncio.sleep(0.05)
        with p.open("a") as fh:
            fh.write(json.dumps({"round": 1}) + "\n")
        for _ in range(100):
            if ws.sent:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        return ws.sent

    assert asyncio.run(run()) == [{"type": "round", "data": {"round": 1}}]


def test_tail_jsonl_file_created_later(tmp_path: Path) -> None:
    p = tmp_path / "attacks.jsonl"

    async def run() -> list[dict]:
        hub = Hub()
        ws = FakeWS()
        await hub.connect(ws)
        task = asyncio.create_task(hub.tail_jsonl(p, "attack", poll_s=0.01))
        await asyncio.sleep(0.03)
        p.write_text(json.dumps({"id": "a00"}) + "\n")
        for _ in range(100):
            if ws.sent:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        return ws.sent

    assert asyncio.run(run()) == [{"type": "attack", "data": {"id": "a00"}}]
