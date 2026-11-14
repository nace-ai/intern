# Shared interface contract (v1 agreed)

This is the agreed handoff between Builder A and Builder B. Both teammates should explicitly agree to any changes in field names or meanings before implementing against them. Files named `*_mock.json` under `tests/fixtures/` are synthetic interface examples only. Files named `asrs_case_*.json` contain real NASA ASRS narratives from the documented June 2024 export.

## General rules

- JSON uses UTF-8, snake_case keys, ISO 8601 UTC timestamps where timestamps are needed, and seconds for durations.
- IDs are strings. Optional or unavailable values are `null`; arrays are empty when no items exist. Never invent a decision, probability, citation, or missing source.
- Unknown extra fields may be ignored by readers. Changes to required fields, enums, or meanings require a contract version update and both owners' agreement.
- Every request and response has `contract_version: "1.0"`.
- Retrieval scores rank text matches only. Drex probabilities are returned decision scores, not calibrated safety probabilities.

## A → B: `CaseContext`

```json
{
  "contract_version": "1.0",
  "case_id": "ASRS-12345",
  "narrative": "Original de-identified report text.",
  "metadata": {"report_date": "2026-01-01", "category": null},
  "candidate_incidents": [
    {"case_id": "ASRS-67890", "narrative": "Original candidate excerpt.", "retrieval_score": 0.71}
  ],
  "retrieval_method": "Documented keyword/metadata retrieval method and score meaning",
  "data_source": {"name": "NASA ASRS", "url": "https://asrs.arc.nasa.gov/search/database.html", "sample": "Export date and filters"}
}
```

`case_id` must be stable and unique within the imported sample. `narrative` is original de-identified source text, not a generated paraphrase. `metadata` contains only fields available in the export, with missing values set to `null`. Each candidate has a different `case_id` from the selected case. Keep the candidate list small (normally 3–5); an empty list is valid. `retrieval_score` is a finite number whose range and interpretation are described in `retrieval_method`. `data_source.sample` identifies the export or fixture provenance.

## B → A: `InvestigationResult`

```json
{
  "contract_version": "1.0",
  "case_id": "ASRS-12345",
  "run_id": "run-unique-id",
  "status": "complete",
  "agent_trace": [],
  "initial_claim": null,
  "revised_claim": null,
  "final_recommended_next_action": "request_context",
  "review_priority": "routine",
  "references": [],
  "total_duration_seconds": 1.2,
  "provider_model_details": {"decision_provider": "Nace Drex", "decision_model": "actual model identifier", "evidence_provider": "Nace NDI"},
  "errors": []
}
```

`status` is `complete` or `incomplete`. A complete result has real agent decisions and a final action. An incomplete result has `final_recommended_next_action: null`, `review_priority: null`, and at least one error; partial real trace messages may remain. The UI must not synthesize missing messages. `initial_claim` and `revised_claim` are structured pattern claims of the form `{"pattern_family": "communication_coordination", "supporting_incident_ids": ["ASRS-67890"]}` or `null`. `revised_claim` is `null` when no additional round occurred.

`final_recommended_next_action` is one of `investigate_cluster`, `request_context`, `specialist_review`, `no_pattern_identified`, or `null` for incomplete runs. `review_priority` is `routine`, `elevated`, `urgent`, or `null` for incomplete runs. Review priority is for human analyst triage only.

### `AgentMessage` in `agent_trace`

Each chronological message has these fields:

| Field | Type and meaning |
| --- | --- |
| `agent` | `pattern`, `evidence`, `investigation`, or `coordinator` |
| `round_number` | Integer starting at 1; at most 2 |
| `input_summary` | Concise summary of the case and previous findings consumed |
| `decision_question` | Exact bounded question sent to Drex |
| `allowed_options` | Nonempty array of option strings supplied to Drex |
| `selected_option` | One of `allowed_options`; never synthesized |
| `probabilities` | Map from each allowed option to the returned numeric decision score |
| `supporting_incident_ids` | Candidate IDs actually used as support |
| `evidence_refs` | Array of `EvidenceRef` records actually located in a source |
| `uncertainty_status` | `supported`, `conflicted`, `insufficient`, or `unresolved` |
| `duration_seconds` | Nonnegative duration for the decision call |
| `action_summary` | Human-readable account of the finding and shared-state change |

For an API failure before a decision is returned, append an error to `errors` and leave out that agent message. Probabilities must come from the provider response, include every allowed option, and be numbers in `[0, 1]`. Do not infer values from a chosen option. The trace order is the execution order, so a second round can contain only the agents that actually ran again.

### `EvidenceRef`

`{"document_id": "doc-id", "title": "Source title", "source": "original URL or authorized local filename", "page_or_region": "p. 3", "passage_text": "Located source passage", "status": "grounded", "relevant_claim": "Claim this passage addresses"}`

`status` is `parsed` or `grounded`; a citation is displayable only when the passage and location came from actual NDI output. `page_or_region` may be `null` if the provider does not return a location, in which case the UI must not imply page-level grounding. `references` at result level is the deduplicated set of trace evidence references.

### Errors

Each error is `{"code": "provider_unavailable", "message": "Safe human-readable detail", "agent": "evidence"}`. `agent` may be `null` for request-level errors. Do not include secrets or raw credential-bearing URLs.

## Local API proposed surface

| Method and path | Response |
| --- | --- |
| `GET /health` | `{"status": "ready" | "degraded", "nace_configured": true | false}`; never expose credentials |
| `GET /evidence/status` | `{"status": "ready" | "not_ready" | "error", "document_count": 0, "detail": "human-readable status"}` |
| `POST /analyze` | Accepts `CaseContext`, returns `InvestigationResult` |

Use HTTP 422 for an invalid request shape. For a valid request whose external processing fails, return an `incomplete` result with error details. Builder A sets a visible timeout and displays service failures without inventing a result. The UI should clearly label cached past runs.

## Fixture use

- `asrs_case_2134849.json` and `asrs_case_2126005.json`: real ASRS `CaseContext` values generated from the June 2024 export. Builder B can use them to exercise the live service without Builder A's database.
- `case_pattern_mock.json` and `result_pattern_mock.json`: synthetic successful round with four messages and an illustrative citation.
- `case_uncertain_mock.json` and `result_uncertain_mock.json`: synthetic incomplete run caused by an external service error.
- Fixture passages, scores, and provider details are fabricated solely to exercise interface rendering. They are never demo evidence or benchmark data.
