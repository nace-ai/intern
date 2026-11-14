"""Evidence capture: the `screenshot` tool (page and terminal modes) and the `evidence` tool.

Page mode shoots a live page as the attacker sees it (a named browser_session and/or inline auth),
at the report's exact evidence frame size (1440×720 CSS px, 2× scale), refusing login pages and
masking secrets in the DOM. Terminal mode RUNS a command in a real PTY (script(1)) and renders the
exact bytes the terminal received in a macOS-style terminal window — what the image shows is what
the program printed. The plain transcript and any recorded HAR are attached beside the image.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import html as html_mod
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from intern.engagement.browser_sessions import (
    BrowserUnavailable,
    Session,
    context_options,
    looks_like_login,
)
from intern.engagement.store import EngagementStore, detect_vuln_ids, now_ms
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

# -- secret masking ------------------------------------------------------------------------

MASK = "••••"


def _keep4(match: re.Match[str]) -> str:
    return match.group(0)[:4] + MASK


def _bearer_basic(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{match.group(3)[:4]}{MASK}"


def _pair_value(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{match.group(3)[:4]}{MASK}"


#: Secrets that must never appear in a client-facing image, masked even if the agent forgets.
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _keep4),  # AWS access key id
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"), _keep4),  # JWT
    (re.compile(r"\b(Bearer|Basic)(\s+)([A-Za-z0-9._~+/=-]{12,})"), _bearer_basic),
    (
        # password-ish pairs; not shell expansions: PGPASSWORD=$(jq …)
        re.compile(
            r'(["\']?(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token'
            r'|client[_-]?secret|aws_secret_access_key)["\']?\s*[:=]\s*)(["\']?)'
            r'(?![\\]?[$`])([^\s"\',}\x1b]{4,})',
            re.IGNORECASE,
        ),
        _pair_value,
    ),
)


def redact_text(text: str, explicit: Sequence[str] = ()) -> str:
    """Mask explicit secrets plus well-known secret shapes (first 4 chars stay recognisable)."""
    out = text
    for s in sorted((x for x in explicit if len(x) >= 3), key=len, reverse=True):
        keep = s[: min(4, len(s) // 3)]
        out = out.replace(s, keep + MASK)
    for pattern, mask in SECRET_PATTERNS:
        out = pattern.sub(mask, out)
    return out


# -- geometry ------------------------------------------------------------------------------

EVIDENCE_VIEWPORT = (1440, 720)
EVIDENCE_SCALE = 2
EVIDENCE_ASPECT = EVIDENCE_VIEWPORT[0] / EVIDENCE_VIEWPORT[1]
MAX_BYTES = int(3.5 * 1024 * 1024)


def image_size(buf: bytes) -> tuple[int, int] | None:
    """Pixel size of a PNG or JPEG from its header; None for anything else."""
    if len(buf) > 24 and buf[:4] == b"\x89PNG":
        return int.from_bytes(buf[16:20], "big"), int.from_bytes(buf[20:24], "big")
    if len(buf) > 4 and buf[0] == 0xFF and buf[1] == 0xD8:
        i = 2
        while i + 9 < len(buf):
            if buf[i] != 0xFF:
                i += 1
                continue
            marker = buf[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(buf[i + 7 : i + 9], "big"), int.from_bytes(buf[i + 5 : i + 7], "big")
            i += 2 + int.from_bytes(buf[i + 2 : i + 4], "big")
    return None


def fits_frame(size: tuple[int, int] | None) -> bool:
    return bool(size and size[1] > 0 and abs(size[0] / size[1] - EVIDENCE_ASPECT) / EVIDENCE_ASPECT <= 0.02)


# -- terminal session composition ----------------------------------------------------------

PS1 = "\x1b[01;32m$\x1b[00m "
TERMINAL_TITLE = "bash"


def strip_ansi(s: str) -> str:
    s = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]", "", s)
    return s.replace("\r\n", "\n").replace("\r", "\n")


def compose_session(command: str, output: str, timed_out: bool) -> str:
    """What the terminal received, in order: prompt + typed command, the output, the next prompt."""
    typed = re.sub(r"\r?\n", "\r\n> ", command)
    out = re.sub(r"(?<!\r)\n", "\r\n", output)
    ended = out if out == "" or out.endswith("\n") else out + "\r\n"
    return f"{PS1}{typed}\r\n{ended}{('^C\r\n' if timed_out else '')}{PS1}"


def render_cols(session: str, run_cols: int, minimum: int = 60) -> int:
    """Columns to render: the widest line the program printed, at least ``min``; the typed command
    and lines already wider than the run terminal are left out."""
    output = [line for line in strip_ansi(session).split("\n") if not re.match(r"^(\$|>) ", line) and line != "$"]
    widest = max([0] + [len(line) for line in output if len(line) <= run_cols])
    return min(run_cols, max(minimum, widest))


def split_leading_cd(command: str, cwd: str) -> tuple[str, str]:
    """A leading ``cd <dir> &&`` (or ``;``) only sets where the command runs: run the rest there."""
    m = re.match(r'^\s*cd\s+("[^"]+"|\'[^\']+\'|[^\s;&|]+)\s*(?:&&|;)\s*(.+)$', command, re.DOTALL)
    if not m:
        return command, cwd
    dir_ = m.group(1).strip("\"'")
    if dir_ == "~" or dir_.startswith("~/"):
        dir_ = os.environ.get("HOME", "~") + dir_[1:]
    return split_leading_cd(m.group(2), str((Path(cwd) / dir_).resolve()))


def transcript_text(command: str, run: PtyRun, shown: str) -> str:
    """Plain-text record of the run, attached next to the image."""
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(run.started / 1000))
    status = "killed after timeout" if run.timed_out else f"exit {run.exit_code}"
    extras = f"{'' if run.pty else ', no PTY available'}{', output clipped' if run.clipped else ''}"
    return "\n".join(
        [
            f"# command  : {command.replace(chr(10), chr(10) + '#            ')}",
            f"# started  : {when} UTC ({run.ms / 1000:.1f} s, {status}{extras})",
            "# captured : by the intern harness (command executed by the harness, not transcribed)",
            "",
            strip_ansi(shown),
            "",
        ]
    )


# -- PTY run ------------------------------------------------------------------------------


@dataclass
class PtyRun:
    output: str
    exit_code: int | None
    timed_out: bool
    clipped: bool
    pty: bool
    started: float
    ms: float


CMD_ENV = "INTERN_CMD"
_FLAVOR_CACHE: str | None = None


def script_flavor() -> str:
    """bsd | util-linux | none — which script(1) is on PATH (cached)."""
    global _FLAVOR_CACHE
    if _FLAVOR_CACHE:
        return _FLAVOR_CACHE
    flavor = "none"
    try:
        r = subprocess.run(["script", "--version"], capture_output=True, text=True, timeout=10)
        out = r.stdout + r.stderr
        if re.search(r"util-linux", out, re.IGNORECASE):
            flavor = "util-linux"
        elif re.search(r"illegal option|usage: script", out, re.IGNORECASE):
            flavor = "bsd"
    except (OSError, subprocess.SubprocessError):
        pass
    _FLAVOR_CACHE = flavor
    return flavor


def _shell() -> str:
    return "/bin/bash" if Path("/bin/bash").exists() else "/bin/sh"


def pty_argv(flavor: str, cols: int, rows: int) -> list[str]:
    """argv that runs $INTERN_CMD in a PTY sized cols×rows."""
    inner = f'stty cols {cols} rows {rows} 2>/dev/null; eval "${CMD_ENV}"'
    if flavor == "bsd":
        return ["script", "-q", "/dev/null", _shell(), "-c", inner]
    if flavor == "util-linux":
        escaped = inner.replace("'", "'\\''")
        return ["script", "-q", "-e", "-c", f"{_shell()} -c '{escaped}'", "/dev/null"]
    return [_shell(), "-c", f'eval "${CMD_ENV}"']


def clean_pty_output(s: str) -> str:
    """BSD script forwards stdin EOF into the PTY, which echoes ^D + two backspaces first."""
    return re.sub(r"^\^D\x08\x08", "", s, flags=re.DOTALL)


async def run_in_pty(
    command: str, *, cwd: str, cols: int, rows: int, timeout_ms: int, max_bytes: int = 2 * 1024 * 1024
) -> PtyRun:
    flavor = script_flavor()
    started = now_ms()
    # pagers would take over the terminal and wait for a key; a capture must run to completion
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "COLUMNS": str(cols),
        "LINES": str(rows),
        "SHELL": "/bin/sh",
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "MANPAGER": "cat",
        "SYSTEMD_PAGER": "",
        "AWS_PAGER": "",
        "PSQL_PAGER": "cat",
        CMD_ENV: command,
    }
    proc = await asyncio.create_subprocess_exec(
        *pty_argv(flavor, cols, rows),
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def drain(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        size, clipped = 0, False
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            if size >= max_bytes:
                clipped = True
                continue
            part = chunk[: max_bytes - size]
            clipped = clipped or len(part) < len(chunk)
            chunks.append(part)
            size += len(part)
        return b"".join(chunks), clipped

    assert proc.stdout is not None and proc.stderr is not None
    out_task = asyncio.create_task(drain(proc.stdout))
    err_task = asyncio.create_task(drain(proc.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout_ms / 1000)
    except TimeoutError:
        timed_out = True
        proc.send_signal(signal.SIGTERM)  # lets script(1) flush what the program already printed
        try:
            await asyncio.wait_for(proc.wait(), 2.0)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(ProcessLookupError):
                await proc.wait()
    out, clipped1 = await out_task
    err, clipped2 = await err_task
    output = clean_pty_output((out + err).decode("utf-8", "replace"))
    return PtyRun(
        output=output,
        exit_code=None if timed_out else proc.returncode,
        timed_out=timed_out,
        clipped=clipped1 or clipped2,
        pty=flavor != "none",
        started=started,
        ms=now_ms() - started,
    )


# -- terminal rendering (pure HTML builder) ------------------------------------------------

#: VS Code "Dark+" terminal palette.
THEME = {
    "background": "#1E1E1E",
    "foreground": "#CCCCCC",
    "30": "#000000",
    "31": "#CD3131",
    "32": "#0DBC79",
    "33": "#E5E510",
    "34": "#2472C8",
    "35": "#BC3FBC",
    "36": "#11A8CD",
    "37": "#E5E5E5",
    "90": "#666666",
    "91": "#F14C4C",
    "92": "#23D18B",
    "93": "#F5F543",
    "94": "#3B8EEA",
    "95": "#D670D6",
    "96": "#29B8DB",
    "97": "#E5E5E5",
}
TERMINAL_FONT = (13.0, 26.0)
CHAR_W = 0.6  # monospace advance as a fraction of the font size
LINE_H = 1.2
BAR_H = 30
PAD_X, PAD_Y = 12, 8

_CSI = re.compile(r"(\x1b\[[0-;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))")
_STYLE_OFF = {"22": "font-weight", "23": "font-style", "24": "text-decoration"}


def _sgr_line_to_html(line: str) -> str:
    """One ANSI line as HTML: SGR colours become spans, every other escape is dropped."""
    parts: list[str] = []
    style: dict[str, str] = {}
    open_span = False

    def emit_style() -> None:
        nonlocal open_span
        if open_span:
            parts.append("</span>")
            open_span = False
        if style:
            parts.append(f'<span style="{";".join(f"{k}:{v}" for k, v in style.items())}">')
            open_span = True

    for token in _CSI.split(line):
        if not token:
            continue
        if token.startswith("\x1b"):
            if not (token.startswith("\x1b[") and token.endswith("m")):
                continue  # cursor moves, OSC titles, … — dropped
            for code in token[2:-1].split(";"):
                if code in ("", "0"):
                    style.clear()
                elif code == "1":
                    style["font-weight"] = "bold"
                elif code == "3":
                    style["font-style"] = "italic"
                elif code == "4":
                    style["text-decoration"] = "underline"
                elif code in _STYLE_OFF:
                    style.pop(_STYLE_OFF[code], None)
                elif code == "39":
                    style.pop("color", None)
                elif code in THEME:
                    style["color"] = THEME[code]
            emit_style()
        else:
            parts.append(html_mod.escape(token))
    if open_span:
        parts.append("</span>")
    return "".join(parts)


def terminal_lines_html(session: str, highlight: Sequence[str] = ()) -> list[str]:
    """Each display line as HTML, proving lines shaded like a selection."""
    needles = [h.lower() for h in highlight if h]
    out = []
    for line in session.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        inner = _sgr_line_to_html(line)
        if needles:
            plain = strip_ansi(line).lower()
            if any(n in plain for n in needles):
                inner = f'<span class="hl">{inner}</span>'
        out.append(inner)
    return out


def wrap_rows(line: str, cols: int) -> int:
    """Display rows a line of ``cols``-wide terminal text occupies."""
    plain = len(strip_ansi(line))
    return max(1, math.ceil(plain / cols))


def fit_terminal(
    lines: Sequence[str], cols: int, area_w: int | None = None, area_h: int | None = None
) -> tuple[float, list[str], bool]:
    """Largest font (13–26 px) at which the whole session fits the terminal area; when nothing
    fits, the LAST rows are kept, like a terminal scrolled to the bottom."""
    width, height = EVIDENCE_VIEWPORT
    area_w = area_w if area_w is not None else width - 2 * PAD_X
    area_h = area_h if area_h is not None else height - BAR_H - 2 * PAD_Y
    rows = sum(wrap_rows(line, cols) for line in lines)
    f = min(TERMINAL_FONT[1], math.floor(area_w / (cols * CHAR_W)))
    while f >= TERMINAL_FONT[0]:
        if rows * LINE_H * f <= area_h and cols * CHAR_W * f <= area_w + 0.5:
            return f, list(lines), False
        f -= 0.5
    font = TERMINAL_FONT[0]
    capacity = max(1, math.floor(area_h / (LINE_H * font)))
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        r = wrap_rows(line, cols)
        if used + r > capacity:
            break
        kept.insert(0, line)
        used += r
    return font, kept, rows > capacity


def terminal_page_html(body_lines: Sequence[str], cols: int, font_size: float, title: str = TERMINAL_TITLE) -> str:
    """A self-contained macOS-style terminal window page showing the session (pure builder)."""
    width, height = EVIDENCE_VIEWPORT
    rows = sum(wrap_rows(line, cols) for line in body_lines)
    body = "\n".join(body_lines)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;background:{THEME["background"]};overflow:hidden}}
#win{{width:{width}px;height:{height}px;display:flex;flex-direction:column;background:{THEME["background"]}}}
#bar{{height:{BAR_H}px;flex:none;position:relative;display:flex;align-items:center;justify-content:center;
background:linear-gradient(#3C3C3E,#323234);border-bottom:1px solid #121214;color:#B8B8BA;
font:500 13px -apple-system,BlinkMacSystemFont,system-ui,sans-serif;letter-spacing:.1px}}
#bar .lights{{position:absolute;left:12px;top:9px;display:flex;gap:8px}}
#bar .lights i{{width:12px;height:12px;border-radius:50%;box-shadow:inset 0 0 0 .5px #0003}}
#bar .lights i:nth-child(1){{background:#FF5F57}}#bar .lights i:nth-child(2){{background:#FEBC2E}}
#bar .lights i:nth-child(3){{background:#28C840}}
#term{{flex:1;min-height:0;padding:{PAD_Y}px {PAD_X}px;overflow:hidden}}
pre{{margin:0;font-family:"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace;
font-size:{font_size}px;line-height:{LINE_H};color:{THEME["foreground"]};white-space:pre}}
.hl{{background:#264F78}}
</style></head><body><div id="win"><div id="bar"><div class="lights"><i></i><i></i><i></i></div>
<span>{html_mod.escape(title)} — {cols}×{rows}</span></div><div id="term"><pre>{body}</pre></div></div></body></html>"""


