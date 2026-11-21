"""Password managers the secrets store knows by name, on top of the plain `[secrets]` entries.

Config keeps the shapes `secret_store` reads: a provider only builds the command entry for a reference and
recognises one it built (or one written by hand), so `list` and the screens can say "1Password ·
op://Work/GitHub/token" instead of a raw command. Values never pass through here; Bitwarden's unlock does (below).

Bitwarden's CLI reads nothing until the vault is unlocked, and an unlock is a session key (`BW_SESSION`).
`unlock_bitwarden` turns the master password into one and keeps it in the keychain, so every Intern process (the
terminal UI, an editor's agent) reads with it; `secret_store` hands it to `bw` and to nothing else.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from intern import names
from intern.secret_store import (
    BITWARDEN_SESSION,
    BITWARDEN_TOOL,
    KEYCHAIN,
    SecretFailure,
    StoreEntry,
    bitwarden_env,
    keychain_command,
    keychain_delete,
    keychain_write,
)

ProviderId = Literal["keychain", "1password", "bitwarden", "command"]
BitwardenState = Literal["unauthenticated", "locked", "unlocked"]

ONE_PASSWORD_TOOL = "op"
_OP_NAME = r"[A-Za-z0-9._-](?:[A-Za-z0-9 ._-]*[A-Za-z0-9._-])?"
_OP_REFERENCE = re.compile(rf"^op://{_OP_NAME}(?:/{_OP_NAME}){{2,3}}(?:\?[a-z-]+=[A-Za-z0-9_-]+)?$")
"""1Password's secret reference syntax: vault/item/field, or vault/item/section/field. A name outside this charset
is written as the item's id. MAX_REFERENCE_CHARS bounds it."""
MAX_REFERENCE_CHARS = 1024
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")
"""What `op --account` takes: a shorthand, sign-in address or ID. Never starts with `-`, so it can't be a flag."""

BITWARDEN_FIELDS = ("password", "username", "notes")
"""What `bw get` reads from an item by name. A TOTP code changes every 30 seconds, so it is not one of them."""
_BW_REFERENCE = re.compile(r"^bw://([A-Za-z0-9][A-Za-z0-9 ._-]{0,199})(?:/(password|username|notes))?$")
"""bw://<item id or exact name>[/field]: the item never starts with `-`, so it can't be a flag."""
UNLOCK_TIMEOUT_S = 60.0


class ProviderStatus(BaseModel):
    """One place `intern secrets` can keep or find a secret, and whether it works on this host now."""

    id: ProviderId
    label: str
    available: bool
    detail: str = ""
    """Why it is not available, or what it needs first, and what to do; empty when it works now."""
    stores_values: bool = False
    """Whether `intern secrets set NAME` can save a typed value there."""
    needs_unlock: bool = False
    """Installed and signed in, but locked: `intern secrets unlock <id>` opens it."""
    reference_hint: str | None = None
    """What a reference to an existing item looks like, for `set NAME --ref`."""


class Linked(BaseModel):
    """What a command entry points at, when a known provider built it."""

    provider: ProviderId
    reference: str
    account: str | None = None


def _install_hint(tool: str, platform: str) -> str:
    package, where = {
        ONE_PASSWORD_TOOL: ("1password-cli", "https://developer.1password.com/docs/cli/get-started"),
        BITWARDEN_TOOL: ("bitwarden-cli", "https://bitwarden.com/help/cli/"),
    }[tool]
    return f"Install it: brew install {package} (or see {where})" if platform == "darwin" else f"Install it: {where}"


def statuses(env: Mapping[str, str], platform: str | None = None, *, home: Path | None = None) -> list[ProviderStatus]:
    """Each provider, in the order a screen offers them. Reads no secret; asks `bw status` when `bw` is installed."""
    platform = platform or sys.platform
    order: tuple[ProviderId, ...] = ("keychain", "1password", "bitwarden", "command")
    return [status_of(provider, env, platform, home=home) for provider in order]


def status_of(
    provider: ProviderId, env: Mapping[str, str], platform: str | None = None, *, home: Path | None = None
) -> ProviderStatus:
    """One provider's status; only Bitwarden's runs anything (`bw status`)."""
    platform = platform or sys.platform
    if provider == "bitwarden":
        return _bitwarden_status(env, platform, home)
    if provider == "1password":
        op = shutil.which(ONE_PASSWORD_TOOL, path=env.get("PATH"))
        install = _install_hint(ONE_PASSWORD_TOOL, platform)
        return ProviderStatus(
            id="1password",
            label="1Password",
            available=op is not None,
            detail="" if op else f"The 1Password CLI (op) is not installed. {install}",
            reference_hint="op://vault/item/field",
        )
    if provider == "command":
        return ProviderStatus(
            id="command",
            label="Custom command",
            available=True,
            reference_hint='a command that prints the value, in config.toml: NAME = ["tool", "arg", ...]',
        )
    try:
        keychain_command("", env, platform)
        return ProviderStatus(id="keychain", label=_keychain_label(platform), available=True, stores_values=True)
    except SecretFailure as failure:
        return ProviderStatus(
            id="keychain", label=_keychain_label(platform), available=False, detail=str(failure).removeprefix(": ")
        )


