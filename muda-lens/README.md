# Muda Lens 🦖

**Live demo:** https://manjeetsingh-satveer.github.io/muda-lens/ (live Drex calls and photo diagnosis need `npm start` locally with API keys)

**Track: Apex Predator, Drex vs. the Giants.**
In semiconductor manufacturing, a Lean Six Sigma black belt makes the same few decisions every week. We give those decisions to **Drex** and test it on cases with known answers against a frontier LLM (Claude Opus 5.5) and classic rule-based SPC.

## The problem

During a summer internship in power-semiconductor manufacturing I saw *muda* (waste) everywhere. Lots queued for days at litho and probe, inspections were repeated beyond the control plan, wafers were carried across the fab, and technicians quietly re-tuned recipes by hand. The data to catch all of this already exists. The hard part is the **decision**: which waste matters most, where it started, which fix to run, and whether the lots can ship.

## The decision Drex makes

Each week the line's data (12 process steps, current week vs. historical baseline) goes to Drex as the `state`, with four typed `choice` questions answered in **one pass**:

| Question | Options |
|---|---|
| `root_waste` | the 8 Lean wastes (TIMWOODS) |
| `culprit_step` | the 12 process steps (oxidation → final test) |
| `countermeasure` | CONWIP/DBR · re-center + SPC · DOE · 8D · sampling plan · standard work · AMHS re-layout |
| `disposition` | release · hold · rework |

Drex returns a choice and a **probability for each option**, which gives the engineer a calibrated confidence value instead of free text.

## The benchmark (why it's an honest test)

`bench/` builds **70 cases with known answers** (10 for each of 7 fault types: bottleneck, mean shift, extra variation, defect spike, over-inspection, manual overrides, transport). Each case contains:
- **one injected fault** at a random step, with random severity, which is the right answer
- **one decoy** problem at another step, sometimes with a *bigger raw change* than the real fault
- ±15% background noise on every step
- a plant rule that every contender is told: *product-quality risk outranks flow waste*

A case counts as correct only if **all 4 decisions** are right. The same data and option text go to every contender.

| Contender | All-4 correct | $ / 1k decisions | p50 latency |
|---|---|---|---|
| Rule-based SPC (no AI) | 67.1% | $0 | <1 ms |
| **Drex (drex-max-v1.1)** | **94.3%** | **$0.16** | **283 ms** |
| Claude Opus 5.5 | 92.9% | $17.18 | 2,313 ms |

**Headline: Drex matches a frontier LLM on accuracy (a 1-case difference out of 70 is a tie) while being ~8× faster and ~105× cheaper.** On a line making thousands of lot decisions a day, that's the difference between $17 and $0.16 per thousand calls, and between a 2–3 s wait and an instant answer at the tool.

Drex gets **30/30 quality cases** (drift, variation, defect spikes) right; the rules engine gets 11/30 because it falls for the decoys.

**How we got from 74% to 94%:** in the first version Drex read raw numbers in mixed units (queue hours, transport metres, inspection counts) and sometimes picked whichever looked biggest. Now the Lean/SPC analytics compute a `changes_vs_baseline` table (only increases beyond normal noise, each tagged with its waste family), and Drex makes the judgment call: which change matters most under the plant rule. The input is shorter too, so the cost dropped from $0.27 to $0.16 per 1k decisions. To avoid tuning on the test set, we tuned on a **separate dev split** (`npm run bench -- --split dev`, different seeds) and scored the 70 test cases once: dev 94.3%, test 94.3%.

**Calibrated hand-off:** when Drex's lowest confidence across the 4 decisions is below 50%, the dashboard sends the case to an engineer. On the test set that let Drex decide 69/70 cases on its own at 95.7% accuracy.

## Run it

```bash
npm install
cp .env.example .env      # add DREX_BASE_URL / DREX_API_KEY (and ANTHROPIC_API_KEY for the baseline)
npm start                 # dashboard + live "Ask Drex" at http://localhost:5173
npm run bench             # full benchmark -> bench/results.json -> shown in the dashboard Arena
npm run bench:quick       # 14-case smoke run
npm test
```

You can use Drex hosted (Nace console) or with open weights run locally (`serve.py` from `nace-ai/drex-decision-models`, then `DREX_BASE_URL=http://127.0.0.1:8000`).

## Run it with Intern

[Intern](https://github.com/nace-ai/intern) can work this repository. Its System 2 does the reading and the edits. Drex, as System 1, watches every turn. `AGENTS.md` is the brief it reads from this repo: synthetic data only, keys stay in `.env`, and a bench case counts only when all four decisions are right.

```bash
uv tool install "intern-agent @ git+https://github.com/nace-ai/intern"
export NACE_API_KEY=...          # System 1: Drex
export OPENROUTER_API_KEY=...    # System 2: any chat model you choose
intern --system1 decisions:nace-ai/drex-max \
  --system2 openrouter/qwen/qwen3.8-27b \
  "Read this fab and say which waste to name at standup, and whether the lots should ship."
```

## Photo diagnosis (Drex Max reads images)

An engineer uploads up to 8 photos from the line: a die under the microscope, wire bonds, an SPC chart screenshot, a shelf of waiting lots, or the shop-floor layout. Drex Max answers six typed questions about them in one pass: **what it shows, defect type, Lean waste, countermeasure, lot disposition, and severity**, each with a confidence value. When any answer is under 50% confident, the page asks for an engineer's review. Photos are downscaled in the browser to stay under the API's 4 MiB request limit.

- **Take a photo:** uses the webcam on a laptop and the rear camera on a phone. With `npm start` running, the page shows a Wi-Fi link (e.g. `http://192.168.x.x:5173`) so a judge can open it on their phone and photograph something live.
- **One-click samples:** three **real photos** from Wikimedia Commons (a wire-bonded chip at 60×, a die in its package, silicon wafers; see `assets/samples/CREDITS.md`) plus three illustrations (scratched die, lifted wire bond, overloaded WIP rack). On the real photos of healthy parts, Drex answers *no defect → release* and flags its low confidence on which waste applies.

## What else is in the dashboard

- **Value stream map**: value-add vs. wait time and process cycle efficiency per step
- **Muda Pareto**: dollar cost of each of the 8 wastes
- **Six Sigma scorecard**: DPU, DPMO, first-pass yield, rolled throughput yield, sigma level, Cp/Cpk for each step's critical-to-quality parameter
- **Scenarios**: baseline, litho bottleneck, wire-bond defect spike, after-kaizen
- **CSV upload** for your own lot data (`lot,step,va_min,queue_min,dies_in,defects,rework,holds,transport_m,extra_inspections,manual_interventions,measured`)

> **Data note:** all data is synthetic, generated with fixed seeds. No proprietary or employer data is included.

## Structure

```
index.html            dashboard
src/engine.js         Lean + Six Sigma analytics (browser + Node)
src/app.js            UI, charts, live Drex panel, photo upload, arena
src/photo.js          photo diagnosis: Drex Max image questions
server.js             static server + /api/decide proxy (keeps keys server-side)
bench/cases.js        ground-truth case generator + the 4 decision questions
bench/contenders.js   Drex, Claude, and rules clients
bench/run.js          benchmark runner -> bench/results.json
```
