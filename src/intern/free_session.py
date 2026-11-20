"""Which credential each system runs on: the free credit while its meter lasts, else the user's own configuration.

Two meters, two systems: `agent` pays for System 2 and the hand-over model, `system1` for System 1. At a session's
open and at the start of each run, a system runs on free credit when `[free] use` is true, a grant is signed in and
active, and its meter has more left than the overdraft (so a run never starts on credit that cannot finish it).
Otherwise it runs on the user's own configuration, and with none the session opens unbound, saying why.

On free credit a system runs on the grant's bundle, not on config: its models are fixed, and picking another is
refused with `FreeLocked`.

A credit that runs out during a run (the console refuses it for good, `registry.FREE_FINAL`) is not retried. System 1
sends the ask again on the user's own console key, the same model; System 2 sends the request again on the user's own
Fireworks key for the same model. Without one, the run stops, and the next starts on the user's own configuration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx

from intern import names
from intern.decisions import Answers, DecisionBackend, FreeRefused, Question
from intern.events import EventSink, FreeNotice
from intern.hooks import RunOutcome
from intern.models.catalog import FreeInfo, FreeSources
from intern.models.registry import FREE_FINAL, ConfigError, ProviderSpec
from intern.nace_free import Bundle, ConsoleUnavailable, FreeCredential, FreeGrant, Meters, Refusal, free_credential
from intern.providers.base import Provider, ProviderError, ProviderRequest, StreamItem

if TYPE_CHECKING:
    from intern.session import Session

System = Literal["agent", "system1"]
SYSTEMS: tuple[System, ...] = ("agent", "system1")
ROLE_SYSTEM: dict[str, System] = {"system2": "agent", "handoff": "agent", "system1": "system1"}
"""The meter each picker's model is paid from."""
METER_NAMES: dict[System, str] = {"agent": "agent", "system1": "decision model"}
LOCKED = "free_locked"
LOW_SHARES = (0.2, 0.05)
"""What is left of a meter, as a share of its limit, when the user hears that it is running low."""
FIREWORKS_MODELS = "fireworks/accounts/fireworks/models/"
# shortcut: the console's routes for the bundle's models are Fireworks' names (`glm-5p3`), and these the harness's own
# names for them; a model the console serves under another name has no own route until it is listed here.
FIREWORKS_NAMES = {"glm-5.3": "glm-5p3", "deepseek-v4.1-flash": "deepseek-v4p1-flash-1017"}


class FreeLocked(ConfigError):
    """A model picked for a system that runs on free credit, whose models are the bundle's."""

    code = LOCKED


def short(ref: str) -> str:
    return ref.partition("/")[2] or ref


def own_route(ref: str) -> str:
    """The user's own route to the free credit's model `ref`: Fireworks, which serves the console's models."""
    name = ref.rpartition("/")[2]
    return FIREWORKS_MODELS + FIREWORKS_NAMES.get(name, name)


