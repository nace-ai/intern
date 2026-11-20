"""What the memory steward keeps out of the shared drive, and where it files what a session saw of a repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from intern.memory.steward.extract import secrets_in
from intern.memory.steward.place import place_for_path

# A made-up webhook address, put together here so that no scanner of this repository takes it for a real one.
WEBHOOK = "https://hooks.slack" + ".com/services/" + "/".join(["T00000000", "B00000000", "X" * 24])


@pytest.mark.parametrize("name", ["environment", "Environment", "general"])
def test_a_repository_named_after_a_reserved_page_does_not_get_that_page(tmp_path: Path, name: str):
    # map/environment is recalled in every session: a repository someone else wrote must not write to it by
    # calling itself "environment" in its manifest.
    (tmp_path / "src").mkdir()
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    page, kind, _ = place_for_path(tmp_path / "src" / "a.py", tmp_path)
    assert (page, kind) == (f"map/{name.lower()}-project", "project")

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "ledger"\n')
    assert place_for_path(tmp_path / "src" / "a.py", tmp_path)[0] == "map/ledger"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("DB_PASSWORD=correcthorsebatterystaple", "correcthorsebatterystaple"),  # no digit in it
        ("password: hunter2", "hunter2"),  # shorter than a token
        ("PASSWORD='S3cr3t!pa$$w0rd#2024'", "S3cr3t!pa$$w0rd#2024"),  # not only word characters
        (
            "export SESSION=9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",  # the name says nothing
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        ),
        (f"notify {WEBHOOK} on failure", WEBHOOK),
    ],
)
def test_a_credential_a_session_read_is_found_before_it_reaches_the_drive(text: str, secret: str):
    assert secret in secrets_in(text)


@pytest.mark.parametrize(
    "text",
    [
        # What is found is replaced everywhere in the session's record, so code and commit ids must not be found.
        "conn = connect(password=password, user=user)",
        "password: Optional[str] = None",
        "password = os.environ.get('DB_PASSWORD')",
        "DB_PASSWORD=${DB_PASSWORD}",
        "DB_PASSWORD=changeme",
        "GIT_COMMIT=9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",
        "fixed in commit 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",
    ],
)
def test_code_and_commit_ids_are_not_taken_for_credentials(text: str):
    assert secrets_in(text) == set()
