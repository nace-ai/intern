"""Slack Socket Mode bridge. On an app mention, or a message in SLACK_CHANNEL_ID, it downloads the attached file,
hands the request to Intern (Drex as System 1, Qwen as System 2, the jury engine as its tool) and posts two replies in
the thread: a short acknowledgment at once, then one summary when the run ends (result, Intern's answer, the file,
the law, how the vote moved, what to do next), with text bar graphs. When Intern or its keys are missing, or Intern stops,
the jury engine runs the trial directly and the summary says so. TAD_HARNESS=engine skips Intern.

The summary also carries one "What if" button per fact that could swing the vote. A click posts, in the same thread, the
jury's re-vote with that fact changed, built from the button's own value (the re-vote ran during the trial; no new model
call). The buttons need Interactivity turned on in the Slack app (Socket Mode needs no request URL).

Needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN (bash tools/setup_demo.sh --slack) and slack_bolt
(.venv/bin/python -m engine.slack_bridge); without them `python3 -m engine.slack_bridge` prints the setup steps.
Tokens are never printed. Posted live on 2026-11-14: one thread through Intern, and one sample summary with the what-if buttons
(tools/slack_sample.py). The listener for incoming messages and button clicks have not been run live.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

from . import cases, config, intern_runner, lawwiki, net, trial

SETUP = """Slack bridge setup
1. api.slack.com/apps -> Create New App, then Settings -> Socket Mode -> enable, create an app-level token with scope connections:write.
2. OAuth & Permissions -> bot scopes: app_mentions:read, chat:write, files:read, channels:history. Install to the workspace.
3. Event Subscriptions -> enable, bot events: app_mention, message.channels.
4. Interactivity & Shortcuts -> turn on (Socket Mode needs no request URL). The what-if buttons need it.
5. export SLACK_BOT_TOKEN=xoxb-...  SLACK_APP_TOKEN=xapp-...  [SLACK_CHANNEL_ID=C... to react to every message there]
6. pip install -r requirements-slack.txt, then python3 -m engine.slack_bridge
Missing: """


def _download(f: dict, token: str, dest: Path) -> Path | None:
    """Fetch one attached file with the bot token (Slack hosts only, at most 20 MB)."""
    url = f.get("url_private_download", "")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(".slack.com") or f.get("size", 0) > 20_000_000:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / re.sub(r"[^\w.\-]", "_", f.get("name", "upload"))[-80:]
    _, body = net.send("GET", url, headers={"Authorization": "Bearer " + token})
    with open(path, "xb") as fh:  # never overwrite an earlier upload
        fh.write(body)
    return path


def _links(keys: list[str]) -> str:
    out = []
    for key in keys:
        rec = lawwiki.statute(key) or {}
        out.append(f"<{lawwiki.link(key)}|{rec.get('citation', key)}>")
    return ", ".join(out)


def harness() -> str:
    """ "intern" when Intern and its keys are on this machine and TAD_HARNESS does not say "engine"; else "engine"."""
    return "intern" if (config.env_key("TAD_HARNESS") or "").lower() != "engine" and intern_runner.ready() else "engine"


def _case_of(e: dict, default: str) -> str:
    """The case a trial event belongs to: read from its run id when the harness chose the case."""
    m = re.match(r"^\d{8}-\d{6}-(.+?)-(?:live|sim)(?:-\d+)?$", e.get("run_id") or "")
    return m.group(1) if m else default


LIMIT = 3500   # characters in the summary message


def _esc(text) -> str:
    """Slack mrkdwn escapes for text that came from a file or a model."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _bar(frac: float, width: int = 12) -> str:
    """A text bar graph: the share `frac` (0 to 1) of `width` cells filled."""
    k = max(0, min(width, round(frac * width)))
    return "\u2588" * k + "\u2591" * (width - k)


def _spark(ps: list[float]) -> str:
    """One block character per probability (0 to 1): the shape of a vote, juror by juror."""
    return "".join("\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"[max(0, min(7, int(p * 8)))] for p in ps)


JURORS = [f"J{i:02d}" for i in range(1, 13)]


def _new_state() -> dict:
    return {"facts": [], "fact_src": "", "role": None, "law": [], "web": None, "tally": {}, "votes": [], "word": "liable",
            "result": None, "page": "", "what_if": [], "next": None, "tools": [], "decisions": 0, "usd": 0.0, "ms": 0,
            "simulated": False, "answer": "", "notes": [], "case": "", "base": None, "final": {}, "revote": {}, "what_if_rows": []}