@dataclass
class FreePlan:
    """The free credit as a session finds it: whether it is in use, the grant, and why none could be read."""

    use: bool | None
    credential: FreeCredential | None = None
    grant: FreeGrant | None = None
    refusal: Refusal | None = None
    spent: set[System] = field(default_factory=set)
    """Systems whose free credit the console refused for good during the session, whatever the meters say."""

    @property
    def bundle(self) -> Bundle | None:
        return self.grant.bundle if self.grant is not None else None

    def meters(self) -> Meters | None:
        """The latest the console said: a response's headers, else the grant."""
        if self.credential is not None and self.credential.meters is not None:
            return self.credential.meters
        return self.grant.meters if self.grant is not None else None

    def runs(self, system: System) -> bool:
        """Whether `system` may start a run on free credit now."""
        meters = self.meters()
        return (
            self.use is True
            and system not in self.spent
            and self.grant is not None
            and self.grant.state == "active"
            and meters is not None
            and getattr(meters, system).runs()
        )

    def why_not(self, system: System) -> str | None:
        """Why `system` runs on the user's own configuration though they use the free credit; None when they do not
        use it, or it runs on it."""
        if self.use is not True or self.runs(system):
            return None
        if self.credential is None:
            return f"the free credit is not signed in (`{names.NAME} free start`)"
        if self.grant is None:
            refused = self.refusal
            return f"the free credit could not be read ({refused.message or refused.code if refused else 'no answer'})"
        if self.grant.state == "paused":
            return "the free trial is paused"
        return f"the free {METER_NAMES[system]} credit is used up"

    def sources(self) -> FreeSources:
        return FreeSources(
            agent="free" if self.runs("agent") else "own", system1="free" if self.runs("system1") else "own"
        )

    def info(self, sources: FreeSources | None = None) -> FreeInfo | None:
        """`models --json`'s `free`; None for an install that never signed in to it."""
        if self.use is None and self.credential is None:
            return None
        if self.grant is not None:
            state = self.grant.state
        elif self.credential is None:
            state = "signed_out"
        else:
            state = self.refusal.code if self.refusal is not None else "unavailable"
        return FreeInfo(
            state=state,
            meters=self.meters(),
            bundle=self.bundle,
            use=self.use,
            paused=state == "paused",
            source=sources or self.sources(),
        )


async def free_plan(use: bool | None, spec: ProviderSpec, env: Mapping[str, str], *, read: bool = False) -> FreePlan:
    """The free credit for the `nace` provider's console. The grant is read when the credit is in use, or with
    `read` (a listing shows it while paused too). A console that does not answer is a refusal: the systems run on
    the user's own configuration. A `$NACE_BASE_URL` that is not a Nace console is a ConfigError while it is in use."""
    if use is None and not read:
        return FreePlan(use=use)
    credential = free_credential(spec, env)
    if credential is None or not (use is True or read):
        return FreePlan(use=use, credential=credential)
    try:
        found = await credential.grant()
    except (httpx.HTTPError, ConsoleUnavailable) as error:
        found = Refusal(code="unavailable", message=f"the console could not be reached ({type(error).__name__})")
    except ConfigError:
        if use is True:
            raise
        found = Refusal(code="unavailable", message="the console's address is not a Nace console")
    if isinstance(found, FreeGrant):
        return FreePlan(use=use, credential=credential, grant=found)
    # A refused refresh or a revoked grant forgets the sign-in.
    signed_in = credential.store.signed_in(credential.console)
    return FreePlan(use=use, credential=credential if signed_in else None, refusal=found)


Switch = Callable[["Session", System], Awaitable[bool]]
Refused = Callable[[System, str, str | None, str | None], None]
"""A system's free credit was refused for good with a code and meter; the model on the user's own key it moved to,
or None."""

SWITCH_REASONS = {
    "free_paused": "The free trial is paused",
    "free_revoked": "The free credit was revoked",
    "model_not_in_plan": "The free credit no longer serves this model",
}


def _name(ref: str) -> str:
    return ref.rpartition("/")[2] or ref


