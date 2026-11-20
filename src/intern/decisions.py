"""The decision protocol: typed small judgments answered by System 1, a fast decision model.

Three question types cover what the harness needs to ask:

- choice: pick one named option;
- score: place the state on an ordered scale between a low and a high anchor;
- noul: the probability that a statement about the state is true.

Any backend can answer them. System 1 is required: the harness never answers a question itself. An ask
that fails (an error, a timeout, a missing or wrong-type answer) is retried with backoff; one that still
fails raises `System1Unavailable`, and the run stops. A free credit the console refuses for good raises it at once.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from collections import OrderedDict
from typing import Annotated, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, SecretStr

from intern import names
from intern.context import INITIAL_CHARS_PER_TOKEN, bound_output
from intern.events import Decision, EventSink
from intern.messages import JsonObject, System1Usage
from intern.models.catalog import DECISIONS_PREFIX, KAPITSA_PREFIX, System1Sources, decision_models
from intern.models.registry import (
    FREE_FINAL,
    CredentialSource,
    StaticKey,
    bearer,
    check_host,
    console_error,
    token_expired,
)


class ChoiceQuestion(BaseModel):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str] = Field(description="Option name to what it means.", min_length=2)


class ScoreQuestion(BaseModel):
    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str] = Field(description="Scale anchors, low first.", min_length=2)


class NoulQuestion(BaseModel):
    type: Literal["noul"] = "noul"
    instructions: str


Question = Annotated[ChoiceQuestion | ScoreQuestion | NoulQuestion, Field(discriminator="type")]


class ChoiceAnswer(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None


class ScoreAnswer(BaseModel):
    type: Literal["score"] = "score"
    score: float = Field(ge=0, le=1, description="Expected position on the scale: 0 is the first level, 1 the last.")
    probabilities: list[float] = Field(
        default_factory=list, description="Each level's probability, lowest first; empty when the backend gives none."
    )
    legend: list[str] = Field(default_factory=list, description="The question's levels, lowest first.")
    confidence: float | None = None


class NoulAnswer(BaseModel):
    type: Literal["noul"] = "noul"
    noul: float = Field(ge=0, le=1)


Answer = Annotated[ChoiceAnswer | ScoreAnswer | NoulAnswer, Field(discriminator="type")]


class Answers(BaseModel):
    answers: dict[str, Answer]
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


# OpenRouter's systemone answers at most 16 questions per request (422 otherwise).
MAX_QUESTIONS_PER_CALL = 16
# A refused request (429) is sent again when the endpoint's `retry-after` says. One that names no wait, as an
# overloaded backend (529) does not, waits twice as long each retry. Any other error, such as a 503, fails over to
# the next model.
RATE_LIMIT_RETRY_S = 1.5
RETRY_STATUSES = (429, 529)
MAX_RETRIES = 2
# Asks in flight at once by default. A code search sends about 140 asks in a few waves, 70 at once at its peak,
# which queued behind 16 slots; 64 drew no 429s when this was set. The console refuses such waves now: see PACE_S.
MAX_CONCURRENCY = 64
# The console refuses requests that arrive together, however few it had that minute: of 10 sent at once it answered
# 8, of 60, 21; sent 70 ms apart, all 210, and 50 ms apart, 194 of 200 (2026-11-17). A search sends its asks in
# waves, so half its requests were refused and it took twice as long (six code searches: 24 s each, 12 s spaced).
# Once an endpoint has refused a request, requests leave PACE_S apart until it has refused none for PACED_S; an
# endpoint that refuses nothing is sent whole waves, as before.
# shortcut: one pace for every endpoint (what the console takes); measure an endpoint's own when another refuses.
PACE_S = 1 / 15
PACED_S = 300.0
# Of a decision model's listed window, the most max_state_tokens may be. A real ask (a session cut to
# max_state_tokens, two of the loop's questions) was 0.65 to 0.81 of max_state_tokens in the model's own tokens on
# Jev, Clef, Liquid d1 and Drex (2026-11-15); at three quarters it fills about 0.6 of the window, which leaves room
# for SYSTEM1.md, denser text and listings that overstate (joey-1 lists 16,384 and refuses over 16,000).
WINDOW_STATE_SHARE = 0.75
# The console refuses (422) a state holding a base64 data URL of any type but text/*, with parameters
# (`;charset=utf-8`) or not, payload or not, as media sent in the text; a page, stylesheet or notebook the agent
# writes often has one. The payload means nothing to System 1, so the type and size stand in for it.
MEDIA_DATA_URL = re.compile(
    r"data:(?!text/)([\w.+-]+/[\w.+-]+)(?:;[\w.+-]+=[^;,\s]*)*;base64,([A-Za-z0-9+/=_-]*)", re.IGNORECASE
)


def _without_media(text: str) -> str:
    return MEDIA_DATA_URL.sub(lambda m: f"[base64 data URL of type {m[1]}, {len(m[2])} characters]", text)


def _retry_after(response: httpx.Response, otherwise: float) -> float:
    """Seconds the endpoint asks a refused request to wait; `otherwise` when it names none."""
    try:
        return min(max(float(response.headers["retry-after"]), 0.0), RETRY_LONGEST_S)
    except (KeyError, ValueError):
        return otherwise


class DecisionBackend(Protocol):
    name: str

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers: ...


class DecisionsApiBackend:
    """A `POST {base_url}/systemone` endpoint, as served by OpenRouter's Decisions API (Jev, `nace-ai/drex-*`)
    and the console (`https://console.nace.ai/v1`). `models` is a failover list tried in order. `api_key` is a fixed
    key or a credential source asked on every request."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | CredentialSource,
        models: list[str],
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = f"decisions-api:{models[0]}"
        self._base_url = base_url
        self._url = f"{base_url.rstrip('/')}/systemone"
        self._credential = StaticKey(api_key) if isinstance(api_key, SecretStr) else api_key
        check_host(self._credential, self._url)
        self._models = models
        self._timeout_s = timeout_s
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._paced_until = 0.0
        self._next_request = 0.0

    def _client(self) -> httpx.AsyncClient:
        """One pooled client per event loop: an ask reuses a kept-alive connection instead of paying for a new
        client, TCP and TLS handshake."""
        loop = asyncio.get_running_loop()
        if self._http is None or self._loop is not loop:
            limits = httpx.Limits(max_connections=None, max_keepalive_connections=MAX_CONCURRENCY)
            # timeout_s bounds connecting only: how long an answer may take is the ask's own limit (DecisionClient
            # wraps every decide in it), which an ask may raise; a read timeout here would cut it at timeout_s.
            timeout = httpx.Timeout(None, connect=self._timeout_s)
            self._http = httpx.AsyncClient(timeout=timeout, transport=self._transport, limits=limits)
            self._loop = loop
        return self._http

    async def window(self) -> int | None:
        """The smallest window its endpoint's decision-model listing gives these models (every one is sent the same
        state); None when it lists none of them, as the console does not."""
        sources = System1Sources(decisions_url=self._base_url, decisions_key=await bearer(self._credential, self._url))
        models = await decision_models(sources, self._transport)
        listed = {m.id.removeprefix(DECISIONS_PREFIX): m.window for m in models}
        return min((w for m in self._models if (w := listed.get(m))), default=None)

    async def pace(self) -> None:
        """Returns when the next request may leave: at once, unless the endpoint refused one lately; then PACE_S
        after the one before it, in the order they came."""
        now = time.monotonic()
        if now >= self._paced_until:
            return
        at = max(self._next_request, now)
        self._next_request = at + PACE_S
        await asyncio.sleep(at - now)

    async def _post(self, client: httpx.AsyncClient, request: dict[str, Any], limit: Any) -> httpx.Response:
        """One request with the bearer the credential gives now; one refused as expired is renewed and sent again,
        once."""

        async def send() -> httpx.Response:
            key = await bearer(self._credential, self._url)
            headers = {**names.CLIENT_HEADERS, **({"Authorization": f"Bearer {key}"} if key else {})}
            response = await client.post(self._url, headers=headers, json=request, timeout=limit)
            self._credential.observe(response.headers)
            return response

        response = await send()
        if token_expired(response.status_code, response.content):
            await self._credential.invalidate()
            response = await send()
        return response

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        items = list(questions.items())
        if len(items) <= MAX_QUESTIONS_PER_CALL:
            return await self._decide(state, questions)
        batches = [dict(items[i : i + MAX_QUESTIONS_PER_CALL]) for i in range(0, len(items), MAX_QUESTIONS_PER_CALL)]
        results = await asyncio.gather(*(self._decide(state, batch) for batch in batches))
        return Answers(
            answers={name: answer for r in results for name, answer in r.answers.items()},
            cost_usd=sum(r.cost_usd for r in results),
            input_tokens=sum(r.input_tokens for r in results),
            output_tokens=sum(r.output_tokens for r in results),
        )

    async def _decide(self, state: str, questions: dict[str, Question]) -> Answers:
        payload = {
            name: q.model_copy(update={"instructions": _without_media(q.instructions)}).model_dump()
            for name, q in questions.items()
        }
        state = _without_media(state)
        last_error: Exception | None = None
        client = self._client()
        for n, model in enumerate(self._models):
            # A model with another after it keeps timeout_s for its answer, so one that hangs fails over within the
            # attempt; the last has the attempt's own limit.
            last = n == len(self._models) - 1
            timeout = httpx.USE_CLIENT_DEFAULT if last else httpx.Timeout(self._timeout_s)
            try:
                request: dict[str, Any] = {"model": model, "state": state, "questions": payload}
                response = await self._post(client, request, timeout)
                for attempt in range(MAX_RETRIES):
                    if response.status_code not in RETRY_STATUSES:
                        break
                    self._paced_until = time.monotonic() + PACED_S
                    await asyncio.sleep(_retry_after(response, RATE_LIMIT_RETRY_S * 2**attempt))
                    await self.pace()
                    response = await self._post(client, request, timeout)
                refused = console_error(response.content) if response.status_code >= 400 else None
                if refused is not None and refused.code in FREE_FINAL:
                    raise FreeRefused(refused.code, refused.meter, response.text[:500])
                response.raise_for_status()
                body = response.json()
                raw = body["answers"]
                for name, q in questions.items():
                    # The protocol scores as the expected level index, 0..n-1; the harness scale is 0..1.
                    if isinstance(q, ScoreQuestion) and isinstance(a := raw.get(name), dict) and "score" in a:
                        a["score"] = min(max(float(a["score"]) / (len(q.criteria) - 1), 0.0), 1.0)
                        # Keyed by level index on the wire; a list in level order lines up with q.criteria.
                        p = a.get("probabilities")
                        levels = range(len(q.criteria))
                        a["probabilities"] = [float(p.get(str(i), 0.0)) for i in levels] if isinstance(p, dict) else []
                        a["legend"] = q.criteria  # the servers echo the levels, as a dict or a list
                usage = body.get("usage") or {}
                return Answers(
                    answers=raw,
                    cost_usd=float(usage.get("cost") or 0),
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                )
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
                last_error = e
        raise RuntimeError(f"every decision model failed: {last_error}")