def _bitwarden_status(env: Mapping[str, str], platform: str, home: Path | None) -> ProviderStatus:
    status = ProviderStatus(id="bitwarden", label="Bitwarden", available=False, reference_hint="bw://<item id>")
    if shutil.which(BITWARDEN_TOOL, path=env.get("PATH")) is None:
        install = _install_hint(BITWARDEN_TOOL, platform)
        return status.model_copy(update={"detail": f"The Bitwarden CLI (bw) is not installed. {install}"})
    state = bitwarden_state(env, home=home)
    if state == "unauthenticated":
        return status.model_copy(update={"detail": "Not signed in. Sign in once in a terminal: bw login"})
    if state == "locked":
        unlock = f"Locked. Unlock it: {names.NAME} secrets unlock bitwarden"
        return status.model_copy(update={"available": True, "needs_unlock": True, "detail": unlock})
    if state is None:
        return status.model_copy(update={"detail": "bw status did not answer. Try: bw status"})
    return status.model_copy(update={"available": True})


def _keychain_label(platform: str) -> str:
    return (
        "macOS Keychain" if platform == "darwin" else "Secret Service" if platform.startswith("linux") else "Keychain"
    )


def provider_for(reference: str) -> ProviderId | None:
    return "1password" if reference.startswith("op://") else "bitwarden" if reference.startswith("bw://") else None


def check_reference(name: str, reference: str, account: str | None) -> None:
    """A ConfigError naming what is wrong with a reference Intern would build an entry from."""
    provider = provider_for(reference)
    if provider is None:
        raise SecretFailure(
            name,
            "invalid",
            "not a reference Intern knows (1Password's start with op://, Bitwarden's with bw://). For another "
            'password manager, add a command entry to [secrets] in config.toml: NAME = ["tool", "arg", ...]',
        )
    if provider == "bitwarden":
        if not _BW_REFERENCE.fullmatch(reference):
            raise SecretFailure(
                name, "invalid", "use a Bitwarden reference like bw://<item id> or bw://<item id>/username"
            )
        if account is not None:
            raise SecretFailure(name, "invalid", "--account is for 1Password; Bitwarden uses the account you signed in")
        return
    if len(reference) > MAX_REFERENCE_CHARS or not _OP_REFERENCE.fullmatch(reference):
        raise SecretFailure(
            name, "invalid", "use a 1Password reference like op://vault/item/field (Copy Secret Reference in 1Password)"
        )
    if "attribute=otp" in reference:
        raise SecretFailure(name, "invalid", "a one-time code changes every 30 seconds; it can't be a stored secret")
    if account is not None and not _ACCOUNT.fullmatch(account):
        raise SecretFailure(
            name, "invalid", "the 1Password account is a shorthand, sign-in address or ID, as `op account list` shows"
        )


def reference_entry(name: str, reference: str, account: str | None = None) -> list[str]:
    """The command entry that reads `reference`.

    - 1Password: `--cache=false` keeps `op` from starting its daemon, which can wait on a prompt nobody sees when an
      editor runs the harness in the background.
    - Bitwarden: `--nointeraction` makes a locked vault fail at once instead of asking for the master password."""
    check_reference(name, reference, account)
    if provider_for(reference) == "bitwarden":
        match = _BW_REFERENCE.fullmatch(reference)
        assert match is not None  # checked above
        return [BITWARDEN_TOOL, "get", match.group(2) or "password", match.group(1), "--nointeraction"]
    return [ONE_PASSWORD_TOOL, "read", "--cache=false", *([f"--account={account}"] if account else []), reference]


