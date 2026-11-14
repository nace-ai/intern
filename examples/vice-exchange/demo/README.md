# VICE EXCHANGE demo videos

Recorded 2026-11-14 on commit 701b34c. Two files:

- `demo/vice-exchange-demo.mp4`: 178.7 s, 1920x1080 H.264 + AAC stereo, narration and captions.
- `demo/vice-exchange-npcs.mp4`: 28.1 s, the multi-agent footage only, with narration.

## How the videos are put together
- **The game footage is the modified Open Theft Auto** (MIT-licensed open-source Godot city, see `integrations/ota-bridge/`):
  `integrations/ota-bridge/frames/vice_exchange_ota_hunt.mp4`, recorded by the native Godot clients against the real
  server in Drex mode. Shot in/out points are in `demo/ota-shots.json`. Per its author: the first 17 s (flyover, the
  job with the witness report and bulletin) are reused from an earlier clip in which the outlaw was a scripted test
  client, not the fugitive agent, so the caption says "a job", not Ryder; 17 to 53.5 s is new footage with the
  Drex-driven fugitive Ryder and a human hunter; the car and helicopter shots are reused from an earlier clip.
- **The browser is shown as the trading-floor terminal**. Terminal shots are composed to fill the frame: either a split screen (Open Theft Auto footage from the hunt clip on the left, the terminal's panels on the right; the two halves are different runs, not synchronised) or a zoom on one panel region. Source panels (decision cards, market, Agent
  Intelligence with the agent P&L strip and swarm graph). It is a separate real run on a fresh server: one headless
  Chrome as a spectator, captured with `Page.startScreencast` at 1600x900 (measured 48 fps
  during the fugitive chain and 57.8 fps during the proof section). Two more headless pages joined
  as human hunters in one crew ("WOLVES") so the crew forms on camera; they were not recorded.
- The Open Theft Auto clip and the terminal take are **different runs of the same server code**. Nothing is mocked.
- Cards (title, track quoted verbatim, "the game", "how it is built", end) are rendered HTML.

