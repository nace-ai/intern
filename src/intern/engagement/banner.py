"""The engagement banner: the two plain lines the terminal and the terminal UI's transcript show
when an engagement opens — target, objective, record path. Silent and art-free by request: no
logo, no tune (the tracker tunes and their player still ship in ``intern.engagement.music``,
inert). ``INTERN_NO_INTRO=1`` suppresses the lines entirely."""

from __future__ import annotations

import os


def banner_text(target: str, folder: str, *, objective: str = "") -> str:
    lines = []
    if target:
        lines.append(f"engage {target}")
    if objective:
        lines.append(f"  objective: {objective}")
    if folder:
        lines.append(f"  record: {folder}")
    return "\n".join(lines)


def startup_text() -> str:
    return "\n".join(
        (
            "engage — the pentest harness is on",
            "  /engage <host> [objective…] names the target and opens its record",
        )
    )


def startup() -> None:
    """What prints before the terminal UI starts when the engagement capability is on."""
    if os.environ.get("INTERN_NO_INTRO"):
        return
    print(startup_text())


def show(target: str, folder: str, *, objective: str = "", print_text: bool = True) -> str:
    """The banner for an opening engagement, as text: printed when a terminal is free to print,
    returned for the transcript when the terminal UI owns the screen."""
    if os.environ.get("INTERN_NO_INTRO"):
        return ""
    text = banner_text(target, str(folder), objective=objective)
    if print_text:
        print(text)
    return text