@dataclass
class FreeSession:
    """What a session's systems run on, and the locks the free credit puts on its pickers."""

    plan: FreePlan
    source: FreeSources
    switch: Switch | None = None
    """Moves a system of a session to the user's own configuration; whether it could."""
    next_system2: Callable[[], str | None] | None = None
    """The System 2 of the user's own configuration a run could start on now; None when none can."""
    emit: EventSink | None = None
    """Where notices go: set by `watching`."""
    _moved: set[System] = field(default_factory=set)
    """Systems that moved to the user's own key for the free model during a run; their own configuration takes over
    at the next run."""
    _codes: dict[System, str] = field(default_factory=dict)
    _told: set[tuple[Any, ...]] = field(default_factory=set)

    def __post_init__(self) -> None:
        # A meter already low when the session opens was low before it: the session tells only what crosses later.
        self._low(tell=False)

    def watching(self, emit: EventSink) -> EventSink:
        """`emit`, and after each event a notice for a meter that has crossed a low share since the last one."""
        self.emit = emit

        def sink(event: Any) -> None:
            emit(event)
            self._low(tell=True)

        return sink

    def _tell(self, key: tuple[Any, ...], notice: FreeNotice) -> None:
        if key not in self._told:
            self._told.add(key)
            if self.emit is not None:
                self.emit(notice)

    def _low(self, *, tell: bool) -> None:
        meters = self.plan.meters()
        if meters is None:
            return
        for system in SYSTEMS:
            meter = getattr(meters, system)
            left = meter.limit_usd - meter.used_usd
            crossed = [share for share in LOW_SHARES if meter.limit_usd > 0 and left <= share * meter.limit_usd]
            # A spent meter is told by the switch to own keys, or by the error that ends the run.
            if not crossed or not self.on_free(system) or (tell and not meter.runs()):
                continue
            keys = [("low", system, share) for share in crossed]
            if not tell:
                self._told.update(keys)
                continue
            if all(key in self._told for key in keys):
                continue
            self._told.update(keys[:-1])
            text = (
                f"Free {METER_NAMES[system]} credit: {min(crossed):.0%} or less left "
                f"(${max(left, 0):.2f} of ${meter.limit_usd:.2f})"
            )
            self._tell(keys[-1], FreeNotice(kind="low", meter=system, text=text))

    def _tell_switch(self, system: System, model: str) -> None:
        paused = self.plan.grant is not None and self.plan.grant.state == "paused"
        code = self._codes.get(system) or ("free_paused" if paused else "")
        why = SWITCH_REASONS.get(code, f"Free {METER_NAMES[system]} credit used up")
        text = f"{why}; continuing on your keys with {_name(model)}"
        self._tell(("switch", system), FreeNotice(kind="switch", meter=system, text=text))

    def refused(self, system: System, code: str, meter: str | None, moved_to: str | None) -> None:
        """The console refused `system`'s free credit for good (`code`, naming `meter`): no run starts on what it
        refused again this session. With `moved_to`, the system went on with that model on the user's own key."""
        if meter in SYSTEMS:
            hit: set[System] = {"agent" if meter == "agent" else "system1"}
        else:
            hit = set(SYSTEMS) if code in ("free_paused", "free_revoked") else {system}
        self.plan.spent |= hit
        for spent in hit:
            self._codes.setdefault(spent, code)
        if moved_to is not None:
            self.source = self.source.model_copy(update={system: "own"})
            self._moved.add(system)
            self._tell_switch(system, moved_to)

    def after_run(self, outcome: RunOutcome) -> None:
        """A run System 2's free credit ended for good says which model the next run will use (`error.next`)."""
        error = outcome.error
        if error is None or error.code not in FREE_FINAL:
            return
        system: System = "system1" if outcome.reason == "system1_unavailable" else "agent"
        self.refused(system, error.code, error.meter, None)
        if system == "agent" and self.next_system2 is not None:
            error.next = self.next_system2()

    @property
    def bundle(self) -> Bundle | None:
        return self.plan.bundle

    def on_free(self, system: System) -> bool:
        return getattr(self.source, system) == "free"

    def locked(self, role: str) -> str | None:
        """The model `role` (`system2`, `handoff`, `system1`) is fixed to, while its system runs on free credit."""
        bundle = self.bundle
        if bundle is None or not self.on_free(ROLE_SYSTEM[role]):
            return None
        return {"system2": bundle.system2, "handoff": bundle.handoff, "system1": bundle.system1}[role]

    def message(self) -> str:
        bundle = self.bundle
        assert bundle is not None
        models = f"{short(bundle.system2)}, {short(bundle.handoff)} and {short(bundle.system1)}"
        return f"This runs on free credit with {models}. To pick models, use your own keys (`{names.NAME} free pause`)."

    def check(self, role: str, ref: str | None) -> bool:
        """Whether `role` is fixed to `ref`, so picking it changes nothing; FreeLocked when it is fixed to another
        model (or `ref` is None, no hand-over); False when the role is not fixed."""
        fixed = self.locked(role)
        if fixed is None:
            return False
        if ref != fixed and not (role == "system1" and ref == f"drex:{fixed}"):
            raise FreeLocked(self.message())
        return True

    async def before_run(self, session: Session) -> None:
        """A system whose meter no longer runs a run moves to the user's own configuration, when there is one; so does
        one that moved to the user's key for the free model during the last run (failing that, it stays there)."""
        for system in SYSTEMS:
            if self.switch is None:
                continue
            if system in self._moved:
                self._moved.discard(system)
                await self.switch(session, system)
                continue
            if not self.on_free(system) or self.plan.runs(system):
                continue
            self.source = self.source.model_copy(update={system: "own"})
            if not await self.switch(session, system):
                self.source = self.source.model_copy(update={system: "free"})
                continue
            self._tell_switch(system, session.deps.system2.ref if system == "agent" else session.system1)

    def wire(self) -> dict[str, Any]:
        """`usage_update._meta.nace.free`: the meters as the console last gave them, and each system's source."""
        meters = self.plan.meters()
        wired: dict[str, Any] = {"source": self.source.model_dump()}
        if meters is not None:
            wired["meters"] = {
                name: {"usedUsd": meter.used_usd, "limitUsd": meter.limit_usd}
                for name, meter in (("system1", meters.system1), ("agent", meters.agent))
            }
        return wired