# -- page capture --------------------------------------------------------------------------


def sanitize_stem(stem: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "_", stem, flags=re.IGNORECASE)[:60]


def out_file(dir: Path, stem: str, ext: str) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    return dir / f"{sanitize_stem(stem)}-{now_ms()}.{ext}"


def auth_headers(a: Mapping[str, Any]) -> dict[str, str]:
    headers = dict(a.get("headers") or {})
    if a.get("basic"):
        headers["Authorization"] = "Basic " + base64.b64encode(str(a["basic"]).encode()).decode()
    if a.get("bearer"):
        headers["Authorization"] = f"Bearer {a['bearer']}"
    return headers


def parse_cookies(cookies: str, url: str) -> list[dict[str, Any]]:
    """'a=1; b=2' → Playwright cookie dicts scoped to the URL's host."""
    split = urlsplit(url)
    out = []
    for c in cookies.split(";"):
        c = c.strip()
        if not c or "=" not in c:
            continue
        i = c.index("=")
        out.append(
            {
                "name": c[:i],
                "value": c[i + 1 :],
                "domain": split.hostname,
                "path": "/",
                "secure": split.scheme == "https",
            }
        )
    return out


@dataclass
class CaptureResult:
    file: Path
    note: str | None = None
    transcript: Path | None = None
    har: Path | None = None
    raw: Path | None = None


