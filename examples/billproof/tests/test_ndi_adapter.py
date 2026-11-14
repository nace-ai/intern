"""NDI extract wire format: job polling and page_region citation conversion."""

import io
import json

import httpx
from PIL import Image

from billproof.adapters import ndi
from billproof.config import settings
from billproof.schema import Citation


def test_extract_polls_and_normalizes_citations(tmp_path, monkeypatch):
    pdf = tmp_path / "bill.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fixture\n")
    monkeypatch.setattr(ndi, "resolve_pdf", lambda case_id, doc: pdf)
    seen = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/documents/upload-grants"):
            assert request.headers["Authorization"] == "Bearer test-nace-key"
            return httpx.Response(
                201,
                json={
                    "workspace_id": "ws_1",
                    "upload_url": "https://uploads.example/put",
                    "token": "up_tok",
                    "expires_at": "2026-11-14T00:00:00Z",
                    "max_bytes": None,
                },
            )
        if request.url.host == "uploads.example":
            assert request.headers["X-Upload-Token"] == "up_tok"
            assert "authorization" not in {k.lower() for k in request.headers}
            return httpx.Response(200, json={"result": {"file": {"workspace_id": "ws_1", "file_id": "file_1"}}})
        if path.endswith("/documents/extract"):
            body = json.loads(request.content)
            assert request.headers["Authorization"] == "Bearer test-nace-key"
            assert request.headers["Idempotency-Key"]
            assert body["source"] == {"type": "workspace_file", "workspace_id": "ws_1", "file_id": "file_1"}
            assert body["citations"]["enabled"] is True
            assert "charge_cents" in request.content.decode()
            assert "patient_balance_cents" in request.content.decode()
            return httpx.Response(202, json={"job_id": "job-extract", "kind": "extract", "status": "queued"})
        if path.endswith("/documents/jobs/job-extract"):
            seen["polls"] += 1
            if seen["polls"] == 1:
                return httpx.Response(200, json={"job_id": "job-extract", "kind": "extract", "status": "running"})
            return httpx.Response(
                200,
                json={
                    "job_id": "job-extract",
                    "kind": "extract",
                    "status": "succeeded",
                    "result": {
                        "result_type": "extract",
                        "fields": [
                            {
                                "path": "/bill/lines/2/code",
                                "value": "70553",
                                "status": "found",
                                "citations": [
                                    {
                                        "location": {
                                            "kind": "page_region",
                                            "page": 1,
                                            "width": 200,
                                            "height": 100,
                                            "polygons": [[[20, 10], [40, 10], [40, 30], [20, 30]]],
                                        },
                                        "source_text": "70553",
                                    }
                                ],
                            },
                            {
                                "path": "/bill/patient_balance_cents",
                                "value": 18000,
                                "status": "found",
                                "citations": [
                                    {
                                        "location": {
                                            "kind": "page_region",
                                            "page": 1,
                                            "polygons": [[[0.2, 0.3], [0.5, 0.3], [0.5, 0.4], [0.2, 0.4]]],
                                        },
                                        "source_text": "180.00",
                                    }
                                ],
                            },
                            {
                                "path": "/patient/date_of_death",
                                "value": None,
                                "status": "not_found",
                                "citations": [],
                            },
                        ],
                    },
                },
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ndi.NdiAdapter(
        client=client,
        api_key="test-nace-key",
        base_url="https://console.nace.ai",
        auth="bearer",
        poll_interval=0,
    )
    fields = {row["path"]: row for row in adapter.extract("SYN-0001", "bill")}

    assert seen["polls"] == 2
    code = fields["bill.lines.3.code"]
    assert code["value"] == "70553"
    assert code["status"] == "found"
    assert code["citations"][0]["doc"] == "bill"
    assert code["citations"][0]["page"] == 1
    assert code["citations"][0]["text"] == "70553"
    assert code["citations"][0]["polygons"] == [[[0.1, 0.1], [0.2, 0.1], [0.2, 0.3], [0.1, 0.3]]]
    balance = fields["bill.patient_balance_cents"]
    assert balance["value"] == 18000
    assert balance["citations"][0]["polygons"] == [[[0.2, 0.3], [0.5, 0.3], [0.5, 0.4], [0.2, 0.4]]]
    assert fields["patient.date_of_death"]["status"] == "not_found"
    assert fields["patient.date_of_death"]["citations"] == []


def test_fixture_ground_crops_pdf_and_caches(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "SYN-G2-NONE_bill.pdf"
    drawing = canvas.Canvas(str(pdf_path), pagesize=(300, 400))
    drawing.drawString(40, 300, "70553")
    drawing.save()
    monkeypatch.setattr(ndi, "SYN_DIR", tmp_path)
    monkeypatch.setattr(ndi, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("MOCK_MODE", "1")
    settings.cache_clear()
    try:
        adapter = ndi.get_ndi()
        assert adapter.simulated is True
        assert adapter.extract("SYN-G2-NONE", "bill") == []
        citation = Citation(
            doc="bill",
            page=1,
            polygons=[[(0.1, 0.1), (0.4, 0.1), (0.4, 0.3), (0.1, 0.3)]],
            text="70553",
        )
        blob = adapter.ground("SYN-G2-NONE", citation)
        assert blob is not None and blob.startswith(b"\x89PNG")
        image = Image.open(io.BytesIO(blob))
        assert image.size[0] < 500
        assert image.size[1] < 700
        pdf_path.unlink()
        assert adapter.ground("SYN-G2-NONE", citation) == blob
    finally:
        settings.cache_clear()
