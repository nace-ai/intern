#!/usr/bin/env python3
"""Post one sample thread to Slack: the summary of a simulated trial with its what-if buttons, then the reply one click gives.

    python3 tools/slack_sample.py [CASE]      # default tx-theft; needs SLACK_BOT_TOKEN and SLACK_CHANNEL_ID in .env

No model call is made (the trial is simulated) and no token is printed. A click on a button is answered only while the
bridge is running (.venv/bin/python -m engine.slack_bridge) and Interactivity is on in the Slack app.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import cases, config, slack_bridge  # noqa: E402
from tools.smoke_e2e import HttpSlack  # noqa: E402


def main() -> int:
    case_id = sys.argv[1] if len(sys.argv) > 1 else "tx-theft"
    token, channel = config.env_key("SLACK_BOT_TOKEN"), config.env_key("SLACK_CHANNEL_ID")
    if not token or not channel:
        print("SLACK_BOT_TOKEN or SLACK_CHANNEL_ID is empty: bash setup/3_slack.sh")
        return 1
    text, blocks = slack_bridge.sample(case_id)
    buttons = [b["accessory"] for b in blocks if b.get("accessory")]
    client = HttpSlack(str(token))
    root = client.chat_postMessage(str(channel), text=f"Sample thread (simulated trial, no model call). A legal team asks: {cases.get_case(case_id)['ask']}")
    client.chat_postMessage(str(channel), thread_ts=root["ts"], text=text, blocks=blocks)
    if buttons:
        value = slack_bridge._parse_value(buttons[0]["value"])
        client.chat_postMessage(str(channel), thread_ts=root["ts"],
                                text="Sample of the reply a click on the first button posts:\n" + slack_bridge.what_if_reply(value))
    print(f"posted {client.posts} messages in one thread; {len(buttons)} buttons; Slack accepted the blocks: {client.block_posts == 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