async def _shoot(page: Any, dir: Path, stem: str) -> Path:
    """Screenshot the viewport; fall back to JPEG when a PNG would be too large to embed."""
    png = out_file(dir, stem, "png")
    await page.screenshot(path=str(png), type="png")
    if png.stat().st_size <= MAX_BYTES:
        return png
    png.unlink()
    jpg = out_file(dir, stem, "jpg")
    await page.screenshot(path=str(jpg), type="jpeg", quality=85)
    return jpg


_DOM_PASS = """([needles, redacts, focus]) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  const hits = []
  const lower = needles.map((n) => n.toLowerCase())
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    let t = n.nodeValue ?? ''
    for (const r of redacts) if (r.length >= 3 && t.includes(r)) t = t.split(r).join(r.slice(0, 4) + '••••')
    if (t !== n.nodeValue) n.nodeValue = t
    const el = n.parentElement
    if (el && lower.some((x) => t.toLowerCase().includes(x)) && !hits.includes(el)) hits.push(el)
  }
  for (const el of hits) { const s = el.style; s.outline = '3px solid #D4183D'; s.outlineOffset = '2px';
    s.background = '#FFF3B0' }
  const target = (focus ? document.querySelector(focus) : null) ?? hits[0] ?? null
  if (target) target.scrollIntoView({ block: 'center', inline: 'nearest' })
  return { highlighted: hits.length }
}"""


