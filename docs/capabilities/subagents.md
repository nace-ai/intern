# Subagents

A subagent is a second run of the agent with a clean conversation and one task. The run that starts it (the
parent) waits for it and gets its final reply back, never its steps, so the parent's context stays small.
Subagents run in `intern.subagents`.

## Turning it on

Subagents are off by default. Turn them on with `/caps subagents` in the terminal UI, the ACP `cap.subagents` option,
or in `config.toml`:

```toml
[capabilities.subagents]
enabled = true
```

The agent then has an `agent` tool. It takes one or more tasks (at most eight a call, and no more than `max_children`),
each with what to do, a short name, and optionally the tools to use, and answers with a line saying how many
completed, then each subagent's report, the tool result shared evenly between them; a report cut to fit says where the
whole reply is. A task may name a model only when `model` or `models` lists one: the schema then offers exactly those,
and otherwise has no model to guess. A configured model is bound when the session opens, so a typo stops the switch
from turning on instead of failing every subagent. The terminal UI shows a line when each subagent starts and ends. Independent tasks whose tools only read run at
the same time. The tool itself changes nothing, so the guard does not judge the call; it judges every call each
subagent makes.

The reports come back as the `agent` call's result. With the tool-result guard on, what a subagent read from the web,
the browser or MCP was screened in the subagent, before it read it; the report itself is screened only with
`screen = "all"`. A subagent on another model is bound inside the session budget and without the session's System 2
fallbacks, which are the parent's backups.

## What a subagent may do

A subagent inherits the parent's capabilities. The parent may narrow them and never widen them.

| | In a subagent |
|---|---|
| Tools | The parent's own tool objects, or the ones the task names. A tool the parent does not have is refused. `process` and `agent` are never passed on: a background job would outlive the subagent. A subagent starts no subagents (the depth is 1): its run has no `ctx.subagents`, whatever tool asks. Tools keep their own state across parent and children: the workspace's `sql` result tables are numbered for both. |
| Where commands run | The parent's executor: the same machine, container or sandbox. |
| Secrets | The parent's redaction applies to every tool output. |
| The guard and the tool-result guard | A guard of its own with the parent's settings, which counts its own refusals. |
| Memory | Read only. `memory_search` passes on with the parent's tools, the drive stays write-protected, recall does not run again, and only the parent's session is stewarded. A subagent reports back; the parent decides what is kept. |
| Code search and the workspace | The parent's index, never built again. A file the subagent changes marks it for a refresh. |
| Budget and deadline | The parent's. A subagent calls the parent's System 2 provider, which the session budget (`[capabilities.session_budget]`) wraps, so its calls, cost and deadline count against the same caps. System 1 is the parent's client, with its cost cap and cache. |
| Turns | Its own budget (`max_turns`, 40 by default, or fewer if the task asks), never above the parent's. |
| Model | The host's default for subagents (`model`), else the parent's; the parent may name its own model or one from the host's list (`models`). Another model is bound as a model switch is, inside the session budget, and under the session's window cap. |
| System 1 supervision | None: System 1 does not review a subagent's turns. Its asks for the guard and compaction still run. |

Each extension says how it runs in a subagent with `Extension.for_subagent()`: a new instance, itself, or None to
leave it out. An extension that can enforce something on a run (it overrides `tool_call`, `tool_result`,
`before_provider_request` or `turn_end`) must answer, and so must a subclass of one that does (a stricter guard built
on `Guard`); until it does, no subagent starts in its session, so a check is never skipped in a subagent without
anyone noticing. An extension that only shapes the parent's own run is left out.

## What a subagent sees

The subagent's system prompt is the parent's, with a note saying that it works for another agent, that only its
final reply is seen, and that it should end with a short report citing its evidence.

Its conversation opens with the user's own latest messages (up to three), as the user's, then the task, as a message
from the harness. The guard and System 1 read only the user's own messages as what the user asked, so a task the parent model
wrote never stands as the user's words. A host that starts a subagent itself speaks for its user: its task is the
user's message.

The parent gets the subagent's status, turns and final reply, marked as notes to check before acting on them.

## Limits

| Setting | Default | |
|---|---|---|
| `max_children` | 8 | Subagents one parent run (or one host call) may start in all. A request for more is refused before any starts. |
| `max_parallel` | 4 | Subagents that run at the same time. |
| `max_turns` | 40 | A subagent's turn budget, never above the parent's. |
| `timeout_s` | none | A subagent's wall-clock limit, from the request, waiting for a slot included. It ends with `timed_out`; the parent goes on. |
| `model` | the parent's | The model subagents use. |
| `models` | none | Further models the parent may pick for a task. Any other is refused. |

A subagent whose tools can only read (`read`, `grep`, `find`, `web_search`, …) runs beside others. One that can write
or run commands waits until no other such subagent runs, so two subagents never change the same files at once.

## How a subagent ends

| Status | |
|---|---|
| `completed` | It replied. |
| `turn_budget` | It used its turns; its last reply says what is done and what is not. |
| `timed_out` | It reached `timeout_s`. |
| `aborted` | The parent run was aborted, or the session closed. |
| `provider_error` | Its model call failed, the session budget or deadline among the reasons. |
| `output_limit` | Its replies were cut off at the output limit. |
| `failed` | A bug or a System 1 outage ended it. |
| `stopped` | A hook or the guard stopped it. |

A subagent that ends in any of these never ends the parent's run.

## Events and journals

The parent's stream gets `subagent_start` (its id, name, task, model, tools and journal) and `subagent_end` (its
status, turns, usage, final reply and duration; the task, reply and detail redacted as tool output is), and the
subagent's `hook_error`s. Everything else it emits, its System 1 asks included, goes to its own journal: a file in a folder beside the parent's (`sessions/<session>.subagents/<id>.jsonl`), or memory only
when the parent's journal is. A client that counts the parent's turns and tool calls counts the parent's alone.

`run_end.usage` is the parent's agent, State writes and subagents together; `run_end.subagent_usage` is the
subagents' share, absent when the run started none.

## From a host

A host that drives the engine as a library can start a subagent itself, beside or between runs:

```python
from intern.subagents import SubagentSettings, SubagentSpec

session.deps.subagents = SubagentSettings(max_parallel=2, model="openrouter/qwen/qwen3.8-27b")
result = await session.spawn(SubagentSpec(task="List the public functions in src/app.py", tools=["read", "grep"]))
print(result.status, result.final_text)
```

`Session.spawn` follows the session's settings and budget (`max_children` per call, `max_parallel` across the
session), adds the subagent's usage to `session.usage`, and is aborted when the session closes. A subagent that is
cancelled still reports what it spent. It raises `SubagentRefused` when the session has no subagent settings or the task
cannot run as asked (a tool the session lacks, a model it does not allow, its limit reached).

Inside a run, an extension reaches the run's subagents as `ctx.subagents` and a tool as `ToolContext.subagents`:
`run(spec)` for one, `run_many(specs)` for several, both `None` in a subagent's own run.
