#!/usr/bin/env python3
"""Live end-to-end smoke test: every service the demo uses, then one real request through the whole path.

    python3 tools/smoke_e2e.py              # service checks + one live request (through Intern when ready, else the engine)
    python3 tools/smoke_e2e.py --slack      # also post a real thread in SLACK_CHANNEL_ID (one more live trial)
    python3 tools/smoke_e2e.py --no-run     # service checks only (well under one cent)
    python3 tools/smoke_e2e.py --case ID    # another case (default tx-theft: a 2-page file)
    python3 tools/smoke_e2e.py --only deepinfra   # one stage (or several, comma separated): host, drex, mcp, keenable,
                                                  # deepinfra, pages, request, ledger, slack, slack-thread

One pass costs about $0.035: Drex under $0.01, NDI $0.024 for 2 pages, a few DeepInfra tokens. --slack doubles it.
Each stage prints PASS, FAIL or SKIP with its time and, after a failure, what to do. No key is ever printed.
A record of the pass is written to runs/smoke-<stamp>.json.
"""
import collections
import json
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import cases, config, drex, intern_runner, keenable, net, server, slack_bridge, trial  # noqa: E402

results: list[dict] = []
ONLY: set[str] = set()
QUESTION = {"wants_refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"}}
STATE = "A customer writes: the kettle arrived cracked, please send my money back."


def stage(key: str, name: str, fn, needs: str = "") -> bool:
    """Run one stage. `fn` returns a note, or raises; `needs` (non-empty) skips it with that reason."""
    if ONLY and key not in ONLY:
        return False
    if needs:
        results.append({"stage": name, "state": "SKIP", "note": needs, "seconds": 0})
        print(f"SKIP  {name}: {needs}", flush=True)
        return False
    t0 = time.monotonic()
    try:
        note, state = fn(), "PASS"
    except Exception as e:  # noqa: BLE001 - a smoke test reports every failure and goes on
        note, state = f"{type(e).__name__}: {str(e)[:300]}", "FAIL"
    took = round(time.monotonic() - t0, 1)
    results.append({"stage": name, "state": state, "note": note, "seconds": took})
    print(f"{state}  {name} ({took} s): {note}", flush=True)
    return state == "PASS"


def check(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def drex_decision() -> str:
    resp, ms = drex.post({"model": config.MODEL_TEXT, "state": STATE, "questions": QUESTION})
    p = resp["answers"]["wants_refund"]["noul"]
    check(p > 0.5, f"unexpected answer {p}")
    return f"{config.MODEL_TEXT} answered p={p:.2f} in {ms} ms on {config.NACE_BASE}"


def nace_mcp() -> str:
    out = subprocess.run([sys.executable, "tools/mcp_call.py", "nace", "call", "decide", json.dumps({"state": STATE, "questions": QUESTION})],
                         cwd=ROOT, capture_output=True, text=True, timeout=180)
    check(out.returncode == 0 and "wants_refund" in out.stdout, (out.stderr or out.stdout).strip()[-300:])
    return "uvx nace-mcp answered a `decide` call"


def keenable_search() -> str:
    hits, ms = keenable.search("Texas courts self-help theft", 3)
    check(bool(hits), "no results")
    return f"{len(hits)} results in {ms} ms ({'key' if config.env_key('KEENABLE_API_KEY') else 'keyless'} endpoint)"


def deepinfra() -> str:
    body = {"model": config.SPEECH_MODEL, "max_tokens": 64, "reasoning_effort": config.SPEECH_REASONING,
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}]}
    try:
        _, data, ms = net.json_call("POST", config.DEEPINFRA_URL, body, {"Authorization": "Bearer " + str(config.env_key("DEEPINFRA_API_KEY"))}, tries=1)
    except net.NetError as e:
        hint = {401: "the key was rejected: bash setup/1_deepinfra.sh", 403: "the key was rejected: bash setup/1_deepinfra.sh",
                404: f"the model name {config.SPEECH_MODEL} was not found on DeepInfra", 402: "the DeepInfra account has no balance"}.get(e.status or 0, "")
        raise AssertionError(f"HTTP {e.status}: {str(e)[:160]}" + (f" ({hint})" if hint else "")) from e
    check("choices" in data and bool(data["choices"]), f"unexpected answer: {str(data)[:200]}")
    message = data["choices"][0].get("message") or {}
    text = (message.get("content") or "").strip()
    check(bool(text), f"no text came back (finish {data['choices'][0].get('finish_reason')}, reasoning {len(message.get('reasoning_content') or '')} characters)")
    return f"{config.SPEECH_MODEL} replied {text[:40]!r} in {ms} ms"


