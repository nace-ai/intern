"""The free trial's credential against a fake console: the device sign-in and each refusal, tokens kept where only
their owner reads them, renewal through `invalidate()` on a real request, one rotation however many processes
refresh at once, and a refused refresh or a sign-out forgetting the sign-in."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from intern import decisions
from intern.decisions import DecisionsApiBackend, NoulQuestion
from intern.free_commands import free_main
from intern.messages import UserMessage
from intern.models import registry
from intern.models.registry import PROVIDERS, ConfigError, nace_credential, resolve_endpoint
from intern.nace_free import DeviceSignIn, FreeCredential, Tokens
from intern.providers.base import NO_RETRIES, Completed, ProviderError, ProviderRequest
from intern.providers.openai_compat import OpenAICompatProvider
from tests.fake_console import GRANT, FakeConsole
from tests.test_secrets_commands import FAKE_SECURITY

CONSENT = "2026-11-18"


@pytest.fixture
def console(monkeypatch):
    """The fake console, which runs on http://127.0.0.1: the host check lets it through and nothing else."""
    with FakeConsole() as server:
        real = registry.check_host

        def check(source: registry.CredentialSource, url: str) -> None:
            if not url.startswith(f"{server.origin}/"):
                real(source, url)

        monkeypatch.setattr(registry, "check_host", check)
        monkeypatch.setattr(decisions, "check_host", check)
        yield server


@pytest.fixture
def env(tmp_path, monkeypatch, console) -> dict[str, str]:
    """A macOS-like host whose `security` keeps items as files, a harness home, and the fake console."""
    monkeypatch.setattr(sys, "platform", "darwin")
    bin_dir, keychain, home = tmp_path / "bin", tmp_path / "keychain", tmp_path / "home"
    for folder in (bin_dir, keychain, home):
        folder.mkdir()
    (bin_dir / "security").write_text(FAKE_SECURITY)
    (bin_dir / "security").chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_KEYCHAIN": str(keychain),
        "INTERN_HOME": str(home),
        **console.env,
    }


def home_of(env: dict[str, str]) -> Path:
    return Path(env["INTERN_HOME"])


def keychain(env: dict[str, str]) -> list[str]:
    return sorted(path.name for path in Path(env["FAKE_KEYCHAIN"]).iterdir())


