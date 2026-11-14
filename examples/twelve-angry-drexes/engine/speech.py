"""Two-sentence courtroom speeches. The language model decides nothing.

Which argument counsel makes is chosen by Drex (`next_argument`) and passed in as `argument`.
Source order: Intern (only if TAD_USE_INTERN=1 and `intern` is on PATH), then DeepInfra chat
completions (if DEEPINFRA_API_KEY is set), then the canned speech from the case.
A model speech is checked in code (at most two sentences, no number that is not in the case brief);
anything that fails falls back to the canned text.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import config, net

SYSTEM = ("You write one short courtroom speech for a mock trial. Exactly two sentences, plain English, "
          "under 60 words. Use only the facts you are given and add no numbers, dates or citations of your own. "
          "Do not decide anything. Output the speech only.")
NUM_RE = re.compile(r"\d[\d,.]*\d|\d")


def _prompt(speaker: str, argument: str, brief: str) -> str:
    return (f"Speaker: {speaker}. The argument to make, already chosen: {argument}.\n"
            f"Case brief:\n{brief}\n\nWrite the two-sentence speech now.")


def _clean(text: str) -> str:
    """Drop <think> blocks and keep at most the first two sentences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().strip('"')
    sentences = [x.strip() for x in re.findall(r"[^.!?]+[.!?]+", " ".join(text.split()))]
    return " ".join(sentences[:2])


def _acceptable(text: str, source: str) -> bool:
    """Checks in code: non-empty, at most 2 sentences, at most 400 characters, every number appears in the source."""
    return bool(text) and len(text) <= 400 and all(n.strip(".,") in source for n in NUM_RE.findall(text))


def _intern(prompt: str) -> str | None:
    """`intern -p --json` with Drex as System 1 and Qwen as System 2. Intern is an agent harness, so it runs in an
    empty temp folder with context files off. Unconfirmed without the CLI: the JSON event layout, so the last
    event carrying text/content/message wins, else the raw stdout."""
    cmd = ["intern", "-p", "--json", "--no-context-files", "--system1", config.INTERN_SYSTEM1,
           "--system2", config.INTERN_SYSTEM2, "--", prompt]
    with tempfile.TemporaryDirectory(prefix="tad-intern-") as cwd:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=90, cwd=cwd, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    last = None
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        for key in ("text", "content", "message", "final"):
            if isinstance(ev, dict) and isinstance(ev.get(key), str) and ev[key].strip():
                last = ev[key]
    return last or out.strip() or None


def _deepinfra(prompt: str) -> str | None:
    body = {"model": config.SPEECH_MODEL, "max_tokens": config.SPEECH_MAX_TOKENS, "temperature": config.SPEECH_TEMPERATURE,
            "reasoning_effort": config.SPEECH_REASONING,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
    try:
        _, data, _ = net.json_call("POST", config.DEEPINFRA_URL, body,
                                   {"Authorization": "Bearer " + str(config.env_key("DEEPINFRA_API_KEY"))})
        return data["choices"][0]["message"]["content"]
    except (net.NetError, KeyError, IndexError, TypeError):
        return None


def write(speaker: str, argument: str, brief: str, canned: str, simulate: bool = False) -> dict:
    """Return {"text", "simulated", "ms", "via"}. `simulated` is True when the canned text was used."""
    t0 = time.monotonic()
    prompt = _prompt(speaker, argument, brief)
    attempts = []
    if not simulate and os.environ.get("TAD_USE_INTERN") == "1" and shutil.which("intern"):
        attempts.append(("intern", _intern))
    if not simulate and config.env_key("DEEPINFRA_API_KEY"):
        attempts.append(("deepinfra", _deepinfra))
    for via, fn in attempts:
        text = _clean(fn(prompt) or "")
        if _acceptable(text, brief + " " + canned):
            return {"text": text, "simulated": False, "ms": int((time.monotonic() - t0) * 1000), "via": via}
    return {"text": canned, "simulated": True, "ms": 0, "via": "canned"}
