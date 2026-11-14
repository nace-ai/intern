"""NdiAdapter.extract: results are cached on disk per (case_id, doc, pdf sha256)."""

import json

import httpx

from billproof.adapters import ndi


def test_extract_caches_on_disk_and_refresh_bypasses(tmp_path, monkeypatch):
    pdf = tmp_path / "bill.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fixture\n")
    monkeypatch.setattr(ndi, "resolve_pdf", lambda case_id, doc: pdf)
    monkeypatch.setattr(ndi, "CACHE_DIR", tmp_path / "cache")
    calls = {"extract": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/documents/upload-grants"):
            return httpx.Response(
                201,
                json={
                    "workspace_id": "ws_1",
                    "upload_url": "https://uploads.example/put",
                    "token": "up_tok",
                },
            )
        if request.url.host == "uploads.example":
            return httpx.Response(200, json={"result": {"file": {"workspace_id": "ws_1", "file_id": "file_1"}}})
        if path.endswith("/documents/extract"):
            calls["extract"] += 1
            return httpx.Response(
                202,
                json={
                    "job_id": "job-extract",
                    "kind": "extract",
                    "status": "succeeded",
                    "result": {
                        "result_type": "extract",
                        "fields": [
                            {"path": "/bill/patient_balance_cents", "value": 18000,
                             "status": "found", "citations": []}
                        ],
                    },
                },
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    def make_adapter():
        return ndi.NdiAdapter(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            api_key="test-nace-key",
            base_url="https://console.nace.ai",
            auth="bearer",
            poll_interval=0,
        )

    fields = make_adapter().extract("SYN-0001", "bill")
    assert calls["extract"] == 1
    assert fields[0]["path"] == "bill.patient_balance_cents"
    cache = ndi.extract_cache_path("SYN-0001", "bill", pdf)
    assert json.loads(cache.read_text()) == fields

    # A brand-new adapter (no upload memo) still serves from disk: no HTTP at all.
    assert make_adapter().extract("SYN-0001", "bill") == fields
    assert calls["extract"] == 1

    # refresh=True re-runs the job and rewrites the cache.
    assert make_adapter().extract("SYN-0001", "bill", refresh=True) == fields
    assert calls["extract"] == 2
