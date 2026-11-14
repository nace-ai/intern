# VICE EXCHANGE: pitch for the Swarm Intelligence track

> **The game is our own.** VICE EXCHANGE is an original game: our own city (Coral Bay, in 2D and 3D in the browser), our own authoritative server, market, agents and rules, with nothing to install. Separately, the repo contains an optional extra client that connects the open-source, MIT-licensed project Open Theft Auto to the same server (`integrations/ota-bridge`); it is not needed to play or judge the game, and its code and assets are not included here.

The track, verbatim:

> Swarm Intelligence: multi-agent systems. Build systems where multiple agents coordinate, compete, or negotiate,
> with Drex making every agent's calls. Think traders in a simulated market, NPCs that think for themselves, or a team
> of agents dividing up a research task.

Every number below is measured. Its source is named next to it. V$ are fictional game credits.

## 30-second pitch (spoken)

VICE EXCHANGE is a trading floor inside a city, where every agent thinks for itself. Nine agents run on Drex: two
witnesses, two informants, two traders, two police officers, and a fugitive called Ryder. Ryder is a stock. The longer
he survives, the more he is worth and the bigger the bounty on him. Witnesses decide whether to report him. Informants
decide who to sell that to. Traders buy or sell his ticker. Police hunt him. When he is captured his stock goes to
exactly zero. Each of those is one Drex call you can click and inspect: the options, the probabilities, the latency.
And humans can join the hunt.

## 2-minute pitch (spoken)

The track asks for multiple agents that coordinate, compete or negotiate, with Drex making every agent's calls. It
gives two examples: traders in a simulated market, and NPCs that think for themselves. VICE EXCHANGE is both at once.

Multiple agents. Nine of them decide with Drex: two witnesses, two informants, two traders, two police officers, and
Ryder, a fugitive who plays the outlaw by himself. Each has a role, a goal and a private memory. An agent only knows
what it saw or what another agent told it.

Coordinate. A witness sees Ryder do a job and reports it. Police publish a wanted bulletin and set off. An informant
passes a rumor to the traders. Human hunters can form a crew and split the bounty.

Compete. Two traders with different temperaments take different positions on the same ticker. Police and human hunters
race for the same capture. A hunter can stun a crewmate and take the whole bounty.

Negotiate, stated plainly: informants make priced side deals. An informant can sell Ryder's location to a hunter crew,
or sell Ryder a warning, and can switch sides later. It is a fixed-price choice, not free-form bargaining.

Drex makes the calls. Every decision is one typed choice question built only from that agent's own knowledge. Click any
agent and you see the options, Drex's probability for each, the request id and the latency. In our last end-to-end run
Drex answered in about a third of a second. The server only checks that the chosen action is legal, then applies it.
When Drex is unsure or unreachable the decision is labeled as a guardrail or a fallback. It is never shown as Drex.

The bonus. Police decisions carry nine rules that NDI parsed from a real San Francisco Police Department foot-pursuit
order, each with its page number.

The proof. One button runs the same seeded scenario three ways: scripted rules, a Meta language model, and Drex, with
measured latency side by side. The whole event log replays with zero model calls and matches the live state. And 247
automated tests pass.

## The track's words, and where a judge can click

| Track wording | In the product | Where to look |
|---|---|---|
| "multiple agents" | Nine Drex-driven agents: 2 witnesses, 2 informants, 2 traders, 2 police, and the fugitive Ryder (ticker `RYD-nn`) | Agent Intelligence panel and swarm graph; Ryder in the city and the market panel |
| "coordinate" | Witness -> police -> public bulletin; informant -> traders; hunter crews share a bounty among members within 25 m | Event timeline (click an event to highlight its causal chain); swarm graph; crew chip in the hunter's HUD |
| "compete" | Traders take different positions on one ticker; police and human hunters go for the same capture; a crew double-cross takes the whole bounty | Trade feed and decision cards; the crash sequence names the captor and the payout |
| "negotiate" | Informant side deals: `sell_to_crew` or `warn_outlaw` for a V$40 fee, and switching sides is recorded. A priced choice, not free-form bargaining | Informant decision in the inspector; `INFORMANT_SWITCHED_SIDES` in the timeline |
| "Drex making every agent's calls" | One typed `choice` per decision; the server validates the label against the legal actions and applies it | Agent Inspector: options, probabilities, confidence, request id, round trip, source badge |
| "traders in a simulated market" | Two trader agents buy, hold or sell fictional tickers on one shared exchange, through the same settlement path as humans | Market panel, trade feed |
| "NPCs that think for themselves" | Each agent decides from its own private memory; nothing is shared unless a message carried it | Inspector: "what it knows", with source and hop count |
| NDI bonus | Nine quoted rules from SFPD General Order 5.25 "Foot Pursuits" in every police decision, with page numbers | Police decision in the inspector; `npm run ndi:ingest` |

## Measured numbers and their sources