def _collect(st: dict, e: dict, case_id: str) -> None:
    """Keep what one trial event would have said; nothing is posted until the run ends."""
    if e.get("src") == "drex":
        st["decisions"] += 1
        st["usd"] += (e.get("usd") or 0) + (e.get("usd2") or 0)
    st["ms"] = max(st["ms"], e.get("t") or 0)
    if e["id"] == "extract" and e.get("fields"):
        st["fact_src"] = "" if not e["simulated"] else " _(stored fields, NDI not called)_"
        st["facts"] = [f"{_esc(n)}: {_esc(v)[:200]}" + (f" (p. {pg})" if pg else "") for n, v, pg, _ in e["fields"]]
    elif e["id"] == "role":
        r = e["result"]
        st["word"], st["role"] = r["finding"], (
            f"{_esc(r['label'])} ({_esc(r['group'])}), picked by Drex at {r['detected']['p_group']:.2f} and {r['detected']['p_area']:.2f}. "
            f"Jurors decide: {_esc(r['finding'])}, {_esc(r['burden'])}. Statutes come from {r['scope_sections']:,} LawWiki sections "
            f"({_esc(', '.join(r['scope']))}).")
    elif e["id"] == "statute_applies" and e["p"] >= 0.5:
        st["law"].append(f"`{_bar(e['p'])}` {e['p']:.2f} \u00b7 {_links([e['cite']])}")
    elif e["id"] == "web_check":
        w = e["result"]
        st["web"] = (f"<{w['url']}|{_esc(w['title'][:80])}>, found by Keenable on a court or government site. "
                     + ("Drex checked it; used as background only." if w["used"] else "Drex's check failed it; left out."))
    elif e["id"] == "liable":
        st["tally"].setdefault(e["phase"], []).append(e["p"] >= 0.5)
        if len(st["tally"][e["phase"]]) == 12:
            n = sum(st["tally"][e["phase"]])
            name = cases.phases()[e["phase"]]
            phase = _esc(name[:1].lower() + name[1:])
            st["votes"].append(f"`{_bar(n / 12)}` {n} of 12 say {_esc(st['word'])} \u00b7 after {phase}")
        if e["phase"] == len(cases.phases()) - 1 and e.get("agent"):   # the final vote, juror by juror
            st["final"][e["agent"]] = e["p"]
    elif e["id"] == "whatif_liable" and e.get("agent") and e.get("fact"):   # one juror's re-vote with one fact flipped
        st["revote"].setdefault(e["fact"], {})[e["agent"]] = e["p"]
    elif e["id"] == "verdict":
        st["simulated"] = bool(e.get("simulated"))
        st["result"] = f"Result: {e['text']} Outcome: {e['result']['tier']}."
        st["case"] = _case_of(e, case_id)
        st["page"] = f"{config.public_url()}/{config.COURTROOM_PAGE}#case={st['case']}"
        v = e["result"]
        st["base"] = {"liable": v["liable"], "mean": v["mean"], "word": v.get("finding") or st["word"]}
    elif e["id"] == "what_if" and e.get("result"):
        st["what_if_rows"] = list(e["result"])
        st["what_if"] = [f"`{_bar(w['liable_if'] / 12)}` {w['liable_if']} of 12 ({w['delta']:+.2f}) \u00b7 "
                         f"if {'it were NOT true' if w.get('on_now') else 'it WERE true'}: {_esc(w['label'])}"
                         for w in e["result"][:3]]
    elif e["id"] == "next_steps":
        n = e["result"]
        st["next"] = (f"For the {_esc(n['for_side'].lower())}: {_esc(n['step'])} (Drex {n['p_step']:.0%})."
                      + (" A document the claim depends on looks missing." if n["proof_gap"] >= 0.5 else "")
                      + (" Low confidence: have a person confirm." if n["needs_person"] else ""))


def _clean_answer(reply: str) -> str:
    """Intern's reply without its closing disclaimer sentence (the summary carries it once)."""
    reply = reply.replace(trial.DISCLAIMER, "")
    reply = re.sub(r"(?is)\s*legal information[^.\n]*\.(\s*a simulation[^.\n]*\.?)?\s*$", "", reply)
    return _esc(reply.strip())