async def capture_page(
    spec: Mapping[str, Any], session: Session | None, out_dir: Path
) -> tuple[CaptureResult, None] | tuple[None, str]:
    """Screenshot a live page as the given identity; refuses a login page."""
    from intern.engagement.browser_sessions import open_browser, open_context
    from intern.engagement.har import sanitize_har  # late: har pulls redact_text from this module

    url = str(spec["url"])
    a: Mapping[str, Any] = spec.get("auth") or {}
    replay = context_options(session)
    headers = {**(replay.get("extra_http_headers") or {}), **auth_headers(a)}
    har_raw = out_file(out_dir, f"{urlsplit(url).hostname}-raw", "har") if spec.get("har") else None
    opts: dict[str, Any] = {
        "viewport": {"width": EVIDENCE_VIEWPORT[0], "height": EVIDENCE_VIEWPORT[1]},
        "device_scale_factor": EVIDENCE_SCALE,
    }
    if replay.get("storage_state"):
        opts["storage_state"] = replay["storage_state"]
    if headers:
        opts["extra_http_headers"] = headers
    if har_raw is not None:
        opts["record_har_path"] = str(har_raw)
        opts["record_har_content"] = "embed"
    result: CaptureResult | None = None
    try:
        async with open_browser() as browser, open_context(browser, opts) as ctx:
            if a.get("cookies"):
                await ctx.add_cookies(parse_cookies(str(a["cookies"]), url))
            local_storage = a.get("local_storage") or {}
            if local_storage:
                entries = json.dumps(dict(local_storage)).replace("</", "<\\/")
                await ctx.add_init_script(
                    f"try {{ for (const [k, v] of Object.entries({entries})) localStorage.setItem(k, v) }} catch {{}}"
                )
            page = await ctx.new_page()
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=15_000)
            await page.wait_for_timeout(int(spec.get("wait_ms") or 1500))
            probe: dict[str, Any] = {"text": "", "pw": False}
            with contextlib.suppress(Exception):
                probe = await page.evaluate(
                    "() => ({ text: (document.body?.innerText ?? '').slice(0, 400),"
                    " pw: !!document.querySelector('input[type=password]') })"
                )
            if not spec.get("allow_login_page") and looks_like_login(
                page.url, str(probe.get("text", "")), bool(probe.get("pw"))
            ):
                reason = (
                    f"landed on a login page ({page.url}) — the identity was not applied"
                    + (
                        f' (session "{session.meta["name"]}" may have expired: browser_session action=verify)'
                        if session
                        else ""
                    )
                    + "; fix auth and retry"
                )
                return None, reason
            zoom = spec.get("zoom")
            if zoom and zoom != 1:
                with contextlib.suppress(Exception):
                    await page.evaluate(
                        "(z) => { document.body.style.zoom = String(z) }", min(2, max(0.5, float(zoom)))
                    )
            mark = {"highlighted": 0}
            with contextlib.suppress(Exception):
                mark = await page.evaluate(
                    _DOM_PASS,
                    [[*(spec.get("highlight") or [])], [*(spec.get("redact") or [])], spec.get("focus")],
                )
            await page.wait_for_timeout(300)
            split = urlsplit(url)
            file = await _shoot(page, out_dir, (split.hostname or "") + split.path)
            note = None
            if spec.get("highlight") and not mark.get("highlighted"):
                note = (
                    "none of the highlight strings were found on the page"
                    f" ({', '.join(spec['highlight'])}) — check the capture shows the proof"
                )
            result = CaptureResult(file=file, note=note)
        # the context is closed now, so the HAR is complete: sanitise it into the output dir
        if har_raw is not None:
            try:
                clean = out_file(out_dir, f"{split.hostname}-traffic", "har")
                extra = [*(spec.get("redact") or []), *headers.values()]
                clean.write_text(sanitize_har(har_raw.read_text(encoding="utf-8"), extra), encoding="utf-8")
                result.har = clean
            except Exception as e:
                result.note = "; ".join(x for x in [result.note, f"HAR not saved: {str(e).splitlines()[0]}"] if x)
        return result, None
    except BrowserUnavailable as e:
        return None, str(e)
    except Exception as e:
        return None, str(e).splitlines()[0]
    finally:
        if har_raw is not None:
            har_raw.unlink(missing_ok=True)