def free(argv: list[str], env: dict[str, str], capsys) -> tuple[int, list[dict]]:
    code = free_main([*argv, "--json"], env)
    return code, [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def start(env: dict[str, str], capsys) -> tuple[int, list[dict]]:
    return free(["start", "--no-browser", "--accept", CONSENT], env, capsys)


def ask() -> ProviderRequest:
    return ProviderRequest(
        model="glm-5.3", messages=[UserMessage(content="hi")], max_output_tokens=64, retries=NO_RETRIES
    )


async def reply(provider: OpenAICompatProvider) -> Completed:
    items = [item async for item in provider.stream(ask())]
    assert isinstance(items[-1], Completed)
    return items[-1]


def stored_access(env: dict[str, str]) -> str:
    return json.loads((home_of(env) / "free-access.json").read_text())["access_token"]


def test_start_signs_in_keeps_the_tokens_private_and_turns_the_free_credit_on(env, console, capsys):
    home = home_of(env)
    (home / "config.toml").write_text('# mine\nsystem2 = "openai/gpt-5"  # the usual\n')
    code, lines = start(env, capsys)
    assert code == 0
    assert [line["step"] for line in lines] == ["code", "granted"]
    assert lines[0]["user_code"] == "ABCD-EFGH" and lines[0]["url"].endswith("/oauth/device?user_code=ABCD-EFGH")
    assert lines[1]["grant"] == GRANT
    sent = console.authorizations[0]
    assert (sent["client_id"], sent["scope"], sent["consent_version"]) == ("intern", "inference", CONSENT)
    assert sent["install_id"] and sent["client_version"]

    assert keychain(env) == ["NACE_FREE_REFRESH_TOKEN"]
    for name in ("free.json", "free-access.json"):
        assert stat.S_IMODE((home / name).stat().st_mode) == 0o600
    assert "nace_rt_" not in (home / "free.json").read_text()  # the refresh token is in the keychain
    assert (home / "config.toml").read_text().startswith('# mine\nsystem2 = "openai/gpt-5"  # the usual\n')

    code, [report] = free(["status"], env, capsys)
    assert code == 0
    assert report == {"signed_in": True, "use": True, "status": console.status, "grant": GRANT, "error": None}


REFUSALS = [
    *(
        ("not_eligible:" + reason, "not_eligible", reason)
        for reason in (
            "account_too_new",
            "email_unverified",
            "network_blocked",
            "grants_paused",
            "invite_only",
            "identity_required",
            "identity_used",
            "account_flagged",
        )
    ),
    ("free_revoked", "free_revoked", None),
    ("access_denied", "access_denied", None),
    ("expired_token", "expired_token", None),
]


@pytest.mark.parametrize(("outcome", "code", "reason"), REFUSALS, ids=[r[0] for r in REFUSALS])
def test_a_refused_sign_in_says_why_and_keeps_nothing(env, console, capsys, outcome, code, reason):
    console.outcome = outcome
    exit_code, lines = start(env, capsys)
    assert exit_code == 1
    assert [line["step"] for line in lines] == ["code", "refused"]
    assert (lines[1]["code"], lines[1]["reason"]) == (code, reason) and lines[1]["message"]
    home = home_of(env)
    assert not (home / "free.json").exists() and not (home / "free-access.json").exists() and keychain(env) == []
    assert "[free]" not in (home / "config.toml").read_text() if (home / "config.toml").exists() else True


def test_a_console_that_takes_no_sign_ins_is_a_refusal_before_any_code(env, console, capsys):
    console.device_status = 404  # the free trial is switched off
    exit_code, lines = start(env, capsys)
    assert exit_code == 1 and [(line["step"], line["code"]) for line in lines] == [("refused", "unavailable")]


async def test_polling_keeps_to_the_consoles_interval_and_slows_down_when_asked(console):
    console.interval, console.slow_down, console.pending = 2, 1, 1
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    sign_in = DeviceSignIn(console.origin, consent_version=CONSENT, install_id="test-install", sleep=sleep)
    await sign_in.start()
    assert isinstance(await sign_in.wait(), Tokens)
    assert waits == [2, 7, 7]  # slow_down adds five seconds for every poll after it


@pytest.mark.parametrize("in_keychain", [True, False], ids=["keychain", "file"])
async def test_an_expired_access_token_on_a_request_is_renewed_and_the_request_sent_again(
    env, console, capsys, monkeypatch, in_keychain
):
    if not in_keychain:
        monkeypatch.setattr(sys, "platform", "linux")  # no keychain: the refresh token goes to free.json
    assert (await asyncio.to_thread(start, env, capsys))[0] == 0
    assert ("refresh_token" in json.loads((home_of(env) / "free.json").read_text())) is not in_keychain
    first = stored_access(env)
    console.expire_access()

    provider = OpenAICompatProvider(resolve_endpoint("nace/glm-5.3", env, free=True))
    assert (await reply(provider)).message.text.strip() == "done"
    renewed = stored_access(env)
    assert console.bearers == [first, renewed] and renewed != first and console.rotations == 1

    # System 1 on the console shares the renewed token: no second rotation.
    credential = nace_credential(PROVIDERS["nace"], env, free=True)
    assert credential is not None
    backend = DecisionsApiBackend(console.url, credential, ["drex-latest"])
    await backend.decide("state", {"ok": NoulQuestion(instructions="Is it?")})
    assert console.bearers[-1] == renewed and console.rotations == 1


RACER = """
import asyncio, json, sys, time
from intern import nace_free
from intern.models import registry

env, origin, start_at = json.loads(sys.argv[1]), sys.argv[2], float(sys.argv[3])
real = registry.check_host
registry.check_host = lambda source, url: None if url.startswith(origin + "/") else real(source, url)
credential = nace_free.free_credential(registry.PROVIDERS["nace"], env)
while time.time() < start_at:
    time.sleep(0.005)
grant = asyncio.run(nace_free.fetch_grant(credential))
token = asyncio.run(credential.bearer())
print(json.dumps({"state": getattr(grant, "state", None), "token": token and token.get_secret_value()}))
"""


def test_two_processes_refreshing_at_once_rotate_once_and_both_keep_working(env, console, capsys, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # the refresh token in free.json, which any OS's process reads
    console.expires_in = 0  # the stored access token is stale at once, so both processes must renew it
    assert start(env, capsys)[0] == 0
    console.expires_in, console.refresh_delay_s = 3600, 0.5
    before = console.rotations
    start_at = str(time.time() + 3)
    racers = [
        subprocess.Popen(
            [sys.executable, "-c", RACER, json.dumps(env), console.origin, start_at],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = []
    for racer in racers:
        out, err = racer.communicate(timeout=60)
        assert racer.returncode == 0, err
        results.append(json.loads(out))
    assert console.rotations - before == 1 and console.revoked == set()
    assert results[0] == results[1] == {"state": "active", "token": stored_access(env)}


async def test_a_refused_refresh_forgets_the_sign_in(env, console, capsys):
    assert (await asyncio.to_thread(start, env, capsys))[0] == 0
    console.sign_ins_ended = True
    console.expire_access()
    credential = nace_credential(PROVIDERS["nace"], env, free=True)
    assert isinstance(credential, FreeCredential)
    with pytest.raises(ProviderError):
        await reply(OpenAICompatProvider(resolve_endpoint("nace/glm-5.3", env, free=True)))
    assert credential.refused is not None and credential.refused.code == "invalid_grant"
    home = home_of(env)
    assert not (home / "free.json").exists() and not (home / "free-access.json").exists() and keychain(env) == []
    code, [report] = await asyncio.to_thread(free, ["status"], env, capsys)
    assert (code, report["signed_in"], report["grant"]) == (0, False, None)


def test_sign_out_revokes_the_sign_in_on_the_console_and_forgets_it(env, console, capsys):
    assert start(env, capsys)[0] == 0
    code, [result] = free(["sign-out"], env, capsys)
    assert (code, result) == (0, {"ok": True, "revoked": True})
    assert console.revoked == {1}
    home = home_of(env)
    assert not (home / "free.json").exists() and not (home / "free-access.json").exists() and keychain(env) == []


def test_pause_and_resume_keep_the_config_file_as_written(env, capsys):
    home = home_of(env)
    written = '# mine\nsystem2 = "openai/gpt-5"  # the usual\n\n[free]\nuse = {}\n'
    (home / "config.toml").write_text(written.format("true"))
    assert free(["pause"], env, capsys) == (0, [{"ok": True, "use": False}])
    assert (home / "config.toml").read_text() == written.format("false")
    assert free(["resume"], env, capsys) == (0, [{"ok": True, "use": True}])
    assert free(["status"], env, capsys)[1][0]["use"] is True


def test_the_real_host_check_sends_nothing_to_the_fake_console(tmp_path, capsys):
    with FakeConsole() as console:
        env = {"INTERN_HOME": str(tmp_path), **console.env}
        code, [error] = free(["start", "--no-browser", "--accept", CONSENT], env, capsys)
        assert code == 2 and "sent only to" in error["error"]
        # A sign-in kept for that console is refused before its refresh token could leave.
        (tmp_path / "free.json").write_text(json.dumps({"console": console.origin, "refresh_token": "nace_rt_test_0"}))
        with pytest.raises(ConfigError, match="sent only to"):
            resolve_endpoint("nace/glm-5.3", env, free=True)
        assert console.authorizations == [] and console.rotations == 0 and console.bearers == []