def _compose(st: dict, answer: str, cuts: set) -> tuple[str, dict]:
    """The summary text, with the lists named in `cuts` shortened; also the line count of each list."""
    blocks, sizes = [], {}

    def add(head: str, lines: list[str], listy: bool = False) -> None:
        if not lines:
            return
        if listy:
            sizes[head] = len(lines)
            if head in cuts:
                lines = lines[:3] + [f"• and {len(lines) - 3} more"]
        blocks.append("\n".join([head, *lines]))

    top = st["result"] and f"*{st['result']}*" + (f" <{st['page']}|Open the courtroom> (a replay with simulated votes)" if st["page"] else "")
    blocks.append(top or "*No verdict was produced.*")
    add("*Intern's answer*", [("> " + ln) for ln in answer.splitlines() if ln.strip()])
    add("*What the file says*" + st["fact_src"], [f"• {f}" for f in st["facts"]], True)
    add("*Legal team*", [st["role"]] if st["role"] else [])
    add("*Law that applies*", [f"• {x}" for x in st["law"]], True)
    add("*Public guide*", [st["web"]] if st["web"] else [])
    add("*How the vote moved*", [f"• {v}" for v in st["votes"]], True)
    add("*What moves the vote most*", [f"• {w}" for w in st["what_if"]], True)
    add("*Suggested next step*", [st["next"]] if st["next"] else [])
    ran = []
    if st["tools"]:
        ran.append("Intern called " + ", ".join(f"`{t}`" for t in st["tools"]) + ".")
    stats = ([f"{st['decisions']} Drex decision{'s' if st['decisions'] != 1 else ''}"] if st["decisions"] else []) \
        + (["simulated"] if st["simulated"] else []) + ([f"${st['usd']:.4f}"] if st["usd"] else []) \
        + ([f"{st['ms'] / 1000:.0f} s"] if st["ms"] and not st["simulated"] else [])
    if stats:
        ran.append(", ".join(stats) + ".")
    add("*How it ran*", [" ".join(ran + st["notes"])] if ran or st["notes"] else [])
    blocks.append(f"_{trial.DISCLAIMER}_")
    return "\n\n".join(blocks), sizes


def summary(st: dict) -> str:
    """The one Slack message that ends a run, under LIMIT characters: the longest lists shrink to three bullets first."""
    cuts, answer = set(), _clean_answer(st["answer"])
    while True:
        text, sizes = _compose(st, answer, cuts)
        if len(text) <= LIMIT:
            return text
        longer = [(n, h) for h, n in sizes.items() if n > 4 and h not in cuts]
        if longer:
            cuts.add(max(longer)[1])
        elif len(answer) > 200:
            answer = answer[:max(200, len(answer) - (len(text) - LIMIT) - 1)].rstrip() + "\u2026"
        else:
            return text[:LIMIT]


SECTION_MAX = 2900   # characters in one section block (Slack allows 3,000)
VALUE_MAX = 2000     # characters in one button value (Slack's limit)
BLOCKS_MAX = 50      # blocks in one message (Slack's limit)


