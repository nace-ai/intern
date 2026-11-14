"""Inspection API guards: unknown cases and one active mock run."""

import asyncio

import httpx
import pytest
from fastapi import HTTPException

import app as app_module
import clients
from storage import ResultStore


def test_unknown_case_and_duplicate_inspection():
    async def run() -> None:
        gate = asyncio.Event()
        original = clients.MockDecisionClient.decide

        async def held(self, request):
            gate.set()
            await release.wait()
            return await original(self, request)

        release = asyncio.Event()
        clients.MockDecisionClient.decide = held
        app_module.ACTIVE["run_id"] = None
        try:
            transport = httpx.ASGITransport(app=app_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                missing = await http.get("/api/shipments/DEV-01")
                assert missing.status_code == 404
                live = await http.get("/api/results", params={"track": "decisions", "mode": "live"})
                assert live.status_code == 200
                body = live.json()
                assert body["mode"] == "live"
                assert body.get("measurements") in (None, "measured")
                first = asyncio.create_task(http.post("/api/inspect", json={"case_id": "TST-056"}))
                await gate.wait()
                second = await http.post("/api/inspect", json={"case_id": "TST-056"})
                assert second.status_code == 409
                release.set()
                started = await first
                assert started.status_code == 202
        finally:
            release.set()
            clients.MockDecisionClient.decide = original
            app_module.ACTIVE["run_id"] = None

    asyncio.run(run())


def test_inspect_mode_gating(monkeypatch):
    async def run() -> None:
        # APP_MODE=live without keys must refuse; it must not silently fall back to the mock.
        monkeypatch.setattr(app_module, "APP_MODE", "live")
        monkeypatch.setattr(app_module, "secret_present", lambda name: False)
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            refused = await http.post("/api/inspect", json={"case_id": "TST-056"})
            assert refused.status_code == 400
            assert "no mock fallback" in refused.json()["detail"]
            health = await http.get("/api/health")
            assert health.json()["inspect_mode"] == "mock"

        # With both keys present but APP_MODE still mock, inspection stays mock.
        monkeypatch.setattr(app_module, "APP_MODE", "mock")
        monkeypatch.setattr(app_module, "secret_present", lambda name: True)
        assert app_module.inspect_mode() == "mock"

        # APP_MODE=live plus both keys is the only live configuration.
        monkeypatch.setattr(app_module, "APP_MODE", "live")
        assert app_module.inspect_mode() == "live"
        monkeypatch.setattr(app_module, "secret_present", lambda name: name == "NACE_API_KEY")
        assert app_module.inspect_mode() == "mock"

    asyncio.run(run())


def test_inspection_run_lookup(tmp_path):
    (tmp_path / "live" / "inspections").mkdir(parents=True)
    (tmp_path / "mock" / "inspections").mkdir(parents=True)
    (tmp_path / "live" / "inspections" / "inspect-aabbccdd00.json").write_text('{"mode": "live"}', encoding="utf-8")
    (tmp_path / "mock" / "inspections" / "inspect-ffeeddccb0.json").write_text('{"mode": "mock"}', encoding="utf-8")
    assert app_module.inspection_path("inspect-aabbccdd00", root=tmp_path) is not None
    assert app_module.inspection_path("inspect-ffeeddccb0", root=tmp_path) is not None
    assert app_module.inspection_path("inspect-0000000000", root=tmp_path) is None


def _bind_store(tmp_path):
    class Bound(ResultStore):
        def __init__(self, root=None):
            super().__init__(tmp_path)

    return Bound


def test_failed_inspection_releases_the_active_run(tmp_path, monkeypatch):
    async def run() -> None:
        monkeypatch.setattr(app_module, "ResultStore", _bind_store(tmp_path))
        monkeypatch.setattr(app_module, "APP_MODE", "mock")
        original = clients.MockDecisionClient.decide

        async def boom(self, request):
            raise RuntimeError("provider down")

        clients.MockDecisionClient.decide = boom
        app_module.ACTIVE["run_id"] = None
        app_module.ACTIVE["task"] = None
        try:
            transport = httpx.ASGITransport(app=app_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                first = await http.post("/api/inspect", json={"case_id": "TST-056"})
                assert first.status_code == 202
                await app_module.ACTIVE["task"]
                assert app_module.ACTIVE["run_id"] is None
                body = await http.get(f"/api/runs/{first.json()['run_id']}")
                assert body.status_code == 200
                assert body.json()["status"] == "error"
                second = await http.post("/api/inspect", json={"case_id": "TST-056"})
                assert second.status_code == 202
                await app_module.ACTIVE["task"]
                assert app_module.ACTIVE["run_id"] is None
        finally:
            clients.MockDecisionClient.decide = original
            app_module.ACTIVE["run_id"] = None
            app_module.ACTIVE["task"] = None

    asyncio.run(run())


def test_live_mode_without_keys_does_not_start_a_run(tmp_path, monkeypatch):
    async def run() -> None:
        monkeypatch.setattr(app_module, "ResultStore", _bind_store(tmp_path))
        monkeypatch.setattr(app_module, "APP_MODE", "live")
        monkeypatch.setattr(app_module, "secret_present", lambda name: False)
        app_module.ACTIVE["run_id"] = None
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            refused = await http.post("/api/inspect", json={"case_id": "TST-056"})
            assert refused.status_code == 400
            assert "no mock fallback" in refused.json()["detail"]
            assert app_module.ACTIVE["run_id"] is None
            assert list(tmp_path.rglob("*.json")) == []

    asyncio.run(run())


def test_media_paths_stay_inside_the_showcase():
    async def run() -> None:
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            for path in (
                "/media/pdfs/DEV-01.pdf",
                "/media/pdfs/TST-056.txt",
                "/media/pdfs/%2e%2e%2f.env",
                "/media/previews/TST-056-p4.png",
                "/media/previews/DEV-01-p1.png",
                "/media/artwork/not_allowed.png",
                "/media/artwork/%2e%2e%2f.env",
            ):
                response = await http.get(path)
                assert response.status_code == 404, path
            allowed = await http.get("/media/pdfs/TST-056.pdf")
            assert allowed.status_code == 200
            assert allowed.content.startswith(b"%PDF")

    asyncio.run(run())


def test_safe_file_rejects_names_outside_the_pattern(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    (root / "TST-056.pdf").write_bytes(b"%PDF-1.4")
    found = app_module.safe_file(root, "TST-056.pdf", app_module.PDF_NAME)
    assert found == (root / "TST-056.pdf").resolve()
    with pytest.raises(HTTPException):
        app_module.safe_file(root, "../TST-056.pdf", app_module.PDF_NAME)
    with pytest.raises(HTTPException):
        app_module.safe_file(root, "TST-999.pdf", app_module.PDF_NAME)
