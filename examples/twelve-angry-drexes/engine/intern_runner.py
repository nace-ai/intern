"""Run one request through Intern, Nace's agent harness.

Intern plans with System 2 (Qwen on DeepInfra) and asks Drex, its System 1, the small typed questions and whether
each tool call is safe. Its tools here are three MCP servers: the jury engine of this repository
(engine/mcp_server.py), Nace's own server (Drex decisions, NDI documents) and Keenable (web search).

    python3 -m engine.intern_runner "question" [--document FILE] [--case ID]   # progress on stderr, the reply on stdout
    python3 -m engine.intern_runner --check                                    # what Intern still needs on this machine

One run keeps a session folder, runs/<stamp>-intern/:
    intern.jsonl     every line Intern printed, as printed (keys masked)
    mcp_calls.jsonl  every jury tool Intern called, written by the jury MCP server
    trial_run_id     the run folder of the trial Intern started
    stream.jsonl     one trace: Intern's steps, the tool calls and the trial's events, in the order they happened
    result.json      Intern's reply, exit status and the trial's run id

Intern is started headless with `intern -p --json` (one JSON event per line). Its event schema is not pinned here:
each line is kept whole and read tolerantly (see `trace_event`). The first real run on a machine should be checked
against `intern schema`.
"""
from __future__ import annotations

import collections
import json
import os
import re
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import config, drex

SYSTEM1 = "drex-latest"
SYSTEM2 = config.INTERN_SYSTEM2
HOME = config.ROOT / "intern" / "home"            # generated; git-ignored
TEMPLATE = config.ROOT / "intern" / "config.example.toml"
GUIDANCE = config.ROOT / "intern" / "SYSTEM1.md"
RUN_TIMEOUT = 420                                 # seconds for one request, document reading included
REPLY_CHARS = 2500
KEY_NAMES = ("NACE_API_KEY", "DEEPINFRA_API_KEY", "KEENABLE_API_KEY")
TEXT_KEYS = ("text", "content", "message", "reply", "answer", "output", "summary", "delta", "reason")


class InternError(RuntimeError):
    pass


def binary() -> str | None:
    """Path of the `intern` command, or None. TAD_INTERN_BIN overrides it (tests)."""
    local = Path.home() / ".local" / "bin" / "intern"
    return os.environ.get("TAD_INTERN_BIN") or shutil.which("intern") or (str(local) if local.is_file() else None)


def missing() -> list[str]:
    """What a real Intern run still needs here."""
    out = [] if binary() else ["Intern (bash setup/2_intern.sh)"]
    fix = {"NACE_API_KEY": "python3 tools/set_secret.py NACE_API_KEY", "DEEPINFRA_API_KEY": "bash setup/1_deepinfra.sh"}
    out += [f"{n} ({fix[n]})" for n in fix if not config.env_key(n)]
    return out


def ready() -> bool:
    return not missing()


def _mask(text: str) -> str:
    """Never let a key value reach a file, a page or Slack."""
    for name in KEY_NAMES + ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        value = config.env_key(name)
        if value and len(value) >= 8:
            text = text.replace(value, "***")
    return text


def _set_refs(line: str, empty: set[str]) -> str:
    """An `env_refs = { A = "B", ... }` or `header_env` line without the pairs whose variable B is empty.
    Intern refuses a server whose config names a variable that is not set. Other lines come back unchanged."""
    m = re.match(r"^(env_refs|header_env) = \{(.*)\}\s*$", line)
    if not m:
        return line or " "
    pairs = [(a, b) for a, b in re.findall(r'(\w+) = "(\w+)"', m.group(2)) if b not in empty]
    return f"{m.group(1)} = {{ " + ", ".join(f'{a} = "{b}"' for a, b in pairs) + " }" if pairs else ""


