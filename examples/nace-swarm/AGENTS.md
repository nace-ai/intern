# Nace Swarm: election simulation with Drex voter agents

You are building a small Python project in this folder. Read TASKS.md and do only the
milestone you were asked for. Run its check command before you say you are done.

## The idea
- **The agents:** a swarm of voter agents lives on a 40x40 grid. Each has wealth, age, gender, health,
  a location, a political lean (-1 = left, +1 = right) and a memory: a list of snapshots of its own past
  states and votes.
- **The country:** has a CountryState: stock_market, class_mobility, foreign_relations, unemployment,
  healthcare_access, each a float from 0 to 1.
- **Each round:**
  1. Two parties, "Left" and "Right", each get 5 candidates written by an LLM. Each candidate has
     policies on foreign, economic and social_welfare: a number from -1 (left) to +1 (right) plus a
     one-line description.
  2. Primaries: left-leaning agents (lean < 0) vote in the Left party's primary and right-leaning
     agents in the Right party's. The top candidate of each wins.
  3. General election: every agent votes between the two winners, or abstains.
  4. The winner's policies change the CountryState (fixed formulas, see policy.py).
  5. The CountryState change updates each agent's wealth and health. The agent's lean is recomputed,
     it moves on the grid (x drifts toward the left edge if lean < 0, the right edge if > 0), and a
     snapshot is added to its memory.
- **The page:** a web page replays the rounds: dots colored blue (left) to red (right), with arrows from
  each dot's previous position to its current one.

## Stack and rules
- Python 3.12, managed with uv: run `uv add <pkg>` to add a dependency and `uv run ...` to run anything.
- Dependencies: httpx, pydantic, numpy, openai, pytest.
- Package `swarm/` with modules models.py, individualizer.py, drex.py, llm.py, policy.py,
  election.py and sim.py. Tests go in `tests/`. The web page is `web/index.html`, plain HTML/CSS/JS
  with no build step, and its colors are CSS variables.
- Every module that calls a network API has a fake mode (`fake=True`) that needs no network.
  All tests use the fake mode.
- Keep functions small and typed. Don't add features that aren't in TASKS.md.
- Never print or log API keys. Read them from the environment: `NACE_API_KEY` and `DEEPINFRA_API_KEY`.

## Drex API (Nace's decision model): exact facts, don't guess
- Send `POST https://console.nace.ai/v1/systemone` with the headers
  `Authorization: Bearer $NACE_API_KEY` and `Content-Type: application/json`. Use a 60 s timeout.
- Request body:
  {"model": "drex-latest",
   "state": <any JSON: the evidence>,
   "questions": {
     "<name>": {"type": "choice", "instructions": "<question>",
                "criteria": {"<option_id>": "<what this option means>", ...}},
     "<name>": {"type": "score", "instructions": "...", "criteria": ["lowest level", "...", "highest level"]},
     "<name>": {"type": "noul", "instructions": "<yes/no question>"}}}
- Response body:
  {"model": "...", "answers": {
     "<name>": {"type": "choice", "choice": "<option_id>", "confidence": 0.98,
                "probabilities": {"<option_id>": 0.98, ...}},
     "<name>": {"type": "score", "score": 1.53, "probabilities": {...}},
     "<name>": {"type": "noul", "noul": 0.97}},
   "usage": {"input_tokens": 106, "output_tokens": 212}}
- Limits: at most **16 questions** per request and **8 requests in flight**. Use an asyncio.Semaphore(8).
- Retry HTTP 429, 529 and 5xx up to 3 times with backoff, honoring the `retry-after-ms` header.
  Never retry 401, 402 or 422; raise a clear error instead.
- Questions can't see each other's answers. So batch up to 16 agents per request: put
  `{"agents": {"a17": {...}, ...}, "country": {...}, "candidates": {...}}` in `state` and ask one choice
  question per agent, named `vote_a17`, whose instructions say:
  "You are agent `agents.a17`. Given your wealth, health, age, memory and the `country` state, which
  candidate in `candidates` would you vote for?"
- Put evidence in `state`, never conclusions. Keep the `state` format identical on every call.
  Every general-election choice includes an `abstain` option.

## Qwen (candidate writer)
- Use the openai package with `base_url="https://api.deepinfra.com/v1/openai"`,
  `api_key=$DEEPINFRA_API_KEY` and `model="Qwen/Qwen3.8-27B"`.
- Ask for JSON only and validate it with pydantic. Retry once if it doesn't validate, then fall back
  to the fake candidates.
