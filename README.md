<p align="center">
  <img src="docs/assets/mascot.svg" alt="Intern: a violet cube in round glasses, smiling, glancing around and blinking" width="160">
</p>

<h1 align="center"><img src="docs/assets/wordmark.svg" alt="Intern" width="360"></h1>

<p align="center">Your intern, with a brain in two parts: one thinks slow and does the work, the other thinks fast, keeps it on track and finds what it needs.</p>

<p align="center">
  <a href="https://github.com/nace-ai/intern/actions/workflows/ci.yml"><img src="https://github.com/nace-ai/intern/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+">
</p>

Small models make keen but shaky interns: they loop, cut replies short, send malformed tool calls, overflow their
context and stop before the work is done. Intern breaks those habits one at a time, and every fix ships with a
measured result.

Your intern's brain has two parts. Its System 2, the agent model, thinks slow and does the work: it reasons, reads
and calls tools. Its System 1, a second and faster model, thinks fast and keeps it on track: every fourth turn it
checks whether your intern is going in circles and nudges it out, it searches code and files for it when you turn
search on, and it decides what your intern keeps in mind when its context fills up.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/tui-dark.png">
  <img src="docs/assets/tui-light.png" alt="The terminal UI after a session with the guard turned on: System 2 runs a failing test, re-reads files three turns in a row, System 1 marks each turn stuck and nudges it, the guard refuses a sudo call, and System 2 fixes the bug and the test passes.">
</picture>

_A session with the guard turned on (`/caps guard`), which is off by default; a default install never shows the
refused `sudo` call._

## What's new in Intern

