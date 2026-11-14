"""Local server: serves mocks/ and streams a trial as Server-Sent Events. Standard library only, loopback only.

    python3 -m engine.server            # http://127.0.0.1:8765/

    GET /api/health                               {"ok": true, "simulated": <bool>, "intern": <bool>, "intern_missing": [...]}
    GET /api/stream?case=<id>&flip=f1,f2&speed=1&role=<id>  one `data: <event json>` per event, then `event: done`
    GET /api/stream?case=<id>&harness=intern      the same trial started by Intern (Drex System 1, Qwen System 2, the
                                                  jury engine as its MCP tool): Intern's steps and the trial's events
    GET /api/run?case=<id>[&id=<run_id>]          {run_id, harness, summary, events, reply} of that case's last finished
                                                  run (a live run before a simulated one); the trace ledger reads this
    GET /api/runs/latest                          events of the most recent run, as a JSON array

In simulated mode the stream sleeps about min(ms * 0.3, 380) / speed ms between events so the UI animates.
Requests whose Host or Origin is not the loopback address are refused (blocks DNS rebinding and other
sites' pages from starting a paid run).
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import cases, config, drex, intern_runner, net, trial

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # one response per connection: the stream ends when we close

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(config.MOCKS_DIR), **kwargs)

    def log_message(self, fmt, *args) -> None:  # keep the console quiet
        pass

    def _local(self) -> bool:
        host = urllib.parse.urlsplit("//" + self.headers.get("Host", "")).hostname
        origin = self.headers.get("Origin")
        return host in LOCAL_HOSTS and (origin is None or urllib.parse.urlsplit(origin).hostname in LOCAL_HOSTS)

    def _json(self, status: int, body) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if not self._local():
            return self._json(403, {"error": "loopback requests only"})
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/api/health":
            return self._json(200, {"ok": True, "simulated": not drex.has_key(), "intern": intern_runner.ready(),
                                    "intern_missing": intern_runner.missing()})
        if url.path == "/api/run":
            q = urllib.parse.parse_qs(url.query)
            found = run_record(q.get("case", [""])[0], q.get("id", [""])[0])
            return self._json(200 if found else 404, found or {"error": "no finished run for that case"})
        if url.path == "/api/runs/latest":
            return self._json(200, latest_events())
        if url.path == "/api/stream":
            return self._stream(urllib.parse.parse_qs(url.query))
        if url.path.startswith("/api/"):
            return self._json(404, {"error": "unknown endpoint"})
        if url.path == "/":
            self.path = "/01-slack-intake.html"
        super().do_GET()

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _send(self, data, event: str | None = None) -> None:
        self.wfile.write(((f"event: {event}\n" if event else "") + f"data: {json.dumps(data)}\n\n").encode("utf-8"))
        self.wfile.flush()

    def _stream_intern(self, q: dict) -> None:
        """The same trial, started by Intern. Events are relayed as Intern and the jury engine produce them."""
        try:
            case = cases.get_case(q.get("case", [cases.all_cases()[0]["id"]])[0])
        except KeyError as e:
            return self._json(400, {"error": str(e)})
        if not intern_runner.ready():
            return self._json(409, {"error": "Intern cannot run here yet", "missing": intern_runner.missing()})
        self._sse()
        events: queue.Queue = queue.Queue()
        box: dict = {}

        def work() -> None:
            try:
                box["res"] = intern_runner.run(case["ask"], None, case["id"], q.get("role", [None])[0] or None, on_event=events.put)
            except Exception as e:  # noqa: BLE001 - shown on the page, class and message only
                box["err"] = f"{type(e).__name__}: {e}"
            events.put(None)

        threading.Thread(target=work, daemon=True).start()
        try:
            while (ev := events.get()) is not None:
                self._send(ev)
            res = box.get("res")
            summary = _summary(res["run_id"]) if res and res["run_id"] else None
            if summary:
                self._send({**summary, "intern": {k: res[k] for k in ("status", "reply", "session", "tools", "seconds")}}, "done")
            else:
                self._send({"error": box.get("err") or f"Intern {res['status']}, no trial ran. {res['stderr'][-300:]}"}, "error")
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page closed the stream; Intern finishes on its own and the run stays on disk

    def _stream(self, q: dict) -> None:
        if q.get("harness") == ["intern"]:
            return self._stream_intern(q)
        try:
            speed = min(50.0, max(0.1, float(q.get("speed", ["1"])[0])))
            run = trial.Trial(q.get("case", [cases.all_cases()[0]["id"]])[0], q.get("flip", [""])[0],
                              True if q.get("simulate") == ["1"] else None, role=q.get("role", [None])[0] or None)
        except (KeyError, ValueError, drex.DrexError) as e:
            return self._json(400, {"error": str(e)})
        self._sse()
        events = run.events()
        try:
            for ev in events:
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode("utf-8"))
                self.wfile.flush()
                if run.simulate:
                    time.sleep(min(ev["ms"] * 0.3, 380) / speed / 1000)
            self.wfile.write(f"event: done\ndata: {json.dumps(run.result['summary'])}\n\n".encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page closed the stream
        except (drex.DrexError, net.NetError) as e:
            self.wfile.write(f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n".encode("utf-8"))
        finally:
            events.close()  # writes summary.json even when the client left early


def _summary(run_id: str) -> dict | None:
    path = config.RUNS_DIR / run_id / "summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def run_record(case_id: str, run_id: str = "") -> dict | None:
    """One finished run for the trace ledger: the named one, else the case's latest live run, else its latest
    simulated one. A run that Intern started carries Intern's steps and tool calls in the same trace."""
    if run_id:
        if "/" in run_id or ".." in run_id:
            return None
        names = [run_id]
    else:
        done = sorted(p.parent.name for p in config.RUNS_DIR.glob(f"*-{case_id}-*/summary.json")) if case_id and "/" not in case_id else []
        names = [n for n in done if "-live" in n][-1:] or done[-1:]
    for name in names:
        summary = _summary(name)
        events_file = config.RUNS_DIR / name / "events.jsonl"
        if not summary or not events_file.is_file():
            continue
        stream = intern_runner.stream_of(name)
        events = stream or [json.loads(ln) for ln in events_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        session = intern_runner.session_of(name)
        result = session / "result.json" if session else None
        reply = json.loads(result.read_text(encoding="utf-8")).get("reply", "") if result and result.is_file() else ""
        return {"run_id": name, "harness": "intern" if stream else "engine", "summary": summary, "events": events, "reply": reply}
    return None


def latest_events() -> list[dict]:
    """Events of the most recent run folder (names start with a UTC stamp, so the last one sorts last)."""
    files = sorted(config.RUNS_DIR.glob("*/events.jsonl"), key=lambda p: p.parent.name)
    if not files:
        return []
    return [json.loads(line) for line in files[-1].read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    server = ThreadingHTTPServer((config.SERVER_HOST, config.SERVER_PORT), Handler)
    print(f"serving mocks/ on http://{config.SERVER_HOST}:{config.SERVER_PORT}/ "
          f"({'simulated' if not drex.has_key() else 'live Drex'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
