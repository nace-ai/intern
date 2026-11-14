# Example: an `AGENTS.md` for a Drex app

A worked example of project rules for System 2, from [Drex Poker Arena](https://github.com/diyajh/PokerArena),
built at Drexathon (Nov 2026): a Flask app where five poker agents make every move with a Drex call.
The project's own `AGENTS.md` is what Intern reads when it works in that repository
(see [the instruction files table](../../README.md)).

## What the file covers

Rules that a reviewer would otherwise repeat on every change:

- **Where things live.** One line per module, so the intern edits the right file instead of searching.
- **How to run and check it.** The exact commands, including a live check that must still pass.
- **Invariants.** Things that are easy to break silently: chips must be conserved after any engine
  change; every bot move must stay a Drex decision; only legal actions are offered to Drex.
- **Honesty in the UI.** Only label a result "Drex" when Drex answered; fallbacks say so.
- **Secrets.** Never commit `.env` or print keys.

## Excerpt

```markdown
## Rules
1. Never commit `.env` or print API keys. Keys live only in `.env` (git-ignored).
2. Chips must be conserved. After any engine change, run a long bots-only simulation and assert the total
   chips never change.
3. Every bot move must stay a Drex decision. Personalities may reshape Drex's probabilities, but never
   replace Drex with hard-coded play.
4. Only legal actions are offered to Drex. Keep it that way.
5. Before finishing: `python -c "import app"` must succeed, and the demo hand must still run end to end.
```

## Running Intern on it

```bash
git clone https://github.com/diyajh/PokerArena && cd PokerArena
export NACE_API_KEY=...          # System 1: Drex from the Nace console
export OPENROUTER_API_KEY=...    # System 2
intern --system2 openrouter/qwen/qwen3.8-27b \
  "Add a personality called Copycat that mirrors the last opponent's action"
```

Instruction files are followed literally (see [usage.md](../usage.md)): keep commands in `AGENTS.md` to
ones you are happy for the intern to run with your shell's credentials.