def summary_of(run_id: str) -> dict:
    return json.loads((config.RUNS_DIR / run_id / "summary.json").read_text(encoding="utf-8"))


def describe(s: dict) -> str:
    t = s["totals"]
    check(s["status"] == "complete" and not s["failures"], f"run {s['run_id']} status {s['status']}, failures {s['failures']}")
    check(t["decisions"] >= 150, f"only {t['decisions']} decisions")
    check(not s["simulated"], "the run was simulated: no live Drex call")
    return (f"vote {s['vote']} ({s['verdict']['label']}), {t['decisions']} decisions, {t['requests']} requests, Drex ${t['usd_billed']:.4f}, "
            f"documents ${t['ndi_usd']:.3f}, {t['flagged']} flagged, next step: {s['next_steps']['step']} ({s['next_steps']['p_step']:.0%}); run {s['run_id']}")


def through_intern(case: dict) -> str:
    kinds: collections.Counter = collections.Counter()
    res = intern_runner.run(case["ask"], config.CASE_FILES_DIR / case["upload"]["name"], case["id"],
                            on_event=lambda e: kinds.update([e.get("kind") or e["id"]]) if e["src"] == "intern" else None)
    seen = ", ".join(f"{k} x{n}" for k, n in kinds.most_common(12))
    check(res["status"] == "ok", f"Intern {res['status']} (exit {res['exit']}); it said: {res['stderr'][-300:] or 'nothing'}; its events: {seen}")
    check(bool(res["reply"]), f"no reply recognised; Intern's events: {seen}; see runs/{res['session']}/intern.jsonl")
    through_intern.run_id = res["run_id"]
    return (f"{res['seconds']} s, tools {sorted(set(res['tools']))}; {describe(summary_of(res['run_id']))}; Intern's events: {seen}; "
            f"reply: {res['reply'][:160]!r}")


def engine_trial(case: dict) -> str:
    res = trial.run_trial(case["id"], intake={"who": "smoke test", "channel": "terminal"})
    engine_trial.run_id = res["run_id"]
    return describe(res["summary"])


def ledger(case_id: str, want_intern: bool) -> str:
    rec = server.run_record(case_id)
    check(bool(rec) and len(rec["events"]) > 150, "no finished run to show")
    check(not want_intern or rec["harness"] == "intern", f"latest run was started by {rec['harness']}, not Intern")
    return f"run {rec['run_id']}: {len(rec['events'])} rows, started by {rec['harness']}"


