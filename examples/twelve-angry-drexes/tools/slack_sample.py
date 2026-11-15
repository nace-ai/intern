#!/usr/bin/env python3
"""Post one thread to Slack through the bridge and check every link in it.

    python3 tools/slack_sample.py [CASE]             # live: Drex decides, through Intern when it is ready (needs NACE_API_KEY)
    python3 tools/slack_sample.py [CASE] --simulate  # no model call; the summary's last lines say the run was simulated

Default case: tx-theft. Needs SLACK_BOT_TOKEN and SLACK_CHANNEL_ID in .env. The thread is the bridge's own: the legal
team's question, the acknowledgment, then the summary with the what-if buttons. Every hyperlink is checked before the
summary is posted (a dead one is posted as plain text), then read back from Slack and checked again. No token is
printed. Exit code 1 when the summary was not posted or a posted link is dead. A click on a button is answered only
while the bridge is running (.venv/bin/python -m engine.slack_bridge) and Interactivity is on in the Slack app.
"""
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import cases, config, linkcheck, net, slack_bridge  # noqa: E402
from tools.smoke_e2e import HttpSlack  # noqa: E402

BARE_RE = slack_bridge.re.compile(r"<(https?://[^|>\s]+)>")   # a Slack link without a label


def thread_links(token: str, channel: str, ts: str) -> list[str]:
    """Every address in the thread as Slack stores it: message texts and blocks."""
    query = urllib.parse.urlencode({"channel": channel, "ts": ts, "limit": "50"})
    _, data, _ = net.json_call("GET", "https://slack.com/api/conversations.replies?" + query, headers={"Authorization": "Bearer " + token}, tries=1)
    if data.get("ok") is not True:
        print(f"Slack did not return the thread: {data.get('error', 'not ok')}")
        return []
    found: list[str] = []

    def walk(x) -> None:
        if isinstance(x, str):
            found.extend(m.group(1).replace("&amp;", "&") for rx in (slack_bridge.LINK_RE, BARE_RE) for m in rx.finditer(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for m in data.get("messages", []):
        walk([m.get("text", ""), m.get("blocks", [])])
    return list(dict.fromkeys(found))


def main() -> int:
    args = sys.argv[1:]
    simulate = "--simulate" in args
    case_id = next((a for a in args if not a.startswith("-")), "tx-theft")
    token, channel = config.env_key("SLACK_BOT_TOKEN"), config.env_key("SLACK_CHANNEL_ID")
    if not token or not channel:
        print("SLACK_BOT_TOKEN or SLACK_CHANNEL_ID is empty: bash setup/3_slack.sh")
        return 1
    if not simulate and not config.env_key("NACE_API_KEY"):
        print("NACE_API_KEY is empty: python3 tools/set_secret.py NACE_API_KEY (or add --simulate)")
        return 1
    token, channel, case = str(token), str(channel), cases.get_case(case_id)
    client = HttpSlack(token)
    root = client.chat_postMessage(channel, text=case["ask"])
    if simulate:
        text, blocks = slack_bridge.sample(case_id)
        client.chat_postMessage(channel, thread_ts=root["ts"], text=text, blocks=blocks)
    else:
        slack_bridge.handle({"ts": root["ts"], "channel": channel, "text": case["ask"], "user": "legal-team"}, client, token, set())
    linkcheck.forget()
    rows = linkcheck.check_many(thread_links(token, channel, root["ts"]))
    for r in rows:
        print(f"  {r['state']:9} {r['status'] or '-'}  {r['url']}" + (f"  ({r['why']})" if r["why"] else ""))
    how = "simulated" if simulate else "live, through " + slack_bridge.harness()
    print(f"posted {client.posts} messages in one thread ({how}); summary with buttons accepted by Slack: {client.block_posts >= 1}")
    print("links read back from Slack: " + linkcheck.tally(rows))
    return 0 if client.block_posts >= 1 and not any(r["state"] == "dead" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