async def render_terminal(
    session: str, cols: int, highlight: Sequence[str], stem: str, out_dir: Path
) -> tuple[CaptureResult, dict[str, Any], None] | tuple[None, None, str]:
    """Render the exact bytes the terminal received in a terminal window and screenshot it."""
    from intern.engagement.browser_sessions import open_browser, open_context

    lines = terminal_lines_html(session, highlight)
    font, kept, scrolled = fit_terminal(lines, cols)
    page_html = terminal_page_html(kept, cols, font)
    try:
        async with (
            open_browser() as browser,
            open_context(
                browser,
                {
                    "viewport": {"width": EVIDENCE_VIEWPORT[0], "height": EVIDENCE_VIEWPORT[1]},
                    "device_scale_factor": EVIDENCE_SCALE,
                },
            ) as ctx,
        ):
            page = await ctx.new_page()
            await page.set_content(page_html, wait_until="load")
            await page.wait_for_timeout(150)
            file = await _shoot(page, out_dir, stem)
            marked = sum(1 for line in kept if 'class="hl"' in line)
            info = {
                "fontSize": font,
                "rows": len(kept),
                "used": len(lines),
                "scrolled": scrolled,
                "marked": marked,
            }
            return CaptureResult(file=file), info, None
    except BrowserUnavailable as e:
        return None, None, str(e)
    except Exception as e:
        return None, None, str(e).splitlines()[0]