def write_home(nace_base: str, only_jury: bool = False) -> Path:
    """Fill intern/config.example.toml with this machine's paths and write intern/home/config.toml.
    Kept: the jury server always; Nace's local server when `uvx` exists; Keenable; Nace's hosted server only when
    TAD_INTERN_HOSTED=1 and its host (the console) is the one answering. `only_jury` keeps the jury server alone:
    the retry after Intern stops at startup, so one bad entry cannot block the demo."""
    uvx = shutil.which("uvx")
    empty = {n for n in KEY_NAMES if not config.env_key(n)}
    hosted = os.environ.get("TAD_INTERN_HOSTED") == "1" and nace_base == config.NACE_HOSTS[0]
    blocks = TEMPLATE.read_text(encoding="utf-8").split("\n\n")
    keep = []
    for block in blocks:
        if "[[capabilities.mcp]]" in block and only_jury and 'namespace = "jury"' not in block:
            continue
        if 'namespace = "nace_hosted"' in block and not hosted:
            continue
        if 'namespace = "nace"' in block and not uvx:
            continue
        keep.append("\n".join(filter(None, (_set_refs(line, empty) for line in block.split("\n")))) if "[[capabilities.mcp]]" in block else block)
    text = "\n\n".join(keep).replace("__REPO__", str(config.ROOT)).replace("__PYTHON__", sys.executable).replace("__UVX__", uvx or "uvx")
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "config.toml").write_text(text, encoding="utf-8")
    if GUIDANCE.is_file():
        (HOME / "SYSTEM1.md").write_text(GUIDANCE.read_text(encoding="utf-8"), encoding="utf-8")
    return HOME


def task_text(question: str, document: str | Path | None = None, case_hint: str | None = None, role: str | None = None) -> str:
    """The instruction System 2 receives. Tool names are the jury MCP server's."""
    doc = f'\nThe uploaded file is saved at: {document}' if document else ""
    hint = f' It is most likely "{case_hint}".' if case_hint else ""
    args = "that case" + (f', document "{document}"' if document else "") + (f', role "{role}"' if role else "")
    return (
        f'A legal team asks in Slack: "{question.strip()}"{doc}\n\n'
        "Answer with the jury tools, in this order:\n"
        f"1. list_cases: pick the case that matches the question.{hint}\n"
        f"2. run_trial with {args}. It reads the file with NDI, finds the statutes in LawWiki, seats 12 Drex jurors "
        "and returns the vote. Call it once.\n"
        "3. Reply in at most 120 words of plain text: the vote and the verdict, the fact that moves the vote most, "
        "the suggested next step with its probability, and the run id. End with this sentence: "
        '"Legal information, not legal advice. A simulation, not a prediction."\n\n'
        "Report what the tools return. Do not add a legal opinion of your own, write files or run shell commands."
    )


def _find(obj, keys: tuple[str, ...], depth: int = 3):
    """First non-empty string or number stored under one of `keys`, nearest level first."""
    level = [obj]
    for _ in range(depth):
        nxt = []
        for node in level:
            if isinstance(node, dict):
                for k in keys:
                    v = node.get(k)
                    if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip():
                        return v
                nxt += [v for v in node.values() if isinstance(v, (dict, list))]
            elif isinstance(node, list):
                nxt += [v for v in node if isinstance(v, (dict, list))]
        level = nxt
    return None


SKIP_KINDS = frozenset({"text_delta", "reasoning_delta", "state_delta", "state_writing", "context_budget"})   # streaming and bookkeeping


