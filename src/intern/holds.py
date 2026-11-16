"""Holds: keep a command running while something it waits on is open (a call to a platform that answers only when it is
done), so `idle_stop_s` does not stop it for the silence.

A session whose commands may be stopped for silence, and run on this machine, listens on a Unix socket in a private
folder. Every command it runs is given the socket's path (`INTERN_HOLD_SOCKET`) and its own id (`INTERN_PROCESS_ID`).
A hold is one connection: the client sends one JSON line, `{"process": id, "reason": text}`, is answered
`{"ok": true}` or `{"ok": false, "error": text}`, and holds the command for as long as it keeps the connection open.
Closing it, or exiting, ends the hold, so a client that crashes leaves nothing held. A hold never extends `timeout_s`.

`intern hold -- cmd` holds the command it runs in for as long as `cmd` runs; `hold()` does the same around a block of
Python. Outside a session (no `INTERN_HOLD_SOCKET`), both just run what they wrap.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from intern.processes import ProcessManager

SOCKET_ENV = "INTERN_HOLD_SOCKET"
PROCESS_ENV = "INTERN_PROCESS_ID"
HOLD_ENV = frozenset({SOCKET_ENV, PROCESS_ENV})
"""The names every command is given; not secrets, so a command with command secrets gets them too."""
REQUEST_S = 5.0  # how long a connection may take to send its request, and a client to be answered
MAX_REQUEST = 4096


class HoldServer:
    """The session's hold socket, over its job manager: `async with HoldServer(manager)` from session open to close."""

    def __init__(self, manager: ProcessManager) -> None:
        self.manager = manager
        self._folder: Path | None = None
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> HoldServer:
        # A private folder: only this user's processes can reach the socket. Kept short, under a Unix socket path's
        # 104-byte limit on macOS.
        self._folder = Path(tempfile.mkdtemp(prefix="intern-"))
        path = self._folder / "hold.sock"
        self._server = await asyncio.start_unix_server(self._serve, path=str(path), limit=MAX_REQUEST)
        self.manager.hold_socket = path
        return self

    async def __aexit__(self, *_: object) -> None:
        self.manager.hold_socket = None
        if self._server is not None:
            self._server.close()
        for task in list(self._connections):
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        if self._folder is not None:
            shutil.rmtree(self._folder, ignore_errors=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._connections.add(task)
        release = None
        try:
            try:
                request = json.loads(await asyncio.wait_for(reader.readline(), REQUEST_S))
                process = request["process"]
                if not isinstance(process, str):
                    raise ValueError("`process` must be a string")
                release = self.manager.hold(process)
                answer: dict[str, object] = {"ok": True}
            except (ValueError, KeyError, TypeError, TimeoutError, asyncio.LimitOverrunError) as error:
                answer = {"ok": False, "error": str(error) or type(error).__name__}
            writer.write(json.dumps(answer).encode() + b"\n")
            await writer.drain()
            if release is not None:
                while await reader.read(MAX_REQUEST):  # held until the client closes the connection
                    pass
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if release is not None:
                release()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self._connections.discard(task)


@contextlib.contextmanager
def hold(reason: str = "") -> Iterator[str | None]:
    """Hold the command this runs in while the block runs. Yields None where no hold was taken (outside a session, or
    the session refused it, said on stderr); the block runs either way."""
    path, process = os.environ.get(SOCKET_ENV), os.environ.get(PROCESS_ENV)
    if not path or not process:
        yield None
        return
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(REQUEST_S)
        connection.connect(path)
        connection.sendall(json.dumps({"process": process, "reason": reason}).encode() + b"\n")
        answer = json.loads(connection.makefile("rb").readline() or b"{}")
        if not answer.get("ok"):
            raise ValueError(answer.get("error") or "no answer")
        connection.settimeout(None)
    except (OSError, ValueError) as error:
        connection.close()
        print(f"intern hold: not held ({error}); running anyway", file=sys.stderr)
        yield None
        return
    try:
        yield process
    finally:
        connection.close()


def hold_main(argv: Sequence[str]) -> int:
    """`intern hold [--reason TEXT] -- cmd...`: run `cmd`, holding the command this runs in until it exits; its exit
    code is `cmd`'s."""
    args = list(argv)
    reason = ""
    if args[:1] == ["--reason"] and len(args) >= 2:
        reason, args = args[1], args[2:]
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        print("usage: intern hold [--reason TEXT] -- command [args...]", file=sys.stderr)
        return 2
    with hold(reason):
        try:
            child = subprocess.Popen(args)
        except OSError as error:
            print(f"intern hold: {args[0]}: {error.strerror}", file=sys.stderr)
            return 127
        # A signal the harness sends this process alone reaches `cmd` too; stopping the command's group reaches both.
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda number, _: child.send_signal(number))
        code = child.wait()
    return 128 - code if code < 0 else code