TERMINAL_LIMITS = {"cols": (60, 160, 110), "timeout_ms": (60_000, 600_000)}


async def capture_terminal(spec: Mapping[str, Any], out_dir: Path) -> tuple[CaptureResult, None] | tuple[None, str]:
    """Run the command in a PTY, render its exact output in a terminal window, screenshot it."""
    col_limits = TERMINAL_LIMITS["cols"]
    cols = round(min(col_limits[1], max(col_limits[0], int(spec.get("cols") or col_limits[2]))))
    command, cwd = split_leading_cd(str(spec["command"]), str(spec["cwd"]))
    timeout_ms = min(TERMINAL_LIMITS["timeout_ms"][1], int(spec.get("timeout_ms") or TERMINAL_LIMITS["timeout_ms"][0]))
    run = await run_in_pty(command, cwd=cwd, cols=cols, rows=40, timeout_ms=timeout_ms)
    redact = spec.get("redact") or []
    shown = redact_text(compose_session(command, run.output, run.timed_out), redact)
    stem = "terminal-" + (command.split()[0] if command.split() else "cmd")
    transcript = out_file(out_dir, stem, "txt")
    transcript.write_text(transcript_text(redact_text(command, redact), run, shown), encoding="utf-8")
    # narrow output renders in fewer columns, so its text is larger in the report frame
    shown_cols = render_cols(shown, cols)
    # the exact (masked) bytes, so the capture can be re-rendered later without running anything again
    raw = out_file(out_dir, stem, "ansi")
    raw.write_text(
        json.dumps({"cols": shown_cols, "highlight": spec.get("highlight") or [], "session": shown}),
        encoding="utf-8",
    )
    shot, info, reason = await render_terminal(shown, shown_cols, spec.get("highlight") or (), stem, out_dir)
    if shot is None or info is None:
        return None, reason or "terminal render failed"
    notes = [
        f"the command was still running after {round(timeout_ms / 1000)} s and was killed (shown as ^C)"
        " — bound it (timeout, -m, head) and re-capture"
        if run.timed_out
        else "",
        f"exit status {run.exit_code} — make sure the capture shows the proof, not an error"
        if run.exit_code not in (None, 0)
        else "",
        f"output is {info['used']} lines; the window shows the last {info['rows']} like a real terminal"
        " — narrow it to the proving lines (grep, head) so the proof is on screen"
        if info["scrolled"]
        else "",
        f"text rendered at {info['fontSize']}px — dense; fewer columns (cols) or fewer lines read better"
        if info["fontSize"] <= TERMINAL_FONT[0] + 0.5 and not info["scrolled"]
        else "",
        f"none of the highlight strings appear in the output ({', '.join(spec.get('highlight') or [])})"
        if spec.get("highlight") and not info["marked"]
        else "",
        "script(1) not available — ran without a PTY, so programs may print without colours" if not run.pty else "",
        "output exceeded 2 MB and was clipped" if run.clipped else "",
    ]
    return (
        CaptureResult(
            file=shot.file,
            transcript=transcript,
            raw=raw,
            note="; ".join(n for n in notes if n) or None,
        ),
        None,
    )


# -- tools ---------------------------------------------------------------------------------


class PageAuth(BaseModel):
    basic: str | None = None
    bearer: str | None = None
    cookies: str | None = Field(default=None, description="'a=1; b=2'")
    headers: dict[str, str] | None = None
    local_storage: dict[str, str] | None = None


class ScreenshotArgs(BaseModel):
    vuln_id: str = Field(description="finding this image proves (state_query what=vulns lists ids)")
    caption: str = Field(description="what the image proves, one sentence (shown under the figure)")
    mode: Literal["page", "terminal"] | None = None
    url: str | None = None
    session: str | None = Field(default=None, description="page mode: named identity from browser_session")
    har: bool | None = Field(default=None, description="page mode: also attach the page's network traffic")
    auth: PageAuth | None = None
    focus: str | None = Field(default=None, description="CSS selector to scroll to the centre of the image")
    zoom: float | None = Field(default=None, ge=0.5, le=2, description="page mode: enlarge a sparse UI; 0.5–2")
    highlight: list[str] | None = Field(default=None, description="text that proves the finding — outlined")
    redact: list[str] | None = Field(default=None, description="secret values to mask")
    wait_ms: int | None = None
    allow_login_page: bool | None = Field(default=None, description="only when the login page itself is the proof")
    command: str | None = Field(default=None, description="terminal mode: the command the harness runs")
    cwd: str | None = Field(default=None, description="terminal mode: working directory")
    cols: int | None = Field(default=None, ge=60, le=160, description="terminal width in columns")
    timeout_s: float | None = Field(default=None, le=600, description="terminal mode: kill after this many seconds")


