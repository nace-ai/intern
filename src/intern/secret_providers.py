"""Password managers the secrets store knows by name, on top of the plain `[secrets]` entries.

Config keeps the shapes `secret_store` reads: a provider only builds the command entry for a reference and
recognises one it built (or one written by hand), so `list` and the screens can say "1Password ·
op://Work/GitHub/token" instead of a raw command. Values never pass through here.
"""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from intern.secret_store import KEYCHAIN, SecretFailure, StoreEntry, keychain_command

ProviderId = Literal["keychain", "1password", "command"]

ONE_PASSWORD_TOOL = "op"
_OP_NAME = r"[A-Za-z0-9._-](?:[A-Za-z0-9 ._-]*[A-Za-z0-9._-])?"
_OP_REFERENCE = re.compile(rf"^op://{_OP_NAME}(?:/{_OP_NAME}){{2,3}}(?:\?[a-z-]+=[A-Za-z0-9_-]+)?$")
"""1Password's secret reference syntax: vault/item/field, or vault/item/section/field. A name outside this charset
is written as the item's id. MAX_REFERENCE_CHARS bounds it."""
MAX_REFERENCE_CHARS = 1024
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")
"""What `op --account` takes: a shorthand, sign-in address or ID. Never starts with `-`, so it can't be a flag."""


class ProviderStatus(BaseModel):
    """One place `intern secrets` can keep or find a secret, and whether it works on this host now."""

    id: ProviderId
    label: str
    available: bool
    detail: str = ""
    """Why it is not available and what to do; empty when it is."""
    stores_values: bool = False
    """Whether `intern secrets set NAME` can save a typed value there."""
    reference_hint: str | None = None
    """What a reference to an existing item looks like, for `set NAME --ref`."""


class Linked(BaseModel):
    """What a command entry points at, when a known provider built it."""

    provider: ProviderId
    reference: str
    account: str | None = None


def _install_hint(platform: str) -> str:
    where = "https://developer.1password.com/docs/cli/get-started"
    return (
        f"Install it: brew install 1password-cli (or see {where})" if platform == "darwin" else f"Install it: {where}"
    )


def statuses(env: Mapping[str, str], platform: str | None = None) -> list[ProviderStatus]:
    """Each provider, in the order a screen offers them. Reads no secret and runs nothing."""
    platform = platform or sys.platform
    try:
        keychain_command("", env, platform)
        keychain = ProviderStatus(id="keychain", label=_keychain_label(platform), available=True, stores_values=True)
    except SecretFailure as failure:
        keychain = ProviderStatus(
            id="keychain", label=_keychain_label(platform), available=False, detail=str(failure).removeprefix(": ")
        )
    op = shutil.which(ONE_PASSWORD_TOOL, path=env.get("PATH"))
    one_password = ProviderStatus(
        id="1password",
        label="1Password",
        available=op is not None,
        detail="" if op else f"The 1Password CLI (op) is not installed. {_install_hint(platform)}",
        reference_hint="op://vault/item/field",
    )
    command = ProviderStatus(
        id="command",
        label="Custom command",
        available=True,
        reference_hint='a command that prints the value, in config.toml: NAME = ["tool", "arg", ...]',
    )
    return [keychain, one_password, command]


def _keychain_label(platform: str) -> str:
    return (
        "macOS Keychain" if platform == "darwin" else "Secret Service" if platform.startswith("linux") else "Keychain"
    )


def check_reference(name: str, reference: str, account: str | None) -> None:
    """A ConfigError naming what is wrong with a reference Intern would build an entry from."""
    if not reference.startswith("op://"):
        raise SecretFailure(
            name,
            "invalid",
            "not a reference Intern knows (1Password's start with op://). For another password manager, add a "
            'command entry to [secrets] in config.toml: NAME = ["tool", "arg", ...]',
        )
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
    """The command entry that reads `reference`. `--cache=false` keeps `op` from starting its daemon, which can wait
    on a prompt nobody sees when an editor runs the harness in the background."""
    check_reference(name, reference, account)
    return [ONE_PASSWORD_TOOL, "read", "--cache=false", *([f"--account={account}"] if account else []), reference]


def recognise(entry: StoreEntry) -> Linked | None:
    """The provider and reference behind an entry: the keychain, an `op read` Intern built or one written by hand,
    or None for any other command."""
    if entry == KEYCHAIN:
        return None
    if len(entry) < 3 or Path(entry[0]).name != ONE_PASSWORD_TOOL or entry[1] != "read":
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
    user wrote by hand (a path to `op`, other flags)."""
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