| What | Value | Source |
|---|---|---|
| Automated tests | 247 passed in 24 files | `npm test`, run at 13:57 on 2026-11-14 |
| Live decisions in the latest end-to-end run | 12: 10 decided by Drex, 2 by the low-confidence guardrail, 0 fallback; all four NPC roles decided live | `e2e/out/report.json` (finished 21:30 UTC), `measured` |
| Drex round trip in that run | median 355 ms, p95 752 ms over 12 calls; provider evaluation median 187 ms | same file, `first_run_metrics.latency.drex` |
| Live Drex versus Meta on the same decisions | 12 compared, 12 agreed; median 355 ms Drex, 722 ms Meta | same file, `first_run_metrics.shadow` |
| Experiment, one 20 s run per mode | Meta: 7 calls, median 696 ms, p95 1674 ms. Drex: 7 calls, median 288 ms, p95 386 ms. Scripted: 0 calls | same file, `measured.experiment` |
| Provider failure | 4 of 4 decisions labeled fallback (auth, then circuit open); header badge `DREX: FALLBACK`; room kept running | same file, `measured.provider_failure` |
| Recorded demo run | 10 decisions, all 10 by Drex; median 389 ms; shadow 10 compared, 7 agreed | `demo/README.md` |
| Police pacing | Job to capture of a stationary outlaw: 25.6 s scripted, 28.4 s Meta, 26.3 s Drex, one run each | `.swarm/board/ENGINE.md` / `.swarm/BRIEF.md` |
| NDI | Parse job 3.6 s, extract job 5.9 s, 9 of 9 fields found with page and quote | `.swarm/board/NDI.md` |
| UDP world stream (native Godot client) | 29 Hz received with 0% loss; 290 world updates over UDP against 105 over WebSocket in the same scripted check | UDP agent's report, headless client |

These are single runs on one laptop. They are demonstrations, not benchmarks. Agreement between Drex and Meta has
ranged from 7 of 10 to 12 of 12 across runs.

That end-to-end run did not pass every check: `S03.socket-idempotent` failed on the provider-failure instance and
`S07.socket-concurrent` was blocked because the scripted hunters' test moves were rejected as too fast. The same
behaviours (exactly-once job, one winner of two simultaneous captures) pass in `apps/server/test/acceptance.test.ts`.

## Judge Q&A

**Is every decision really Drex?**
In Drex mode every agent decision is sent to Drex as one typed `choice` question. Two outcomes are not Drex's choice and
are labeled as such: when the margin between Drex's top two labels is under 0.1 the agent takes its safe action
(`drex_low_confidence_guardrail`, Drex's real numbers kept in the trace), and when the call fails or times out a rule
decides (`fallback`, with the error type and no confidence). In the latest end-to-end run that was 10 Drex, 2
guardrail, 0 fallback out of 12. The other two modes, scripted and Meta, exist only as labeled baselines.

**What happens when Drex is down?**
The room keeps running. Decisions are labeled `fallback`, the header badge changes to `DREX: FALLBACK`, and a circuit
breaker stops calling for 15 seconds. This is tested with a rejected key, a 429 and a hung provider
(`apps/server/test/acceptance.test.ts`, `apps/server/src/decisions/scheduler.test.ts`) and was seen in the end-to-end run.

**Why is this not LLM role-play?**
No agent produces text. Each decision is a label from a short list of actions that are legal at that moment, and the
label changes shared state: a message to another agent, an order on the exchange, a police move, a shot. The server
owns positions, wallets, prices and the capture rule. A decision that arrives after the world moved on is rejected.
Private memory is enforced in code and tested: an agent that was told nothing has no trace of the incident in the state
sent to Drex (`apps/server/src/agents/brains.test.ts`).

**How do agents hunt an agent?**
Ryder is a server-side outlaw that thinks every six seconds, and at once when he becomes wanted or is stunned. His
options are `do_job`, `lay_low`, `flee` and `fire`, built only from what he can see. He acts through the same commands
a human client sends. After a capture a new fugitive with a new ticker appears twelve seconds later
(`apps/server/src/demo/Fugitive.ts`, `apps/server/test/fugitive.test.ts`).

**Where do humans come in?**
A human joins as an outlaw (their own ticker), a hunter (alone or in a crew of up to four) or a spectator. Humans and
agents use the same rules, the same market and the same capture.

**What is not built?**
Free-form negotiation between agents; persistent accounts and rooms that survive a restart; matchmaking; cloud
deployment. Guns stun for three seconds and do no damage: capture is the only thing that ends a character. Moving
traffic and background pedestrians are scenery, not agents. No Adobe tooling was used.

**What is the 3D world built on?**
The browser has its own 2D and 3D city. The larger 3D world is a client built on the open-source, MIT-licensed Godot
project Open Theft Auto, connected to the same server. Its upstream assets are not in this repository. The project
does not use, include or depend on any commercial game's code or assets.

**Is any of the money real?**
No. V$ are fictional, nonredeemable game credits. There is no payment code in the repository.