def _brief(value, limit: int = 160) -> str:
    """Any value as one short line."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _row(id_: str, text: str, kind: str, ms=0, **more) -> dict:
    return {"src": "intern", "id": id_, "ms": int(ms or 0), "simulated": False, "text": _mask(text), "agent": "Intern", "kind": kind, **more}


def _known_event(kind: str, raw: dict) -> dict | None:
    """A trace row for the event types of Intern's own schema (`intern schema`, version 0.30). None: not one of them."""
    if kind == "run_start":
        tools = [str(t) for t in raw.get("tools") or []]
        return _row("intern.step", f"Intern run started: model {raw.get('model')}, {len(tools)} tools: {_brief(', '.join(tools), 420)}", kind, tools=tools)
    if kind == "turn_start":
        return _row("intern.step", f"Intern turn {raw.get('turn')}", kind)
    if kind == "message":
        m = raw.get("message") or {}
        if m.get("role") != "assistant":
            return None     # the system prompt, the task and tool results: tool results have their own event
        calls = [str(c.get("name")) for c in m.get("tool_calls") or []]
        text = str(m.get("text") or "").strip()
        if m.get("error"):
            return _row("intern.error", f"Intern System 2 (Qwen) error: {_brief(m['error'], 300)}", kind, m.get("duration_ms"))
        said = (": " + _brief(text, 260) if text else "") + (f" [calls {', '.join(calls)}]" if calls else "")
        ev = _row("intern.system2", "Intern System 2 (Qwen)" + (said or ": (no text)"), kind, m.get("duration_ms"))
        if text and not calls:
            ev["reply"] = _mask(text)[:REPLY_CHARS]
        return ev
    if kind == "decision":
        what = f"{raw.get('purpose')}: {raw.get('questions')} question(s)" + (f" on {_brief(raw['subject'], 60)}" if raw.get("subject") else "")
        tail = (" " + _brief(raw["answers"], 200) if raw.get("answers") else "") + (" (cached)" if raw.get("cached") else "") \
            + (f" FAILED: {_brief(raw.get('error'), 160)}" if not raw.get("ok", True) else "")
        return _row("intern.system1", f"Intern System 1 (Drex) {what}{tail}", kind, raw.get("duration_ms"))
    if kind == "tool_guard":
        odds = "".join(f", {label} {raw[key]:.2f}" for key, label in (("risk", "risk"), ("asked", "asked for")) if isinstance(raw.get(key), (int, float)))
        return _row("intern.system1", f"Intern guard ({raw.get('layer')}) on {raw.get('name')}: {_brief(raw.get('rule'), 80)}. {_brief(raw.get('reason'), 200)}{odds}",
                    kind, tool=str(raw.get("name")))
    if kind == "tool_call_start":
        return _row("intern.tool", f"Intern tool call {raw.get('name')} {_brief(raw.get('arguments'), 200)}", kind, tool=str(raw.get("name")))
    if kind == "tool_call_end":
        status = str(raw.get("status"))
        more = "" if status == "ok" else ": " + _brief(raw.get("content"), 260)
        return _row("intern.tool", f"Intern tool result {raw.get('name')}: {status}, {int(raw.get('duration_ms') or 0)} ms{more}", kind, raw.get("duration_ms"),
                    tool=str(raw.get("name")), status=status)
    if kind == "run_end":
        reason, s1 = str(raw.get("reason")), raw.get("system1") or {}
        cost = sum(float((raw.get(k) or {}).get("cost_usd") or 0) for k in ("usage",)) + float(s1.get("cost_usd") or 0)
        text = (f"Intern finished: {reason}" + (f" ({_brief(raw['detail'], 200)})" if raw.get("detail") else "") + f", {raw.get('turns')} turn(s), "
                f"System 1 asks {s1.get('asks', 0)} ({s1.get('failed', 0)} failed), ${cost:.4f}")
        ev = _row("intern.reply" if reason == "completed" else "intern.error", text, kind, raw.get("duration_ms"), reason=reason)
        if str(raw.get("final_text") or "").strip():
            ev["reply"] = _mask(str(raw["final_text"]).strip())[:REPLY_CHARS]
        return ev
    if kind in ("secret_unavailable", "hook_error"):
        return _row("intern.error", f"Intern {kind.replace('_', ' ')}: {_brief({k: v for k, v in raw.items() if k not in ('type', 'timestamp')}, 300)}", kind)
    if kind in ("nudge", "extension", "compaction", "state", "reduced", "indexing", "memory_mount", "memory_recall", "process", "process_check"):
        body = {k: v for k, v in raw.items() if k not in ("type", "timestamp", "text", "summary") and v not in (None, "", [], {})}
        return _row("intern.step", f"Intern {kind.replace('_', ' ')}: {_brief(body, 200)}", kind)
    return None