class ScreenshotTool(Tool):
    name = "screenshot"
    description = (
        f"Capture an evidence image for a finding and attach it to that finding in one step (embedded"
        f" inline in the report). Images are {EVIDENCE_VIEWPORT[0] * EVIDENCE_SCALE}×"
        f"{EVIDENCE_VIEWPORT[1] * EVIDENCE_SCALE} (2:1) — exactly the report's evidence frame. "
        "mode=page: screenshot a live page as the attacker sees it — url plus an identity:"
        " session=<name> (a browser_session you established) and/or auth (basic 'user:pass', bearer,"
        " cookies 'a=1; b=2', headers, local_storage {key: token} for SPAs); har=true also records"
        " that page's network traffic (sanitised) and attaches it to the finding; highlight[] ="
        " text on the page that proves the point (outlined), focus = CSS selector to centre,"
        " redact[] = values to mask. It refuses a login/SSO page (auth not applied) — fix auth and"
        " retry. mode=terminal: proof from the command line (RCE output, curl/API responses, DB"
        " query rows, files read, cloud CLI calls) — give the COMMAND, not its output: the harness"
        " runs it itself in a real terminal (PTY) and screenshots the terminal window with the"
        " program's real colours and output behind a neutral $ prompt. The run's transcript"
        " (command, time, exit code) is attached too. Set the working directory with cwd (a"
        " leading `cd <dir> &&` is treated the same way and not shown); cols 60–160 (default 110);"
        " timeout_s kills the command (default 60, max 600). Always pass vuln_id and a caption"
        " that says what the image proves."
    )
    effects: ClassVar[frozenset[Effect]] = frozenset({"network", "write", "exec"})
    Args = ScreenshotArgs

    def __init__(self, store: EngagementStore) -> None:
        self.store = store

    async def run(self, args: ScreenshotArgs, ctx: ToolContext) -> ToolOutput:
        store = self.store
        vuln = next((v for v in store.state.vulns if v.id == args.vuln_id), None)
        if vuln is None:
            return fail(
                f"no finding {args.vuln_id}",
                "state_query what=vulns lists the finding ids; pass one as vuln_id.",
                argument="vuln_id",
            )
        mode = args.mode or ("page" if args.url else "terminal")
        if mode == "page" and not args.url:
            return fail("page mode needs url", "Pass the url of the page that shows the proof.", argument="url")
        if mode == "terminal" and not (args.command and args.command.strip()):
            return fail(
                "terminal mode needs command — the harness runs it and captures the terminal",
                "Pass the command that produces the proof (bounded, read-only).",
                argument="command",
            )
        cwd = ctx.resolve(args.cwd) if args.cwd else store.work_dir  # where the agent's shell runs
        if mode == "terminal" and not cwd.exists():
            return fail(f"cwd {cwd} does not exist", "Pass cwd as a directory that exists.", argument="cwd")
        from intern.engagement.browser_sessions import SessionStore

        session = SessionStore(store).load(args.session) if args.session else None
        if args.session and session is None:
            return fail(
                f'no browser session "{args.session}"',
                "Create it with browser_session action=login|import.",
                argument="session",
            )
        incoming = store.evidence_dir / ".incoming"
        if mode == "page":
            result, reason = await capture_page(
                {
                    "url": args.url,
                    "auth": args.auth.model_dump() if args.auth else None,
                    "har": args.har,
                    "zoom": args.zoom,
                    "focus": args.focus,
                    "highlight": args.highlight,
                    "redact": args.redact,
                    "wait_ms": args.wait_ms,
                    "allow_login_page": args.allow_login_page,
                },
                session,
                incoming,
            )
        else:
            result, reason = await capture_terminal(
                {
                    "command": args.command,
                    "cwd": cwd,
                    "cols": args.cols,
                    "timeout_ms": int(args.timeout_s * 1000) if args.timeout_s else None,
                    "highlight": args.highlight,
                    "redact": args.redact,
                },
                incoming,
            )
        if result is None:
            return fail(f"screenshot not attached: {reason}", "Fix the reason above and capture again.")
        who = (
            f" as {session.meta['name']}" + (f" ({session.meta['role']})" if session.meta.get("role") else "")
            if session
            else ""
        )
        record = store.add_evidence(
            move=True,
            description=args.caption + who,
            file=result.file,
            kind="screenshot",
            vuln_ids=[vuln.id],
            url=args.url if mode == "page" else vuln.host,
            caption=f"{args.caption}{f' — viewed{who}' if who else ''}",
        )
        stored = record.name if record else None
        # keep the exact session bytes beside the engagement, so the capture can be re-rendered
        if result.raw is not None and stored:
            raw_dir = store.evidence_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(result.raw), str(raw_dir / f"{stored}.json"))
        transcript = None
        if result.transcript is not None:
            t = store.add_evidence(
                move=True,
                description=f"Terminal transcript behind: {args.caption}",
                file=result.transcript,
                kind="response",
                vuln_ids=[vuln.id],
                caption=f"Terminal transcript (command run by the harness) for: {args.caption}",
            )
            transcript = t.name if t else None
        har = None
        if result.har is not None:
            h = store.add_evidence(
                move=True,
                description=f"Network traffic behind: {args.caption}{who}",
                file=result.har,
                kind="response",
                vuln_ids=[vuln.id],
                caption=f"Network traffic (HAR, secrets masked) for: {args.caption}{who}",
            )
            har = h.name if h else None
        px = f"{EVIDENCE_VIEWPORT[0] * EVIDENCE_SCALE}×{EVIDENCE_VIEWPORT[1] * EVIDENCE_SCALE}"
        body = (
            f"captured {mode} evidence {px}{who} (fills the report frame) → attached to {vuln.id}: {stored}"
            + (f"\n+ traffic HAR attached: {har}" if har else "")
            + (f"\n+ transcript attached: {transcript}" if transcript else "")
            + (f"\n⚠ {result.note}" if result.note else "")
        )
        return ToolOutput(content=body)


