from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from intern.config import Bootstrap, Options, harness_keys, load_user_config
from intern.secrets_commands import secrets_main

FAKE_SECURITY = r"""#!/bin/sh
store="$FAKE_KEYCHAIN"
case "$1" in
  -i) read line; set -- $line; mkdir -p "$store/$4"; printf "%s" "$8" > "$store/$4/$6" ;;
  find-generic-password) [ -f "$store/$3/$5" ] || exit 44
    python3 -c "import sys; print(bytes.fromhex(open(sys.argv[1]).read()).decode())" "$store/$3/$5" ;;
  delete-generic-password) [ -f "$store/$3/$5" ] || exit 44; rm "$store/$3/$5" ;;
esac
"""
"""`security` keeping each item as a file `<service>/<account>` under `$FAKE_KEYCHAIN`, its value in hex."""


def items(keychain: Path) -> list[str]:
    """The accounts of every item in a fake keychain, whatever their service."""
    return sorted(path.name for path in keychain.rglob("*") if path.is_file())


@pytest.fixture
def mac(tmp_path, monkeypatch) -> dict[str, str]:
    """A macOS-like host whose `security` keeps items as files, and an env for the harness home."""
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
    }


def run(argv: list[str], env: dict[str, str], monkeypatch, capsys, stdin: str = "") -> tuple[int, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = secrets_main(argv, env)
    return code, capsys.readouterr().out


def test_set_list_test_remove_round_trip_keeps_the_config_file_as_written(mac, monkeypatch, capsys):
    home = Path(mac["INTERN_HOME"])
    (home / "config.toml").write_text('# my settings\nsystem2 = "openai/gpt-5"\n')
    code, out = run(["set", "GITHUB_TOKEN", "--stdin", "--json"], mac, monkeypatch, capsys, "ghp_a'b\"c$d\n")
    assert (code, json.loads(out)) == (0, {"ok": True, "name": "GITHUB_TOKEN"})
    config = (home / "config.toml").read_text()
    assert config.startswith('# my settings\nsystem2 = "openai/gpt-5"\n') and 'GITHUB_TOKEN = "keychain"' in config
    assert "ghp_" not in config

    assert json.loads(run(["list", "--json"], mac, monkeypatch, capsys)[1]) == [
        {"name": "GITHUB_TOKEN", "source": "keychain", "provider": "keychain", "harness_only": False}
    ]
    run(["set", "OPENAI_API_KEY", "--stdin"], mac, monkeypatch, capsys, "sk-model-key-value")
    listed = {e["name"]: e["harness_only"] for e in json.loads(run(["list", "--json"], mac, monkeypatch, capsys)[1])}
    assert listed == {"GITHUB_TOKEN": False, "OPENAI_API_KEY": True}  # a model key is the harness's alone
    run(["remove", "OPENAI_API_KEY"], mac, monkeypatch, capsys)
    code, out = run(["test", "--json"], mac, monkeypatch, capsys)
    assert code == 0 and json.loads(out)[0]["ok"] is True and "ghp_" not in out
    assert Bootstrap(Options(cwd=home), mac).env["GITHUB_TOKEN"] == "ghp_a'b\"c$d"

    code, out = run(["remove", "GITHUB_TOKEN", "--json"], mac, monkeypatch, capsys)
    assert code == 0 and "GITHUB_TOKEN" not in (home / "config.toml").read_text()
    assert items(Path(mac["FAKE_KEYCHAIN"])) == []


def test_a_key_set_and_removed_in_another_home_leaves_the_default_homes_key_as_an_older_intern_stored_it(
    mac, monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))  # where `~/.intern`, the default home, is in this test
    default_home = tmp_path / "user" / ".intern"
    default_home.mkdir(parents=True)
    (default_home / "config.toml").write_text('[secrets]\nOPENAI_API_KEY = "keychain"\n')
    stored = Path(mac["FAKE_KEYCHAIN"]) / "intern" / "OPENAI_API_KEY"  # as every release before scoping kept it
    stored.parent.mkdir()
    stored.write_text(b"sk-users-own-fake-key".hex())
    default = {name: value for name, value in mac.items() if name != "INTERN_HOME"}
    named = {**mac, "INTERN_HOME": str(default_home)}  # the default home, named

    def key(env: dict[str, str]) -> str | None:
        return Bootstrap(Options(cwd=tmp_path), env).env.get("OPENAI_API_KEY")

    run(["set", "OPENAI_API_KEY", "--stdin"], mac, monkeypatch, capsys, "sk-test-home-fake-key")
    assert key(mac) == "sk-test-home-fake-key"
    assert key(default) == key(named) == "sk-users-own-fake-key"
    run(["remove", "OPENAI_API_KEY"], mac, monkeypatch, capsys)
    assert key(default) == "sk-users-own-fake-key"


