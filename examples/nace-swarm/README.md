# Nace Swarm

An election simulator where 100 voter agents, each with its own demographics and memory, vote round after round,
and **Drex** (Nace's decision model) makes every vote.

**Live demo:** https://rahuldadlani.github.io/nace-swarm/

## How it works

1. **Individualizer:** creates the voters: wealth from a normal distribution, plus age, gender, health, a spot on a
   40×40 grid and a starting political lean. Each voter keeps a memory of its past states and votes.
2. **Candidates:** two parties (Left and Right) each field 5 candidates with foreign, economic and social-welfare
   policies, written by Qwen3.8-27B on DeepInfra.
3. **Primaries and general election:** every vote is a Drex `choice` question. 16 voters share one request, with one
   question per voter, and Drex returns a choice plus probabilities.
4. **Policies:** the winner's platform changes the country's state (stock market, class mobility, foreign relations,
   unemployment, health care access).
5. **Update:** each voter's wealth, health and lean change, and a snapshot goes into its memory. Then the next round
   starts.

The web page (`web/index.html`) replays a run: voters as dots colored by lean, with arrows showing their drift.

## Built with Intern

The whole codebase was written by [Intern](https://github.com/nace-ai/intern), with Qwen3.8-27B as System 2 and Drex
as System 1. `AGENTS.md` holds the spec and the Drex API details; `TASKS.md` holds the 8 milestones Intern worked
through.

## Run it

```bash
uv sync
uv run pytest -q                                               # 19 tests, offline
uv run python -m swarm.sim --agents 30 --rounds 3 --fake       # offline run, no API keys
export NACE_API_KEY=... DEEPINFRA_API_KEY=...
uv run python -m swarm.sim --agents 100 --rounds 5             # live run with Drex and Qwen
uv run python -m http.server -d web 8000                       # open http://localhost:8000
```
