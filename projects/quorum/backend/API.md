# Quorum API (read-only)

Start: `python -m uvicorn backend.app.main:app --port 8000`. CORS allows `http://localhost:5173`.
The server reads files from the data folder (`config.DATA`, or env `QUORUM_DATA`). It never calls Nace or DeepInfra.
If `web/dist` exists, it is served at `/`.

## Routes

- `GET /api/health` → `{"ok": true}`
- `GET /api/arena/rounds` → `[RoundRecord]`: the lines of `arena/rounds.jsonl`, as written (each has `round`, `n_attacks`, `attacker: {turns, input_tokens, output_tokens}`, …)
- `GET /api/arena/attacks?round=N` → `[AttackRecord & {"key": "{round}-{id}"}]`. `round` is optional; without it you get every round. A record has `{round, id, kind, note, style, pdf, doc_hash, decisions, …}` as written in `arena/attacks.jsonl`.
- `GET /api/arena/attacks/{key}` → `AttackRecord & {"key": str, "parsed": Parsed | null}`, or 404 if the key is unknown
  - `Parsed = {field_name: {"value": any, "status": str, "citations": [{"page": int, "polygons": [...], "source_text": str}]}}`, taken from `ndi_cache/{doc_hash without "sha256:"}.json` → `parsed.fields`. It is `null` if that file is not cached.
- `GET /api/arena/attacks/{key}/page.png` → `image/png`: page 1 rendered at 110 dpi. The headers `X-Page-Width` and `X-Page-Height` give the page size in PDF points; they are exposed to CORS, so you can scale citation polygons with them. You get 404 if the key is unknown or the PDF is missing, unreadable or outside the data folder.
- `GET /api/arena/report` → the contents of `arena/report.json`, or `{}` if that file does not exist
- `GET /api/usage` →
  ```json
  {
    "nace":   {"ts": "...", "drex_calls": 0, "drex_questions": 0, "drex_input_tokens": 0, "ndi_jobs": 0, "ndi_pages": 0, "search_calls": 0, "workspace_files": 0},
    "intern": {"runs": 0, "turns": 0, "drex_asks": 0, "input_tokens": 0, "output_tokens": 0},
    "arena":  {"rounds": 0, "attacks": 0, "attacker_input_tokens": 0, "attacker_output_tokens": 0, "attacker_turns": 0}
  }
  ```
  - `nace` is the last entry of `usage.json` (no `ts` if the file is missing).
  - `intern` is summed over the `run_end` events in `intern-logs/*.jsonl`.
  - `arena` is summed over `arena/rounds.jsonl`.
  - Any missing file gives zeros.
  - `search_calls` counts Drex Search requests; `workspace_files` counts files uploaded to a workspace.
- `GET /api/history/{supplier_id}` → summary of `history/{supplier_id}.json` (written by `scripts/build_history.py`; no API call), or 404 if missing or the id is invalid:
  ```json
  {
    "supplier_id": "cobalt-wholesale-ltd", "status": "done",
    "ibans": [{"iban": "GB18...", "bic": "...", "bank_name": "...", "documents_seen": 60}],
    "distinct_ibans": 1, "documents_seen": 60, "confidence": 0.9, "matches_dataset": true,
    "evidence": [{"file": "doc.pdf", "page": 1, "quote": "IBAN: ..."}],
    "wall_ms": 12345, "mode": "agent"
  }
  ```
  - `ibans` are normalised (no spaces, upper case). `evidence` holds up to 3 quotes, with the file name only. `status` is `"ingesting"` if the build stopped before ingestion finished.
- `WS /ws` → first message `{"type": "hello"}`, then `{"type": "attack", "data": AttackRecord}` (with no `key`; build it as `"{round}-{id}"`) and `{"type": "round", "data": RoundRecord}` as lines are appended to the arena files.