def test_set_refuses_what_the_store_cannot_keep_and_says_why(mac, monkeypatch, capsys):
    code, out = run(["set", "PIN", "--stdin", "--json"], mac, monkeypatch, capsys, "1234")
    assert code == 1 and "PIN: value is 4 characters" in json.loads(out)["error"]
    code, out = run(["set", "ACCENT", "--stdin", "--json"], mac, monkeypatch, capsys, "unicodé-value-123")
    assert code == 1 and "one line of plain ASCII" in json.loads(out)["error"]
    home = Path(mac["INTERN_HOME"])
    (home / "config.toml").write_text('[secrets]\nNPM_TOKEN = ["pass", "show", "npm"]\n')
    code, out = run(["set", "NPM_TOKEN", "--stdin", "--json"], mac, monkeypatch, capsys, "long-enough-value")
    assert code == 2 and "comes from a command" in json.loads(out)["error"]
    monkeypatch.setattr(sys, "platform", "linux")
    code, out = run(["set", "OTHER_TOKEN", "--stdin", "--json"], mac, monkeypatch, capsys, "long-enough-value")
    assert code == 1 and "no keychain on this host" in json.loads(out)["error"]


def test_test_reports_each_failure_by_name_without_the_value(mac, monkeypatch, capsys):
    home = Path(mac["INTERN_HOME"])
    (home / "config.toml").write_text('[secrets]\nMISSING_TOKEN = "keychain"\nBROKEN = ["sh", "-c", "exit 3"]\n')
    code, out = run(["test", "--json"], mac, monkeypatch, capsys)
    assert code == 1
    by_name = {status["name"]: status for status in json.loads(out)}
    assert by_name["MISSING_TOKEN"]["reason"] == "missing"
    assert "intern secrets set MISSING_TOKEN" in by_name["MISSING_TOKEN"]["message"]
    assert by_name["BROKEN"]["reason"] == "command_failed" and by_name["BROKEN"]["path"].endswith("/sh")


FAKE_OP = r"""#!/bin/sh
for word; do ref="$word"; done
[ "$1" = read ] || exit 2
case "$ref" in
  op://Work/GitHub/token) printf "ghp-from-1password-value\n" ;;
  *) echo "[ERROR] could not read $ref" >&2; exit 1 ;;
esac
"""
"""`op read ... REF` knowing one item, and failing the way `op` does for any other."""


@pytest.fixture
def mac_with_op(mac, tmp_path) -> dict[str, str]:
    op = Path(mac["PATH"].split(":")[0]) / "op"
    op.write_text(FAKE_OP)
    op.chmod(0o755)
    return mac


def test_a_1password_reference_is_read_once_to_check_it_then_saved_and_reaches_sessions(
    mac_with_op, monkeypatch, capsys
):
    home = Path(mac_with_op["INTERN_HOME"])
    code, out = run(
        ["set", "GITHUB_TOKEN", "--ref", "op://Work/GitHub/token", "--json"], mac_with_op, monkeypatch, capsys
    )
    assert (code, json.loads(out)) == (0, {"ok": True, "name": "GITHUB_TOKEN"})
    config = (home / "config.toml").read_text()
    assert 'GITHUB_TOKEN = ["op", "read", "--cache=false", "op://Work/GitHub/token"]' in config
    assert "ghp-" not in config and "ghp-" not in out

    listed = json.loads(run(["list", "--json"], mac_with_op, monkeypatch, capsys)[1])
    assert listed[0]["provider"] == "1password" and listed[0]["reference"] == "op://Work/GitHub/token"
    assert Bootstrap(Options(cwd=home), mac_with_op).env["GITHUB_TOKEN"] == "ghp-from-1password-value"
    assert items(Path(mac_with_op["FAKE_KEYCHAIN"])) == []  # nothing of the value is kept by Intern


