"""pytest collects the project suite and does not walk ignored Intern files."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pytest_configuration_limits_collection_to_project_tests():
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths" in ini
    assert "tests" in ini
    assert "work" in ini
    decoy = ROOT / "work" / "test_should_not_collect.py"
    decoy.write_text("def test_from_work_tree():\n    raise AssertionError('work tree was collected')\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        collected = completed.stdout.replace("\\", "/")
        assert "test_from_work_tree" not in collected
        assert "work/" not in collected
        node_ids = [line for line in collected.splitlines() if "::" in line]
        assert node_ids
        assert all(line.startswith("tests/") for line in node_ids)
    finally:
        decoy.unlink(missing_ok=True)
