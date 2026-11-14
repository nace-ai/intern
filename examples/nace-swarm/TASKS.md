# Milestones (one intern run each)

Run: intern --system1 drex-latest --system2 deepinfra/Qwen/Qwen3.8-27B "Do milestone N in TASKS.md"

## 1. Project skeleton and models
- `uv init`, add the dependencies from AGENTS.md, and create `swarm/__init__.py`.
- swarm/models.py, with pydantic models:
  - `Snapshot`: round, wealth, health, lean, x, y, vote, vote_probs.
  - `Agent`: id, wealth, age, gender, health, x, y, lean, history: list[Snapshot].
  - `CountryState`: the 5 floats from AGENTS.md.
  - `Policy`: value in -1..1 and text.
  - `Candidate`: id, party, name, slogan, and policies: dict with the keys foreign, economic and
    social_welfare.
- Check: `uv run python -c "import swarm.models"`.

## 2. Individualizer (Service 1)
- swarm/individualizer.py, `make_agents(n: int, seed: int) -> list[Agent]`, using numpy:
  - wealth ~ normal(mean 50_000, sd 20_000), clipped at 0
  - age uniform 18-90; gender from a list; health uniform 0.3-1
  - x, y uniform on the 40x40 grid
  - starting lean = tanh(0.8 * z(wealth) + 0.3 * z(age) + noise), where z() is the z-score
- tests/test_individualizer.py: the same seed gives the same agents, the lean is within [-1, 1], and
  the mean wealth is near 50k.
- Check: `uv run pytest -q`.

## 3. Drex client
- swarm/drex.py, `async def vote(agents, country, candidates, stage, fake=False)
  -> dict[agent_id, (choice, probabilities)]`, following the AGENTS.md Drex section exactly:
  - batch 16 agents per request, with a Semaphore(8), retries, and the `abstain` option in the general
    election
  - `fake=True` picks the candidate whose average policy is closest to the agent's lean
- tests/test_drex.py (fake mode only):
  - every agent gets a vote
  - the requests built for 40 agents number 3, each with 16 or fewer questions
- Check: `uv run pytest -q`.
- Then one live check, when a key is set:
  `uv run python -m swarm.drex --live-check` sends one request for 2 agents and prints the choices,
  the `model` field and `usage`. (The user runs this.)

## 4. Candidate writer
- swarm/llm.py, `generate_candidates(party, country, prev_winner, fake=False) -> list[Candidate]`
  (5 per party).
  - The Left party's policy values lean negative, the Right party's positive, with variety between
    candidates.
  - Fake mode returns fixed candidates.
- tests/test_llm.py: fake mode gives 5 valid candidates per party.
- Check: `uv run pytest -q`.

## 5. Policy formulas
- swarm/policy.py:
  - `apply_policies(country, winner) -> CountryState`, using small fixed formulas clamped to 0..1:
    - a right-leaning economic policy raises stock_market and lowers class_mobility
    - a left-leaning social_welfare policy raises healthcare_access and lowers unemployment
    - a foreign policy far from 0 lowers foreign_relations
  - `update_agent(agent, old_country, new_country, vote, probs, round) -> Agent`:
    - wealth changes with the stock_market change, scaled by the agent's wealth, plus a
      class_mobility bonus for poorer agents
    - health changes with healthcare_access
    - new lean = 0.7 * old lean + 0.3 * (P(right-party winner) - P(left-party winner))
    - x moves by lean * 2, clamped to the grid
    - append a Snapshot to the agent's history
- tests/test_policy.py: values stay in range, and the history grows by 1.
- Check: `uv run pytest -q`.

## 6. Election round and simulation CLI
- swarm/election.py, `async def run_round(world, k, fake)`, following steps 1-5 of "Each round" in
  AGENTS.md. It returns a round record: candidates, primary tallies, general tally, winner, the
  country before and after, and every agent's (id, x, y, prev_x, prev_y, lean, vote, probs).
- swarm/sim.py, run as `python -m swarm.sim --agents 100 --rounds 5 --seed 1 [--fake]`, writes
  `web/data/run.json` with the shape {"rounds": [record, ...]} and prints the tallies.
- Check: `uv run python -m swarm.sim --agents 30 --rounds 3 --fake`.
  The votes must not all go one way, and the leans must change between rounds.

## 7. Web page
- web/index.html loads `data/run.json` and shows:
  - a canvas grid of dots colored blue-to-red by lean, with arrows from (prev_x, prev_y) to (x, y)
  - a round slider and a play button
  - each party's candidates with the winner highlighted
  - bar charts for the primary and general tallies
  - small line charts of the CountryState over the rounds
  - clicking a dot shows that agent's memory timeline
- Check: `uv run python -m http.server -d web 8000`, then the user opens http://localhost:8000.

## 8. Live run
- With real keys: `uv run python -m swarm.sim --agents 100 --rounds 5`. Print the total Drex usage.