def test_a_reference_that_does_not_read_is_not_saved_and_the_error_says_which_and_what_to_do(
    mac_with_op, monkeypatch, capsys
):
    home = Path(mac_with_op["INTERN_HOME"])
    code, out = run(["set", "NPM_TOKEN", "--ref", "op://Work/npm/typo", "--json"], mac_with_op, monkeypatch, capsys)
    error = json.loads(out)["error"]
    assert code == 1 and "op://Work/npm/typo" in error and "op signin" in error
    assert not (home / "config.toml").exists()

    code, out = run(["set", "NPM_TOKEN", "--ref", "https://example.com", "--json"], mac_with_op, monkeypatch, capsys)
    assert code == 1 and "command entry" in json.loads(out)["error"]
    code, out = run(
        ["set", "NPM_TOKEN", "--ref", "op://Work/GitHub/token", "--account=--evil", "--json"],
        mac_with_op,
        monkeypatch,
        capsys,
    )
    assert code == 1 and "account" in json.loads(out)["error"]  # never passed to op as a flag

    run(["set", "KEPT_TOKEN", "--stdin"], mac_with_op, monkeypatch, capsys, "kept-keychain-value")
    code, out = run(
        ["set", "KEPT_TOKEN", "--ref", "op://Work/GitHub/token", "--json"], mac_with_op, monkeypatch, capsys
    )
    assert code == 2 and "remove it first" in json.loads(out)["error"]
    assert 'KEPT_TOKEN = "keychain"' in (home / "config.toml").read_text()


def test_without_op_installed_a_reference_is_refused_with_how_to_install_it(mac, monkeypatch, capsys):
    env = {**mac, "PATH": mac["PATH"].split(":")[0]}  # the fake security only: no op anywhere
    providers = {p["id"]: p for p in json.loads(run(["providers", "--json"], env, monkeypatch, capsys)[1])}
    assert providers["keychain"]["available"] and providers["keychain"]["stores_values"]
    assert not providers["1password"]["available"] and "brew install 1password-cli" in providers["1password"]["detail"]
    code, out = run(["set", "GITHUB_TOKEN", "--ref", "op://Work/GitHub/token", "--json"], env, monkeypatch, capsys)
    assert code == 1 and "brew install 1password-cli" in json.loads(out)["error"]


def test_a_hand_written_op_entry_is_listed_as_1password_and_other_commands_as_commands(mac, monkeypatch, capsys):
    home = Path(mac["INTERN_HOME"])
    (home / "config.toml").write_text(
        '[secrets]\nA_KEY = ["op", "read", "--account", "my.1password.com", "op://Private/OpenAI/key"]\n'
        'B_KEY = ["pass", "show", "npm"]\n'
    )
    listed = {e["name"]: e for e in json.loads(run(["list", "--json"], mac, monkeypatch, capsys)[1])}
    assert (listed["A_KEY"]["provider"], listed["A_KEY"]["account"]) == ("1password", "my.1password.com")
    assert listed["B_KEY"]["provider"] == "command" and "reference" not in listed["B_KEY"]
    code, out = run(["set", "B_KEY", "--ref", "op://Work/GitHub/token", "--json"], mac, monkeypatch, capsys)
    assert code == 2 and "comes from a command" in json.loads(out)["error"]  # a hand-written command is never replaced
    assert '"pass", "show", "npm"' in (home / "config.toml").read_text()


FAKE_BW = r"""#!/bin/sh
state="$FAKE_BW"
printf '%s\n' "$*" >> "$state/argv"
valid() { [ -f "$state/key" ] && [ "$BW_SESSION" = "$(cat "$state/key")" ]; }
case "$1" in
  status)
    if [ -f "$state/signed-out" ]; then echo '{"status":"unauthenticated"}'
    elif valid; then echo '{"status":"unlocked"}'; else echo '{"status":"locked"}'; fi ;;
  unlock)
    [ -f "$state/signed-out" ] && { echo "You are not logged in." >&2; exit 1; }
    [ "$(head -n 1 "$3")" = "correct horse battery staple" ] || { echo "Invalid master password." >&2; exit 1; }
    printf 'session-%s-key' "$$" > "$state/key"; cat "$state/key" ;;
  get)
    valid || { echo "Vault is locked." >&2; exit 1; }
    case "$2/$3" in
      password/github-token) echo "bw-secret-password-value" ;;
      username/github-token) echo "octocat-username" ;;
      *) echo "Not found." >&2; exit 1 ;;
    esac ;;
  lock) rm -f "$state/key" ;;
esac
"""
"""`bw` with a vault that the master password `correct horse battery staple` unlocks: each unlock issues a new session
key, `lock` voids it, and every call's arguments are logged to `$FAKE_BW/argv`."""


@pytest.fixture
def mac_with_bw(mac, tmp_path) -> dict[str, str]:
    bw = Path(mac["PATH"].split(":")[0]) / "bw"
    bw.write_text(FAKE_BW)
    bw.chmod(0o755)
    (tmp_path / "bw").mkdir()
    return {**mac, "FAKE_BW": str(tmp_path / "bw")}


