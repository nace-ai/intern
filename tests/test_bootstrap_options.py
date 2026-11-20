"""What a session opens with, as the TUI's capability switch reads and sets it: the effective capability
settings, and the memory drive, which the options can name or turn off."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from intern.capability_settings import CapabilitySettings
from intern.config import Bootstrap, Options
from intern.models.registry import ConfigError


def home_with(tmp_path: Path, text: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(text)
    return {"INTERN_HOME": str(home)}


def test_capability_settings_are_the_options_whole_object_else_the_saved_ones(tmp_path: Path):
    env = home_with(tmp_path, '[capabilities]\ntool_packs = ["base", "web-read"]\n')
    saved = Bootstrap(Options(cwd=tmp_path), env).capability_settings()
    assert saved.tool_packs == ["base", "web-read"] and not saved.browser.enabled
    given = CapabilitySettings(tool_packs=["base"], browser=saved.browser.model_copy(update={"enabled": True}))
    assert Bootstrap(Options(cwd=tmp_path, capabilities=given), env).capability_settings() is given


def test_the_memory_drive_comes_from_the_options_the_config_or_the_environment_and_memory_off_wins(tmp_path: Path):
    bare = home_with(tmp_path, "")
    assert Bootstrap(Options(cwd=tmp_path), bare).memory_path() == tmp_path / "home" / "memory"
    assert Bootstrap(Options(cwd=tmp_path), {**bare, "INTERN_MEMORY": "/drives/env"}).memory_path() == Path(
        "/drives/env"
    )
    saved = home_with(tmp_path, '[memory]\ndrive = "/drives/saved"\n')
    assert Bootstrap(Options(cwd=tmp_path), {**saved, "INTERN_MEMORY": "/drives/env"}).memory_path() == Path(
        "/drives/saved"
    )
    named = Options(cwd=tmp_path, memory=Path("/drives/named"))
    assert Bootstrap(named, saved).memory_path() == Path("/drives/named")
    assert Bootstrap(named.model_copy(update={"memory_off": True}), saved).memory_path() is None
    assert Bootstrap(Options(cwd=tmp_path, memory_off=True), saved).memory() is None


def strict_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user's git set up the way some companies require: every commit signed, and hooks that refuse this one."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("pre-commit", "commit-msg", "prepare-commit-msg"):
        (hooks / name).write_text("#!/bin/sh\nexit 1\n")
        (hooks / name).chmod(0o755)
    settings = tmp_path / "gitconfig"
    settings.write_text(f"[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n[core]\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(settings))


def test_the_default_drive_is_made_whatever_the_users_git_requires_of_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    strict_git(tmp_path, monkeypatch)
    env = home_with(tmp_path, "")
    drive = Bootstrap(Options(cwd=tmp_path), env).memory()
    assert drive is not None and drive.root == (tmp_path / "home" / "memory").resolve()
    assert drive.index("") is not None
    assert [entry.name for entry in (tmp_path / "home").iterdir() if entry.name.startswith(".memory")] == []


def test_a_drive_left_half_made_is_made_again_and_one_with_notes_is_left_alone(tmp_path: Path):
    """An earlier version stopped at the first commit and left the new drive's files behind; every session after
    that failed to mount it."""
    env = home_with(tmp_path, "")
    made = Bootstrap(Options(cwd=tmp_path), env).memory()
    assert made is not None
    root = made.root
    files = {name: (root / name).read_text() for name in ("MEMORY.md", ".wiki/schema.md")}

    def leave_half_made() -> None:
        shutil.rmtree(root / ".git")
        subprocess.run(["git", "-C", str(root), "init", "--quiet", "--initial-branch", "main"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "--all"], check=True)

    leave_half_made()
    again = Bootstrap(Options(cwd=tmp_path), env).memory()
    assert again is not None and again.commit
    assert {name: (root / name).read_text() for name in files} == files

    leave_half_made()
    (root / "notes.md").write_text("something the user wrote\n")
    with pytest.raises(ConfigError, match="cannot be mounted"):
        Bootstrap(Options(cwd=tmp_path), env).memory()
    assert (root / "notes.md").read_text() == "something the user wrote\n"
