import importlib

from backend.app import config


def test_paths():
    assert config.DATA == config.ROOT / "backend" / "data"
    assert config.NDI_CACHE.parent == config.DATA
    assert config.USAGE_FILE.name == "usage.json"
    assert config.CONCURRENCY == 8


def test_env(monkeypatch):
    monkeypatch.setenv("DREX_MODEL", "drex-x")
    monkeypatch.setenv("QUORUM_STUB", "1")
    m = importlib.reload(config)
    assert m.DREX_MODEL == "drex-x" and m.STUB is True
    monkeypatch.delenv("DREX_MODEL")
    monkeypatch.delenv("QUORUM_STUB")
    m = importlib.reload(config)
    assert m.DREX_MODEL == "drex-max-finance" and m.STUB is False