def trace_event(raw) -> dict | None:
    """One line of Intern's output as a trace row. `raw` is the parsed JSON object, or a plain text line.
    None for the lines the trace leaves out (streamed text pieces, bookkeeping)."""
    if not isinstance(raw, dict):
        return {"src": "intern", "id": "intern.output", "ms": 0, "simulated": False, "text": _mask(str(raw))[:400]}
    kind = str(raw.get("type") or raw.get("event") or raw.get("kind") or "event")
    if kind in SKIP_KINDS:
        return None
    known = _known_event(kind, raw)
    if known or kind == "message":
        return known
    low = kind.lower()      # an event type this code does not know: still a row, labelled by what its name suggests
    tool = _find(raw, ("tool", "tool_name")) or (_find(raw, ("name",)) if "tool" in low else None)
    text = _find(raw, TEXT_KEYS)
    if any(w in low for w in ("system1", "decision", "guard", "judge", "verdict")):
        id_, label = "intern.system1", "System 1 (Drex)"
    elif tool or "tool" in low:
        id_, label = "intern.tool", "tool call" if "result" not in low else "tool result"
    elif any(w in low for w in ("final", "reply", "answer", "done", "complete", "finish")):
        id_, label = "intern.reply", "reply"
    elif any(w in low for w in ("error", "refus", "fail", "unavailable")):
        id_, label = "intern.error", "stopped"
    elif any(w in low for w in ("assistant", "system2", "model", "message", "think")):
        id_, label = "intern.system2", "System 2 (Qwen)"
    else:
        id_, label = "intern.step", "step"
    parts = [f"Intern {label}", f"[{kind}]"] + ([str(tool)] if tool else []) + ([str(text).strip().replace("\n", " ")[:240]] if text else [])
    ev = _row(id_, " ".join(parts), kind, _find(raw, ("ms", "duration_ms", "latency_ms")))
    if tool:
        ev["tool"] = str(tool)
    if id_ in ("intern.reply", "intern.system2") and text:
        ev["reply"] = _mask(str(text))[:REPLY_CHARS]
    return ev


def _mcp_event(call: dict) -> dict:
    args = ", ".join(f"{k}={str(v)[:40]}" for k, v in (call.get("args") or {}).items())
    state = "" if call.get("ok", True) else " (refused: " + str(call.get("error", "error"))[:80] + ")"
    return {"src": "intern", "id": "mcp.call", "ms": int(call.get("ms", 0)), "simulated": False, "agent": "Intern",
            "tool": "jury." + str(call.get("tool")), "text": f"Intern called jury.{call.get('tool')}({args}){state}", "result": call.get("args") or {}}


class _Tail:
    """Whole new lines of a file that another process is appending to."""

    def __init__(self, path: Path):
        self.path, self.pos = path, 0

    def lines(self) -> list[str]:
        if not self.path.is_file():
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            data = fh.read()
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self.pos += end + 1
        return [ln for ln in data[: end + 1].decode("utf-8", "replace").splitlines() if ln.strip()]


def _new_session() -> Path:
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for n in range(100):
        path = config.RUNS_DIR / f"{stamp}-intern{'' if n == 0 else f'-{n}'}"
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise InternError("could not create a session folder")


def session_of(run_id: str) -> Path | None:
    """The Intern session folder that started this trial run, if one did."""
    link = config.RUNS_DIR / run_id / "session"
    if link.is_file():
        path = config.RUNS_DIR / link.read_text(encoding="utf-8").strip()
        return path if path.is_dir() else None
    return None