def recognise(entry: StoreEntry) -> Linked | None:
    """The provider and reference behind an entry Intern built or one written by hand in the same shape, or None for
    the keychain and any other command."""
    if entry == KEYCHAIN or len(entry) < 3:
        return None
    tool = Path(entry[0]).name
    if tool == BITWARDEN_TOOL and entry[1] == "get" and entry[2] in BITWARDEN_FIELDS and len(entry) >= 4:
        field = "" if entry[2] == "password" else f"/{entry[2]}"
        return Linked(provider="bitwarden", reference=f"bw://{entry[3]}{field}")
    if tool != ONE_PASSWORD_TOOL or entry[1] != "read":
        return None
    reference, account, words = None, None, iter(entry[2:])
    for word in words:
        if word.startswith("--account="):
            account = word.removeprefix("--account=")
        elif word == "--account":
            account = next(words, None)
        elif word.startswith("op://"):
            reference = word
    return Linked(provider="1password", reference=reference, account=account) if reference else None


def built_by_intern(entry: StoreEntry) -> bool:
    """Whether `entry` is exactly what `reference_entry` builds, so a screen can rewrite it without losing anything a
    user wrote by hand (a path to the tool, other flags)."""
    linked = recognise(entry)
    if linked is None:
        return False
    try:
        return entry == reference_entry("", linked.reference, linked.account)
    except SecretFailure:
        return False


def provider_of(entry: StoreEntry) -> ProviderId:
    if entry == KEYCHAIN:
        return "keychain"
    linked = recognise(entry)
    return linked.provider if linked else "command"


def bitwarden_state(env: Mapping[str, str], *, home: Path | None = None) -> BitwardenState | None:
    """What `bw status` says, with the stored unlock if there is one; None when it does not answer."""
    executable = shutil.which(BITWARDEN_TOOL, path=env.get("PATH"))
    if executable is None:
        return None
    try:
        done = subprocess.run(
            [executable, "status", "--nointeraction"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=bitwarden_env(env, home=home),
            timeout=30,
            check=False,
        )
        state = json.loads(done.stdout).get("status") if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return None
    return state if state in ("unauthenticated", "locked", "unlocked") else None


def unlock_bitwarden(password: str, env: Mapping[str, str], *, home: Path | None = None) -> None:
    """Unlock the vault with the master password and keep the session key in the keychain. The password goes to
    `bw` in a file only the user can read, removed at once: never in an argument or the environment, where other
    processes could see it. It is not kept."""
    name = "Bitwarden"
    executable = shutil.which(BITWARDEN_TOOL, path=env.get("PATH"))
    if executable is None:
        install = _install_hint(BITWARDEN_TOOL, sys.platform)
        raise SecretFailure(name, "command_failed", f"bw is not installed. {install}")
    if not password:
        raise SecretFailure(name, "invalid", "the master password is empty")
    try:
        keychain_command(BITWARDEN_SESSION, env, home=home)  # say so before asking bw anything
    except SecretFailure:
        raise SecretFailure(
            name,
            "no_keychain",
            "no keychain on this host to keep the unlock in. Instead, start Intern from a shell with "
            'BW_SESSION="$(bw unlock --raw)" exported',
        ) from None
    child_env = {key: value for key, value in env.items() if key != "BW_SESSION"}
    with tempfile.TemporaryDirectory(prefix=f"{names.NAME}-bw-") as folder:  # mode 0700
        path = Path(folder) / "master"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as out:
            out.write(password + "\n")
        try:
            done = subprocess.run(
                [executable, "unlock", "--passwordfile", str(path), "--raw", "--nointeraction"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=child_env,
                timeout=UNLOCK_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SecretFailure(name, "timeout", f"bw unlock did not answer within {UNLOCK_TIMEOUT_S:g} s") from None
        except OSError as error:
            raise SecretFailure(name, "command_failed", f"bw could not start ({error.strerror})") from None
        finally:
            path.unlink(missing_ok=True)
    session = done.stdout.decode(errors="replace").strip()
    if done.returncode != 0 or not session or any(char.isspace() for char in session):
        state = bitwarden_state(env, home=home)
        why = (
            "not signed in. Sign in once in a terminal: bw login"
            if state == "unauthenticated"
            else ("wrong master password, or bw could not reach the vault")
        )
        raise SecretFailure(name, "command_failed", f"could not unlock: {why}")
    keychain_write(BITWARDEN_SESSION, session, env, home=home)


def lock_bitwarden(env: Mapping[str, str], *, home: Path | None = None) -> None:
    """Lock the vault (which voids the session key) and forget the stored unlock."""
    executable = shutil.which(BITWARDEN_TOOL, path=env.get("PATH"))
    if executable is not None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [executable, "lock", "--nointeraction"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=bitwarden_env(env, home=home),
                timeout=30,
                check=False,
            )
    with contextlib.suppress(SecretFailure):  # no keychain: nothing of Intern's to forget
        keychain_delete(BITWARDEN_SESSION, env, home=home)
