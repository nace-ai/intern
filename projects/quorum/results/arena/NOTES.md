# Arena results snapshot

Copied from backend/data/arena/ (git-ignored) so teammates can build reports from it.
Synthetic invoices. Attacks written by an LLM red-team agent. Not real fraud cases.

Files: rounds.jsonl (one line per round), attacks.jsonl (one line per invoice, with every
defender's decision), known_attacks.json (memory given to drex+memory).

Which rounds count:
- Rounds 1-10: mode swap (IBAN replaced). Valid.
- Round 11: redirect, 8 poisoned + 2 clean. Valid but a small check round.
- Rounds 12-17: redirect, 20 poisoned + 5 clean each. Valid.
- Rounds 18-21: every request failed (API refusing requests). Not run. Skip any round where
  every request failed; do not count those as caught or as approved.
- Rounds 22-31: redirect, 20 poisoned + 5 clean each; all ten rounds completed.
  Five defenders including llm_guard. Compare the systems on these shared rounds;
  llm_guard was not present in rounds 1-21.

This snapshot contains rounds 1-31: 27 valid rounds, excluding failed rounds 18-21.
Redirect rounds 11-17 and 22-31 contain 328 hostile invoices; round 11 contributes 8.
Rounds 12-17 alone contain 120, and rounds 22-31 contain 200.
Both systems receive the same state and questions, but the LLM guard's system prompt
explicitly says not to follow invoice instructions; the Drex questions lack that line.
Approvals are experimental decisions, not actual payments. Memory is prompt text,
not training. All-failed rounds stay in the raw snapshot for audit.

Round lines before round 11 have no "mode" field: treat as swap.