def _chunks(text: str) -> list[str]:
    """The text cut at blank lines into pieces of at most SECTION_MAX characters; a longer paragraph is cut hard."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        for i in range(0, max(len(para), 1), SECTION_MAX):
            piece = para[i:i + SECTION_MAX]
            if cur and len(cur) + 2 + len(piece) > SECTION_MAX:
                out.append(cur)
                cur = ""
            cur = cur + "\n\n" + piece if cur else piece
    return [c for c in [*out, cur] if c.strip()]


def _button_value(st: dict, w: dict) -> str:
    """What one what-if button carries back to the bridge: the numbers its reply needs, as compact JSON."""
    base, final, rev = st["base"], st["final"], st["revote"].get(w["id"], {})
    full = all(isinstance(d.get(j), (int, float)) for d in (final, rev) for j in JURORS)
    label = str(w["label"])[:300]
    while True:
        value = json.dumps({
            "c": st["case"], "f": w["id"], "l": label, "on": bool(w.get("on_now")), "n": w["liable_if"], "m": round(w["mean_if"], 3),
            "d": round(w["delta"], 3), "n0": base["liable"], "m0": round(base["mean"], 3), "w": base["word"], "s": bool(st["simulated"]),
            "b": [round(final[j] * 100) for j in JURORS] if full else [],
            "a": [round(rev[j] * 100) for j in JURORS] if full else []}, separators=(",", ":"), ensure_ascii=False)
        if len(value) <= VALUE_MAX or not label:
            return value
        label = label[:len(label) // 2]


def blocks(st: dict, text: str) -> list[dict]:
    """Slack Block Kit for the summary: its text, one what-if button per changed fact, then the disclaimer as a footnote."""
    paras = text.split("\n\n")
    if paras[-1] == f"_{trial.DISCLAIMER}_":
        paras = paras[:-1]
    mrkdwn = lambda t: {"type": "mrkdwn", "text": t}  # noqa: E731
    tail: list[dict] = [{"type": "context", "elements": [mrkdwn(trial.DISCLAIMER)]}]
    rows = st["what_if_rows"][:5] if st["base"] else []
    if rows:
        ask = "*What if?* Pick one fact. The jury's vote with that fact changed is posted in this thread."
        buttons = []
        for i, w in enumerate(rows):
            on = bool(w.get("on_now"))
            label = {"type": "plain_text", "text": f"What if this {'were NOT' if on else 'WERE'} true?"}
            buttons.append({"type": "section", "text": mrkdwn(f"*{_esc(w['label'])}*\nIn the file: {'true' if on else 'not true'}"),
                            "accessory": {"type": "button", "action_id": f"whatif_{i}", "value": _button_value(st, w), "text": label}})
        tail = [{"type": "divider"}, {"type": "section", "text": mrkdwn(ask)}, *buttons, *tail]
    head = [{"type": "section", "text": mrkdwn(c)} for c in _chunks("\n\n".join(paras))]
    return head[:BLOCKS_MAX - len(tail)] + tail


def sample(case_id: str) -> tuple[str, list[dict]]:
    """The summary text and blocks of a simulated trial of `case_id` (no model call, nothing written): what a thread would show."""
    st = _new_state()
    trial.run_trial(case_id, simulate=True, write=False, on_event=lambda e: _collect(st, e, case_id))
    text = summary(st)
    return text, blocks(st, text)


def what_if_reply(v: dict) -> str:
    """The automated answer to one button click, built from the button's value alone (no new model call)."""
    label, word = _esc(v["l"][:1].lower() + v["l"][1:]), _esc(v["w"])
    lines = [f"*What if this were {'not true' if v['on'] else 'true'}: {label}?*",
             f"`{_bar(v['n'] / 12)}` {v['n']} of 12 jurors say {word} (as filed: {v['n0']} of 12)",
             f"`{_bar(v['m'])}` mean probability {v['m']:.2f} (as filed: {v['m0']:.2f}, {v['d']:+.2f})"]
    if v.get("b") and v.get("a"):
        lines += ["Juror by juror, J01 to J12:", f"`{_spark([x / 100 for x in v['b']])}` as filed",
                  f"`{_spark([x / 100 for x in v['a']])}` with this one fact changed"]
    how = "A simulated re-vote from the same run (no Drex call was made)" if v["s"] else "Drex decided this re-vote during the run: 12 decisions"
    lines.append(f"_{how}; nothing else in the file changed. {trial.DISCLAIMER}_")
    return "\n".join(lines)


def _count(x, hi: int) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= hi


def _num(x, lo: float, hi: float) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi


def _parse_value(raw) -> dict | None:
    """A button value after its round trip through Slack, checked as untrusted data: the dict, or None unless every field fits."""
    if not isinstance(raw, str) or len(raw) > VALUE_MAX:
        return None
    try:
        v = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    ok = isinstance(v, dict) and set(v) == {"c", "f", "l", "on", "n", "m", "d", "n0", "m0", "w", "s", "b", "a"} \
        and all(isinstance(v[k], str) and 0 < len(v[k]) <= n for k, n in (("c", 80), ("f", 40), ("l", 300), ("w", 40))) \
        and isinstance(v["on"], bool) and isinstance(v["s"], bool) and _count(v["n"], 12) and _count(v["n0"], 12) \
        and _num(v["m"], 0, 1) and _num(v["m0"], 0, 1) and _num(v["d"], -1, 1) \
        and all(isinstance(v[k], list) and len(v[k]) in (0, 12) and all(_count(x, 100) for x in v[k]) for k in ("b", "a"))
    return v if ok else None