## What is real in the terminal take
- 16 agent decisions: 13 decided by Drex, 3 low-confidence guardrail, 0 fallback. By role: {'TRADER': 8, 'FUGITIVE': 2, 'WITNESS': 2, 'POLICE': 3, 'INFORMANT': 1}.
- Drex round trip in that run: median 307 ms, p95 402 ms over 16 calls. Live shadow comparison: 14 compared, 13 agreed, Meta median 721 ms.
- The fugitive agent Ryder (ticker RYD-01) was captured by Sgt. Imani Duarte (police NPC); the listing halted at exactly V$0.
- Traders, read from the server snapshot at the end of the take: [{'name': 'Priya Castellan', 'credits': 1997, 'holdings': {}}, {'name': 'Rook Halvorsen', 'credits': 2000, 'holdings': {}}]; trades: [['Priya Castellan', 'BUY', 'RYD-01', 125], ['Priya Castellan', 'SELL', 'RYD-01', 122]].
- Experiment table on screen: [{'mode': 'scripted', 'calls': 0, 'p50': None, 'p95': None, 'trades': 0, 'captured': True}, {'mode': 'meta', 'calls': 8, 'p50': 809, 'p95': 1076, 'trades': 2, 'captured': True}, {'mode': 'drex', 'calls': 8, 'p50': 326, 'p95': 366, 'trades': 2, 'captured': True}].
- Replay check: ok=True, 193 events, 0 mismatches (shown as the server's own response).

## Audio
- Narration: **Piper neural TTS, voice `en_US-ryan-high`** from the rhasspy/piper-voices repository (check that
  repository's model card for the voice's license before any use beyond this demo). Each line is loudness-normalised
  to about -16 LUFS. If Piper is missing, `assemble.mjs` falls back to macOS `say -v Daniel`.
- Bed and effect cues (a tick per resolved decision, a low hit on capture) are generated with ffmpeg sine and noise
  sources. No music or samples from anywhere else.
- The opening line names another game once, in speech only, as the inspiration; nothing on screen presents this
  project as that game.

## Not shown, and limits
- A crew bounty split or double-cross is not in the video: in the take the police reached the fugitive before the
  hunter crew did, so the crew moment is the crew forming and the trader agents reacting to it.
- The browser gun hunt (stun pulse in the browser 3D view) is not in the video; the gun footage is from the Open Theft
  Auto client. A driven browser version exists behind `HUMANS=1` in `record.mjs` but is untested end to end.
- The provider-failure moment (badge DREX: FALLBACK) is not shown; it is covered by the end-to-end harness.
- Nobody has listened to the final audio end to end; levels were checked with ffmpeg volumedetect only
  and silencedetect (no silence longer than 2.5 s in the final cut).

## How to re-record
```
python3.12 -m venv /tmp/vxtts && /tmp/vxtts/bin/pip install piper-tts    # use a SHORT path: Piper fails under very long paths
mkdir -p /tmp/vxvoice   # put en_US-ryan-high.onnx and .onnx.json from rhasspy/piper-voices there as ryan.onnx / ryan.onnx.json
PORT=3781 DATABASE_URL=/tmp/vx.sqlite WEB_ORIGIN=http://localhost:5781 npx tsx apps/server/src/index.ts
(cd apps/web && VICE_SERVER=http://localhost:3781 npx vite --port 5781 --strictPort)
OUT=/tmp/vx-take BASE=http://localhost:5781 API=http://localhost:3781 node demo/record.mjs      # start it right after the server boots
TESTS=<passing count> TAKE=/tmp/vx-take WORK=/tmp/vx-work node demo/assemble.mjs               # ONLY=main or ONLY=npcs for one file
```
Requires Google Chrome, ffmpeg and (for the neural voice) Piper. Record on a quiet machine with nothing else running.

## Main demo shot list
| At | Length | Shot | Speed | Caption | Narration |
|---|---|---|---|---|---|
| 0:00 | 12.3 s | hype | 1x | (card) | Are you guys ready for G T A six? Say woo! Well, it's not out yet. So, inspired by open-world games like it, we built our own. Vice Exchange: a city where every agent is a trader. |
| 0:12 | 5.5 s | title | 1x | (card) | Built on Open Theft Auto, an open source city. All the money is fictional game credits. |
| 0:17 | 18.9 s | track | 1x | (card) | The track is Swarm Intelligence: multi-agent systems. Build systems where multiple agents coordinate, compete, or negotiate, with Drex making every agent's calls. Think traders in a simulated market, characters that think for themselves, or a team of agents dividing up a research task. This project is the first two at once. |
| 0:36 | 14.4 s | game | 1x | (card) | Every agent is out for its own profit. They team up, and they turn on each other. The longer one survives, the more it is worth, and the bigger the bounty on its head. The game is Open Theft Auto, an open source city, modified so that a swarm of agents runs it. |
| 0:51 | 14.0 s | how-built | 1x | (card) | One step of the swarm. An agent sees something, or gets a message. One Drex call, built only from what that agent knows, returns a choice with probabilities. The server checks it is legal, applies it, and that triggers the next agent. |
| 1:05 | 8.4 s | ota-city | 1x | Inside the modified Open Theft Auto: witnesses, informants, traders, police and Ryder, a Drex-driven fugitive with his own ticker. | Nine agents decide with Drex: two witnesses, two informants, two traders, two police, and Ryder, a fugitive who plays the outlaw by himself. |
| 1:13 | 7.5 s | ota-job | 1.6x | A job is witnessed and reported. Police publish a bulletin: WANTED, and the price falls. | A job is done. A witness sees it and reports. Police put out a bulletin. The outlaw is wanted, and the price falls. |
| 1:21 | 8.0 s | terminal-chain | 2.32x | The trading floor terminal, same server: each agent's decision card with options, probabilities and latency, and the swarm graph. | The trading floor terminal shows every decision as it happens, with the options, the probabilities and the latency. |
| 1:29 | 7.3 s | crew | 1.1x | Two human hunters form a crew to split the bounty. The trader agents see it and each decides what to do about it. | They team up. Two human hunters form a crew to split the bounty. The traders see it, and each one decides what to do about it. |
| 1:36 | 7.5 s | pnl | 1x | After the capture: the officer is paid the bounty, the witness who reported gets a tip, and the traders mark their books. | Every agent is out for its own profit. The officer earns the bounty. The witness who reported earns a tip. The traders mark their books. |
| 1:43 | 6.7 s | memory | 1.26x | Each agent knows only what it saw or was told: source and hop count on every fact. | Each agent knows only what it saw, or what it was told. Every fact carries its source and how many hands it passed through. |
| 1:50 | 7.9 s | traders | 1x | Traders in a simulated market, each deciding with Drex to grow its own V$: a new ticker lists and both trader agents decide. | Traders in a simulated market, each deciding with Drex to grow its own money. A new stock lists, and both traders make their call. |
| 1:58 | 11.4 s | traders-wallets | 1x | Priya Castellan (momentum): 1 buys, 1 sells, wallet V$1997, 0 shares. Rook Halvorsen (risk-averse): 0 buys, 0 sells, wallet V$2000, 0 shares. | Priya chases momentum. In this run she bought 1 time and sold 1, and has 1997 credits left. Rook is risk averse. He held every time and kept all 2000 credits. |
| 2:09 | 7.1 s | ota-hunt | 1.83x | Ryder fires his stun pulse at a human hunter. An officer pursues on a Drex decision and captures him: V$0. | Ryder turns on a human hunter and stuns him. Then an officer, on a Drex decision, runs him down. His stock goes to zero. |
| 2:16 | 6.3 s | ota-capture | 2.46x | The hunter chases the next fugitive, stuns him and makes the capture: the ticker goes to exactly V$0 and the bounty is paid. | The hunter goes after the next one, stuns him, and takes the bounty. The longer he survived, the bigger it is. |
| 2:23 | 5.0 s | ota-carheli | 2.3x | Still Open Theft Auto (MIT-licensed, open source): steal a car or take the helicopter. | And it is still Open Theft Auto. Steal a car, or take the helicopter. |
| 2:28 | 5.0 s | ndi | 1.16x | Police decisions cite facts from a real document. | Police decisions cite a real San Francisco police order, parsed with N D I. |
| 2:33 | 12.7 s | experiment | 3.24x | This run's metrics panel: 16 NPC decisions: 13 decided by Drex, 3 low-confidence guardrail, 0 fallback. Then one seed, three ways: scripted, Meta LLM, Drex (sped up). | In this run the metrics panel counts 16 decisions: 13 decided by Drex, 3 by the low confidence guardrail, and 0 by fallback. Then the same seed runs three ways: scripted, a language model, and Drex. |
| 2:45 | 3.0 s | replay | 1x | The event log replayed with no model calls matches live state (ok=true, 193 events, 0 mismatches). | And the log replays with zero model calls. |
| 2:48 | 9.8 s | end | 1x | (card) | Every agent is out for its own profit. They team up, and they turn on each other. The longer one survives, the more it is worth, and the bigger the bounty on its head. Vice Exchange. |

## NPC video shot list
| At | Length | Shot | Speed | Caption | Narration |
|---|---|---|---|---|---|
| 0:00 | 7.3 s | ota-city | 1.1x | Nine agents in the modified Open Theft Auto city: witnesses, informants, traders, police and the fugitive Ryder. | Nine agents share this city: witnesses, informants, traders, police, and a fugitive. Every one of them decides with Drex. |
| 0:07 | 6.3 s | ota-job | 1.9x | A job is witnessed and reported: bulletin, WANTED. | When a job is done, only the agents who saw it react. A witness reports, and a bulletin goes out. |
| 0:13 | 5.5 s | terminal | 3.37x | Several agents decide at once. Each card shows who decided, the options, probabilities and latency. | Traders and officers each make their own call, shown live with the options and the probabilities. |
| 0:19 | 5.0 s | ota-hunt | 2.6x | The fugitive agent stuns a human hunter; an officer captures him on a Drex decision. | The fugitive stuns a hunter. An officer runs him down, and the stock goes to zero. |
| 0:24 | 4.0 s | ota-capture | 3.88x | The hunter stuns the next fugitive and takes the bounty. | Then the hunter takes the next one, and the bounty. |
