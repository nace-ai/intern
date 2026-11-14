"""Evidence pipeline tests using NDI-SHAPED synthetic job results (shapes from the Perception docs)."""

from evidence.ingest import build_passages
from evidence.store import EvidenceStore
from agents.roles import SharedState, _retrieve_passages
from service.contracts import PatternClaim

DOC = {"document_id": "SYN", "title": "Synthetic doc", "url": "test://synthetic"}
PARSE = {"job_id": "p1", "status": "succeeded", "result": {"document": {"page_count": 3}}}
EXTRACT = {
    "job_id": "e1",
    "status": "succeeded",
    "result": {
        "result_type": "extract",
        "data": {
            "statements": [
                {"text": "Read back all hold short instructions.", "topic": "runway_surface"},
                {"text": "Brief the taxi route before moving.", "topic": "procedural_deviation"},
                {"text": "Unlocatable sentence.", "topic": "communication_coordination"},
                {"text": "Bad topic.", "topic": "not_a_topic"},
            ]
        },
        "fields": [
            {"path": "/statements/0", "status": "found", "citations": []},
            {
                "path": "/statements/1",
                "status": "found",
                "citations": [{"location": {"kind": "page_region", "page": 2, "polygons": []}, "source_text": "Brief the taxi route"}],
            },
            {"path": "/statements/2", "status": "found", "citations": []},
        ],
    },
}
GROUND = [
    {
        "job_id": "g1",
        "status": "succeeded",
        "result": {
            "result_type": "ground",
            "targets": [
                {
                    "id": "w0s0",
                    "status": "found",
                    "matches": [
                        {
                            "rank": 1,
                            "matched_text": "Read back all hold short instructions.",
                            "match_method": "exact",
                            "confidence": None,
                            "location": {"kind": "page_region", "page": 3, "polygons": []},
                            "cropped_image_url": None,
                        }
                    ],
                },
                {"id": "w0s1", "status": "not_found", "matches": []},
                {"id": "w0s2", "status": "not_found", "matches": []},
            ],
        },
    }
]


def test_build_passages_only_keeps_ndi_located_text():
    passages, rejected = build_passages(DOC, PARSE, [EXTRACT], GROUND)
    by_id = {p["passage_id"]: p for p in passages}

    grounded = by_id["SYN#w0s0"]
    assert grounded["status"] == "grounded" and grounded["page"] == 3
    assert grounded["passage_text"] == "Read back all hold short instructions."
    assert grounded["ndi"]["ground_job_id"] == "g1"

    parsed = by_id["SYN#w0s1"]  # Ground missed it; Extract citation text and page are used
    assert parsed["status"] == "parsed" and parsed["page"] == 2
    assert parsed["passage_text"] == "Brief the taxi route"

    assert "SYN#w0s2" not in by_id  # no location anywhere -> rejected, never fabricated
    assert rejected and rejected[0]["statement"] == "Unlocatable sentence."
    assert all(p["topic"] != "not_a_topic" for p in passages)


def test_store_status_and_retrieval(synthetic_store, empty_store):
    assert synthetic_store.status()["status"] == "ready"
    assert empty_store.status()["status"] == "not_ready"
    top = synthetic_store.retrieve("crew stopped at the hold short line before the runway", "runway_surface", k=1)
    assert top[0].topic == "runway_surface"
    ref = top[0].to_ref("claim")
    assert ref.page_or_region == "p. 2" and ref.status == "parsed"


def test_store_reports_corrupt_cache(tmp_path):
    (tmp_path / "passages.json").write_text("{not json", encoding="utf-8")
    assert EvidenceStore(tmp_path).status()["status"] == "error"


def test_evidence_compares_guidance_topics_and_skips_uncovered_family(pattern_case, synthetic_store):
    shared = SharedState(case=pattern_case)
    shared.pattern_claim = PatternClaim(pattern_family="communication_coordination", supporting_incident_ids=[])
    passages = _retrieve_passages(synthetic_store, shared)
    assert {p.topic for p in passages} == {"communication_coordination", "runway_surface"}
    shared.pattern_claim = PatternClaim(pattern_family="aircraft_systems", supporting_incident_ids=[])
    assert _retrieve_passages(synthetic_store, shared) == []