- **A supervisor that never sleeps.** System 1 looks at every fourth turn and nudges your intern out of circles. On
  by default. [Supervision](#supervision)
- **Code search by meaning.** `decision_code` finds where a repository does something, even when the words aren't
  in it: the right file first for 87% of SWE-bench Verified issues, where Semble does for 37%. Off by default.
  [Search](#search-judged-by-system-1)
- **Search for professional files.** `decision_files` searches PDFs, workbooks and scans for audit-style
  questions. Off by default. [Search](#search-judged-by-system-1)
- **Web results System 1 judges.** `web_search` and `web_read` can have System 1 keep only what the task needs.
  Off by default. [Search](#search-judged-by-system-1)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/how-it-works-dark.svg">
  <img src="docs/assets/how-it-works-light.svg" alt="One run over nine turns. System 2 calls its tools directly; the guard sits beside the path, off by default. At turn 2 System 2 searches, and System 1 walks the index and returns ranked places. Every turn goes into the journal. System 1 reads the session cut, built by code, at turn 4 and calls the turns advancing; at turn 8 it calls them stuck, looks again at turn 9, still stuck, and nudges System 2. When System 2's context passes the threshold, the turns fold into the State, written as edits, beside the reads System 1 kept.">
</picture>

1. Your intern's System 2 reasons and calls tools. With the guard on, each call is checked first; it is off by
   default.
2. When System 2 searches with `decision_code` or `decision_files`, System 1 walks the index and returns ranked
   places.
3. Every turn is appended to the session's journal.
4. Code, not a model, builds a compact cut of the session from the journal for your intern's System 1 to read.
5. Every fourth turn, System 1 judges whether your intern is advancing, re-reading or retrying. A stuck verdict
   that holds at the next turn gets a nudge.
6. When the context fills, System 2 writes its State as edits, System 1 keeps the reads still in use, and the turns
   the State covers are cut.

The [architecture](docs/architecture.md) has the details and the measurements behind each step.

## Install

```bash
# Python 3.12+; installs the `intern` command
uv tool install "intern-agent @ git+https://github.com/nace-ai/intern"
```

Then run `intern`. The first time, it opens on a short setup that explains what Intern is and connects both of its
models: pick a provider for System 2 (Anthropic, OpenAI, OpenRouter, Gemini, xAI, DeepSeek, Mistral, Groq and 12
more, or a model on your machine), paste a key (or sign in to OpenRouter in the browser), and pick System 1, Drex Max
on OpenRouter by default. Keys go to your system keychain; `/setup` brings the screen back any time.

Or give it a task straight away, with one OpenRouter key for both models:

```bash
export OPENROUTER_API_KEY=...
intern --system2 openrouter/qwen/qwen3.8-27b \
  --system1 decisions:nace-ai/drex-max \
  "Find why tests/test_parser.py fails and fix it"
```

The terminal UI opens with the task as its first prompt; `-p` runs it headlessly and prints the reply.

<details>
<summary>The two models, and the Nace console for System 1</summary>

- **System 2** does the work. It can be any chat model: OpenRouter, Fireworks, Together, DeepInfra, Groq, OpenAI,
  Anthropic, or a local model.
- **System 1** keeps it on track. It can be any decision model: Drex, Jev, Kev and 40+ others, from the
  [Nace console](https://console.nace.ai) or OpenRouter's decisions API. `intern models` lists them all.

Sign up at [console.nace.ai](https://console.nace.ai) and create an API key. Drex from the console is the default
System 1, so no `--system1` is needed:

```bash
export NACE_API_KEY=...        # System 1: Drex from the Nace console
export FIREWORKS_API_KEY=...   # System 2: any provider, here Fireworks
intern --system2 fireworks/accounts/fireworks/models/deepseek-v4-flash \
  "Find why tests/test_parser.py fails and fix it"
```

</details>

Every provider, flag and setting is in [docs/usage.md](docs/usage.md).

## Supervision

Every fourth turn, your intern's System 1 reads the session cut and judges what the latest four turns are doing:
advancing, re-investigating what earlier turns already covered, retrying, off plan or finished. When
re-investigating and retrying together reach 0.7, the verdict is stuck, and System 1 looks again at the next turn.
A stuck verdict that holds gets a nudge to act on what your intern found. On by default.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/supervise-dark.svg">
  <img src="docs/assets/supervise-light.svg" alt="System 1 supervising one run of thirteen turns. It looks at turn 4: advancing, so the next look is turn 8. At turn 8 re-investigating and retrying together pass 0.7, a stuck verdict, so it looks again at turn 9; still stuck, it nudges System 2 to make the change as its next call. Turns 10 and 11 edit and pass the tests, and the look at turn 13 calls them advancing. 0.6 s a look; replayed over 42 stored runs (1,935 turns), the rule sent 23 stuck nudges and asked System 1 at 27% of turns. The probability bars are illustrative.">
</picture>

Replayed through System 1 over 42 stored runs (1,935 turns), this rule sends 23 stuck nudges and asks System 1 at
27% of the turns, 0.6 s a look. No blind judge has rated its nudges yet. The rule before it looked at every turn and
nudged after three stuck verdicts in a row: over 82 stored sessions, 16 of its 19 nudges fell on turns an
independent judge also called stuck. [How it is judged](docs/architecture.md#system-2-and-system-1)

## Compaction

When the context fills up, both parts of your intern's brain pitch in. Its System 2 writes the State: the plan, what
the work found and changed, and what comes next, as edits to the State before it. Then it carries on from the
State. Its System 1 goes over the files your intern read before they are cut, and keeps the ones it is still
working on. System 1 never reads the State, only the session itself, so a State that misses something cannot hide
it. A number the run never showed is sent back to the writer or blanked.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/compaction-dark.gif">
  <img src="docs/assets/compaction-light.gif" alt="An animation of the terminal UI compacting a session: System 2 writes its State into the compaction's card, System 1 judges the file reads being cut and keeps the three your intern is still working on, and a click opens the State.">
</picture>

On `drex-max`, the reads System 1 keeps agree with a judge on 81% (recall 56%, precision 78%), and on 83% with a
`SYSTEM1.md` that says how its author works (recall 70%, precision 75%)
([method](docs/architecture.md#system-2-and-system-1)).

## Capabilities

Switch each with `/caps` in the terminal UI, the ACP `cap.*` options, or `~/.intern/config.toml`
([capability settings](docs/capability-settings.md)); `/caps` tags the searches and guards System 1 runs. Every
System 1 check that reads the session also reads your [`SYSTEM1.md`](#instructions), where you tell it what is
normal in your work; `decision_code` and `decision_files` read only the question.

### Search, judged by System 1

Word search finds the words it is given. System 1 reads each candidate and judges it against the question, or, on
the web, against the task.

| Tool | What System 1 does | Default |
|---|---|---|
| `decision_code` | Walks an index of the repository (folders, then files, then the classes and functions in them, in 14 languages), scores each against the question and opens only the branches worth opening | off ([turn it on](docs/usage.md#code-search)) |
| `decision_files` | Walks PDFs, workbooks, Word files and scans by page, section and tab beside a BM25 list of passages, judges the best of both, then follows the strongest passages' terms | off ([turn it on](docs/usage.md#workspace)) |
| `web_search` | Asks the provider for three times the results and keeps the ones the task needs, best first | off, and its System 1 too ([`system1`](docs/capability-settings.md#system-1-on-web-results)) |
| `web_read` | Of a page longer than the preview, keeps the parts the task needs, in page order, where it showed the page's start | off, and its System 1 too ([`system1`](docs/capability-settings.md#system-1-on-web-results)) |

Measured with no agent in the loop, against other searches ([below](#how-well-system-1-does)):

- **`decision_code`** puts the file a GitHub issue's fix changed first for 87% of 100 SWE-bench Verified issues,
  where Semble does for 37%, BM25 for 18% and grep for 10%, and the right function first for 64% (Semble: 12%).
- **`decision_files`** puts the right Treasury Bulletin first for 34% of 133 OfficeQA Pro questions, where SAG2 does
  for 22% and BM25 for 4%. On HotpotQA it finds 98.8% of the passages a question needs in its top ten, level with
  SAG2 (99.3%) and ahead of BM25 (88.5%).
- **`web_search` and `web_read`** are not yet measured with System 1, which is why it is off.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/code-search-dark.gif">
  <img src="docs/assets/code-search-light.gif" alt="An animation of the terminal UI as decision_code searches this repository for where a run that goes in circles is detected. System 1 descends the folders, ranks files on a leaderboard, scores the definitions in the best ones, and ranks supervise.py first with class Supervisor, then loop.py. 62 asks, $0.0035.">
</picture>

_Asked where this repository decides that a run is going in circles. "Circles" is in none of that code, so grep
lands on the wrong files; `decision_code` puts `supervise.py` first, at the class that does it._

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/files-search-dark.gif">
  <img src="docs/assets/files-search-light.gif" alt="An animation of the terminal UI as decision_files searches a folder of audit papers for: has the client ever been the victim of a scam or theft, and what did it cost? System 1 walks the PDFs' pages beside a BM25 list of passages, judges both, hops through the best passages' terms, and ranks page 1 of the fraud-risk questionnaire first at 0.98. 16 asks, $0.0026.">
</picture>

_Asked "Has the client ever been the victim of a scam or theft, and what did it cost?" of an audit folder.
"Victim", "scam" and "theft" are in none of its files; `decision_files` puts first the questionnaire page where
management reports a 2021 cyber attack and its loss._

### More that System 1 runs

- **Supervision** (on). Every fourth turn System 1 nudges your intern out of circles. [Above](#supervision)
- **Compaction** (always). System 1 keeps the file reads still in use when the context fills. [Above](#compaction)
- **Memory** (on). Before the first request, System 1 recalls the pages of earlier sessions the task needs; after
  the session, it judges what is worth keeping. [Memory](docs/memory.md)
- **Tool-call guard** (off). Fixed rules refuse `rm -rf ~`, force-pushes, piping a download into a shell and
  reading your credentials; System 1 judges every other call against what you asked for. It stops all 42 risky
  calls of a labeled set and none of its 62 routine ones ([below](#how-well-system-1-does)). Say in `SYSTEM1.md`
  what you allow. [Guard](docs/capability-settings.md#guard)
- **Tool-result guard** (off). System 1 removes the parts of web, browser and MCP output addressed to your intern
  before it reads them. [Tool-result guard](docs/capability-settings.md#tool-result-guard)
- **Auto thinking level** (Claude models, when you pick it). At the start of each run, System 1 picks how hard
  System 2 should think for the request. [Measurements](docs/architecture.md#system-2-and-system-1)

### Other capabilities

- **Subagents** (off). Your intern hands self-contained tasks to subagents with the `agent` tool, with its tools or
  fewer, never more. [Subagents](docs/capability-settings.md#subagents)
- **Prompt-cache share.** The terminal UI's masthead shows the share of System 2's prompts read from the cache.
  [Terminal UI](docs/tui.md)
- **Optional tools.** A browser, MCP servers, a Docker workspace, secrets from your keychain or password manager.
  [Capability settings](docs/capability-settings.md)

## How well System 1 does

On its own, a small model does not notice it is going in circles, cannot tell a routine command from a risky one,
and searches only for the words it was given. Your intern's System 1 does those jobs. Each row is one of them, with
System 1 and without it, or against another search.

| System 1 | With System 1 | Without it, or another search | Measured on |
|---|---|---|---|
| Notices your intern going in circles | 23 stuck nudges, asking at 27% of turns; 0.6 s a look. No blind judge has rated them yet | No nudges | 42 stored runs (1,935 turns), replayed ([method](docs/architecture.md#system-2-and-system-1)) |
| The same, by the rule before 0.39.0, which looked at every turn | 19 nudges, 16 of them on turns an independent judge also called stuck | No nudges | 82 stored sessions, replayed ([method](docs/architecture.md#system-2-and-system-1)) |
| Finds the code a GitHub issue's fix changes (`decision_code`) | Right file first for 0.87, right function first for 0.64; $0.006 a search | Semble: 0.37 and 0.12. BM25: 0.18 and 0.13. grep: 0.10 and 0.13 | 100 SWE-bench Verified issues, `drex-latest` ([method](docs/architecture.md#system-2-and-system-1)) |
| Finds the files a task needs, across repositories (`decision_code`) | An oracle file first for 0.67, in the top ten for 0.89 | Semble: 0.28 and 0.72. BM25: 0.30 and 0.69. grep: 0.11 and 0.46 | 54 searches over CodeScaleBench's 42 tasks, `drex-latest` ([method](docs/architecture.md#system-2-and-system-1)) |
| Finds where this repository does something (`decision_code`) | Right file first for 0.93 of questions, in the top five for all; right definition first for 0.67 (top five 0.93); 102 asks, $0.006 and 4.5 to 9.5 s a question | BM25 word search alone: right file first for 0.53, in the top five for 0.73; right definition first for 0.40 | 15 questions about this repository, two repeats, `drex-max` ([method](docs/architecture.md#system-2-and-system-1)) |
| Finds the report a figure is in (`decision_files`) | Right Treasury Bulletin first for 0.34, in the top ten for 0.68; $0.036 a search | SAG2: 0.22 and 0.56. BM25: 0.04 and 0.20. grep: 0.02 and 0.08 | 133 OfficeQA Pro questions over 697 bulletins, `drex-latest` ([method](docs/architecture.md#system-2-and-system-1)) |
| Finds the passages a two-hop question needs (`decision_files`) | 0.988 of them in the top ten; $0.006 a search | SAG2: 0.993. BM25: 0.885 | 300 HotpotQA questions over 9,811 passages, `drex-latest` ([method](docs/architecture.md#system-2-and-system-1)) |
| Finds the clause a contract question asks for (`decision_files`) | The right contract first for 0.96; the clause in the first 20,000 characters for 0.80 | SAG2: 1.00 and 0.97. BM25: 0.98 and 0.78 | 100 CUAD questions over 200 contracts, `drex-latest` ([method](docs/architecture.md#system-2-and-system-1)) |
| Stops risky tool calls you did not ask for (guard, opt-in) | All 42 stopped, and none of the 62 routine calls; it reads each script a call runs, and asks the registries about each package an install names, before judging it; 0.4 s a check | The fixed rules stop 9 of the 42 | 140 labeled calls, two repeats, `drex-latest` ([set](evals/tool_guard/)) |

System 1 judges from a condensed view of the session, at most 24.6k tokens and built by code with no model call.
Asked about the session, it answers 95% of questions from that view, as many as from a transcript cut to the same
length (94%) ([method](docs/architecture.md#system-2-and-system-1)).

## Instructions

Three optional files tell your intern's System 2 and System 1 how to work. None ships with Intern: create the ones
you want in `~/.intern/` (or `$INTERN_HOME`, if you set it). Without them, System 2 uses the packaged prompt and
System 1 its own defaults.

| File | Read by | Where from | What it is |
|---|---|---|---|
| `SYSTEM2.md` | System 2 | `~/.intern/` | Intern's system prompt, in place of the packaged one (pi's `SYSTEM.md`, still read under that name) |
| `AGENTS.md` | System 2 | `~/.intern/`, and each folder from the repository root down to the working directory | Rules for your intern: the project's workflow, conventions and checks |
| `SYSTEM1.md` | System 1 | `~/.intern/` only | Your guidance for System 1: how to judge, what work is normal for you, what you still want to be asked |

System 1 reads `SYSTEM1.md` after the session in every check that reads the session, and the guard counts what
it calls normal as asked. It never reads `AGENTS.md`, which a repository you cloned can write. When a step in
an `AGENTS.md` acts outside the repository (filing an issue, opening a PR), allow it in your `SYSTEM1.md` too.
A `SYSTEM1.md` in a project is ignored: a repository must not tell the guard what you allow. Yours might read:

```markdown
## What is normal for me
- GitHub (my-org): pushes to feature branches, opening and commenting on PRs, reading CI.
- Linear: creating, updating and commenting on issues.

Still mine to ask for: pushing to `main`, merging PRs, deleting anything shared, changing production.
```

`--no-context-files` skips all three. [Configuration](docs/usage.md#configuration)

## Models

A model is `provider/model`; everything after the first slash goes to the provider as is.

In the terminal UI, `/system2` and `/system1` swap either part of your intern's brain: each opens a picker (type
to filter), and `/system2 provider/model` or `/system1 <model>` sets one directly.

| Provider | Key |
|---|---|
| `openrouter`, `fireworks`, `together`, `deepinfra`, `groq` | `<NAME>_API_KEY` |
| `openai` (Responses API), `anthropic` (Messages API) | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` |
| `ollama`, `llamacpp`, `lmstudio`, `vllm` | none, on localhost |

Model profiles tune the window, sampling and thresholds for Qwen3.8 27B, Nemotron 3.5 Lightning 30B-A3B,
DeepSeek V4 Flash, GPT and Claude. On a Mac with Apple silicon, [Kapitsa](https://github.com/nace-ai/kapitsa)
runs Qwen and Nemotron locally ([guide](docs/local-models.md)). Providers, profiles and the working window:
[docs/usage.md](docs/usage.md#models).

## Use it from your editor

| Client | Command |
|---|---|
| Terminal UI, with the State and System 1's decisions on screen ([docs](docs/tui.md)) | `intern` |
| Agent Client Protocol: T3 Code, Zed ([docs](docs/acp.md)) | `intern acp` |
| JSON-RPC over stdio | `intern rpc --system2 M` |
| One task, one JSON event per line | `intern -p --json --system2 M -- "task"` |

`M` is a model such as `openrouter/qwen/qwen3.8-27b`. All four drive the same `Session`; `intern schema`
prints the event JSON Schema.

## What Intern does not do

- **It is not a sandbox.** Commands run with your user's permissions, and the guard is off unless you turn it on.
  With it on, it refuses the clearly dangerous and System 1 judges the rest, but run untrusted work in the
  [Docker workspace](docs/capabilities/docker-workspace.md) or another container.
- **It does not work without its System 1.** Your intern's System 1 is a hosted decision model. A session that
  cannot reach it is refused with the reason; there is no offline fallback.
- **It does not start subagents unless you turn them on.** With `cap.subagents` on, it hands self-contained tasks
  to subagents, which inherit its capabilities and never get more. Off by default.
  [Subagents](docs/capability-settings.md#subagents)
- **It has no editor plugin of its own.** Editors reach it over the Agent Client Protocol.

## Data

Intern collects no telemetry. What leaves your machine:

- Your intern's System 2 requests, to the provider you chose.
- Your intern's System 1 asks, to the Nace console or OpenRouter: the session cut (your messages, your intern's replies, tool
  calls and a short note on each result), your `SYSTEM1.md` if you keep one, for the guard your requests and the call
  being judged, and for a search what it judges (passages of your code and files, web results and pages).
- Calls made by tools you turn on, such as web search.

Sessions are journaled, and memory kept, on your machine in `~/.intern/`.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) first: one concern per PR, and evidence for any changed default.
Report security issues privately, as [SECURITY.md](SECURITY.md) describes.

## License

[Apache-2.0](LICENSE)
