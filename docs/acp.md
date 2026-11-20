# Agent Client Protocol server

`intern acp` serves the [Agent Client Protocol](https://agentclientprotocol.com) (ACP v1) over
stdio, so t3code, Zed and other ACP clients can drive the harness and pick its System 2 and System 1
models. This page is the contract: a change to anything below ships as a release (see
[Versioning](#versioning)), and a breaking one moves the minimum version clients pin.

**Minimum version for clients: `0.25.0`.** Clients start `intern` and check `intern --version`. [0.2.1](#021) was
additive; [0.3.0](#030) changes the `system1` option and is where clients that showed `heuristic` need
a look. [0.4.0](#040) is additive: clients that offer a window picker need it. [0.6.0](#060) is
additive: clients that offer capability toggles need it, and [0.7.0](#070) tells them which toggles
cannot switch on here. [0.9.0](#090) and [0.10.0](#0100) added runner toggles, which [0.19.0](#0190) replaces with one
`runner` option. [0.14.0](#0140) removes the heuristic System 1: clients that send `heuristic` or read `fallenBack`
need a look, and a prompt may now fail with `system1_unavailable`. [0.15.0](#0150) is additive: clients that
offer a thinking picker read each model's `efforts`. From [0.23.0](#0230) a toggle switched on installs its missing
extra, and a toggle that cannot work here is not offered. [0.25.0](#0250) renames the harness to Intern, and
[0.27.0](#0270) removes its old names. [0.49.0](#0490) is additive: clients that show or pick the hand-over need it.
[0.54.0](#0540) runs sessions on free credit: clients that send a model with every turn need a look.

```bash
uv tool install "intern-agent @ git+https://github.com/nace-ai/intern@v0.44.1"
intern acp                         # System 2 from config.toml or $INTERN_SYSTEM2; System 1 drex-latest
intern acp --system2 openrouter/qwen/qwen3.8-27b --system1 drex-max-v1.1
```

`acp` takes the same options as `rpc` (`--system2`, `--system1`, `--window`, `--max-turns`,
`--profile`, `--no-context-files`, `--no-extensions`, `--extension`, `--no-session`) as defaults for
every session it opens. `--cwd` and `--session` are ignored: ACP sends the working directory with
each session, and journals are found by session id. Every install can serve ACP: the official
`agent-client-protocol` Python SDK is a dependency of `intern-agent`, not an extra.

stdout carries only the protocol. Warnings and logs go to stderr. stdin and stdout must be pipes (or a
terminal); redirected to a file, the command exits with code 2. When stdin closes, every request
already received is still answered: runs in progress are aborted (their prompts answer `cancelled`)
and the server exits once the replies are written (at most 10 s later). On SIGTERM it aborts every
run and exits at once. Either way every session is closed.

## Lifecycle

| Method | What the harness does |
|---|---|
| `initialize` | Returns `protocolVersion: 1`, `agentInfo {name: "intern", title: "Intern", version}` and `agentCapabilities {loadSession: true, promptCapabilities {image: true, embeddedContext: true}}`. No auth methods. |
| `session/new {cwd, mcpServers, _meta?}` | Builds one harness `Session` for this ACP session; one process serves any number of sessions. `sessionId` is the session's journal id. Returns `configOptions` and `modes`. Opens on the models and capability toggles in [`_meta.nace`](#picking-models-at-open) when given. Fails with `-32602` when System 1 cannot be set up (its key is missing) or does not answer the check at open: the harness never runs without System 1. |
| `session/load {sessionId, cwd, mcpServers, _meta?}` | Finds the journal through `~/.intern/sessions/index.json` (falling back to a scan of `sessions/*.jsonl`), rebuilds the session on the System 2 and System 1 models and the capability toggles it last used (or those in `_meta.nace`), replays the active branch as `session/update`s, then returns `configOptions` and `modes`. Unknown ids fail with `-32002` (resource not found). |
| `session/prompt {sessionId, prompt}` | Runs one turn to the end, including follow-ups queued while it ran, streams `session/update`s, then resolves with `stopReason` and `usage`. When [capability toggles](#capability-toggles) changed since the last prompt, the session is first reopened on them. A prompt while another runs in the same session fails with `-32000` (`session busy`); a prompt before a System 2 is bound fails with `-32602` (see [below](#when-no-system-2-can-run)). |
| `session/cancel {sessionId}` | Aborts the run in progress; the pending `session/prompt` resolves with `stopReason: "cancelled"` once its last updates are sent. A cancel with nothing running is ignored. |
| `session/set_config_option {sessionId, configId, value}` | See [Config options](#config-options). Allowed only while the session is idle; returns every option with its current value. |
| `session/set_mode {sessionId, modeId}` | Accepts only `full-access`. |

Prompt content: text blocks are joined in order. An embedded text resource is added as a fenced block
headed by its URI; a resource link becomes its URI. An image is saved under the session's spill folder
and the prompt names its path, so the agent can `read` it (a model sees it when its profile sets `vision`
or its provider's model listing says it takes images).
Audio is refused.

`mcpServers` from the client are not used in v1: MCP servers come from the harness's own
`[[capabilities.mcp]]` config. `fs/*` and `terminal/*` client methods are not used: tools run in the
harness, on the session's `cwd`.

### Picking models at open

`session/new` and `session/load` take the models and capability toggles to open on in `_meta`, so a
session never has to start on the configured default first, or be rebuilt at its first prompt:

```json
{"_meta": {"nace": {"system2": "openrouter/qwen/qwen3.8-27b", "system1": "drex-latest", "runner": "docker",
                    "capabilities": {"cap.browser": true, "cap.web-search": false}}}}
```

Every key is optional; a missing one means the default (`session/new`) or what the journal last used
(`session/load`). A System 2 picked this way is journaled like a `model` change, and toggles like a
toggle change, so a later load without `_meta` continues on them. `capabilities` takes any of the
[`cap.*` ids](#capability-toggles); on `session/load` it is set over the journal's toggles. `runner` takes a
[runner](#runner) name, over the one the journal last ran on. A `_meta.nace` whose models are not strings, whose
`capabilities` names an id the session does not offer or a value that is not a JSON boolean, or whose `runner`
cannot run here, fails with `-32602`.

### When no System 2 can run

`session/new` and `session/load` succeed even when the System 2 they would open on cannot be bound:
none is configured, or its provider's API key is missing. The session is then open but unbound:

- `model` is advertised with `currentValue` the model it would have run on (`""` when none is
  configured) and the catalog as options; `system1` and the `cap.*` toggles as usual; no
  `thought_level`, `context_window` or `handoff`.
- `session/set_config_option model` binds it. A model that cannot bind either fails with `-32602` and
  the session stays unbound. `system1`, `context_window` and `cap.*` toggles may be set first and apply
  once a model binds.
- `session/prompt` fails with `-32602`: `no System 2 model can run: <reason>; pick a model`.
- When the free credit is why (it is in use, but spent or paused, and no own key can take over), both failures carry
  `data: {code, meter}` (0.58.0 and later): `code` is `free_exhausted` with `meter` `agent`, or `free_paused` or
  `free_revoked` with `meter` `null`, the codes a [`provider_error`](#stop-reasons-and-errors) carries. A client shows
  its free-credit state for these instead of the message. Any other reason has no `data`.
- `session/load` of an unbound session replays its history as usual.

Other configuration errors (an invalid `config.toml`, an unknown extension) still fail `session/new`
with `-32602`.

## Config options

Every session advertises these `configOptions`, in this order, then the
[capability toggles](#capability-toggles). Values are opaque strings, except the toggles' booleans.

| `id` | `category` | Type | Values | Default |
|---|---|---|---|---|
| `model` | `model` | select | System 2 as `provider/model`, grouped by provider | the session's System 2 |
| `system1` | `model_config` | select | decision models served by the console, then `decisions:<model>` for decision models on the decisions API | `drex-latest`, unless config or `--system1` says otherwise |
| `thought_level` | `thought_level` | select | `default` (no effort sent) through Anthropic's and OpenAI's APIs and where the profile lists `effort_levels`, then the levels the model takes: `low` to `max` through Anthropic's API, `low` to `xhigh` through OpenAI's, the profile's `effort_levels`, else `low`, `medium`, `high` | the model profile's `reasoning_effort`, else `default` |
| `context_window` | `model_config` | select | `auto`, or a token count: `32000`, `64000`, `128000`, `256000`, `512000`, `1000000` | `auto` |
| `handoff` | `_nace_handoff` | select | `off`, then the models a run can hand over to: the session's target first, then the catalog's System 2 models | `[capabilities.handoff] to` (or `--handoff-to`), else `off` |
| `runner` | `_nace_runner` | select | where commands run: `local`, `docker`, `opensandbox`, then each runner an installed package registers | `[capabilities.runner] name`, else `local` |

- **`model`** lists the [model catalog](#model-catalog)'s System 2 models, plus the current model. The
  catalog is fetched in the background when the first session opens, so `session/new` never waits on
  provider listings: until it arrives the list holds only the current model, and then every open
  session gets a `config_option_update` with the full list. Any `provider/model` the harness can
  resolve is accepted, listed or not, so clients may offer custom models. Changing it rebinds System 2
  (profile, window) and journals a `model_change` entry. Its options are ACP v1 option groups,
  `{group, name, options: [{value, name}]}`, with `group` the provider; that key is what
  `protocolVersion: 1` defines, so clients generated from a later ACP schema should not expect another
  name for it.
- **`system1`** lists the [model catalog](#model-catalog)'s System 1 models: the decision models the console serves (`POST {console_url}/systemone` with the key in `$NACE_API_KEY`; `[system1].models`,
  default `["drex-latest"]`, until the console has been listed), then the decision models the decisions
  API serves as `decisions:<model>` (`POST {base_url}/systemone` with the key in `$OPENROUTER_API_KEY`:
  Jev, `nace-ai/drex-max`, ...). Like `model`, the list fills in once the catalog arrives; until then it
  holds only the current value. The System 1 the session started on (from `[system1]` or `--system1`,
  as `decisions:<model>` when on the decisions API) is listed too and keeps its own endpoint and
  fallbacks. Any model name is accepted, listed or not; there is no `heuristic` (System 1 off). Changing
  it rebuilds the session's System 1 client, keeping its usage, and journals a `model_change` entry. A model whose key is missing is refused with
  `-32602` and the session keeps the System 1 it had.
- **`thought_level`** is advertised whenever System 2 takes a reasoning effort: the models on Anthropic's
  and OpenAI's APIs, which take one for the models they serve, and an OpenAI-compatible model when its
  profile lists `effort_levels` or sets `reasoning_effort` (some of those endpoints reject the field, so
  the profile is the only word). It sets the effort sent with each request, among the levels the model
  takes (Anthropic's `output_config.effort` goes to `max`, OpenAI's `reasoning.effort` to `xhigh`, a
  profile's `effort_levels` are its own, and a profile with only `reasoning_effort` takes `low`, `medium`,
  `high`). A profile's `effort_levels` replace the API's run for a model that takes fewer, or none (`[]`): the
  packaged profiles give none to OpenAI's GPT-4.1, GPT-4o and gpt-live and to Claude Sonnet and Haiku 4.5, whose
  APIs refuse the field, so sessions on them advertise no `thought_level`, and the few the Pro models and
  chat-latest take. On Anthropic's and OpenAI's APIs and with `effort_levels`, `default` comes first and sends
  none, so the model uses its own; it is the current value when the profile names no effort. Until [0.48.0](#0480) `auto` came last through Anthropic's API, where System 1 picked a level for each
  run; a client that still sets it gets `default`, and a profile whose `reasoning_effort` names it loads as one
  that names none. The packaged
  profiles list `effort_levels` for DeepSeek V4 Flash on Fireworks. It holds across a capability
  reopen but is not journaled: a loaded session starts at the profile's value. A `model` change may add or drop it; the
  `set_config_option` response always lists every option as it now stands.
- **`context_window`** caps System 2's working window for this session. `auto` is the process default:
  the model's window, or `--window` when the server was started with it (the option's name says which,
  for example `Auto (128K)`). The presets are listed up to what the model can use (its window, no larger
  than its profile's effective window), plus the current cap. Any positive token count is accepted,
  listed or not; one above what the model can use leaves the window there. The cap holds across `model`
  changes, is journaled as a `window_cap` entry and restored on `session/load`, and the next
  `usage_update` reports the new `size`. Anything other than `auto` or a positive integer fails with
  `-32602`.
- **`handoff`** names the cheaper model a run hands over to once System 1 judges the rest of the task within its
  reach ([0.45.0](#0450)), or `off`. It is `[capabilities.handoff] to` for this session: the model is bound when it
  is set, from the next run, with the rest of `[capabilities.handoff]` (`effort`, `threshold`, `streak`,
  `min_turn`) as configured. Like `model`, its list fills in once the catalog arrives, any model the harness can
  resolve is accepted, listed or not, and a model that cannot be bound (its key is missing) fails with `-32602` and
  changes nothing. A change is journaled as a `model_change` entry with role `handoff` (model `""` for `off`), holds
  across a capability reopen, and is restored on `session/load`, unless the model can no longer be bound there, when
  the session opens on the configured one. Unbound sessions do not advertise it. When a run hands over, the client
  is sent [`_nace/handoff`](#updates).

```toml
# ~/.intern/config.toml
[system1]
models = ["drex-latest"]                  # the picker's Drex models until the console is listed; the first is the default
console_url = "https://console.nace.ai/v1" # where they are served and listed ($DREX_BASE_URL overrides it)
console_key_env = "NACE_API_KEY"           # ($DREX_API_KEY also works)
base_url = "https://openrouter.ai/api/v1"  # the decisions API, for decisions:<model>
api_key_env = "OPENROUTER_API_KEY"
```

An unknown `configId`, a `thought_level`, `context_window` or `handoff` value it does not take, or a model the harness cannot resolve (for
example, its provider's API key is missing) fails with `-32602` (invalid params) and the harness's
message. A change during a run fails with `-32000` (`session busy`).

### Runner

`runner` picks where the session's commands, file tools and background jobs run: one
[runner](capabilities/runners.md) per session. Its values are every runner this install knows, in order:
`local`, `docker`, `opensandbox`, each runner an installed package registers, then the configured one when it is a
`module:Class`. Each value is `{value, name, description}`. When the runner cannot run in this install and
environment, `description` starts with `Unavailable:` and gives the reason. The reason is a missing extra, an unset
key, or options that only `config.toml` can give. `models --json` gives the same list under
[`runners`](#model-catalog).

- `session/set_config_option {configId: "runner", value}` takes a runner name.
  - A runner that cannot run here fails with `-32602` and its reason, and the session keeps the runner it has.
  - Setting the runner in force changes nothing.
  - Any other runner is pending: the response shows it as the current value, and the next `session/prompt` applies
    it, with every capability change made by then, in one reopen on the same journal (see below).
- The configured runner runs with its configured `[capabilities.runner.options]`. Any other runner runs on its own
  defaults.
- The runner is journaled in the `capabilities_change` entry and restored on `session/load`, unless it can no
  longer run there. A session with no journal (`--no-session`) refuses a change with `-32602`.

### Capability toggles

After the options above, every session (unbound ones too) advertises one boolean option per capability
a user can switch on or off: the same list as the terminal UI's `/caps`, without the `base` pack.
Fixed extensions (a `module:Class`) have no toggle: the profile and `--extension`
choose them for the whole session. Extensions behind a capability (code search, the workspace, memory, the guard,
the tool-result guard) and the subagents' `agent` tool are switched by that capability's toggle ([CONTRIBUTING](../CONTRIBUTING.md#extensions-and-capabilities)).

| `id` | Turns on | Listed |
|---|---|---|
| `cap.web-search` | the `web-search` tool pack | always |
| `cap.web-read` | the `web-read` tool pack | always |
| `cap.browser` | `[capabilities.browser] enabled` | always |
| `cap.code-search` | `[capabilities.code_search] enabled` | always |
| `cap.workspace` | `[capabilities.workspace] enabled` | always |
| `cap.memory` | `[capabilities.memory] enabled` | always |
| `cap.guard` | `[capabilities.guard] enabled` | always |
| `cap.tool-result-guard` | `[capabilities.tool_result_guard] enabled` | always |
| `cap.subagents` | `[capabilities.subagents] enabled` | always; when a fixed extension cannot run in a subagent, or a subagent model cannot be bound, the prompt that switches it on fails with the reason and the session continues without it |
| `cap.mcp.<namespace>` | that `[[capabilities.mcp]]` connection | for each connection config.toml names |

A toggle that cannot switch on in this install and environment, even after the install below, is left out unless
it is on: web search with no provider or key, or an extra with no uv or pip to install it.
[`models --json`](#model-catalog) still lists it, with the reason.

Each is `{type: "boolean", category: "_nace_capability", name, description, currentValue}`, with
`currentValue` what config.toml and the profile turn on until the session changes it. Pack toggles
start from `tool_packs`, or the profile's packs when it is unset. Ids are stable; readers should show
any `cap.*` option they do not know by its `name` and `description`. `cap.process` is no longer offered: the
`process` tool is a base tool from 0.30.0. A client that still sets it, with `session/set_config_option` or in
`_meta.nace.capabilities`, gets no error, and nothing changes.

`session/set_config_option {configId: "cap.…", type: "boolean", value}` takes a JSON boolean
(`-32602` otherwise, or for an id the session does not offer) and an idle session (`-32000`). Setting
the value in force changes nothing, so a client may send every toggle on a thread's first turn. A
change is pending: the response shows it as the option's value, and the next `session/prompt` applies
every change made by then at once by reopening the session on the same journal (same `sessionId`,
conversation, models, thinking level, window cap and usage totals) before it runs.

A toggle switched on whose capability lacks its extra (`browser`, `code-search`, `workspace`, `mcp`) gets it
installed first, into the environment the harness runs in, with uv's pip or else pip, then the capability's setup
(`playwright install chromium` for the browser, which downloads Chromium once). The prompt waits for it. Each install
is a `tool_call` (kind `other`, title `Install the <extra> extra`, `in_progress`) whose content `tool_call_update`s
say what is being installed, ending `completed`, or `failed` with the reason. `session/cancel` stops the install,
the prompt answers `cancelled`, and the change stays pending for the next prompt. An install that fails leaves its
toggle off as below, with the reason and the command to run by hand.

A toggle switched on that still cannot switch on in this install and environment (the
[`unavailable`](#model-catalog) reason: a missing search provider, or an extra that could not be installed) stays
off while the other changes apply. The harness sends a `config_option_update` showing it off, and that prompt fails with
`-32602`: `<reason>. <Name> stays off; the other capability changes applied` (the last clause only
when another change applied). A client that sends the switch again gets the same answer at its next
prompt. When the session cannot open on the rest (for example an MCP connection that fails to start), it
opens again on the toggles it had, sends a `config_option_update` with them, and that prompt fails with
`-32602`: `capabilities not switched: <reason>; the session continues on the ones it had`.

The toggles are journaled as a `capabilities_change` entry and restored on `session/load`, except one
that can no longer switch on, which the loaded session leaves off. A session started with
`--no-session` (no journal) refuses a change with `-32602`.

A reopen is not the session's end, so it is not stewarded into memory; `session/close` and shutdown
are. It starts the capabilities again: code search indexes the working directory at the next turn, and
a configured `[capabilities.session_budget]` starts a new ledger.

## Modes

`modes` advertises one mode, `full-access` (the agent runs every tool without asking). There are no
`session/request_permission` calls in v1.

## Updates

`session/update` notifications, from harness events:

| Harness event | ACP update |
|---|---|
| `text_delta` | `agent_message_chunk` (text) |
| `reasoning_delta` | `agent_thought_chunk` (text) |
| `tool_call_start` | `tool_call`, `status: "in_progress"`, `kind` from the tool (below), `title` the tool name and its main argument, `rawInput` the arguments, `locations` from a `path` argument |
| `tool_call_end` | `tool_call_update`, `status` `completed` (`ok`) or `failed` (`error`, `refused`), `content` the result text the model saw; for a `write` or `edit` that changed a text file, also a `diff` item `{path, oldText, newText}` (`oldText` null for a new file); none for binary files, files over 256k characters before and after together, or the Docker workspace's tools |
| `context_budget` | `usage_update {used: tokens in the request, size: working window}`, with [`_meta.nace.free`](#free-credit) while the free credit is in use |
| `run_end` | `usage_update {used, size, cost: {amount: session cost so far, currency: "USD"}}`, with `_meta.nace.free` likewise |
| `free_notice` | `usage_update {used, size}` whose `_meta.nace.free` has `notice`, the text to show ([running out](#running-out)) |
| `handoff` | the extension notification `_nace/handoff` (below), not a `session/update` |
| everything else | nothing |

Tool `kind` comes from the tool's declared effects: `grep` and `find` are `search`; otherwise `exec`
is `execute`, then `network` is `fetch`, then `write` is `edit`, then `read` is `read`; a tool with no
effects is `other`.

`agent_message_chunk`, `agent_thought_chunk`, `tool_call` and `tool_call_update` carry `_meta.nace.model`, the
`provider/model` that wrote the reply or called the tool: the session's System 2, or, after a hand-over, the model
the run handed over to. The harness's own messages (a secret or a window it could not load) carry none.

When a run hands over ([`handoff`](#config-options)), the client is sent one notification, in its place among the
run's updates: after the last update of the turn it handed over at, before the first of the model it handed over to.

```json
{"jsonrpc": "2.0", "method": "_nace/handoff",
 "params": {"sessionId": "…", "turn": 9, "from": "anthropic/claude-opus-5-5", "to": "anthropic/claude-haiku-5-5",
            "probability": 0.93}}
```

`turn` is the turn it handed over after, `from` and `to` the models, and `probability` System 1's P(ready) there.
The run goes on to its end on `to`; the next prompt is System 2's again. A client that does not know the method
ignores it, as ACP asks of extension notifications.

On `session/load` the active branch is replayed: user prompts as `user_message_chunk`, replies as
`agent_thought_chunk` and `agent_message_chunk`, and each tool call as a `tool_call` followed by its
`tool_call_update` (without a diff: diffs are not journaled), each with the `_meta.nace.model` that wrote it, and each
hand-over as `_nace/handoff` in its place. Messages the harness added itself (nudges) are not replayed. Journals
written before 0.47.0 kept no hand-overs, so none is replayed from them.

A session that memory has stewarded since it last closed is then sent one more `usage_update`. Its
`cost` is what the steward spent on the session: its System 1 asks and its writer's System 2 calls.
The session's cost so far starts from it. `_meta.nace.steward` gives the totals of every steward run
on the session, split by system. A writer whose provider reports no cost and that has no price in the
harness's table has `costUsd` 0 with its tokens counted.

```json
{"sessionUpdate": "usage_update", "used": 0, "size": 65536, "cost": {"amount": 0.0191, "currency": "USD"},
 "_meta": {"nace": {"steward": {
   "runs": 2, "costUsd": 0.0191,
   "system1": {"asks": 43, "failed": 0, "inputTokens": 90000, "outputTokens": 900,
               "costUsd": 0.013},
   "writer": {"inputTokens": 12000, "outputTokens": 1500, "cachedReadTokens": 0, "cachedWriteTokens": 0,
              "thoughtTokens": 0, "costUsd": 0.0061}}}}}
```

## Stop reasons and errors

| Harness `run_end.reason` | Result |
|---|---|
| `completed`, `stopped_by_hook`, `stopped_by_tool`, `stopped_by_guard` | `stopReason: "end_turn"` |
| `aborted` | `stopReason: "cancelled"` |
| `turn_budget` | `stopReason: "max_turn_requests"` |
| `output_limit` | `stopReason: "max_tokens"` |
| `provider_error` | JSON-RPC error `-32001`, `data.kind` below |
| `system1_unavailable` | JSON-RPC error `-32603`, `data: {"reason": "system1_unavailable", code?, meter?}` |
| `internal_error` | JSON-RPC error `-32603` |

A provider error carries `data: {kind, detail, status}`, where `status` is the HTTP status when there
was one and `kind` is:

- `rate_limit`: HTTP 429, or the provider said so (rate limit, too many requests);
- `quota`: HTTP 402, or out of credits, quota or billing;
- `auth`: HTTP 401 or 403;
- `provider`: anything else.

When the Nace console refused the request (System 2 on `nace`, free credit or `$NACE_API_KEY`), `data` also has
`code`, the console's `error.code` (`free_exhausted`, `free_revoked`, `free_paused`, `model_not_in_plan`,
`rate_limited`, `request_too_large`, `upstream_error`, …), and `meter` (`agent` or `system1`) when it named one. When
the code ends the free credit (the first four), `data.next` is the System 2 the session's next prompt will run on, the
user's own; `null` when no own model can run, so the client offers to add a key ([running out](#running-out)).
`system1_unavailable` has `code` and `meter` likewise when the console refused System 1's free credit.

Clients show `rate_limit` and `quota` as limited, not as a crash. The updates sent before the error
stay valid; the turn is journaled up to the failure.

`system1_unavailable`: System 1 did not answer an ask the run needed, after retrying with backoff
(1 s, doubling to 15 s) for about two minutes, or at once when the console refused its free credit for good. The
message names the backend and its last error. The
harness does not run without System 1, so nothing answered in its place; the session stays open and
the next prompt asks again. A session whose System 1 does not answer when it opens fails
`session/new` (or `session/load`) with `-32602`, as one whose System 1 key is missing does.

`usage` on the prompt response is the turn's System 2 tokens: `inputTokens`, `outputTokens`,
`cachedReadTokens`, `cachedWriteTokens`, `thoughtTokens`, `totalTokens`.

`_meta.nace.usage` splits the turn's work by system, follow-ups included. Clients sum turns for a
session's totals; `usage_update`'s `cost` is the session's total for both.

```json
{
  "system2": {
    "model": "openrouter/deepseek/deepseek-v4-flash",
    "inputTokens": 2152,
    "outputTokens": 5,
    "cachedReadTokens": 1792,
    "cachedWriteTokens": 0,
    "thoughtTokens": 0,
    "costUsd": 0.000087
  },
  "system1": {
    "model": "drex-latest",
    "asks": 3,
    "failed": 0,
    "inputTokens": 1840,
    "outputTokens": 96,
    "costUsd": 0.00042
  }
}
```

When a run of the prompt handed over, `handoff` is the model it handed over to and what that model spent, with the
same fields as `system2` (its replies and the State it wrote), and `system2` is the rest, the run's subagents
included. The two add up to the prompt's `usage`. Without a hand-over there is no `handoff` key and
`system2` is everything, as before 0.49.0. A client that reads only `system2` shows less than the prompt cost after
a hand-over; `usage_update`'s `cost` still holds all of it.

```json
{
  "system2": {"model": "anthropic/claude-opus-5-5", "inputTokens": 61200, "outputTokens": 2400, "cachedReadTokens": 52000,
              "cachedWriteTokens": 3100, "thoughtTokens": 900, "costUsd": 0.1043},
  "handoff": {"model": "anthropic/claude-haiku-5-5", "inputTokens": 30400, "outputTokens": 800, "cachedReadTokens": 0,
              "cachedWriteTokens": 30000, "thoughtTokens": 0, "costUsd": 0.0418},
  "system1": {"model": "drex-latest", "asks": 9, "failed": 0, "inputTokens": 18400, "outputTokens": 260,
              "costUsd": 0.0041}
}
```

`asks` are those the backend answered; `failed` are those it did not answer after every retry (each
one stopped the run), and answers from the cache count in neither. Tokens and cost are the backend's.

System 2's cost is the provider's where it reports one (OpenRouter). Anthropic's and OpenAI's own APIs
and Fireworks report only tokens, so the harness prices them from the models' published prices
(`src/intern/models/prices.toml`): standard tier, the 5-minute cache on Anthropic and the
default 30-minute one on OpenAI, the higher rates for a long prompt where the model has them. A model
with no price there costs 0, such as a Fireworks dedicated deployment, which is billed per GPU-second.

## Model catalog

`intern models --json` prints the catalog without opening a session, for status probes:

```json
{
  "system2": [
    {"id": "fireworks/accounts/example/deployments/qwen3p8-27b", "name": "Qwen3.8 27B (Fireworks)", "default": true, "reasoning": true, "efforts": ["low", "medium", "high"], "window": 131072},
    {"id": "anthropic/claude-opus-5-5", "name": "Claude Opus 5.5", "reasoning": true, "efforts": ["default", "low", "medium", "high", "xhigh", "max"], "window": 1000000},
    {"id": "vllm/my-model", "name": "my-model"}
  ],
  "system1": [
    {"id": "drex-latest", "name": "drex-latest"}, {"id": "drex-v2.0", "name": "drex-v2.0"},
    {"id": "decisions:~typesafe/jev-latest", "name": "TypeSafe: Jev Latest"}
  ],
  "capabilities": [
    {"id": "cap.web-search", "name": "Web search", "description": "web_search: the web for ...", "enabled": false,
     "unavailable": "Web search is not set up: set INTERN_SEARCH_PROVIDER to brave, tavily, exa or searxng"},
    {"id": "cap.browser", "name": "Browser", "description": "browser: Playwright pages ...", "enabled": true}
  ],
  "runners": [
    {"id": "local", "name": "Local", "description": "commands run directly on this machine", "default": true},
    {"id": "docker", "name": "Docker", "description": "bash, the file tools and background jobs run in a container ...", "default": false},
    {"id": "opensandbox", "name": "OpenSandbox", "description": "...", "default": false,
     "unavailable": "OpenSandbox needs $OPEN_SANDBOX_API_KEY; a server without a key takes api_key_env = \"\""}
  ],
  "handoff": {"models": ["anthropic/claude-haiku-5-5", "anthropic/claude-opus-5-5", "vllm/my-model"],
              "default": "anthropic/claude-haiku-5-5"},
  "recommended": [
    {"id": "one-key", "title": "One key for everything", "why": "Claude Sonnet 5.5 does the work, ...",
     "system2": "openrouter/anthropic/claude-sonnet-5.5", "handoff": "openrouter/deepseek/deepseek-v4.1-flash",
     "system1": "decisions:nace-ai/drex-max",
     "keys": [{"env": "OPENROUTER_API_KEY", "provider": "OpenRouter", "url": "https://openrouter.ai/settings/keys"}]},
    {"id": "best-results", "...": "..."}, {"id": "free-models", "...": "..."}
  ]
}
```

`handoff` (0.49.0 and later) lists the values of the [`handoff` option](#config-options) a session offers, less
`off`: the configured target first, then the System 2 models. `default` is the configured target
(`[capabilities.handoff] to`, or `--handoff-to`), absent when runs hand over to no model.

`recommended` (0.51.0 and later) lists Nace's recommended combinations on a user's own keys, best first, the same
list the terminal UI's setup offers first. It does not depend on the keys set or what the providers list. Each entry
has `id`, `title`, `why` (one sentence to show under the title), `system2`, `handoff` and `system1`, named as a
session takes them (`--system2`, the `handoff` option or `[capabilities.handoff] to`, and `--system1`, where a
decisions-API model is `decisions:<model>`), and `keys`: every key it runs on, once each, in the order to ask for
them, with `env` (the variable, as `intern secrets` names it), `provider` and `url` (where to make one). Today's ids
are `one-key`, `best-results` and `free-models`; a client should show what is listed, not look for an id.

`runners` lists the values of the [`runner` option](#runner), in its order: `default` marks the configured one, and
`unavailable` (only when it cannot run here) says why. `capabilities` lists the [capability toggles](#capability-toggles) a session offers, in their order,
each with `enabled` as config.toml and the default System 2's profile turn it on. Every entry has
`id`, `name`, `description` and `enabled`. From 0.7.0, `unavailable` is present when the toggle cannot
switch on in this install and environment, with the reason: web search has no provider or key, or an extra is
missing and there is no uv or pip to install it. From 0.23.0, an extra that switching on installs is not a reason.
Combinations are not covered; a reopen still rejects them.

Every entry has `id` and `name`. System 2 entries may also carry these keys, each only when true or
known (readers must ignore keys they do not know):

- `default`: the System 2 a session opens on unless the client picks one (`--system2`, then
  `system2`/`model` in config.toml, then `$INTERN_SYSTEM2`). At most one entry has it; none when
  no default is configured or its provider cannot be reached.
- `reasoning`: the model takes a reasoning effort, so a session on it advertises `thought_level`.
- `efforts` (0.15.0 and later): the values that `thought_level` takes on it, in the order a session lists
  them: `default` first on Anthropic's and OpenAI's APIs and with a profile's `effort_levels`, then the
  levels, least to most (0.41.0 to 0.47.0 listed `auto` last on Anthropic's API).
- `window`: the most context, in tokens, a session on it can use: the profile's `window`, else what the
  provider's listing says, no larger than the profile's `effective_window`. Absent when neither says. A
  session's `context_window` presets go up to it.

System 2 lists every model served by a provider the harness can reach: each built-in provider and
each `[providers]` entry whose API key variable is set (or that needs none), asked with `GET
{base_url}/models` (5 s timeout each, all at once; one that fails or times out is left out), keeping
only models System 2 can chat with: the listing decides where it says (a `type` such as `embedding` or
`audio`, or Fireworks' `kind` `EMBEDDING_MODEL`, first; then OpenRouter's `architecture.output_modalities`
or Fireworks' `supports_chat`),
else a model whose id names an embedding, speech, image, moderation or completions-only model
(`text-embedding-3-small`, `tts-1`, `whisper-large-v3`, `dall-e-3`, `omni-moderation-latest`, `davinci-002`) is
left out. To those it adds `system2` from config and the concrete names in profile `match` lists (how models a provider does not
list, such as Fireworks dedicated deployments, get in). Ids are `provider/model`; names are the
provider's display name where it gives one, else the profile's `name` for a model from a profile. The server caches the catalog for 10
minutes. System 1 is listed from the console (`GET {console_url}/models` with `$NACE_API_KEY`,
retired models left out; `[system1].models` stand in when the key is missing or the console does not
answer) and from the decisions API (`GET {base_url}/models?output_modalities=decisions` with
`$OPENROUTER_API_KEY`, OpenRouter's filter for decision models), as `decisions:<model>`. Exit code 0,
even when every provider fails (the lists are then shorter); 2 on a configuration error.

The console is also a System 2 provider (0.50.0 and later), `nace`: `https://console.nace.ai/v1`, or
`$NACE_BASE_URL`, with `$NACE_API_KEY`. Its models are `nace/<model>`, such as `nace/glm-5.3`, listed from the
chat models in its `GET /models` (`data`) and never from its decision models (`models`), which stay System 1's. The
console lists no chat models yet, so `nace/` entries appear only once it serves some. A token the console issues
(the free credit's) is sent only to `console.nace.ai`, `console.test.nace.ai` and `console.stage.nace.ai`
over HTTPS: a `$NACE_BASE_URL` or `$DREX_BASE_URL` pointing elsewhere is a configuration error, and nothing is
sent.

## Free credit

The harness owns the free trial's sign-in, for every client (0.52.0 and later). A client drives it by CLI, as it
drives `intern secrets`: `intern free start --json --accept <consent_version> [--no-browser]` prints one JSON line
per step (`code`, then `granted` or `refused` with `code`, `reason` and `message`), and `status`, `pause`, `resume`
and `sign-out` with `--json` print one document each. [docs/free.md](free.md) has the lines, the refusal codes and
where the tokens are kept. A client shows the consent text before `start` and passes the version it showed.

Sessions run on free credit (0.54.0 and later), system by system. The agent meter pays for System 2 and the hand-over
model, the System 1 meter for System 1. At `session/new`, `session/load`, a reopen and the start of every run, each
system runs on free credit when `[free] use` is true, a grant is signed in and `active`, and its meter has more left
than its overdraft (so a run never starts on credit that cannot finish it). Otherwise it runs on the user's own
configuration, as before. A user with own keys and a grant runs everything free; once the agent meter is spent, the
next run's System 2 and hand-over are their own while System 1 stays free.

While a system runs on free credit, its models are the grant's `bundle`, whatever config, `--system2`, the journal or
`_meta` say:

- System 2 is `bundle.system2` and the hand-over always on (auto mode), to `bundle.handoff` with
  `bundle.handoff_tuning`; System 1 is `bundle.system1` on the console. Memory's steward writes with the hand-over
  model.
- System 2's working window is at most `bundle.window_tokens`, whatever `context_window` says, and no request asks for
  more than `bundle.max_output_tokens` (a reply cut off at the cap does not raise it past that).
- `model`, `system1` and `handoff` list only the bundle's model; `handoff` has no `off`.
- Another model is refused with `-32602` and `data: {"code": "free_locked"}`: by `_meta.nace.system2` or `system1`
  (the session is not opened), by `session/set_config_option` for `model`, `system1` or `handoff` (`off` too), and by
  `--system2`, `--system1` or `--handoff-to` when `intern acp` was started with them (every session/new fails). The
  bundle's own model is accepted and changes nothing. The message says to use own keys with `intern free pause`.
- The bundle's models are not journaled: a `session/load` once the credit is spent continues on the models the
  session last ran on with the user's own keys, or their configured ones.

When a system's meter is spent and the user's own configuration cannot run it, the session opens as it does for a
missing key: [unbound](#when-no-system-2-can-run) for System 2, with the reason starting `the free agent credit is
used up; add your own key to keep going: …`, and for System 1 the `-32602` of a missing System 1 key, with the same
start.

### Running out

A run starts on free credit only when the meter can pay for it to the end, so a meter usually runs out between runs.
When the console still refuses a free credit during a run (`free_exhausted`, `free_revoked`, `free_paused` or
`model_not_in_plan`), the harness does not retry it or try another of the bundle's models (0.55.0 and later):

- **System 1** sends the ask again with the user's own console key (`$NACE_API_KEY`, or `$DREX_API_KEY`), on the
  same model, and so does every ask after it; the run does not notice. With no such key the prompt fails at once with
  [`system1_unavailable`](#stop-reasons-and-errors), its `data` carrying `code` and `meter`.
- **System 2** and the hand-over model send the request again with the user's own Fireworks key
  (`$FIREWORKS_API_KEY`) for the same model, `accounts/fireworks/models/glm-5p3` or `…/deepseek-v4p1-flash-1017`, and
  the run goes on. With no such key the prompt fails with [`provider_error`](#stop-reasons-and-errors), `kind`
  `quota`, `code`, `meter` and `next`, the model the next prompt will use. The turn is journaled up to the refusal:
  sending the next prompt ("Continue on your keys") picks it up there on that model.

Either way the next run starts on the user's own configuration for that system, as when a meter is spent between
runs, and its pickers come back in a `config_option_update`.

A system on the user's own configuration goes back to free credit at the first run that starts once the console has
said its meter can pay again (0.57.1 and later): an answer's meters with room (an admin raised the limit), or a
grant read that is `active` again after a pause or with room. The grant is read again at most every 10 minutes; the
start of a run makes no call to the console of its own.

Each move to the user's keys, and each move back, is told once, as is each meter crossing 20% and 5% left, in a
`usage_update` whose `_meta.nace.free` has `notice`, a sentence to show as it is:

```json
{"sessionUpdate": "usage_update", "used": 18200, "size": 160000,
 "_meta": {"nace": {"free": {
   "meters": {"system1": {"usedUsd": 0.12, "limitUsd": 1.0}, "agent": {"usedUsd": 4.02, "limitUsd": 4.0}},
   "source": {"system1": "free", "agent": "own"},
   "notice": "Free agent credit used up; continuing on your keys with glm-5p3"}}}}
```

A meter already under a share when the session opens is not told again.

When a run starts on the user's own keys after the one before ran on free credit, or the other way round, the
prompt's updates are followed by a `config_option_update` with the pickers as they are now.

Every `usage_update` of a session that uses the free credit carries `_meta.nace.free`, the meters as the console last
reported them (from the `x-nace-free-used` and `x-nace-free-limit` headers of each answer, so the gauge moves as the
session works) and what each system runs on:

```json
{"sessionUpdate": "usage_update", "used": 18200, "size": 160000,
 "_meta": {"nace": {"free": {
   "meters": {"system1": {"usedUsd": 0.12, "limitUsd": 1.0}, "agent": {"usedUsd": 0.75, "limitUsd": 4.0}},
   "source": {"system1": "free", "agent": "free"}}}}}
```

`source` is `free` or `own` for `system1` and `agent`. `meters` is absent when the console has not given them yet.

[`models --json`](#model-catalog) gains `free` once the install has signed in:

```json
"free": {"state": "active", "use": true,
         "meters": {"system1": {"used_usd": 0.1, "limit_usd": 1.0}, "agent": {"used_usd": 0.5, "limit_usd": 4.0}},
         "bundle": {"system2": "nace/glm-5.3", "handoff": "nace/deepseek-v4.1-flash", "system1": "drex-latest",
                    "handoff_tuning": {"threshold": 0.7, "streak": 2, "min_turn": 8},
                    "window_tokens": 160000, "max_output_tokens": 8192},
         "source": {"system1": "free", "agent": "free"}}
```

`state` is the grant's (`active`, `paused`, `exhausted`), `signed_out`, or why the grant could not be read
(`free_revoked`, `unavailable`); `use` is `[free] use` (false while paused with `intern free pause`); `paused` is
present and true while the console has paused free calls; `source` is what each system would run on now. For a
system on free credit, `system2` (with its `window` capped), `system1` and `handoff` list only the bundle's model.

## Versioning

- The harness's event protocol (`intern schema`, `intern rpc`) is at `PROTOCOL_VERSION`
  3: `tool_call_end` gained an optional `diff`, then an optional `error`.
- ACP clients pin a minimum harness version, never a commit. Releases are tagged `vX.Y.Z` by CI when
  a version bump lands on `main`; [AGENTS.md](../AGENTS.md#release-what-clients-see) says when to bump.
- Planned (not in v1): `session/request_permission` from the tool hook, which adds supervised modes.
- Journals written by 0.6.0 may hold `capabilities_change` entries, which older harnesses cannot load.

### 0.2.1

Additive; 0.2.0 clients keep working.

- `session/new` and `session/load` take `_meta.nace.system2` / `system1`, and succeed when no System 2
  can run, opening the session unbound until the client picks a model.
- The catalog lists the concrete ids in profiles' `match` lists, such as a Fireworks dedicated deployment;
  profiles take an optional `name` for them.
- `nace-harness models --json` marks the default System 2 with `"default": true` and models that take
  a reasoning effort with `"reasoning": true`.
- Documented: grouped `model` options use ACP v1's `group` key (unchanged since 0.2.0).

### 0.3.0

System 1 is required; clients that showed or sent `heuristic` need a look.

- The `system1` option no longer lists `heuristic`, defaults to `drex-latest`, lists the console's
  decision models and the decisions API's models as `decisions:<model>`, and reports a session started on
  `--system1 decisions:<m>` as `decisions:<m>`. `heuristic` is still accepted when sent, as the
  explicit opt-out.
- `session/new` fails with `-32602` when System 1 cannot be set up (its key is missing) instead of
  opening on heuristics; a `system1` pick whose key is missing is refused with `-32602` and the session
  keeps its System 1.
- `--system1` also takes a bare Drex model (`drex-max-v1.1`) for the console; `drex:<model>[,...]`
  keeps working.

### 0.4.0

Additive; 0.3.0 clients keep working.

- Live sessions advertise [`context_window`](#config-options), a per-session cap on System 2's working
  window that holds across model changes and loads.
- `nace-harness models --json` gives System 2 entries a `window` when the profile or the provider's
  listing says it.
- `thought_level` lists the levels the model takes: `low` to `max` through Anthropic's API, `low` to
  `xhigh` through OpenAI's, still `low`, `medium`, `high` elsewhere.

### 0.5.0

Additive; 0.4.0 clients keep working.

- The prompt response carries [`_meta.nace.usage`](#stop-reasons-and-errors): the turn's System 2 model,
  tokens and cost, and its System 1 model, asks, failures, fallbacks, tokens and cost.

### 0.5.1

Fix; 0.5.0 clients keep working.

- System 2 on Anthropic's and OpenAI's own APIs has a cost: `system2.costUsd` in
  [`_meta.nace.usage`](#stop-reasons-and-errors) and `usage_update`'s `cost` were 0 for them, since
  those APIs report only tokens, and are now priced from the models' published prices.

### 0.6.0

Additive; 0.5.0 clients keep working and see the toggles as extra boolean options.

- Sessions advertise [capability toggles](#capability-toggles): one boolean `cap.*` option per
  capability, category `_nace_capability`, applied by reopening the session at the next prompt,
  journaled as `capabilities_change` and restored on `session/load`.
- `session/new` and `session/load` take [`_meta.nace.capabilities`](#picking-models-at-open).
- `nace-harness models --json` lists [`capabilities`](#model-catalog) with each one's configured
  default.

### 0.7.0

Additive; 0.6.0 clients keep working.

- `nace-harness models --json` capabilities carry [`unavailable`](#model-catalog), the reason one
  cannot switch on here, so a client can show it before the user switches it on.
- A [capability switch](#capability-toggles) that cannot switch on here stays off and is named in the
  prompt's `-32602`, while the other changes made with it apply. Before, one such switch kept all of
  them off. The error no longer runs a reason's full stop into the next clause.
- `session/load` leaves off a journaled toggle that can no longer switch on, instead of failing.

### 0.8.0

Agent behaviour only; nothing in the protocol changes, so 0.7.0 clients keep working.

- A session's first message is no longer pinned in every request. Every message of the user's is a
  turn of the conversation, and once compaction cuts it, the State keeps the user's last three
  requests as they wrote them. A later request is no longer left to a summary of it while the first
  one stays forever.
- Every model brings the State up to date with edits after its first one (before, only Opus, GPT and
  Qwen3.8 27B did), and a new message from the user no longer makes the next State a full rewrite. A
  profile of your own that still sets `state_edits` loads as before; the key is ignored.

### 0.9.0

Additive; 0.8.0 clients keep working and show the new toggles as extra boolean options.

- A session picks where its commands run: next to `cap.docker`, one [`cap.executor.<name>`
  toggle](#capability-toggles) per executor backend an installed package registers (and the configured
  one when it is not built in), listed in `models --json` with `unavailable` when it cannot switch on.
  The executor toggles are one choice.
- `cap.docker` switched off goes back to the configured backend; before, it always went to `local`.

### 0.9.1

Fix; 0.9.0 clients keep working.

- `cap.process` works with Docker (and any runner that runs background jobs): each job runs where the session's
  commands run, in its own container under Docker. Before, the prompt after switching it on failed.

### 0.10.0

Additive; 0.9.0 clients keep working and show the new runner as one more toggle.

- `cap.executor.opensandbox`: the [OpenSandbox](capabilities/opensandbox.md) runner ships with the harness, so its
  toggle is always listed. `models --json` gives it `unavailable` until the `opensandbox` extra is installed and its
  API key is set.
- A runner toggle's `unavailable` now also carries the backend's own reason (a missing extra or key), not only
  "cannot load" or "needs options".

### 0.10.1

Additive; nothing an ACP session sends changes, so 0.10.0 clients keep working.

- The harness's event protocol (`nace-harness schema`, `nace-harness rpc`, `-p --json`) has one more
  event, `extension`: an extension's own report as it works (`extension`, `kind`, and a `data` object
  whose shape the extension owns). Still `PROTOCOL_VERSION` 2: no event changed. A client that reads the
  stream should skip event types it does not know; ACP sends nothing for this one. In the terminal UI,
  an extension can draw these with a view of its own ([docs/tui.md](tui.md#extension-views)).

### 0.10.2

Fix; 0.10.1 clients keep working.

- `session/close` over stdio closes the session and stewards it into memory. Before, it answered
  `-32601` Method not found, although `initialize` advertises it, so a closed session never reached
  memory.

### 0.10.3

Fix; nothing an ACP session sends changes, so 0.10.2 clients keep working.

- `decision_code` indexes a repository the size of audit-app (16k files git sees; 478k with its ignored client
  workspaces): in a git work tree the index holds the files git sees, so what `.gitignore` keeps out of git is
  neither indexed nor searched, and the index's TF-IDF rows and neighbours are sparse and computed in blocks. The
  build there takes 28 s and 1.9 GB where it took 12 GB, or ran out of memory and took the terminal with it. One
  index holds at most 100,000 files; past that `decision_code` and `nace-harness index` say so instead of building.
  The `code-search` extra now includes scipy.

### 0.11.0

Additive; nothing an ACP session sends changes, so 0.10.3 clients keep working.

- `decision_code`'s `scope` may name a folder outside the working directory, such as another repository: the git
  work tree holding it is indexed the first time (into its own `.nace/index.json`, kept and refreshed like the
  working directory's) and searched within the scope; the places come back with paths `read` opens from the
  working directory. An `indexing` event's `root` is then that repository, not the working directory.

### 0.12.0

Additive; 0.11.0 clients keep working, and show the steward's cost in the session's `cost` with no change.

- What the memory steward spent on a session after it closed now reaches the client: its System 1
  asks and its writer's System 2 calls. `session/load` sends a [`usage_update`](#updates) with
  that cost, and the session's cost so far starts from it. `_meta.nace.steward` on that update has the
  tokens and cost of every steward run on the session, split into `system1` and `writer`. Before, the
  steward's cost was shown nowhere.

### 0.13.0

Additive; 0.12.0 clients keep working with no change.

- A session the client keeps open is stewarded into memory once it has been idle
  `[capabilities.memory] steward_idle_s` seconds after a prompt (default 300; 0 waits for
  `session/close` as before). Before, a client that never closed its sessions, such as T3 Code with its
  threads, reached memory only when its agent process exited. A prompt after that is stewarded again;
  the steward's cost still arrives on `session/load`.

### 0.13.1

Agent behaviour only; nothing in the protocol changes, so 0.13.0 clients keep working.

- A session's first State is written once System 1 finds the work worth one, usually within a task's
  first few turns. Before, System 1 was asked whether a State that did not exist yet was out of date,
  so a chat or short session on a large window could go without a State until compaction, often for
  its whole life, while a single lookup sometimes got one. A greeting, a quick answer or a lookup
  still gets none.

### 0.14.0

Breaking: System 1 is required at every step. Clients that send `heuristic` or read `fallenBack` need a
look; others keep working, and see one more prompt error.

- There is no heuristic System 1. `--system1 heuristic` and a `system1` pick of `heuristic` are refused
  with "System 1 is required", and a config with `[system1] backend = "heuristic"` no longer loads. Nothing stands in for an
  answer: not for the supervisor, the guard, compaction's keep verdicts, memory recall or `decision_code`.
- An ask System 1 fails is retried with backoff for up to 30 minutes; still unanswered, the prompt fails
  with [`system1_unavailable`](#stop-reasons-and-errors) (`-32603`). Before, a heuristic answered and, for
  the guard, the call went through unjudged.
- `session/new` and `session/load` check that System 1 answers (up to 15 s of retries) and fail with
  `-32602` when it does not.
- `fallenBack` is gone from `_meta.nace.usage.system1` and `_meta.nace.steward.system1`; `failed` counts
  the asks left unanswered after their retries.
- The System 1 cost cap and circuit breaker are gone; a session budget
  (`[capabilities.session_budget]`) still bounds System 1's calls and cost.

### 0.15.0

Additive; 0.14.0 clients keep working, and see a thinking picker on more models.

- `thought_level` is advertised for every model on Anthropic's and OpenAI's APIs, not only those whose
  profile sets `reasoning_effort`, as the terminal UI already offered it. Before, a Claude model without a
  packaged profile, and every GPT model, had none.
- On those APIs `thought_level` lists `default` first: no effort sent, so the model uses its own. It is
  the current value when the profile names no effort, and picking it goes back to sending none.
- `nace-harness models --json` gives System 2 entries `efforts`, the values `thought_level` takes on that
  model, so clients can offer the right levels before a session opens. `reasoning` now means the model
  takes an effort at all, as `thought_level` does.
- System 2 in `models --json` and the `model` option lists only models System 2 can chat with. Before,
  embedding, text-to-speech, transcription, image and moderation models a provider serves were listed too,
  and a session on one failed at its first prompt.

### 0.15.1

Fix; nothing an ACP session sends changes shape, so 0.15.0 clients keep working.

- A reply from a Fireworks serverless model now has a cost: Fireworks reports only tokens, and they are
  priced from its published Standard rates (input, cached input, output). Before, `usage_update`'s
  `cost` and `_meta.nace.usage.system2.costUsd` were 0 for them, and the TUI said `price unknown`. A
  dedicated deployment is billed per GPU-second, not per token, so it still costs 0.

### 0.15.2

A fix; older clients keep working with no change.

- Memory keeps what the user said in a long message. The steward used to read only the first 4,000
  characters of each user message, and T3 Code puts about 5 KB of its own instructions before the user's
  words in the first message of a thread. So nothing said in a thread's first message reached memory. A
  long message now keeps its end as well as its start.

### 0.15.3

Additive; 0.15.0 clients keep working, and see a thinking picker on more models.

- A profile can list `effort_levels`, the reasoning efforts an OpenAI-compatible model takes, which that
  API does not report. Its sessions then advertise `thought_level` with `default` first, and `models
  --json` lists them in `efforts`. Until a level is picked no effort is sent, so the model behaves as
  before; a model whose endpoint rejects the field fails only once a level is picked. Unknown levels make
  the profile fail to load.
- DeepSeek V4 Flash on Fireworks (`deepseek-v4p1-flash`, its `-1017` build and the spot and ultrafast
  routers) gets a packaged profile listing `low` to `max`. Each variant took every level, and refused an
  unknown one with 400.

### 0.15.4

Fix; 0.15.3 clients keep working.

- The OpenSandbox runner (`cap.executor.opensandbox`) runs commands on a real OpenSandbox server: every command
  failed with a 400 before, because commands went as an argument list, which the server's execd does not take.
  Now checked against a live server (copy and mount workspaces, deadlines, abort, background jobs).

### 0.15.5

A fix; older clients keep working with no change.

- Fireworks' embedding and reranker models (`qwen3-embedding-8b`, `qwen3-reranker-8b`) are no longer listed
  as System 2. Fireworks marks them `supports_chat`, which the catalog trusted; their `kind`,
  `EMBEDDING_MODEL`, now decides first.

### 0.15.6

A fix; nothing an ACP session sends or receives changes, so 0.15.5 clients keep working.

- `decision_code`'s `scope` starts the search at that folder. Before, the walk still began at the repository's
  root and judged every folder above the scope, so one low score on a broad card there ended the search
  after a single ask with nothing found (on audit-app, a question scoped to `services/main_service`). A scope
  that does not exist is refused, as one outside the working directory already was.
- A search where no folder scores high enough before any file is judged enters the best ones anyway, and
  says its places are weak; before, it ended with nothing found.
- A search with nothing to show says where its walk started, how far it got and the best folder it did not
  enter, instead of only "Nothing … looks related".

### 0.15.7

A fix; nothing an ACP session sends or receives changes, so 0.15.6 clients keep working.

- A session with memory on no longer fails at mount when the drive's working tree is off `main` or has
  edits. Before, a steward killed while it committed left the tree on `proposed/<session>`, and one shell
  write into a page left uncommitted changes; either failed every later session. A session reads
  `main`'s commit and only warns now.
- The steward builds its commit without touching the working tree, and a merging steward resets the tree
  to `main` and publishes or deletes a `proposed/` branch an earlier, killed steward left behind.

### 0.16.0

An addition; 0.15.7 clients keep working.

- `cap.workspace` ("Workspace"): a boolean capability option, on as `[capabilities.workspace] enabled` has it
  (off unless set). It gives the agent `index_files`, `decision_files`, `sql` and a document-aware `read` over a
  folder of PDFs, Word documents, workbooks, CSV and Parquet files, indexed in that folder's `.decision_workspace/`.
  Without the `[workspace]` extra it is listed with `unavailable` saying so.

### 0.17.0

An addition; 0.16.0 clients keep working.

- `cap.workspace` also gives the agent `sql_export`: it writes a `sql` result (a kept result, a scratch table
  or a SELECT) to a `.csv` or `.parquet` file in the working directory, never over an indexed document and
  never into `.decision_workspace/`, `.nace/` or `.git/`. Its calls come with kind `edit`.
- The workspace's `sql` reads tables of any size, keeps each SELECT's result as `res_N` (up to a million
  rows), and builds scratch tables. A write reaches only the session's scratch database, and `COPY`, `SET`,
  `ATTACH`, `INSTALL` and `PRAGMA` are refused. Each query is bounded in memory and time. `decision_files` and
  `DESCRIBE` show each table's column profile.

### 0.18.0

A rename; 0.17.0 clients keep working unless they match tool names.

- The search tools are renamed: `drex_code` is now `decision_code` and `drex_files` is now `decision_files`.
  Tool calls carry the new names. A client that matched the old names (to style or hide their calls) should
  match the new ones. Earlier entries in this list use the new names.
- The workspace index lives in `.decision_workspace/` (was `.drex_workspace/`). An existing index is moved
  there the first time its folder is opened, so nothing is rebuilt.
- System 1's descriptions in config options and `models --json` say "decision model" and "the console"; the
  `system1` values, `drex:<model>` references, `_meta.nace`, `_nace_capability` and every environment
  variable are unchanged. The bundled Qwen3.8 27B profile is named "Qwen3.8 27B (Fireworks)".

### 0.19.0

**Breaking**: the runner is one option, and its config is renamed. Clients that showed `cap.docker` or
`cap.executor.*` need a look; there is no alias.

- Where commands run is one [`runner`](#runner) select (category `_nace_runner`), listed in `models --json` as
  `runners`, and settable at open with `_meta.nace.runner`. The `cap.docker` and `cap.executor.<name>` toggles are
  gone from config options and from `capabilities`.
- A runner that cannot run here is refused when it is picked (`-32602` with the reason), not at the next prompt.
- config.toml: `[capabilities.executor] backend = "docker"` is now `[capabilities.runner] name = "docker"`, and
  `[capabilities.executor.options]` is `[capabilities.runner.options]`. The old table fails to load, with a
  message naming the new one.
- Plugins: the entry-point group is `nace_harness.runners` (was `nace_harness.executors`), and the base class is
  `nace_harness.runners.Runner` (was `nace_harness.executor_backends.ExecutorBackend`).

### 0.20.0

No change to the protocol. For Python hosts: `SecretResolver.resolve` is now `async`, and a resolver whose
`resolve` is a plain function is refused when the `SecretExecutor` is built, with a `TypeError` that says to wrap
blocking work in `asyncio.to_thread`. `[capabilities.secrets]` bindings can read a value from a command.

### 0.21.0

System 2 writes the State only for a compaction; 0.20.0 clients keep working.

- No State is written between compactions: `state_writing` and `state` events come only with a compaction
  (`trigger` `compaction`; `stale` and `first` no longer occur). `get_state`'s `state` and the State panel stay
  empty until a session's first compaction.
- System 1 reads a cut of the session built by code instead (the user's latest requests and the session in order,
  tool outputs as a note with their status, size, a check's tally and their first 300 characters, fitted to its
  window). An extension's `ctx.state()` returns it: a JSON object, `goal` and `history`, where it was text.
- The supervisor's `decision` events (purpose `supervise`) carry only the `mode` answer; `rewrite` and `worth` are
  gone.
- The supervisor's nudge (`nudge` event) has `reason` `stuck_gate`, was `stall_gate`; the rule that sends it is
  unchanged.

### 0.22.0

Additive; 0.21.0 clients keep working.

config.toml: a new top-level [`[secrets]`](secrets.md) table names keys kept in the OS keychain or a password
manager (`NAME = "keychain"` or `NAME = ["op", "read", "..."]`). Keys the harness reads itself (model, search, MCP)
stay out of the agent's commands. Keychain entries are read each session, command
entries once per process. One that can't be read doesn't stop the session: the client gets an agent message,
`Secret not loaded: NAME: <what to do>`, when the session opens (event `secret_unavailable`).
`nace-harness secrets set|list|test|remove [--json]` manages the table; a GUI can spawn it and pass a value with
`set --stdin`.
Stored values are replaced with `[REDACTED]` in every tool's output before it is recorded or sent, so
`tool_call_update` content, diffs and `RunEnd.detail` never carry one.

### 0.23.0

Additive; 0.22.0 clients keep working.

- A [capability toggle](#capability-toggles) switched on whose extra is missing (`browser`, `code-search`,
  `workspace`, `mcp`) gets it installed at the next prompt, then its setup (Chromium for the browser), as the
  terminal UI's `/caps` does. Progress comes as agent message text; `session/cancel` stops the install and keeps the
  change pending. Before, the prompt failed with the extra's install command.
- A toggle that cannot switch on here even then (web search with no provider or key) is no longer in a session's
  `configOptions` unless it is on. A client that still sends it gets the same `-32602` at its next prompt.
- `models --json`: a missing extra that switching on installs is no longer an `unavailable` reason.

### 0.24.0

0.23.0 clients keep working, unless they start the agent with `--extension requirements-check`.

- The `requirements-check` extension is gone. `--extension requirements-check`, or the name in a profile's
  `extensions`, now fails the session with `unknown extension 'requirements-check'`. No extension is packaged
  now; `--extension` still loads a `module:Class`.

### 0.24.1

Fix; 0.24.0 clients keep working.

- With the `process` capability on, the agent's `process poll` that waits ends as soon as the user sends a message
  (`session/prompt` while the turn is in progress), and the tool's description tells the agent to keep working
  while a job runs and to wait only when nothing else is left.
- A job the agent starts reads end-of-file on its input unless it asks for `stdin: true` (only those take `write`): a
  command that reads its input no longer hangs in a job.

### 0.25.0

The harness is now Intern. Nothing in the protocol changes; 0.24.0 clients keep working.

- The command is `intern`, the package to install `intern-agent` (`uv tool install "intern-agent[acp] @
  git+https://github.com/nace-ai/nace-harness@v0.25.0"`) and the import package `intern`. `initialize` returns
  `agentInfo {name: "intern", title: "Intern"}`; `--version` prints `intern 0.25.0`.
- Env vars are `INTERN_*`, the config home `~/.intern`, the per-project folder `.intern/` and the keychain
  service `intern`.
- `_meta.nace` and the `_nace_capability` / `_nace_runner` categories keep their names: Nace is the vendor
  namespace.
- Until 0.27.0 the old names still work. A `nace-harness` command runs `intern` (with a notice on stderr), so
  a client that starts `nace-harness acp` needs no change. A `NACE_HARNESS_*` variable is read when its
  `INTERN_*` one is unset, with a warning. On first run, the entries of `~/.nace-harness` move into `~/.intern`
  (beside the Intern app's own) and `~/.nace-harness` becomes a symlink to it, so session ids and paths saved
  before still load; an entry `~/.intern` already has is left in place, with a warning. A project's `.nace/`
  becomes `.intern/` and `.nace-resources/` becomes `.intern-resources/`. A keychain item stored under
  `nace-harness` is read from there and copied under `intern`.
- `uv tool upgrade nace-harness` cannot cross the rename: run `uv tool uninstall nace-harness` first, then
  install `intern-agent`. Both ship a `nace-harness` command, so uv refuses to install one beside the other.
- 0.27.0 removes the `nace-harness` command and the fallbacks, and raises the minimum version for clients to
  0.25.0. Clients should start `intern` before then.

### 0.27.0

The old `nace-harness` names are gone. **Minimum version for clients is now 0.25.0**: a client that starts
`nace-harness` or installs a harness older than 0.25.0 has to start `intern` and install `intern-agent`
0.25.0 or later. A client that already does keeps working.

- There is no `nace-harness` command any more, only `intern`.
- `NACE_HARNESS_*` variables (and `NACE_HARNESS_MODEL`) are not read: set the `INTERN_*` ones.
- `~/.nace-harness` no longer moves into `~/.intern`, a project's `.nace/` no longer becomes `.intern/`, and
  `.nace-resources/` no longer becomes `.intern-resources/`. Homes and projects that a harness from 0.25.0
  to 0.26.x has run in have moved already; move any others by hand. The `~/.nace-harness` symlink that the
  move left is kept, so session paths saved before the rename still resolve.
- A keychain item stored under service `nace-harness` is no longer read. Items that 0.25.0 to 0.26.x read
  were copied under `intern`; store any others again with `intern secrets set`.

### 0.27.1

The guard refuses reading your intern's own keys file, `~/.intern/.env`, with the read and grep tools or a
reading command (`cat ~/.intern/.env`), as it does `~/.ssh` and `~/.aws`: before, an agent asked to print it
printed your API keys. A project's `.env` can still be read; sending any `.env` stays refused. Nothing a client
sends or reads changed, so every client keeps working.

### 0.28.0

Additive; 0.27.0 clients keep working. Python hosts can disable automatic image attachments with
`ReadTool(attach_images=False)`, including through an extension's same-name tool replacement for CLI/RPC
sessions. Image paths then return a text notice without loading the bytes. Vision and other tools' images
are unaffected; the default still attaches images. The disabled reader's vision guidance uses the host's
viewing tools. Setup refuses an enabled Workspace capability that would override that fixed reader.
The setting does not remove images already in history; hosts with per-turn admission must keep filtering
or readmit those images. See [host-controlled image viewing](read-images.md).

### 0.28.1

The guard lets your intern install what its task needs from a standard package registry (PyPI, npm,
crates.io, apt, brew) or the maker's official download. Before, System 1 could refuse these as untrusted
code: `npm install -g typescript`, or the official Java runtime. Installs from an unknown index or host, or
from a stranger's repository, are still refused unless you ask for them.

When you tell your intern to follow a policy, an SOP or a runbook from your own organization, the guard
counts the records that document says to create or update, in the app you named, as asked for. Before, it
could refuse them, for example a HubSpot update a cleanup policy calls for. A "policy" from an outside sender
is not followed, and a step the document adds that your request does not need (deleting, overwriting data
with outside values, paying, refunding, or writing to people or places your request does not involve) is
still refused. Nothing a client sends or reads changed, so every client keeps working.

### 0.29.0

Additive; 0.28.0 clients keep working. System 1 has a second per-turn check: whether the request is done and
checked while the agent keeps changing what it did not ask for.

- A `decision` event with purpose `wrap_up` comes beside the supervisor's `supervise` one at each turn's end, until
  the wrap-up nudge has gone in. Its answer is `wrap`: a choice of `working`, `done_polishing`, `done_unsaved` or
  `unclear`, with probabilities.
- The nudge is a `nudge` event with `reason` `wrap_up`, at most once per run.

### 0.30.0

Additive for clients; 0.25.0 clients keep working. What the agent can call changed: `process` is in every session that
can run background jobs, and its `poll` no longer waits.

- The `process` tool is a base tool: every session whose runner runs background jobs (`local`, Docker, OpenSandbox)
  offers it, and a runner that runs none goes without it instead of refusing the session. `cap.process` is no longer
  listed in `configOptions` or `models --json`; a client that still sets it gets no error, and nothing changes.
- A `process` event reports each command that starts or ends in the session: a `bash` call, a job, or what a command
  left running (`cmd &`), with its command, state, exit code and times. It comes between runs too, is journaled, and
  is in the JSON-RPC and CLI `--json` streams. ACP sends nothing for it. Its `state` can be `moved` (a call that went
  on as a job), with `note` saying why.
- A State written for a compaction (`get_state`, the `state` event) has a `## Running processes` section before
  `## User requests` while jobs or what commands left running still run.
- `process` tool results carry the job's `command` and `elapsed_s`. `process start` returns the result of a job that
  ends within 3 s, else its handle.
- System 1's view of the session (an extension's `ctx.state()`) has a `processes` list while commands run.
- When a background job ends, or a process a command left running, the agent is told: a harness message (a `nudge`
  event, reason `process_ended`) at the run's next turn, with how it ended and the end of its output. A run does not
  end while such news is untold, so a reply that crossed a job's end gets one more turn. Between prompts an ACP session
  holds it, and tells it right after the next prompt; it starts no run of its own (the terminal UI's sessions do).
- System 1 looks at the commands running (at 30 s, 1, 2, 4, 8 and 16 minutes of each one's age, then every 30
  minutes): a `process_check` event (journaled; ACP sends nothing for it) with a verdict a command. A command it finds
  stalled, failing, or up and waited on is told to the agent as a harness message (`nudge`, reason `process_check`)
  at the run's next turn; between prompts, an ACP session holds it for the next prompt. A look System 1 cannot answer
  stops the run with `system1_unavailable`.
- A `bash` command goes on in the background as a job: as it starts when it is plainly a wait (a `sleep` of 10 s or
  more first, `tail -f`, `watch`, `gh run watch`); once it has run `[capabilities.process] move_after_s` (30 s by
  default); when System 1 finds it waits or serves, or finds it stuck; and at once when a message from the user comes
  in during it. Its `tool_call_update` completes with `[Running in the background as job <handle> (<why>). …]` and
  the output so far, and the agent is told when it ends. `decision` events with purpose `background` carry System 1's
  word on a command still running 2 s on.
- The agent's `process poll` no longer waits: `wait_s` is gone from its arguments (0.24.1's wait, which a user's
  message ended, is replaced). A turn that only polls (or lists) jobs that still run hands over, and the harness waits
  instead: an ACP prompt's turn, and its `session/prompt`, hold until the job ends, System 1 finds something, the user
  sends a message (`session/prompt` with the turn in progress), or System 1's next scheduled look at what runs (a
  `nudge` event, reason `process_look`, when it found nothing to tell); `session/cancel` stops it. A command System 1
  finds serves (a server, a watcher) is not waited for.
- `run_end` can have reason `waiting` (the agent handed over), in sessions that wake the agent when a job ends: the
  terminal UI, not ACP or JSON-RPC.

### 0.30.1

Fixes for background jobs; 0.30.0 clients keep working, and nothing a client sends or reads changed shape.

- A session reopened on its journal (`session/load`, a resumed terminal session, `/reload`) runs none of the jobs it
  ran before, since closing stopped them. The agent now hears of each one whose end it was not told: a harness message
  (`nudge`, reason `process_ended`) at the next prompt's first turn. Before, it was never told: it could wait for a job
  that was gone, or poll its handle and guess what had happened to it.
- Polling a handle from before a reopen says what became of its job. An unknown handle's error lists the session's
  jobs, so a mistyped handle is corrected in one call; it no longer says the handle came from another session.
- A `bash` command that moves to the background because it serves (System 1 judged it `serves`, or a later look found
  it up and `ready`) is no longer stopped at the call's `timeout_s`. It runs until it is stopped, the session closes,
  or 24 hours pass. Before, a server the agent reported as up was killed at the limit it had given a call it expected
  to block on, such as 60 s. Its `tool_call_update` says so. Any other moved command keeps its `timeout_s`, and its
  result now says when it will still be stopped.
- In the terminal UI, the nudge an agent gets before it hands over without a word (reason `process_waiting`) tells
  it, when the user asked how the job is going or whether it is stuck, to read the job's output or the file it writes
  to and answer from that. Before, it could answer "I can't tell" about a build whose output went to a log.
- The terminal UI keeps one line per command for System 1's routine looks, updated at each look, where it added a
  line per look. A finding the agent is told still gets its own line.

### 0.30.2

A security fix; 0.30.1 clients keep working, and nothing a client sends or reads changed shape.

- The harness's own keys are replaced with `[REDACTED]` in every tool's output when they come from the environment
  or `~/.intern/.env`, as stored `[secrets]` values already were: a model provider's `api_key_env`, System 1's
  `NACE_API_KEY` or `DREX_API_KEY`, and any variable the config names in a `*_env`, `header_env` or `env_refs`
  setting. Before, a plain `env` printed System 1's key into the model's context, the journal and `tool_call_update`
  content. Commands still get these variables, so a project's tests that need one keep working; only their printed
  values are hidden.

### 0.30.3

0.30.2 clients keep working; only how an install shows changed.

- Installing a capability's missing extra when its toggle switches on is shown as a `tool_call` (kind `other`, title
  `Install the <extra> extra`) instead of agent message text. Its content is what the install says as it goes, and it
  ends `completed`, or `failed` with the reason (also when `session/cancel` stops it). Before, those lines landed in the
  agent's reply.

### 0.30.4

Longer waits before a run stops on an outage; 0.30.3 clients keep working, and nothing a client sends or reads
changed shape.

- A model provider that keeps failing with retryable errors (HTTP 429, 5xx, an overloaded server, a dropped
  connection) is waited out: after the provider's own retries and the reply retries at 5 and 20 s, the reply is
  requested again every 30 s until 30 minutes have passed since the first failure, then the prompt fails with
  [`provider_error`](#stop-reasons-and-errors) as before. Errors that are not retryable (`auth`, `quota`, a bad
  request) still fail at once, and a cancel ends the wait. Before, a rate limit held for about a minute ended the run.
- An ask System 1 fails is retried with backoff (at most 30 s apart) for up to 30 minutes before the prompt fails with
  [`system1_unavailable`](#stop-reasons-and-errors); before, about two minutes.

### 0.30.5

Model fixes; 0.30.4 clients keep working, and nothing a client sends or reads changed shape.

- OpenAI's GPT-5 and GPT-6 models get their real window. OpenAI's model listing gives none, so they ran in the 32K
  default: now 1.05M for GPT-6, 5.6, 5.5 and 5.4 (and Pro), and 272K, the most input they take, for GPT-5, 5.1, 5.2,
  5.3 Codex, 5.4 mini and nano, and GPT-5 Pro. `models --json` reports these windows.
- Qwen3.8 Max on Fireworks gets its 262,144-token window, where it ran in the 32K default: Fireworks' listing gives
  none for it.
- Claude Opus 4.5, Sonnet 4.5 and Haiku 4.5 work again: they refuse adaptive thinking, which every request asked for,
  and now think on a fixed budget.
- Every Claude model sends its thinking as a summary, where those without a profile (Fable 5.x, Opus 4.6 to 5,
  Sonnet 4.6 and 5) had it hidden: `agent_thought_chunk` updates now carry their reasoning, which arrived empty.
  They also run at high effort by default, as the Claude 5.5 models do.
- In the terminal UI, a model's thinking that was hidden says so when opened, where it showed nothing.

### 0.30.6

A System 1 fix; 0.30.5 clients keep working.

- A System 1 on the decisions API whose model has a smaller window than the state it is sent is sent less: no more
  than three quarters of the window its listing gives, at the start of a session and when it is picked. Before, a
  model such as `baseten/joey-1` (16K) or `cloudflare/clef-flash` (24K) refused the state with HTTP 422 once the
  session grew past about 10K tokens, and the run stopped with `system1_unavailable`. `[system1].max_state_tokens`
  stays the most any model is sent, and models with a larger window, `drex-*` and Jev among them, are unchanged.
- `models --json` gives a `window` for the decisions API's System 1 entries too, from its listing.

### 0.30.7

Fewer turns while a job runs; 0.30.6 clients keep working, and nothing a client sends or reads changed shape.

- A turn that only waits on what runs holds the prompt until there is news, as a turn that only polls a job does:
  a `bash` wait that went on in the background (`sleep 300`, `tail -f`, `watch`, a CI watch) and, in a held prompt,
  a check that only reads how a job is doing (`ps`, `tail -n 20 train.log`). Before, a model that waited with
  `sleep` and checked with `ps` took a turn every second or two, each sending the whole session again. A `sleep`
  now costs one turn; a check holds 60 s at most. A wait that does not end by itself (`tail -f`, `watch`) still
  gets System 1's routine looks, so it never holds the prompt until its timeout.
- The note a command gets when it goes on in the background tells the model to wait with `process poll` rather than
  `sleep`, `ps` or `tail`.

### 0.31.0

The tool-call guard is off by default; 0.30.7 clients keep working, and nothing a client sends or reads changed
shape.

- `[guard] mode` in `~/.intern/config.toml` sets it: `off` (the new default) checks no call, `rules` runs the fixed
  rules only, `full` runs the fixed rules and System 1's judgement, as every session did before. A user who wants
  the old behaviour sets `mode = "full"`.
- With `off`, no call is refused, so no `tool_guard` event comes, no `decision` event has purpose `guard`, and no run
  stops with `stopped_by_guard`; with `rules`, no `tool_guard` event has layer `system1`. System 1 is still required.

### 0.32.0

A silent command can be stopped; 0.31.0 clients keep working, and nothing changes until it is turned on.

- `[capabilities.process] idle_stop_s` stops a command that writes nothing for that many seconds (0, the default,
  stops none; 600 is what Prime does): a `bash` call, one that went on in the background, and a `process` job unless
  `idle_stop_jobs = false`. Servers, watchers and long `sleep`s are left alone. The agent is told why it stopped and
  how to keep a long silent command alive. See [processes](capabilities/processes.md#a-silent-command-is-stopped).
- The `process` event of a stopped command has state `idle_stopped` and `quiet_s`, the seconds it had written
  nothing. `-p --json` and JSON-RPC carry it; ACP sends nothing for `process` events, as before.

### 0.33.0

A setting to turn model retries off; 0.32.0 clients keep working, and nothing a client sends or reads changed shape.

- `[capabilities] model_retries = false` makes each System 2 call once, for a client whose own gateway retries. The
  first retryable failure (HTTP 429, 5xx, an overloaded server, a dropped connection) ends the prompt with
  [`provider_error`](#stop-reasons-and-errors), where before the provider retried after 2, 8 and 20 s and the
  reply was requested again for up to 30 minutes. `system2_fallbacks` are still tried. The default, `true`, keeps
  the retries. See [model retries](capability-settings.md#model-retries).

### 0.34.0

A command can be held against `idle_stop_s`; 0.33.0 clients keep working, and nothing a client sends or reads changed
shape.

- `intern hold [--reason TEXT] -- cmd` runs `cmd` and keeps the command it runs in from being stopped for silence until
  `cmd` exits, for a command that waits on a platform call answered only when it is done. Python code can use
  `intern.holds.hold()`. Commands run by a session with `idle_stop_s` on the `local` runner are given
  `INTERN_HOLD_SOCKET` and `INTERN_PROCESS_ID`; any client can hold over that socket. See
  [processes](capabilities/processes.md#holding-a-command-while-it-waits-on-a-call).

### 0.34.1

A cancel no longer waits for the tool that is running; 0.34.0 clients keep working, and nothing a client sends or reads
changed shape.

- `session/cancel` (Esc in the terminal UI, `abort` over JSON-RPC) ends the prompt at once while `find`, `grep`,
  `memory_search`, a `decision_code` or `decision_files` search, `index`, `sql` or `sql_export` runs. Before, these
  did not watch the abort and were waited for: a `find` or `grep` over a large folder held the prompt for up to its
  60 s, a search or a query for as long as it took. Such a call now ends `failed` with `the run was aborted while
  this call ran`, ripgrep is stopped and the query interrupted. `bash`, `process`, the web tools, the browser and
  MCP tools stop themselves as before. A tool of an extension's own is waited for unless it sets `cancel_on_abort`.
- An index being built when the cancel comes is not cut: it finishes in the background and is kept, and the next
  search or `index` call waits for it instead of building beside it. Its `indexing` events (`-p --json`, JSON-RPC)
  can therefore arrive after the run's `run_end`.

### 0.35.0

Additive; 0.34.1 clients keep working.

- When no source gives System 2's window (no profile `window`, and the provider's listing fails or does not name the
  model), the model runs at 32,768 tokens, which also caps its replies to a share of that. That used to be silent.
  The client now gets an agent message, `Window not known: MODEL: its window could not be looked up (REASON), ...`,
  when the session opens and when the model is switched (event `window_fallback`, with `model`, `window`, `reason`
  and `message`). `run_start.window` has a new `lookup_error` field with the same reason, null otherwise.
- Before falling back, a listing call that times out, is rate-limited or fails on the server is tried twice more,
  after 1 s and 3 s. A refusal such as 404 is not retried. A host that does not answer the listing is not asked for
  llama.cpp's `/props` as well, so it holds the start for about 19 s at most.

### 0.36.0

Additive; 0.35.0 clients keep working, and nothing a client sends or reads changed shape.

- Twelve more System 2 providers, each OpenAI-compatible, by `provider/model` and the key variable it reads:
  `gemini` (`GEMINI_API_KEY`), `xai` (`XAI_API_KEY`), `deepseek` (`DEEPSEEK_API_KEY`), `mistral`
  (`MISTRAL_API_KEY`), `moonshot` (`MOONSHOT_API_KEY`), `zai` (`ZAI_API_KEY`), `minimax` (`MINIMAX_API_KEY`),
  `huggingface` (`HF_TOKEN`), `vercel` (`AI_GATEWAY_API_KEY`), `requesty` (`REQUESTY_API_KEY`), `nebius`
  (`NEBIUS_API_KEY`) and `sambanova` (`SAMBANOVA_API_KEY`). `models --json` and the `model` option list their models
  when their key is set; like the others, these keys are the harness's own and never reach the agent's commands.
- The terminal UI's first launch opens on a setup screen (`/setup`) that saves `system2`, `[system1]` and the keys.
  ACP clients are unaffected: a session that cannot open still fails with the reason, as before.

### 0.37.0

The tool-call guard is a capability; 0.36.0 clients keep working, and nothing a client sends or reads changed shape.

- Sessions offer one more boolean option, `cap.guard` ("Guard"), off unless `[capabilities.guard] enabled = true`.
  Switching it on makes the next prompt reopen the session with the guard, as every `cap.*` toggle does; a client
  that shows unknown `cap.*` options by `name` and `description` needs no change. `models --json` lists it too.
- The `[guard]` table in `~/.intern/config.toml` is gone: a config that still has it is refused, with where its keys
  went. `mode = "full"` is `[capabilities.guard] enabled = true`, `mode = "rules"` is that with `system1 = false`,
  and `system_writes` moves into `[capabilities.guard]`. A client whose session fails to open shows that reason,
  as for any invalid config.
- With the guard on, events are as before: `tool_guard` for each refusal, `decision` with purpose `guard`, and a run
  stopped by repeated refusals ends `stopped_by_guard`. `--extension guard` or a profile's `extensions = ["guard"]`
  is refused with a pointer to the capability.

### 0.37.1

A fix to how an index refresh is reported; 0.37.0 clients keep working, and nothing they read changed shape.

- Before each message, code search brings its index up to date. A refresh that read no file (a file was only removed,
  or another session in the same folder had already refreshed the index) sent a finished `indexing` event with
  `done` and `total` 0, which the terminal UI showed as `Indexed 0 files`. It now sends no `indexing` event. An index
  another session refreshed is taken up as it is, rather than rebuilt from this session's older copy.
- A finished `indexing` event has a new optional field, `read`: the files that build read, new or changed. `total` is
  still every file in the index, so a refresh that read 1 of 259 says `read: 1, total: 259`; the terminal UI shows
  `⌁ Refreshed 1 changed file for decision_code`, and `-p` prints `refreshed 1 of 259 files`. A first build has
  `read` equal to `total`. Clients that ignore unknown fields need no change.

### 0.38.0

Additive; 0.37.1 clients keep working, and nothing a client sends or reads changed shape.

- Sessions offer one more boolean option, `cap.tool-result-guard` ("Tool-result guard"), off unless
  `[capabilities.tool_result_guard] enabled = true`; `models --json` lists it too. With it on, System 1 screens what
  web, browser and MCP tools return (`screen = "all"`: every result but the agent's own writes) and replaces the
  chunks addressed to the agent in the result's text. Each screen is an `extension` event (`tool-result-guard`, kind
  `screen`) naming the removed chunks, and its ask a `decision` event with purpose `tool_result_guard`.

### 0.38.1

Two fixes to what the search tools find; 0.38.0 clients keep working, and nothing a client sends or reads changed
shape.

- With `cap.workspace` on, a CSV whose timestamps name a time zone (`2024-01-01T00:00:00Z`) was left out of the
  index, so `decision_files` could not find it and `sql` had no table for it, and a query returning such a column
  failed: DuckDB returns those values through `pytz`, which the `workspace` extra did not install. It does now, and
  an index picks the files up at its next refresh.
- A folder's card in the search tree names, for each group of files under it, the group's other files after its
  three most central ones, while the card has room. `decision_code` and `decision_files` judge a group by that card
  before opening it, and a file a group did not lead with could go unjudged. An index built earlier gets the new
  cards at its next refresh, with no file read again and so no `indexing` event for it.

### 0.39.0

System 1 supervises every fourth turn in one ask, where it asked twice at every turn; 0.38.1 clients keep working.

- `decision` events with purpose `supervise` come at every fourth turn's end, and at the turn after one whose
  verdict would act (stuck, or done and still changing things), not at every turn. A run's System 1 tokens
  (`run_end.system1`) fall accordingly: on stored runs it is asked at about a quarter of the turns.
- The `supervise` decision carries two answers: `mode` as before, and `wrap`, the choice of `working`,
  `done_polishing`, `done_unsaved` or `unclear` that the `wrap_up` decision carried. No `decision` event has purpose
  `wrap_up` any more. A client that showed a verdict per turn shows one per look.
- The `nudge` events are unchanged in shape (`reason` `stuck_gate`, at most three a run; `wrap_up`, at most one).
  The rule that sends them changed: a look that finds the turns stuck (P(re_investigating) + P(retrying) at 0.7 or
  more), or the request done (P(done_polishing) at 0.8 or more), is repeated at the next turn, and the nudge goes
  out when it holds. Once neither nudge can be sent any more, System 1 is not asked.
- System 1's State (`ctx.state()` for extensions) keeps the newest eight messages whole, was six.

### 0.39.1

A fix to what Claude costs through OpenRouter; 0.39.0 clients keep working, and nothing a client sends or reads changed
shape.

- Claude models reached through OpenRouter (`openrouter/anthropic/claude-*`) read nothing from Anthropic's prompt cache:
  Anthropic caches only where a request asks, and intern's OpenAI-compatible requests did not, so every turn paid its
  whole prompt at the full input price. The packaged `openrouter-claude` profile now asks for OpenRouter's automatic
  caching (a top-level `cache_control`), on each of Claude's upstreams there. In a live session (Claude Haiku 5.5, six
  turns of reads, edits and test runs) every request after the first read 85-95% of its prompt from the cache; a
  request of a 20k-token conversation fell from $0.00206 to $0.00021. `run_end.usage` shows the cache reads as before.

### 0.40.0

Additive; 0.39.1 clients keep working, and nothing a client sends or reads changed shape.

- Sessions offer one more boolean option, `cap.subagents` ("Subagents"), off unless `[capabilities.subagents] enabled
  = true`; `models --json` lists it too. With it on, the agent has an `agent` tool that hands tasks to subagents: runs
  with a clean conversation and the agent's tools or fewer. A client sees an `agent` tool call (kind `other`) whose
  result holds each subagent's report. A subagent's own steps are not sent, its file edits included: a client that
  shows diffs does not see the files a subagent changed.
- The event stream (`-p --json`, JSON-RPC) has two more event types, `subagent_start` and `subagent_end`, and
  `run_end` an optional `subagent_usage`. `run_end.usage` includes what the run's subagents spent. ACP sends nothing
  for the new events.

### 0.40.1

A fix to the thinking levels offered; 0.40.0 clients keep working, and nothing a client sends or reads changed shape.

- Every model on Anthropic's and OpenAI's APIs offered the API's whole run of levels, and some models refuse some or
  all of them: each request at such a level failed with a 400 (measured 2026-11-17). Sessions now offer each model the
  levels its API takes, and `models --json` lists those in `efforts`:
  - none for OpenAI's GPT-4.1 and GPT-4o (and their minis and dated ids) and gpt-live-1, and for Claude Sonnet 4.5 and
    Haiku 4.5: their sessions advertise no `thought_level`, and `models --json` gives them `"reasoning": false`;
  - `medium` alone for chat-latest, `high` alone for GPT-5 Pro, `medium` to `xhigh` for GPT-5.2, 5.4 and 5.5 Pro,
    each after `default`.
- A profile's `effort_levels` now replace the API's run when stated, where they counted only on an API that gives
  none; `effort_levels = []` is a model that takes no level. A profile whose `reasoning_effort` is not one of its
  `effort_levels` is refused when it loads, naming both.

### 0.40.2

Two fixes to OpenAI's models in the catalog; 0.40.1 clients keep working, and nothing a client sends or reads changed
shape.

- `models --json` no longer lists OpenAI's search models (`openai/gpt-5-search-api` and its dated id). OpenAI serves
  them on Chat Completions only, and System 2 reaches OpenAI through the Responses API, which refuses them: a session
  on one failed every request. A search model through another provider (OpenRouter's Sonar Pro Search) is still listed.
- GPT-4o and GPT-4.1 (with their minis and dated ids) run at their own windows, 128K and 1,047,576 tokens, with 16K and
  32K of output, where OpenAI's listing gives none and they ran at the 32K fallback. `models --json` gives each its
  `window`, and a session on one runs at it (`context_window` `auto`). They take images (`read` shows image files).

### 0.41.0

A thinking level more, `auto`; 0.40.2 clients keep working, and nothing a client sends or reads changed shape.

- On a model served through Anthropic's API, `thought_level` takes `auto`, listed last, after the model's levels;
  `models --json` lists it last in those models' `efforts`. At `auto`, System 1 picks one of the model's levels at the
  first request of each run, from the request that started it, and every request of the run goes with that level; the
  next run asks again. A client that lists the options it is given shows it with no change.
- Each pick is a `decision` event with purpose `effort` and one answer, `level`: a choice among the levels the model
  takes, with its probabilities. A run at `auto` makes one System 1 ask more than one at a fixed level.
- Only there, because only there does a change of level keep the prompt cache (Claude Haiku and Sonnet 5.5: every
  token before the new message read from the cache after low → high → low). On every other provider a session can
  pick System 2 from, the first request at a level not used lately read nothing from the cache: OpenAI's Responses API
  (directly or through OpenRouter), Fireworks (DeepSeek V4 Flash, gpt-oss-120b), DeepInfra (Qwen3.8-27B) and
  OpenRouter's upstreams for Qwen3.8-27B. The provider writes the level into the prompt's head, so `auto`, which may
  change it every run, would cost an uncached prompt each time ([architecture](architecture.md)).

### 0.42.0

`decision_code` returns a function or a class in thirteen more languages, where it returned a window of 120 lines;
0.41.0 clients keep working, and nothing a client sends or reads changed shape.

- With `cap.code-search` on, code in JavaScript, TypeScript, Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift, shell and
  SQL is indexed by its classes and functions, as Python is: a place in a `decision_code` result is a function or a
  class with its lines, named by its signature. Code outside every definition (a script's body), a file that defines
  nothing and code in any other language are found by windows of 120 lines, as before.
- In a file with more than 160 chunks (a long source file defines that many functions), a search judges the 160
  nearest the question's words and then the file's first ones, where it judged the first 160 only, so a function at
  the end of a long file can be found. This holds for Python files too.
- The `code-search` extra now includes tree-sitter and the grammars of those languages (14 packages, each with wheels
  for macOS, Linux and Windows). Without them, in an environment that installed the extra from an earlier version,
  `decision_code` works as it did in 0.41.0.
- An index built earlier has the files of those languages read again at its next refresh, with `indexing` events
  as for any build and no model call: about 1 ms a file (15 s for the 15,500 Go files of Kubernetes). Documents are
  not read again. The index holds more chunks, so `.intern/index.json` grows: Envoy's (11,400 files) from 48 MB to
  105 MB.

### 0.43.0

Every failed or refused tool call says where it failed; 0.42.0 clients keep working, and nothing ACP sends changed shape.

- A tool result's `error` (in the journal, and on `tool_call_end` of the event protocol, now at `PROTOCOL_VERSION` 3)
  has a `kind`: `unknown_tool` (the call named no tool of the session), `bad_arguments` (its arguments did not parse),
  `refused` (an extension refused it; `refused_by` names the extension, `guard` for the guard), `stopped` (the run
  stopped or was aborted before or while it ran), `failed` (the tool ran and reported a failure), `crashed` (the tool
  raised) or `interrupted` (an earlier run ended before the call's result was saved). These were told apart only by
  their text. A call whose arguments were not JSON is not run and has no result, as before: it stays on the reply's
  `invalid_tool_calls`, with a `nudge` of reason `invalid_tool_call`.
- `tool_call_end` carries the call's `error`, `null` when it succeeded, so a `-p --json` run's stream gives the same
  record as its journal.
- Older harnesses load journals written by this one; the errors in journals written before it have no `kind`.

### 0.44.0

System 1 can judge what `web_search` and `web_read` return, each behind its own option, off by default; 0.43.0
clients keep working, and with both options off a client reads only the toggles' new descriptions.

- `[capabilities.web_search] system1 = true`: a search asks its provider for three times `limit` (at most 20), and
  System 1, reading the session, keeps the best `limit` the task needs, best first. Each kept hit has `relevance`, the
  probability System 1 gave it; `warnings` says how many were kept, or that none was and the agent should search
  differently. One `decision` event with purpose `web_search` per search.
- `[capabilities.web_reader] system1 = true`: of a page longer than the preview, System 1, reading the session,
  keeps the parts the task needs, shown in page order with the skipped lines named, where the result was the page's
  start. A `decision` event with purpose `web_read` per 16 parts. When no part is kept, the start comes back as before.
- The `cap.web-search` and `cap.web-read` toggles' `description` says what System 1 does for the pack, and, with
  the option off, the setting that turns it on.
- `[capabilities.search]` and `[capabilities.reader]` are now `[capabilities.web_search]` and
  `[capabilities.web_reader]`, named for the packs they set up. A config.toml with the old names loads as before; one
  with both an old and a new name for the same table fails to load, saying which to keep. From Python,
  `CapabilitySettings` takes either name, and its fields are `web_search` and `web_reader`.

### 0.44.1

Every install of `intern-agent` serves ACP: the `agent-client-protocol` SDK is now a dependency, not the `acp` extra.
An `intern` installed the way the README says, without `[acp]`, made `intern acp` exit with code 2 (`No module named
'acp'`), so every thread a client opened on it failed to start. Nothing in the protocol changes; 0.44.0 clients keep
working. `intern-agent[acp]` still installs, the extra now empty.

### 0.45.0

A run can hand over, once, to a cheaper model when System 1 judges that the rest of the task is within its reach; off
by default, and 0.44.1 clients keep working.

- `[capabilities.handoff] to = "<model>"` (or `--handoff-to`) names the model; `effort`, `threshold`, `streak` and
  `min_turn` (8 by default) say how and when. The model is bound when the session opens, so a bad reference or key
  fails then.
- With `transfer = "context"` (the default) the cheaper model finishes the run as a child run: it goes on from the
  conversation with the session's tools, and its events stream to the client as the run's own. The session's journal
  keeps only the user's messages during it and its final reply, so the next prompt is answered by the session's System
  2 again. The child run's turns are kept in `<session>.subagents/handoff-<run>.jsonl`. With `transfer = "logfile"`
  the session switches to the cheaper model, which starts afresh from a file of the turns so far.
- A new `handoff` event (`turn`, `source`, `target`, `transfer`, `probability`, `tokens`, and `logfile` or `journal`)
  announces the hand-over; a client that skips event types it does not know is unaffected.
- `run_end` gains `by_model`, the run's usage by model; its `usage` totals both models.

### 0.46.0

A hand-over always continues the conversation: `transfer = "logfile"`, where the cheaper model started afresh from
a file of the turns so far, is gone, and `[capabilities.handoff]` no longer has a `transfer` setting. A config.toml
that still sets it loads as before and hands over as `context` did. The `handoff` event keeps `transfer`, now always
`"context"`, and drops `logfile`, which only that mode set. Still `PROTOCOL_VERSION` 3: a client that reads the event
gets the values it could get before. ACP does not carry the event, and 0.45.0 clients keep working.

### 0.46.1

The process watch skips commands that are only `sleep` and numeric durations: their answer never changes, so they
cost no scheduled System 1 looks. Their ends are still reported as before; other commands keep their schedule.
Older clients keep working, and nothing a client sends or reads changed shape.

### 0.47.0

A hand-over stays in the session. The cheaper model's turns are entries of the session's own journal, each reply
under the model that wrote it, instead of a child run's in `<session>.subagents/handoff-<run>.jsonl`; that file is no
longer written. The next prompt is still answered by the session's System 2, which is now sent those turns as part of
the conversation rather than the final reply alone. The journal also keeps the `handoff` event, so a resumed session
shows where a run handed over. The event drops `journal`, which named the child run's file. The note that tells the
cheaper model it is taking over is no longer a message of the conversation, so no `nudge` (reason `handoff`) and no
`message` event carry it: that model is sent it for the rest of the run, the journal does not keep it, and System 2
never reads it; nor does System 1, whose State is the journal's. When a run hands over is unchanged. Still
`PROTOCOL_VERSION` 3; ACP does not carry the event, and 0.46.1 clients keep working.

### 0.48.0

The `auto` thinking level is gone: System 1 no longer picks System 2's reasoning effort for each run. A change of
level costs the prompt cache through Anthropic's API too, where only the system prompt, the tools and the first
user message are read back after one, so a pick that changed from one run to the next paid for the conversation
again. 0.47.0 clients keep working.

- `thought_level` no longer lists `auto`, and `models --json` no longer lists it in a model's `efforts`. A client
  that still sets `thought_level` to `auto` gets `default` (the model's own level), not an error.
- No `decision` event has purpose `effort`.
- A profile whose `reasoning_effort` is `auto` loads as one that names none.
- In the terminal UI, `/thinking` and Shift+Tab set the levels the model takes, and the masthead shows the one in
  force.

### 0.49.0

ACP clients see and pick the hand-over ([0.45.0](#0450)), which ACP did not carry before. Additive: 0.48.0 clients
keep working, and a session where no run hands over sends what it sent before, but for `_meta.nace.model` on updates.

- A [`handoff` option](#config-options) (category `_nace_handoff`, select): `off`, then the models a run can hand
  over to, the session's target first. Setting it is `[capabilities.handoff] to` for the session: bound at once (a
  model that cannot be bound fails with `-32602` and changes nothing), journaled as a `model_change` of role
  `handoff`, kept across a capability reopen and restored on `session/load`. Older harnesses load such a journal and
  ignore the entry.
- The extension notification [`_nace/handoff`](#updates) `{sessionId, turn, from, to, probability}` when a run hands
  over, in its place among the run's updates, and replayed there on `session/load`. ACP asks clients to ignore an
  extension notification they do not know.
- `agent_message_chunk`, `agent_thought_chunk`, `tool_call` and `tool_call_update` carry `_meta.nace.model`, the
  model that wrote them, live and on replay.
- [`_meta.nace.usage`](#stop-reasons-and-errors) gains `handoff` (model, tokens, `costUsd`) after a prompt whose run
  handed over, from `run_end.by_model`, and `system2` then leaves that model's share out. A client that reads only
  `system2` shows less for such a turn than it did; the prompt's `usage` and `usage_update`'s `cost` are unchanged.
- [`models --json`](#model-catalog) gains `handoff: {models, default}`.

### 0.50.0

The Nace console is a System 2 provider, `nace` ([model catalog](#model-catalog)). Additive: 0.49.0 clients keep
working, and `models --json` lists what it listed before until the console serves chat models.

- `--system2 nace/<model>` and `system2` in config.toml take `nace/` models, with `$NACE_API_KEY` (and
  `$NACE_BASE_URL` for another console). `nace/glm-5.3` runs on GLM 5.3's profile and
  `nace/deepseek-v4*-flash*` on DeepSeek V4 Flash's.
- `models --json` lists the console's chat models as `nace/<model>` once it serves them, never its decision models.
- A `401` whose `error.code` is `token_expired`, from System 2 on `nace` or from System 1, has its bearer renewed and
  the request sent again, once.
- `NACE_FREE_REFRESH_TOKEN`, like `NACE_API_KEY`, is a harness key: a `[secrets]` entry of that name is kept out of
  the agent's commands, and its value is redacted from what they print.

### 0.51.0

[`models --json`](#model-catalog) gains `recommended`: Nace's recommended combinations on a user's own keys (System 2,
the hand-over model and System 1, each with the keys it needs), so a client's welcome flow offers the same list as the
terminal UI's setup. Additive: 0.50.0 clients keep working and ignore the field.

- The terminal UI's setup offers the combinations first, and asks for the hand-over model after System 1. What it
  saves is `[capabilities.handoff] to`, which ACP sessions read too: a user who picks a hand-over model in the terminal
  UI's setup gets it in the app as the `handoff` option's value.

### 0.52.0

The [free credit](#free-credit)'s sign-in: `intern free status|start|pause|resume|sign-out`, each with `--json`.
Additive: 0.51.0 clients keep working, and nothing a session does changes until a later release runs sessions on
free credit.

- `intern free start --json --accept <consent_version>` signs in with the console's device flow and prints `code`,
  then `granted` (with the grant: state, meters and bundle) or `refused` (`code`, `reason`, `message`); see
  [docs/free.md](free.md).
- The tokens are kept in the OS keychain and in `~/.intern/free.json` and `free-access.json` (mode 0600), shared by
  every harness process on the machine, renewed by one at a time, and redacted from what the agent's commands print.
- `config.toml` takes `[free] use`, which `pause` and `resume` set.

### 0.53.0

Intern speaks of three models: the **frontier model** (System 2) starts each task and does the hard part, the
**efficient model** (the hand-over target) finishes the rest, and the **decision model** (System 1) picks the moment
to hand over. Option ids, fields and flags are unchanged: 0.52.0 clients keep working.

- [`models --json`](#model-catalog)'s `recommended` lists frontier and efficient pairs on one OpenRouter key:
  `opus-hy4` (Claude Opus 5.5 → Hy4 Flash), `hy4` (Hy4 → Hy4 Flash) and `gpt` (GPT-6.2 Astra → GPT-6 Luna), each
  with Drex Max as the decision model. The ids `one-key`, `best-results` and `free-models` are gone: a client that
  shows the list as given keeps working; one that looked for those ids by name finds none.
- The `cap.*` options' descriptions say "the decision model" where they said "System 1".
- A session that opens with no model configured says to run `intern` in a terminal to connect one; the message
  still starts `no model given`.
- The terminal UI's setup always sets a pair, and its screens, masthead, transcript and commands use the three names;
  `/frontier`, `/efficient` and `/decision` sit beside `/system2`, `/auto` and `/system1`, which keep working.

### 0.54.0

Sessions run on the [free credit](#free-credit), system by system: on the grant's bundle while its meter lasts, then
on the user's own configuration. Additive: 0.53.0 clients keep working. A client that sends a model, `system1` or
`handoff` on every turn gets `-32602` with `data.code` `free_locked` while that system runs on free credit, so it
should send none then (`models --json`'s `free.source` says which systems do).

- With `[free] use` true and an active grant, System 2, the hand-over (always on) and System 1 run on the bundle, and
  memory's steward writes with the hand-over model. The window is capped at `bundle.window_tokens` and each request's
  output at `bundle.max_output_tokens`.
- `model`, `system1` and `handoff` list only the bundle's model while their system runs on free credit, and refuse
  another (`handoff` refuses `off`), as do `_meta.nace.system2` and `system1`, `--system2`, `--system1` and
  `--handoff-to`.
- A system whose meter is spent runs on the user's own configuration from the next run; with none, the session opens
  unbound, saying the free credit is used up.
- `usage_update` gains `_meta.nace.free` (the meters, from the console's response headers, and each system's source);
  `models --json` gains `free` (`state`, `meters`, `bundle`, `use`, `paused`, `source`).

### 0.55.0

A free credit that runs out during a run no longer fails it the way the console refused it
([running out](#running-out)). Additive: 0.54.0 clients keep working; the new `data` fields and `notice` are ignored
by a client that does not read them, and the errors keep their codes and `kind`.

- System 1 moves to the user's own console key, on the same model, and System 2 to the user's own Fireworks key for
  the same model, and the run goes on. With no such key, a refused System 1 stops the run at once (no retry window,
  where before it retried for up to 30 minutes), and a refused System 2 ends the prompt with `provider_error`.
- `provider_error`'s `data` gains `code` and `meter` when the Nace console refused the request, and `next` (the
  model the next prompt will use, or `null`) when the free credit ended; `system1_unavailable`'s gains `code` and
  `meter`. The console's `free_*` and `model_not_in_plan` refusals are not retried.
- `usage_update`'s `_meta.nace.free` gains `notice`: once for each move to the user's keys, and once each when a
  meter crosses 20% and 5% left. Harness event `free_notice` (`kind` `switch` or `low`, `meter`, `text`).

### 0.56.0

The terminal UI runs on the free credit: setup asks how to run Intern first (Start free, or your own keys), signs
in with the console's device flow and opens the session on the grant's models; `/free` shows the credit and pauses,
resumes or signs out of it ([terminal UI](tui.md#free-credit)). Nothing changes on ACP: 0.55.0 clients keep working.
`intern free status --json` prints the same document as before.

### 0.56.1

System 2 and the hand-over model run on the free credit: the harness asks the console for the bundle's model by the
id the grant names (`nace/glm-5.3`), where before it sent `glm-5.3`, which the console refuses as
`model_not_in_plan`. Nothing changes on ACP: 0.56.0 clients keep working, and the model ids they see are the same.

### 0.56.2

The memory steward writes again on GLM 5.3: its writer asks for low reasoning effort when the model's profile lists
it, where before GLM 5.3 thought past the writer's two-minute timeout and every session's memory was deferred.
Nothing changes on ACP: 0.56.1 clients keep working.

### 0.57.0

The agent can look through memory during the run ([memory](memory.md#during-the-run)): the `memory_search` tool
becomes `memory`, whose `action` lists a folder's pages, reads a page or one block, searches as before, or traces a
line to the session step it was recorded from. Session tool calls, and `tool_call` updates, now show `memory` with
an `action` where they showed `memory_search`. Additive for clients: 0.56.2 clients keep working; one that matched
the name `memory_search` should match `memory` too.

### 0.57.1

A session that moved a system to the user's own keys goes back to free credit at the next run once the console says
the meter can pay again (a limit raised, or free calls resumed after a pause), where before it stayed on the user's
keys until the session was opened again ([running out](#running-out)). `usage_update`'s `_meta.nace.free.source`
then reads `free` again, the move is told in its `notice`, and the pickers come back in a `config_option_update`.
Nothing new on the wire: 0.57.0 clients keep working.

### 0.57.2

When free credit ends a run for good, the terminal UI's stop line says so in words ("Free agent credit used up;
your next message runs on your keys with …"), where before it showed the console's JSON body. The harness event
`run_end` gains an optional `error`, the console's `code`, `meter` and `next`, as the prompt's
[`provider_error`](#stop-reasons-and-errors) `data` already carries them. Nothing changes on ACP: 0.57.1 clients keep
working.

### 0.57.3

Each config home has its own keychain items. `~/.intern` keeps service `intern`, so its keys and free-credit sign-in
stay where they are and nobody signs in again. Any other `INTERN_HOME` uses service `intern-<hash of its path>`,
where before every home shared `intern`: a test or second home no longer overwrites the user's keys with
`intern secrets set`, and two homes no longer share, rotate or sign out of one free-credit refresh token. A client
that runs the harness with its own `INTERN_HOME` and stored keychain entries before this release finds them
`missing` in `intern secrets test --json` and stores them again (`intern secrets set`), and signs in to the free
credit again (`intern free start`). Clients on the default home see no change: 0.57.2 clients keep working.

### 0.58.0

An unbound session whose free credit is why it cannot bind says so by code: `session/prompt` and
`session/set_config_option model` still fail with `-32602` and the same message, and now carry
`data: {code, meter}` (`free_exhausted` with `meter` `agent`, `free_paused`, `free_revoked`), as a
[`provider_error`](#stop-reasons-and-errors) does ([when no System 2 can run](#when-no-system-2-can-run)). A client
can show the free credit's state instead of matching the wording. Older clients see the same code and message:
0.57.3 clients keep working.

### 0.58.1

When the Nace console refuses a request without ending the free credit (`rate_limited`, `upstream_error`,
`request_too_large`, `overloaded`, `internal_error`), the terminal UI's stop line says what the console said in
words ("The model provider is busy. Retry after 1 seconds"), where before it showed `provider_error: HTTP 429 from
nace:` and the console's JSON body. The harness event `run_end.error` gains an optional `message`, the console's
`error.message`. Nothing changes on ACP: 0.58.0 clients keep working.

### 0.58.2

The memory steward's writer turns reasoning off on DeepSeek V4 Flash (Fireworks and `nace/`), where before it asked for
low effort: at low that model still reasoned 9,000 to 21,000 tokens per note, a lessons draft could outrun the
writer's two-minute timeout, and the steward deferred the session again and again. A full simulated week now
completes, with steward time over its first 34 sessions down from 5,172 to 442 seconds. A profile names how to turn
reasoning off with `thinking_off`; sessions never send it, and the model's efforts in `models --json` and the effort
picker are unchanged. Nothing changes on ACP: 0.58.1 clients keep working.

### 0.58.3

A request in a session to forget something removes it from memory where before part of it stayed and a later session
gave it back. The request is recognised in any wording ("wipe", "I should not have shared"), and found by word
endings or one rare word. Lines that hold the names and numbers of the removed lines go too, and so does what the
session itself said of the thing, its searches for it and their results. A line of another transcript that names a
forgotten session by its id becomes the marker. [Memory](memory.md) has the details. Nothing changes on ACP: 0.58.2
clients keep working.

### 0.58.4

In the terminal UI, setup on your own keys while the free credit is in use pauses the free credit (`[free] use =
false`, as `/free pause` does), so the session reopens on the models just picked. Before, it reopened on the free
credit's models and refused the picks. A setup that only connects a decision model leaves the free credit as it was;
`/free resume` goes back to it. Nothing changes on ACP: 0.58.3 clients keep working.