def on_action(body: dict, client, seen: set) -> None:
    """Answer one what-if button click in the clicked message's thread; a second click on the same button posts nothing."""
    try:
        act, msg = body["actions"][0], body["message"]
        ch, ts = body["channel"]["id"], msg["ts"]
        key = (ts, act.get("action_id", ""))
        value = act.get("value")
    except (KeyError, IndexError, TypeError, AttributeError):
        return
    if key in seen:
        return
    seen.add(key)
    v = _parse_value(value)
    try:
        client.chat_postMessage(channel=ch, thread_ts=msg.get("thread_ts") or ts,
                                text=what_if_reply(v) if v else "That what-if could not be read. Post the question again to run the trial.")
    except Exception:
        seen.discard(key)   # the reply did not go out: let the click be tried again
        raise


def handle(event: dict, client, token: str, seen: set) -> None:
    """Run one request end to end: acknowledge in the message's thread at once, post one summary when the run ends."""
    if event.get("bot_id") or event.get("subtype") not in (None, "file_share") or event["ts"] in seen:
        return
    seen.add(event["ts"])
    ch, thread = event["channel"], event.get("thread_ts") or event["ts"]
    post = lambda text: client.chat_postMessage(channel=ch, thread_ts=thread, text=text)  # noqa: E731

    def post_summary(st: dict) -> None:   # the text stays as the fallback; a rejected block payload never loses the summary
        text = summary(st)
        try:
            client.chat_postMessage(channel=ch, thread_ts=thread, text=text, blocks=blocks(st, text))
        except Exception:
            post(text)
    files = event.get("files") or []
    case_id = cases.match(event.get("text", ""), files[0].get("name", "") if files else "")
    use = harness()
    post(f"Received. Reading {'the file' if files else 'the question'} and seating twelve jurors for {cases.get_case(case_id)['title']}."
         + (" Intern is running this: Drex is its System 1, Qwen its System 2, the jury engine its tool." if use == "intern" else "")
         + " One summary follows in about 40 seconds.")
    st = _new_state()

    def on_event(e: dict) -> None:
        _collect(st, e, case_id)

    def relay(e: dict) -> None:   # Intern's stream: its own steps, the jury tools it called, the trial's events
        if e["id"] == "mcp.call":
            if e["tool"] not in st["tools"]:
                st["tools"].append(e["tool"])
        elif not e["id"].startswith("intern."):
            on_event(e)

    try:
        path = _download(files[0], token, config.RUNS_DIR / "slack-uploads" / event["ts"]) if files else None
        if use == "intern":
            try:
                res = intern_runner.run(event.get("text", ""), path, case_id, on_event=relay)
            except Exception as e:  # Intern could not start: the engine still answers
                res = {"reply": "", "run_id": None, "status": type(e).__name__}
            if res["run_id"]:   # the trial ran and its stages were collected
                st["answer"] = res["reply"] or ""
                if res["status"] != "ok":
                    st["notes"].append(f"Intern stopped early ({res['status']}); the trial result is complete.")
                post_summary(st)
                return
            st["notes"].append(f"Intern stopped before the trial ({res['status']}). The jury engine ran it directly.")
        trial.run_trial(case_id, on_event=on_event, document=path, intake={"who": event.get("user", "someone"), "channel": f"<#{ch}>"})
        post_summary(st)
    except Exception as e:  # report the class only; messages can carry URLs
        post(f"The run stopped: {type(e).__name__}. No result was produced.")


def main() -> int:
    bot, app_tok = config.env_key("SLACK_BOT_TOKEN"), config.env_key("SLACK_APP_TOKEN")
    missing = [n for n, v in (("SLACK_BOT_TOKEN", bot), ("SLACK_APP_TOKEN", app_tok)) if not v]
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        missing.append("slack_bolt (not installed)")
    if missing:
        print(SETUP + ", ".join(missing))
        return 0
    app, seen, channel = App(token=bot), set(), config.env_key("SLACK_CHANNEL_ID")
    app.event("app_mention")(lambda event, client: handle(event, client, bot, seen))
    app.event("message")(lambda event, client: channel and event.get("channel") == channel and handle(event, client, bot, seen))
    clicked = set()

    def on_click(ack, body, client) -> None:   # a what-if button: acknowledge within 3 s, then reply in the thread
        ack()
        on_action(body, client, clicked)

    app.action(re.compile(r"^whatif_\d+$"))(on_click)
    print("slack bridge connected (Socket Mode); requests go through "
          + ("Intern" if harness() == "intern" else "the jury engine directly (Intern needs: " + ("; ".join(intern_runner.missing()) or "TAD_HARNESS unset") + ")"))
    SocketModeHandler(app, app_tok).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
