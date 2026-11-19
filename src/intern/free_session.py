"""Which credential each system runs on: the free credit while its meter lasts, else the user's own configuration.

Two meters, two systems: `agent` pays for System 2 and the hand-over model, `system1` for System 1. At a session's
open and at the start of each run, a system runs on free credit when `[free] use` is true, a grant is signed in and
active, and its meter has more left than the overdraft (so a run never starts on credit that cannot finish it).
Otherwise it runs on the user's own configuration, and with none the session opens unbound, saying why.

On free credit a system runs on the grant's bundle, not on config: its models are fixed, and picking another is
refused with `FreeLocked`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx

from intern import names
from intern.models.catalog import FreeInfo, FreeSources
from intern.models.registry import ConfigError, ProviderSpec
from intern.nace_free import Bundle, ConsoleUnavailable, FreeCredential, FreeGrant, Meters, Refusal, free_credential

if TYPE_CHECKING:
    from intern.session import Session

System = Literal["agent", "system1"]
SYSTEMS: tuple[System, ...] = ("agent", "system1")
ROLE_SYSTEM: dict[str, System] = {"system2": "agent", "handoff": "agent", "system1": "system1"}
"""The meter each picker's model is paid from."""
METER_NAMES: dict[System, str] = {"agent": "agent", "system1": "decision model"}
LOCKED = "free_locked"


class FreeLocked(ConfigError):
    """A model picked for a system that runs on free credit, whose models are the bundle's."""

    code = LOCKED


def short(ref: str) -> str:
    return ref.partition("/")[2] or ref


@dataclass
class FreePlan:
    """The free credit as a session finds it: whether it is in use, the grant, and why none could be read."""

    use: bool | None
    credential: FreeCredential | None = None
    grant: FreeGrant | None = None
    refusal: Refusal | None = None

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


@dataclass
class FreeSession:
    """What a session's systems run on, and the locks the free credit puts on its pickers."""

    plan: FreePlan
    source: FreeSources
    switch: Switch | None = None
    """Moves a system of a session to the user's own configuration; whether it could."""

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
        """A system whose meter no longer runs a run moves to the user's own configuration, when there is one."""
        for system in SYSTEMS:
            if self.switch is None or not self.on_free(system) or self.plan.runs(system):
                continue
            self.source = self.source.model_copy(update={system: "own"})
            if not await self.switch(session, system):
                self.source = self.source.model_copy(update={system: "free"})

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