def pages() -> str:
    httpd = ThreadingHTTPServer((config.SERVER_HOST, 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://{config.SERVER_HOST}:{httpd.server_address[1]}"
    try:
        health = json.loads(urllib.request.urlopen(base + "/api/health", timeout=10).read())
        for page in ("01-slack-intake.html", "02-live-courtroom.html", "03-trace-ledger.html"):
            check(urllib.request.urlopen(f"{base}/{page}", timeout=10).status == 200, page)
    finally:
        httpd.shutdown()
    return f"3 pages served; health: live Drex {not health['simulated']}, Intern ready {health['intern']}"


class HttpSlack:
    """The one Slack call the bridge makes, over plain HTTPS (no slack_bolt needed for the outbound side)."""

    def __init__(self, token: str):
        self.token, self.posts, self.block_posts = token, 0, 0

    def call(self, method: str, body: dict) -> dict:
        _, data, _ = net.json_call("POST", "https://slack.com/api/" + method, body, {"Authorization": "Bearer " + self.token}, tries=1)
        check(data.get("ok") is True, f"Slack {method}: {data.get('error', 'not ok')}")
        return data

    def chat_postMessage(self, channel: str, thread_ts: str | None = None, text: str = "", blocks: list | None = None) -> dict:
        out = self.call("chat.postMessage", {"channel": channel, "text": text, **({"thread_ts": thread_ts} if thread_ts else {}),
                                             **({"blocks": blocks} if blocks else {})})
        self.posts += 1                      # counted only when Slack accepted the message
        self.block_posts += bool(blocks)
        return out


def slack_tokens() -> str:
    who = HttpSlack(str(config.env_key("SLACK_BOT_TOKEN"))).call("auth.test", {})
    HttpSlack(str(config.env_key("SLACK_APP_TOKEN"))).call("apps.connections.open", {})
    return f"bot token accepted (bot user {who.get('user', '?')}); app token opens a Socket Mode connection"


def slack_thread(case: dict) -> str:
    token, channel = str(config.env_key("SLACK_BOT_TOKEN")), str(config.env_key("SLACK_CHANNEL_ID"))
    client = HttpSlack(token)
    root = client.chat_postMessage(channel, text=f"Smoke test of the bridge. A legal team asks: {case['ask']}")
    slack_bridge.handle({"ts": root["ts"], "channel": channel, "text": case["ask"], "user": "smoke-test"}, client, token, set())
    check(client.posts >= 3, f"only {client.posts - 1} replies posted in the thread")
    check(client.block_posts >= 1, "Slack did not accept the summary's blocks (the text fallback was posted)")
    return f"{client.posts - 1} replies posted under one message in the channel (summary with what-if buttons), through {slack_bridge.harness()}"


def main() -> int:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    if "--only" in args:
        ONLY.update(args[args.index("--only") + 1].split(","))
    case = cases.get_case(args[args.index("--case") + 1] if "--case" in args else "tx-theft")
    have = {n: bool(config.env_key(n)) for n in ("NACE_API_KEY", "DEEPINFRA_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL_ID")}
    print("keys set: " + ", ".join(n for n, v in have.items() if v) + " | empty: " + (", ".join(n for n, v in have.items() if not v) or "none"), flush=True)
    no_nace = "" if have["NACE_API_KEY"] else "NACE_API_KEY is empty: python3 tools/set_secret.py NACE_API_KEY"
    stage("host", "Nace API host", lambda: drex.live_base(), no_nace)
    stage("drex", "Drex decision", drex_decision, no_nace)
    stage("mcp", "Nace MCP server", nace_mcp, no_nace)
    stage("keenable", "Keenable search", keenable_search)
    stage("deepinfra", "DeepInfra (System 2, speeches)", deepinfra, "" if have["DEEPINFRA_API_KEY"] else "DEEPINFRA_API_KEY is empty: bash setup/1_deepinfra.sh")
    stage("pages", "Pages and API", pages)
    ran_intern = False
    if "--no-run" not in args and (not ONLY or ONLY & {"request", "ledger"}):
        need = intern_runner.missing()
        ran_intern = stage("request", f"Request through Intern ({case['id']})", lambda: through_intern(case), no_nace or ("not ready: " + "; ".join(need) if need else ""))
        if not ran_intern:
            stage("request", f"Request on the jury engine ({case['id']})", lambda: engine_trial(case), no_nace)
        stage("ledger", "Trace ledger record", lambda: ledger(case["id"], ran_intern), no_nace)
    slack_ready = have["SLACK_BOT_TOKEN"] and have["SLACK_APP_TOKEN"]
    stage("slack", "Slack tokens", slack_tokens, "" if slack_ready else "Slack tokens are empty: bash setup/3_slack.sh")
    if "--slack" in args:
        stage("slack-thread", "Slack thread, posted for real", lambda: slack_thread(case),
              "" if slack_ready and have["SLACK_CHANNEL_ID"] else "needs both Slack tokens and SLACK_CHANNEL_ID (a channel the bot is in)")
    failed = [r for r in results if r["state"] == "FAIL"]
    skipped = [r for r in results if r["state"] == "SKIP"]
    print(f"\n{len(results) - len(failed) - len(skipped)} passed, {len(failed)} failed, {len(skipped)} skipped"
          + ("" if not failed else ". Failed: " + "; ".join(r["stage"] for r in failed)))
    if not ONLY:   # a full pass leaves a record; a single-stage check does not
        config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        record = config.RUNS_DIR / f"smoke-{time.strftime('%Y%m%d-%H%M%S')}.json"
        with open(record, "x", encoding="utf-8") as fh:
            json.dump({"case": case["id"], "results": results}, fh, indent=2)
        print(f"record: {record.relative_to(ROOT)}")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