class EvidenceArgs(BaseModel):
    description: str
    file: str | None = None
    kind: Literal["screenshot", "response", "loot", "note"] | None = None
    vuln_id: str | None = Field(
        default=None,
        description="finding this proves — the artifact is embedded inline in that finding in the report",
    )
    url: str | None = Field(default=None, description="URL shown in the screenshot frame")
    caption: str | None = Field(default=None, description="what the screenshot shows and why it confirms the finding")


class EvidenceTool(Tool):
    name = "evidence"
    description = (
        "Attach an evidence FILE to the engagement (saved HTTP response, command output, DB dump,"
        " loot, or an image you already have) and link it to the finding it proves with vuln_id —"
        " it is then embedded inline in that finding in the report. To CAPTURE an image of proof"
        " (a page as the attacker sees it, or command output / API response / data rows), use the"
        " `screenshot` tool instead: it shoots at the report's exact frame size, refuses login"
        " pages, masks secrets and links the image in one step."
    )
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})
    Args = EvidenceArgs

    def __init__(self, store: EngagementStore) -> None:
        self.store = store

    def _resolve_file(self, file: str, ctx: ToolContext) -> Path:
        p = Path(file).expanduser()
        if p.is_absolute():
            return p
        for base in (ctx.cwd, self.store.work_dir):
            candidate = base / p
            if candidate.exists():
                return candidate
        return ctx.cwd / p

    async def run(self, args: EvidenceArgs, ctx: ToolContext) -> ToolOutput:
        store = self.store
        if args.vuln_id and not any(v.id == args.vuln_id for v in store.state.vulns):
            return fail(f"no finding {args.vuln_id}", "state_query what=vulns lists ids.", argument="vuln_id")
        # Link an artifact that is already stored (by name) instead of copying it again.
        if args.vuln_id and args.file and "/" not in args.file:
            try:
                name = store.link_evidence(args.file, [args.vuln_id])
                return ToolOutput(content=f"linked {name} to {args.vuln_id} — it is embedded inline in that finding")
            except ValueError:
                pass  # no stored evidence by that name: attach the file below
        file = self._resolve_file(args.file, ctx) if args.file else None
        record = store.add_evidence(
            description=args.description,
            file=file,
            kind=args.kind or "note",
            vuln_ids=[args.vuln_id] if args.vuln_id else [],
            url=args.url,
            caption=args.caption,
        )
        if record is None:
            return fail(
                f"file not found: {args.file}",
                "Save the proof to a file first, then attach it (or drop file= to record a note).",
                argument="file",
            )
        ids = [v.id for v in store.state.vulns]
        links = list(dict.fromkeys([*record.vuln_ids, *detect_vuln_ids(record.description, record.name, ids)]))
        warn = (
            "\n⚠ not linked to any finding — the report can only list it in the appendix."
            f" Link it: evidence vuln_id=<vN> file={record.name}"
            if (args.kind or "note") != "note" and not links
            else ""
        )
        body = (
            f"evidence recorded (stored {record.name})"
            + (f" · proves {', '.join(links)} (embedded inline in the report)" if links else "")
            + warn
        )
        return ToolOutput(content=body)