class FreeSystem1:
    """System 1 on the free credit. When the console refuses the credit for good, the ask is sent again with the
    user's own console key, on the same model, and so is every ask after it: the run does not notice. With no own
    key the refusal goes up, and the run stops."""

    def __init__(self, free: DecisionBackend, own: DecisionBackend | None, model: str, refused: Refused) -> None:
        self.name = free.name
        self._free, self._own, self._model, self._refused = free, own, model, refused
        self._active = free

    async def window(self) -> int | None:
        lookup = getattr(self._active, "window", None)
        return await lookup() if lookup is not None else None

    async def pace(self) -> None:
        pace = getattr(self._active, "pace", None)
        if pace is not None:
            await pace()

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        asked = self._active
        try:
            return await asked.decide(state, questions)
        except FreeRefused as refused:
            if asked is not self._free:
                raise
            if self._active is self._free:  # the first ask refused: the asks in flight beside it are refused too
                self._refused("system1", refused.code, refused.meter, self._model if self._own else None)
            if self._own is None:
                raise
            self._active = self._own
        return await self._own.decide(state, questions)


@dataclass
class OwnRoute:
    """The free credit's model on a key of the user's own."""

    ref: str
    wire_model: str
    provider: Provider


class FreeModel:
    """System 2 or the hand-over model on the free credit. When the console refuses the credit for good, the request
    is sent again with the user's own key for the same model (`own`), and so is every request after it: nothing of the
    refused reply was streamed, so the run goes on. With no such key the refusal goes up, and the run ends."""

    def __init__(self, free: Provider, own: Callable[[], OwnRoute | None], refused: Refused) -> None:
        self._free, self._own, self._refused = free, own, refused
        self._moved: OwnRoute | None = None

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamItem]:
        if self._moved is None:
            try:
                async for item in self._free.stream(request):
                    yield item
                return
            except ProviderError as error:
                if error.code not in FREE_FINAL:
                    raise
                if self._moved is None:  # else a request beside it was refused first, and moved
                    own = self._own()
                    self._refused("agent", error.code, error.meter, own.ref if own else None)
                    if own is None:
                        raise
                    self._moved = own
        moved = self._moved
        async for item in moved.provider.stream(request.model_copy(update={"model": moved.wire_model})):
            yield item