def stream_of(run_id: str) -> list[dict] | None:
    """The whole trace of a run that went through Intern (Intern's steps, tool calls and the trial's events)."""
    session = session_of(run_id)
    path = session / "stream.jsonl" if session else None
    if not path or not path.is_file():
        return None
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def run(question: str, document: str | Path | None = None, case_hint: str | None = None, role: str | None = None,
        on_event: Callable[[dict], None] | None = None, timeout: float = RUN_TIMEOUT) -> dict:
    """Run the request through Intern. `on_event` gets every trace row as it happens: Intern's own steps
    (`id` starts with "intern."), each jury tool it called ("mcp.call") and the trial's events.
    Returns {status, exit, reply, run_id, session, tools, stderr}. status is "ok" only when Intern exited
    cleanly and a trial ran; callers fall back to the engine otherwise."""
    exe = binary()
    if not exe:
        raise InternError("Intern is not installed: bash setup/2_intern.sh")
    need = [n for n in ("NACE_API_KEY", "DEEPINFRA_API_KEY") if not config.env_key(n)]
    if need and not os.environ.get("TAD_INTERN_BIN"):
        raise InternError("missing " + ", ".join(need) + ": python3 tools/set_secret.py --missing")
    nace_base = drex.live_base() if drex.has_key() else config.NACE_BASE
    session = _new_session()
    env = {**os.environ, "INTERN_HOME": str(write_home(nace_base)), "NACE_BASE_URL": nace_base, "TAD_SESSION_DIR": str(session)}
    for name in KEY_NAMES:
        env[name] = config.env_key(name) or ""      # every name the config refers to exists, even when empty
    env["NACE_AUTHORIZATION"] = "Bearer " + env["NACE_API_KEY"]
    task = task_text(question, document, case_hint, role)
    cmd = [exe, "-p", "--json", "--no-context-files", "--system1", SYSTEM1, "--system2", SYSTEM2, "--", task]
    t0, n, reply, final, tools, run_id, status = time.monotonic(), 0, "", False, [], None, "ok"
    ended: str | None = None                         # the reason in Intern's own run_end event
    err_tail: collections.deque = collections.deque(maxlen=12)
    stream = open(session / "stream.jsonl", "x", encoding="utf-8")
    raw_log = open(session / "intern.jsonl", "x", encoding="utf-8")

    def emit(ev: dict) -> None:
        nonlocal n
        ev = {**ev, "i": n, "t": int((time.monotonic() - t0) * 1000)}
        n += 1
        stream.write(json.dumps(ev) + "\n")
        stream.flush()
        if on_event:
            on_event(ev)

    emit({"src": "intern", "id": "intern.start", "ms": 0, "simulated": False, "agent": "Intern",
          "text": f"Intern started: System 1 {SYSTEM1} (Drex), System 2 {SYSTEM2}; tools: jury engine, Nace MCP, Keenable"})
    calls, trial_tail, pointer = _Tail(session / "mcp_calls.jsonl"), None, session / "trial_run_id"

    def drain_files() -> None:
        nonlocal trial_tail, run_id
        for ln in calls.lines():
            call = json.loads(ln)
            tools.append("jury." + str(call.get("tool")))
            emit(_mcp_event(call))
        if trial_tail is None and pointer.is_file():
            run_id = pointer.read_text(encoding="utf-8").strip() or None
            if run_id:
                trial_tail = _Tail(config.RUNS_DIR / run_id / "events.jsonl")
        if trial_tail:
            for ln in trial_tail.lines():
                emit({**json.loads(ln), "harness": "intern", "run_id": run_id})

    def attempt() -> int:
        """Start Intern once and relay everything it prints and everything the trial writes. Returns its exit code."""
        nonlocal reply, final, status, ended
        proc = subprocess.Popen(cmd, cwd=session, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1, errors="replace")
        lines: queue.Queue = queue.Queue()

        def pump(pipe, tag: str) -> None:
            for line in pipe:
                lines.put((tag, line.rstrip("\n")))
            lines.put((tag, None))

        for pipe, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
            threading.Thread(target=pump, args=(pipe, tag), daemon=True).start()
        open_pipes = 2
        while open_pipes:
            try:
                tag, line = lines.get(timeout=0.15)
            except queue.Empty:
                tag, line = None, None
            if tag and line is None:
                open_pipes -= 1
            elif tag == "err":
                err_tail.append(_mask(line))
            elif tag == "out" and line.strip():
                raw_log.write(_mask(line) + "\n")
                raw_log.flush()
                try:
                    ev = trace_event(json.loads(line))
                except ValueError:
                    ev = trace_event(line)
                if ev:
                    ended = ev.get("reason") or ended
                    if ev.get("reply") and (ev["id"] == "intern.reply" or not final):   # the run's final text wins over System 2 text
                        reply, final = ev["reply"], final or ev["id"] == "intern.reply"
                    emit(ev)
            drain_files()
            if time.monotonic() - t0 > timeout:
                proc.kill()
                status = "timeout"
                break
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = -9
        time.sleep(0.3)   # the jury server writes its last lines as Intern closes it
        drain_files()
        return code

    code = attempt()
    if status == "ok" and code != 0 and not run_id and not tools:
        # Stopped before any tool call: most often one MCP entry it could not set up. Try once with the jury server alone.
        why = (err_tail[-1] if err_tail else "no message")[:200]
        emit({"src": "intern", "id": "intern.retry", "ms": 0, "simulated": False, "agent": "Intern",
              "text": f"Intern stopped at startup (exit {code}: {why}). Trying again with the jury server as its only tool."})
        write_home(nace_base, only_jury=True)
        reply, final = "", False
        code = attempt()
    if status == "ok" and (code != 0 or not run_id):
        status = "failed"
    if not reply:   # no reply event was recognised: fall back to the last plain lines Intern printed
        plain = [ln for ln in (session / "intern.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("{")]
        reply = "\n".join(plain[-12:])[:REPLY_CHARS]
    result = {"status": status, "exit": code, "reason": ended, "reply": reply.strip(), "run_id": run_id, "session": session.name, "tools": tools,
              "stderr": " | ".join(err_tail)[-600:], "seconds": round(time.monotonic() - t0, 1)}
    emit({"src": "intern", "id": "intern.end", "ms": 0, "simulated": False, "agent": "Intern",
          "text": f"Intern finished: {status}, exit {code}, {len(tools)} jury tool call(s), {result['seconds']} s"})
    stream.close()
    raw_log.close()
    with open(session / "result.json", "x", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if args[0] == "--check":
        need = missing()
        print("Intern can run here." if not need else "Intern still needs: " + "; ".join(need))
        return 0 if not need else 1
    document = case = None
    rest = []
    it = iter(args)
    for a in it:
        if a == "--document":
            document = str(Path(next(it)).resolve())
        elif a == "--case":
            case = next(it)
        else:
            rest.append(a)

    def show(ev: dict) -> None:
        if ev["id"].startswith(("intern.", "mcp.")) or ev["id"] in ("role", "verdict", "next_steps", "web_check", "extract"):
            print(f"[{ev['t'] / 1000:6.1f}s] {ev.get('text') or ev['id']}"[:220], file=sys.stderr)

    try:
        res = run(" ".join(rest), document, case, on_event=show)
    except (InternError, drex.DrexError) as e:
        print(e, file=sys.stderr)
        return 1
    print(res["reply"] or "(Intern printed no reply)")
    print(f"\nstatus {res['status']} · trial run {res['run_id']} · session runs/{res['session']} · trace: python3 -m engine.server, then "
          f"http://127.0.0.1:{config.SERVER_PORT}/03-trace-ledger.html?engine=1", file=sys.stderr)
    if res["status"] != "ok" and res["stderr"]:
        print("Intern said: " + res["stderr"], file=sys.stderr)
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