# kapitsa-systemone's default --context, the one Drex is calibrated for (`kRecommendedContext`).
KAPITSA_CONTEXT = 16_384
# An ask of a full state (3/4 of KAPITSA_CONTEXT) took about 55 s on an M4 Pro; an older Mac takes longer.
KAPITSA_TIMEOUT_S = 180.0


class KapitsaBackend(DecisionsApiBackend):
    """kapitsa-systemone on this machine (`kapitsa:<model>`): no key, one ask at a time, each slower than a hosted
    model's. `DecisionClient` reads `max_concurrency` and `least_timeout_s`."""

    max_concurrency = 1
    least_timeout_s = KAPITSA_TIMEOUT_S

    def __init__(self, base_url: str, model: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(base_url, SecretStr(""), [model], transport=transport)
        self.name = f"{KAPITSA_PREFIX}{model}"

    async def window(self) -> int | None:
        # shortcut: assumes the default --context, as /health does not report it; read it there once Kapitsa does.
        return KAPITSA_CONTEXT


class FreeRefused(Exception):
    """The console refused System 1's free credit for good (`registry.FREE_FINAL`): no retry or other model changes
    that, so the ask is not retried."""

    def __init__(self, code: str, meter: str | None, detail: str) -> None:
        super().__init__(f"the console refused the free credit ({code}): {detail}")
        self.code = code
        self.meter = meter


class System1Unavailable(Exception):
    """System 1 did not answer within its retry window, or the console refused its free credit for good; nothing may
    stand in for it, so the run stops. `code` and `meter` are the console's, for a refused free credit."""

    def __init__(self, message: str, *, code: str | None = None, meter: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.meter = meter


def compact(answer: Answer) -> JsonObject:
    """An answer as the event carries it: a choice with its probabilities, a noul's value, a score with its level
    probabilities; the legend of a score stays with the question."""
    if isinstance(answer, ChoiceAnswer):
        return {"choice": answer.choice, "probabilities": dict(answer.probabilities)}
    if isinstance(answer, NoulAnswer):
        return {"noul": answer.noul}
    return {"score": answer.score, "probabilities": list(answer.probabilities)}


def _fits(question: Question, answer: Answer) -> bool:
    if isinstance(question, ChoiceQuestion):
        return isinstance(answer, ChoiceAnswer) and answer.choice in question.criteria
    return answer.type == question.type


CACHE_SIZE = 500
# A down System 1 is retried for this long, the wait doubling from the first delay up to the longest, before the
# ask gives up. A restart or a rate-limit burst clears in seconds, but outages of System 1's service have lasted
# tens of minutes, and two minutes ended runs that had hours of budget left; the run waits instead, since nothing
# may answer in System 1's place.
RETRY_WINDOW_S = 1800.0
RETRY_FIRST_S = 1.0
RETRY_LONGEST_S = 30.0
# The check a session opens with: fail at open, not minutes into the first turn.
CHECK_WINDOW_S = 15.0
CHECK = NoulQuestion(instructions="Is this a connectivity check?")


class DecisionClient:
    """Asks the configured backend, with a cache and retries; System 1 is required, so there is no fallback.

    Args:
        backend: The System 1 every ask goes to.
        emit: Receives one `decision` event per attempt.
        timeout_s: Per-attempt time limit; a timeout counts as a failure.
        max_state_tokens: A longer state is cut to its head and tail, with a marker, before it is sent.
        max_concurrency: Asks in flight at once, across every caller; the rest wait their turn.
        retry_window_s: How long a failing ask is retried before it raises `System1Unavailable`; RETRY_WINDOW_S
            when not given.
    """

    def __init__(
        self,
        backend: DecisionBackend,
        emit: EventSink,
        *,
        timeout_s: float = 10.0,
        max_state_tokens: int = 32_768,
        max_concurrency: int = MAX_CONCURRENCY,
        retry_window_s: float | None = None,
    ) -> None:
        self.backend = backend
        self.emit = emit
        """Where the `decision` events go; a session journals them on the way (`journal.recording`)."""
        self._timeout_s = timeout_s
        self._configured_state_tokens = max_state_tokens
        # No meter for System 1; 3 chars per token errs toward sending fewer tokens than the limit.
        self._max_state_tokens = max_state_tokens
        self._max_concurrency = max_concurrency
        self._retry_window_s = RETRY_WINDOW_S if retry_window_s is None else retry_window_s
        self._max_state_chars = int(max_state_tokens * INITIAL_CHARS_PER_TOKEN)
        # A backend on this machine takes fewer asks at once, and more time for each, than the config gives.
        self._least_timeout_s: float = getattr(backend, "least_timeout_s", 0.0)
        self._slots = asyncio.Semaphore(min(max_concurrency, getattr(backend, "max_concurrency", max_concurrency)))
        self._cache: OrderedDict[str, dict[str, Answer]] = OrderedDict()
        self.usage = System1Usage()
        self.last_error: str | None = None

    @property
    def max_state_tokens(self) -> int:
        """The longest state an ask sends whole; a longer one is cut to its head and tail."""
        return self._max_state_tokens

    def emitting_to(self, emit: EventSink) -> DecisionClient:
        """The same client (backend, cache, concurrency, usage) sending its events to `emit`: a subagent's, so its
        System 1 asks are kept in its own journal."""
        client = copy.copy(self)
        client.emit = emit
        return client

    def with_backend(self, backend: DecisionBackend) -> DecisionClient:
        """The same client on another backend, for a System 1 switch: the session's usage carries over; the cache
        starts fresh."""
        client = DecisionClient(
            backend,
            self.emit,
            timeout_s=self._timeout_s,
            max_state_tokens=self._configured_state_tokens,
            max_concurrency=self._max_concurrency,
            retry_window_s=self._retry_window_s,
        )
        client.usage = self.usage
        return client

    async def fit(self) -> None:
        """Keep the state within the backend's model's window, when its listing gives one: a decision model with
        a window smaller than the configured state refuses a long one (422), which stops the run."""
        lookup = getattr(self.backend, "window", None)
        window = await lookup() if lookup is not None else None
        if window:
            self._max_state_tokens = min(self._configured_state_tokens, int(window * WINDOW_STATE_SHARE))
            self._max_state_chars = int(self._max_state_tokens * INITIAL_CHARS_PER_TOKEN)

    async def check(self) -> None:
        """One ask, retried for a short window, so a session whose System 1 cannot be reached fails when it opens.
        It sends no `decision` event: a session's stream starts with its first run."""
        window = min(CHECK_WINDOW_S, self._retry_window_s)
        await self._ask("check", "A session is opening.", {"ok": CHECK}, retry_window_s=window, events=False)

    async def ask(
        self,
        purpose: str,
        state: str,
        questions: dict[str, Question],
        *,
        turn: int | None = None,
        subject: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Answer]:
        """One ask, answered by System 1 or not at all: `System1Unavailable` once the retries are spent. ``turn``
        and ``subject`` (what is judged, such as a read's path) go on the event, so a stream can be audited: which
        turn a stuck strike came from, which read was kept. ``timeout_s`` replaces the client's
        per-attempt time limit for this ask."""
        return await self._ask(purpose, state, questions, turn=turn, subject=subject, timeout_s=timeout_s)

    async def _ask(
        self,
        purpose: str,
        state: str,
        questions: dict[str, Question],
        *,
        turn: int | None = None,
        subject: str | None = None,
        timeout_s: float | None = None,
        retry_window_s: float | None = None,
        events: bool = True,
    ) -> dict[str, Answer]:
        started = time.monotonic()
        sent, truncated = bound_output(state, self._max_state_chars)
        backend_name = self.backend.name

        def report(
            ok: bool,
            cached: bool,
            result: Answers | None,
            answers: dict[str, Answer] | None,
            error: str | None = None,
        ) -> None:
            if not events:
                return
            self.emit(
                Decision(
                    purpose=purpose,
                    backend=backend_name,
                    questions=len(questions),
                    ok=ok,
                    cached=cached,
                    cost_usd=result.cost_usd if result else 0.0,
                    input_tokens=result.input_tokens if result else 0,
                    output_tokens=result.output_tokens if result else 0,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=error,
                    truncated=truncated,
                    turn=turn,
                    subject=subject,
                    answers=None if answers is None else {name: compact(a) for name, a in answers.items()},
                )
            )

        # The full state, so two states that differ only in the part the cut removes never share an answer.
        key = hashlib.sha256(
            json.dumps([backend_name, state, {k: q.model_dump() for k, q in questions.items()}]).encode()
        ).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            report(True, True, None, self._cache[key])
            return self._cache[key]
        deadline = started + (self._retry_window_s if retry_window_s is None else retry_window_s)
        delay = RETRY_FIRST_S
        while True:
            # Set once the backend answers, so an attempt whose answers are unusable still reports what it cost.
            result: Answers | None = None
            try:
                # The timeout starts once a slot is held and a backend that spaces its requests lets this one leave, so
                # waiting in either queue never counts as a backend failure.
                async with self._slots:
                    pace = getattr(self.backend, "pace", None)
                    if pace is not None:
                        await pace()
                    limit = max(timeout_s or self._timeout_s, self._least_timeout_s)
                    result = await asyncio.wait_for(self.backend.decide(sent, questions), limit)
                self.usage.input_tokens += result.input_tokens
                self.usage.output_tokens += result.output_tokens
                self.usage.cost_usd += result.cost_usd
                # A wrong-type answer, or a choice that is not an option, counts as missing; the ask is retried.
                answers = {n: a for n, a in result.answers.items() if n in questions and _fits(questions[n], a)}
                if missing := sorted(set(questions) - set(answers)):
                    raise ValueError(f"no usable answer for {', '.join(missing)}")
            except FreeRefused as e:
                self.last_error = str(e)
                report(False, False, result, None, self.last_error)
                self.usage.failed += 1
                raise System1Unavailable(
                    f"The decision model ({backend_name}) did not answer {purpose}: {e}", code=e.code, meter=e.meter
                ) from e
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                report(False, False, result, None, self.last_error)
                if time.monotonic() + delay > deadline:
                    self.usage.failed += 1
                    waited = int(time.monotonic() - started)
                    raise System1Unavailable(
                        f"The decision model ({backend_name}) did not answer {purpose} in {waited}s of retries: "
                        f"{self.last_error}"
                    ) from e
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_LONGEST_S)
                continue
            self.usage.asks += 1
            self._cache[key] = answers
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            report(True, False, result, answers)
            return answers
