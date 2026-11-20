"""A fake Nace console for the free trial, on the fake OpenAI server: Intern's device sign-in, tokens that rotate
with reuse detection, the grant and status routes, revocation, and chat and System 1 behind the free access token.

It follows the console's contract (drex `docs/oauth.md` and `docs/free-trial.md`): OAuth errors as
`{"error": "authorization_pending"}`, the free trial's as `{"error": {"type", "code", "message", "reason"}}`.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs

from tests.fake_openai import FakeOpenAI, Reply, send_json

GRANT = {
    "state": "active",
    "meters": {"system1": {"used_usd": 0.1, "limit_usd": 1.0}, "agent": {"used_usd": 0.5, "limit_usd": 4.0}},
    "bundle": {
        "system2": "nace/glm-5.3",
        "handoff": "nace/deepseek-v4.1-flash",
        "system1": "drex-latest",
        "handoff_tuning": {"threshold": 0.7, "streak": 2, "min_turn": 8},
        "window_tokens": 160000,
        "max_output_tokens": 8192,
    },
}


def free_error(code: str, message: str, reason: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"type": "invalid_request_error", "code": code, "message": message}
    return {"error": {**error, **({"reason": reason} if reason else {})}}


@dataclass
class Refresh:
    family: int
    spent: bool = False


@dataclass
class Access:
    family: int
    expired: bool = False


class FakeConsole(FakeOpenAI):
    def __init__(self, replies: list[Reply] | None = None) -> None:
        super().__init__(replies or [])
        self.lock = threading.Lock()
        self.outcome: str = "granted"
        """What the user does in the browser: `granted`, an OAuth error (`access_denied`, `expired_token`), or a
        console refusal: `not_eligible:<reason>`, `free_revoked`."""
        self.device_status = 200
        """Not 200: device authorization answers that (404 while the trial is switched off)."""
        self.interval = 0
        self.pending = 0
        """Polls answered `authorization_pending` before the outcome."""
        self.slow_down = 0
        """Polls answered `slow_down` before those."""
        self.expires_in = 3600
        self.sign_ins_ended = False
        """Every refresh is refused, as when a sign-in's 90 days are over."""
        self.refresh_delay_s = 0.0
        """How long a refresh takes after the old token is spent: the window two processes could race in."""
        self.authorizations: list[dict[str, str]] = []
        self.rotations = 0
        self.revocations = 0
        self.bearers: list[str] = []
        """The bearer each chat and System 1 request carried."""
        self.refused: list[str] = []
        """The meter of each chat and System 1 request refused as `free_exhausted`."""
        self.refreshes: dict[str, Refresh] = {}
        self.accesses: dict[str, Access] = {}
        self.revoked: set[int] = set()
        self.status = {"grants": "open", "inference": "open", "message": None}
        self.grant: dict[str, Any] = copy.deepcopy(GRANT)
        """What `/v1/free/grant` answers; its meters grow by `charge` with each call, as the headers say."""
        self.charge = {"system2": 0.0, "system1": 0.0}
        """What one chat (`system2`) or System 1 call costs its meter, in USD."""
        self._issued = 0
        self._families = 0

    def spend(self, meter: str, used_usd: float) -> None:
        with self.lock:
            self.grant["meters"][meter]["used_usd"] = used_usd

    def headers(self, route: str) -> dict[str, str]:
        meters = self.grant["meters"]
        with self.lock:
            meter = meters["agent" if route == "system2" else "system1"]
            meter["used_usd"] = round(meter["used_usd"] + self.charge[route], 6)
            return {
                "x-nace-free-used": ";".join(f"{name}={meters[name]['used_usd']}" for name in ("system1", "agent")),
                "x-nace-free-limit": ";".join(f"{name}={meters[name]['limit_usd']}" for name in ("system1", "agent")),
            }

    @property
    def origin(self) -> str:
        return self.url.removesuffix("/v1")

    @property
    def env(self) -> dict[str, str]:
        """What points a harness process's `nace` provider at this console."""
        return {"NACE_BASE_URL": self.url}

    def expire_access(self) -> None:
        """Every access token issued so far has expired, by the console's clock."""
        with self.lock:
            for access in self.accesses.values():
                access.expired = True

    def _issue(self, family: int) -> dict[str, Any]:
        self._issued += 1
        access, refresh = f"nace_at_test_{self._issued:04d}", f"nace_rt_test_{self._issued:04d}"
        self.accesses[access] = Access(family)
        self.refreshes[refresh] = Refresh(family)
        return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": self.expires_in}

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        """Whether the bearer is a live access token; if not, the refusal is sent."""
        token = handler.headers.get("Authorization", "").removeprefix("Bearer ")
        with self.lock:
            access = self.accesses.get(token)
            if access is None:
                send_json(handler, free_error("invalid_token", "Not an Intern access token."), 401)
            elif access.family in self.revoked:
                send_json(handler, free_error("free_revoked", "This free grant was revoked."), 403)
            elif access.expired:
                send_json(handler, free_error("token_expired", "Access token expired."), 401)
            else:
                return True
        return False

    def _in_plan(self, handler: BaseHTTPRequestHandler, body: bytes) -> bool:
        """Whether the chat names one of the bundle's agent models, by its exact id; if not, the refusal is sent."""
        with self.lock:
            bundle = self.grant["bundle"]
            served = {bundle["system2"], bundle["handoff"]}
        if json.loads(body or b"{}").get("model") in served:
            return True
        message = "This model isn't in the free plan. Send the model the free bundle names."
        send_json(handler, free_error("model_not_in_plan", message), 403)
        return False

    def _spent(self, handler: BaseHTTPRequestHandler, meter: str) -> bool:
        """Whether `meter` has nothing left, as the console reserves before a call; if so, the refusal is sent."""
        with self.lock:
            used, limit = (self.grant["meters"][meter][k] for k in ("used_usd", "limit_usd"))
            if used < limit:
                return False
            self.refused.append(meter)
        error = free_error("free_exhausted", f"The free {meter} credit is used up.")
        error["error"]["meter"] = meter
        send_json(handler, error, 402)
        return True

    def answer(self, handler: BaseHTTPRequestHandler, body: bytes | None) -> bool:
        path = handler.path
        if body is None:
            if path == "/v1/free/status":
                send_json(handler, self.status)
            elif path == "/v1/free/grant":
                if self._authorized(handler):
                    with self.lock:
                        send_json(handler, self.grant)
            else:
                return False
            return True
        if path in ("/v1/chat/completions", "/v1/systemone"):
            self.bearers.append(handler.headers.get("Authorization", "").removeprefix("Bearer "))
            # An authorized request for a bundle model on a meter with credit left goes on to the fake model.
            if not self._authorized(handler):
                return True
            if "chat" in path and not self._in_plan(handler, body):
                return True
            return self._spent(handler, "agent" if "chat" in path else "system1")
        form = {name: values[0] for name, values in parse_qs(body.decode()).items()}
        if path == "/oauth/device_authorization":
            self._device(handler, form)
        elif path == "/oauth/token":
            if form.get("client_id") != "intern":
                send_json(handler, {"error": "invalid_client"}, 401)
            elif form.get("grant_type") == "refresh_token":
                self._refresh(handler, form.get("refresh_token", ""))
            else:
                self._poll(handler)
        elif path == "/oauth/revoke":
            with self.lock:
                if (refresh := self.refreshes.get(form.get("token", ""))) is not None:
                    self.revoked.add(refresh.family)
                    self.revocations += 1
            send_json(handler, {})
        else:
            return False
        return True

    def _device(self, handler: BaseHTTPRequestHandler, form: dict[str, str]) -> None:
        self.authorizations.append(form)
        if self.device_status != 200:
            handler.send_error(self.device_status)
            return
        send_json(
            handler,
            {
                "device_code": "nace_dc_test_device",
                "user_code": "ABCD-EFGH",
                "verification_uri": f"{self.origin}/oauth/device",
                "verification_uri_complete": f"{self.origin}/oauth/device?user_code=ABCD-EFGH",
                "expires_in": 600,
                "interval": self.interval,
            },
        )

    def _poll(self, handler: BaseHTTPRequestHandler) -> None:
        with self.lock:
            if self.slow_down:
                self.slow_down -= 1
                send_json(handler, {"error": "slow_down"}, 400)
            elif self.pending:
                self.pending -= 1
                send_json(handler, {"error": "authorization_pending"}, 400)
            elif self.outcome == "granted":
                self._families += 1
                send_json(handler, self._issue(self._families))
            elif self.outcome.startswith("not_eligible:"):
                reason = self.outcome.split(":", 1)[1]
                send_json(handler, free_error("not_eligible", "This account cannot get free credit.", reason), 403)
            elif self.outcome == "free_revoked":
                send_json(handler, free_error("free_revoked", "This free grant was revoked."), 403)
            else:
                send_json(handler, {"error": self.outcome}, 400)

    def _refresh(self, handler: BaseHTTPRequestHandler, token: str) -> None:
        with self.lock:
            refresh = self.refreshes.get(token)
            if refresh is None or refresh.family in self.revoked or self.sign_ins_ended:
                answer, status = {"error": "invalid_grant"}, 400
            elif refresh.spent:
                self.revoked.add(refresh.family)  # reuse: the whole sign-in is revoked
                answer, status = {"error": "invalid_grant"}, 400
            else:
                refresh.spent = True
                self.rotations += 1
                answer, status = self._issue(refresh.family), 200
        time.sleep(self.refresh_delay_s)
        send_json(handler, answer, status)