def test_bitwarden_is_unlocked_once_for_intern_and_its_items_reach_sessions_but_the_unlock_never_does(
    mac_with_bw, monkeypatch, capsys
):
    env, home = mac_with_bw, Path(mac_with_bw["INTERN_HOME"])
    bitwarden = {p["id"]: p for p in json.loads(run(["providers", "--json"], env, monkeypatch, capsys)[1])}["bitwarden"]
    assert bitwarden["available"] and bitwarden["needs_unlock"] and "secrets unlock bitwarden" in bitwarden["detail"]
    code, out = run(["set", "GH_TOKEN", "--ref", "bw://github-token", "--json"], env, monkeypatch, capsys)
    assert code == 1 and "unlock bitwarden" in json.loads(out)["error"] and not (home / "config.toml").exists()

    code, out = run(["unlock", "bitwarden", "--stdin", "--json"], env, monkeypatch, capsys, "wrong password\n")
    assert code == 1 and "wrong master password" in json.loads(out)["error"]
    assert items(Path(env["FAKE_KEYCHAIN"])) == []
    code, out = run(
        ["unlock", "bitwarden", "--stdin", "--json"], env, monkeypatch, capsys, "correct horse battery staple\n"
    )
    assert (code, json.loads(out)) == (0, {"ok": True, "provider": "bitwarden"})
    assert items(Path(env["FAKE_KEYCHAIN"])) == ["intern-bitwarden-session"]
    assert "correct horse" not in (Path(env["FAKE_BW"]) / "argv").read_text()  # never an argument
    assert not list(Path(env["FAKE_BW"]).glob("master*"))

    run(["set", "GH_TOKEN", "--ref", "bw://github-token"], env, monkeypatch, capsys)
    run(["set", "GH_USER", "--ref", "bw://github-token/username"], env, monkeypatch, capsys)
    config = (home / "config.toml").read_text()
    assert 'GH_TOKEN = ["bw", "get", "password", "github-token", "--nointeraction"]' in config
    listed = {e["name"]: e for e in json.loads(run(["list", "--json"], env, monkeypatch, capsys)[1])}
    assert (listed["GH_USER"]["provider"], listed["GH_USER"]["reference"]) == (
        "bitwarden",
        "bw://github-token/username",
    )
    session = Bootstrap(Options(cwd=home), env).env
    assert (session["GH_TOKEN"], session["GH_USER"]) == ("bw-secret-password-value", "octocat-username")
    assert "BW_SESSION" not in session  # the unlock reaches bw, not the agent's commands
    stale = {**env, "BW_SESSION": "an-expired-session-key"}  # a key exported in the shell, voided by the unlock
    code, out = run(["test", "GH_TOKEN", "--json"], stale, monkeypatch, capsys)
    assert code == 0 and json.loads(out)[0]["ok"]
    assert "BW_SESSION" in harness_keys(load_user_config(home))  # an exported one is never printed to the model

    code, out = run(["lock", "bitwarden", "--json"], env, monkeypatch, capsys)
    assert code == 0 and items(Path(env["FAKE_KEYCHAIN"])) == []
    code, out = run(["test", "GH_TOKEN", "--json"], env, monkeypatch, capsys)
    assert code == 1 and "secrets unlock bitwarden" in json.loads(out)[0]["message"]


def test_bitwarden_signed_out_or_given_a_bad_reference_says_what_to_do(mac_with_bw, monkeypatch, capsys):
    env = mac_with_bw
    (Path(env["FAKE_BW"]) / "signed-out").touch()
    bitwarden = {p["id"]: p for p in json.loads(run(["providers", "--json"], env, monkeypatch, capsys)[1])}["bitwarden"]
    assert not bitwarden["available"] and "bw login" in bitwarden["detail"]
    code, out = run(
        ["unlock", "bitwarden", "--stdin", "--json"], env, monkeypatch, capsys, "correct horse battery staple"
    )
    assert code == 1 and "bw login" in json.loads(out)["error"]
    for reference in ("bw://-flag", "bw://github-token/totp", "bw://"):
        code, out = run(["set", "X_KEY", "--ref", reference, "--json"], env, monkeypatch, capsys)
        assert code == 1 and "bw://<item id>" in json.loads(out)["error"], reference
    code, out = run(["set", "X_KEY", "--ref", "bw://github-token", "--account=me", "--json"], env, monkeypatch, capsys)
    assert code == 1 and "--account is for 1Password" in json.loads(out)["error"]
