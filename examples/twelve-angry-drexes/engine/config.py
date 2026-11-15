"""Endpoints, models, price, thresholds and caps in one place."""
from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "cases" / "cases.json"
STATUTES_DIR = ROOT / "data" / "statutes"
LAW_STORE = ROOT / "data" / "lawwiki" / "lawwiki.sqlite"  # built by tools/ingest_lawwiki_local.py; optional
MOCKS_DIR = ROOT / "mocks"
CASE_FILES_DIR = ROOT / "cases" / "files"   # the documents each case uploads
RUNS_DIR = Path(os.environ.get("TAD_RUNS_DIR") or ROOT / "runs")   # TAD_RUNS_DIR: another folder (tests, child processes)


def _load_dotenv() -> None:
    """Read KEY=value lines from the repository's git-ignored .env into the environment, without
    replacing anything already set. Values are never printed or written anywhere else."""
    try:
        lines = (ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        name, sep, value = line.strip().partition("=")
        if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            os.environ.setdefault(name, value.strip().strip("\"'"))


_load_dotenv()


def _nace_base() -> str:
    """API host. The docs read on 2026-11-14 name console.nace.ai in one place and drex.nace.ai in
    another, so NACE_API_BASE can switch it. Only https nace.ai hosts are accepted, which keeps the
    key from ever being sent anywhere else."""
    base = os.environ.get("NACE_API_BASE", "https://console.nace.ai").strip().rstrip("/")
    host = urllib.parse.urlsplit(base).hostname or ""
    if not (base.startswith("https://") and (host == "nace.ai" or host.endswith(".nace.ai"))):
        raise ValueError(f"NACE_API_BASE must be an https nace.ai host, got {base!r}")
    return base


NACE_BASE = _nace_base()
SYSTEMONE_URL = NACE_BASE + "/v1/systemone"
# Both hosts are named in Nace's own docs and take the same key. On 2026-11-14 the console host began
# answering every API client with a bot check (HTTP 403, code "challenge"); drex.nace.ai kept working.
NACE_HOSTS = ["https://console.nace.ai", "https://drex.nace.ai"]


def use_other_nace_host() -> bool:
    """Switch this process to the next documented Nace host. False when there is none left to try."""
    global NACE_BASE, SYSTEMONE_URL
    later = NACE_HOSTS[NACE_HOSTS.index(NACE_BASE) + 1:] if NACE_BASE in NACE_HOSTS else []
    if not later:
        return False
    NACE_BASE = later[0]
    SYSTEMONE_URL = NACE_BASE + "/v1/systemone"
    return True
NDI_BASE = NACE_BASE + "/v1/documents"
NDI_JOB_URL = NDI_BASE + "/jobs/{id}"
DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
LAWWIKI_BASE = "https://www.lawwiki.ai/api/v1"

MODEL_TEXT = "drex-v2.0"          # text and JSON states
MODEL_IMAGE = "drex-max-v1.1"     # images (the bare `drex-max` id is retired and resolves to this one)
NDI_MODE = "low"                  # parse effort for document jobs: low, medium or high
NDI_WAIT_SECONDS = 180
SPEECH_MODEL = "Qwen/Qwen3.8-27B"
INTERN_SYSTEM1 = "drex-latest"
INTERN_SYSTEM2 = "deepinfra/Qwen/Qwen3.8-27B"
SPEECH_MAX_TOKENS = 160
SPEECH_REASONING = "none"   # Qwen 3.8 reasons before it answers and spent the whole 160-token budget on that: no speech came back
SPEECH_TEMPERATURE = 0.4

# Drex bills per input token, and the state is billed once per request however many questions ride on it.
# Drex Max is $0.12 per million input tokens (console docs, 2026-11-14). The docs do not print the
# drex-v2.0 figure as text, so the same price is assumed for it; the console Usage page shows the charge.
USD_PER_M_INPUT = {"drex-max-v1.1": 0.12, "drex-v2.0": 0.12}
USD_PER_M_INPUT_DEFAULT = 0.12
USD_PER_1K_DECISIONS = 0.06       # Drex technical report, Table 8. Shown for comparison only; not used for billing.
FRONTIER_USD_PER_1K = 3.33        # same table: 1,000 decisions on a frontier chat model, for the comparison line
FRONTIER_SEC_EACH = 2.0

# UNFITTED PLACEHOLDERS: these two thresholds were picked by hand, not fitted. Fit them on labeled
# cases (answers a lawyer marked right or wrong) before anyone relies on the "needs a person" route.
CONF_MIN = 0.20                   # confidence below this routes a decision to a person
ROUTE_UNSTABLE_TO_PERSON = True   # a choice whose top option changes when the options are reordered routes to a person

MAX_REQUESTS = 400                # hard caps per run: the run aborts above either
MAX_USD = 0.50
MAX_QUESTIONS_PER_REQUEST = 512   # API limit is 1-512 questions per request
MAX_OPTIONS = 5                   # design rule: yes/no, or at most 5 options

HTTP_TIMEOUT = 30
HTTP_TRIES = 3
USER_AGENT = "twelve-angry-drexes/0.1"

KEENABLE_TITLE = "legal-research-demo"  # app title header of the keyless Keenable endpoints (generic on purpose)
WEB_CHARS = 1200                   # characters of a checked web page that may enter the court state
EXTRA_CANDIDATES = 2               # role-scoped search hits scored next to a case's own statutes (needs the local store)
STATUTE_CHARS = 2500              # characters of statute text put in a state, after the key excerpt
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765
COURTROOM_PAGE = "02-live-courtroom.html"


def usd_per_m(model: str) -> float:
    """Price of one million input tokens for `model`."""
    return USD_PER_M_INPUT.get(model, USD_PER_M_INPUT_DEFAULT)


def env_key(*names: str) -> str | None:
    """Return the first non-empty environment variable among `names` (never logged)."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


PUBLIC_SITE = "https://twelve-angry-drexes.vercel.app"   # the public demo site


def link_check() -> bool:
    """Whether a message's hyperlinks are checked before it is posted (on unless TAD_LINK_CHECK=0)."""
    return os.environ.get("TAD_LINK_CHECK", "1") != "0"


def public_url() -> str:
    """Base URL of the courtroom page that Slack and MCP replies link to: TAD_PUBLIC_URL when set, else the public demo site.
    The local server's address (http://127.0.0.1:8765) opens only on the machine that runs it, so it is not the default."""
    return (os.environ.get("TAD_PUBLIC_URL") or PUBLIC_SITE).rstrip("/")
