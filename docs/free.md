# Free credit

Intern can run on free credit from the Nace console, with no keys: one sign-in in the browser with GitHub or
Google, and the console pays for a fixed set of models until the credit is spent. Signing in is only for the free
credit; with your own keys you never sign in.

With both the free credit and your own keys, the free credit comes first, system by system, and your keys take over
once it is spent ([below](#which-credit-a-session-runs-on)).

## Commands

```bash
intern free start --accept 2026-11-18   # sign in; the version of the consent text you were shown
intern free status                      # the grant, its meters and models, and whether the trial is open
intern free pause                       # use your own keys, keeping the credit for later
intern free resume                      # use the free credit again
intern free sign-out                    # revoke the sign-in on the console and forget it here
```

`start` opens the console's sign-in page and prints its address and a code to check there (`--no-browser` only
prints them, for a remote machine or SSH). It waits until you have signed in and agreed, then turns the free credit
on (`[free] use = true` in `~/.intern/config.toml`). `pause` and `resume` set that line, keeping the rest of the file
as you wrote it.

Every command takes `--json`, for the desktop app. `start --json` prints one JSON object a line as the sign-in goes:

```json
{"step": "code", "user_code": "ABCD-EFGH", "verification_uri": "https://console.nace.ai/oauth/device", "verification_uri_complete": "https://console.nace.ai/oauth/device?user_code=ABCD-EFGH", "expires_in": 600, "url": "https://console.nace.ai/oauth/device?user_code=ABCD-EFGH"}
{"step": "granted", "grant": {"state": "active", "meters": {"system1": {"used_usd": 0.0, "limit_usd": 1.0}, "agent": {"used_usd": 0.0, "limit_usd": 4.0}}, "bundle": {"system2": "nace/glm-5.3", "handoff": "nace/deepseek-v4.1-flash", "system1": "drex-latest", "handoff_tuning": {"threshold": 0.7, "streak": 2, "min_turn": 8}, "window_tokens": 160000, "max_output_tokens": 8192}}}
```

or, in place of `granted`, `{"step": "refused", "code", "reason", "message"}` (exit code 1). `refused` can also be
the only line, when the console takes no sign-ins. The codes:

| `code` | When |
| --- | --- |
| `not_eligible` | The console turned the account down; `reason` says why: `account_too_new`, `email_unverified`, `network_blocked`, `grants_paused`, `invite_only`, `identity_required` (sign in with GitHub or Google, not an email code), `identity_used` (that identity or account has a grant elsewhere), `account_flagged` |
| `free_revoked` | This account's grant was revoked |
| `access_denied` | The sign-in was declined in the browser |
| `expired_token` | The code expired (10 minutes) before the sign-in was finished |
| `unavailable` | The console could not be reached, or takes no sign-ins now |

`grant` is null when the sign-in worked but the grant could not be read; `intern free status` reads it again. A
meter may also carry `overdraft_usd` (see [below](#which-credit-a-session-runs-on)) when the console gives it.
`status --json` prints `{signed_in, use, status, grant, error}`: `status` is the console's
`{grants, inference, message}` (null when it could not be asked), `grant` as above, and `error` a refusal like
those above when the grant could not be read (a revoked grant also signs you out).

## Which credit a session runs on

The free credit has two meters: the agent meter pays for System 2 and the model a run hands over to, the System 1
meter for System 1. When a session opens, and when each run starts, each system runs on free credit if:

- `[free] use` is true (`intern free start` and `resume` set it, `pause` clears it),
- you are signed in and the grant is active (not spent, and the console has not paused free calls), and
- its meter has more left than its overdraft, so a run never starts on credit that cannot finish it. The console
  does not report an overdraft yet, so today this means some credit is left.

Otherwise the system runs on your own configuration (`system2`, `[capabilities.handoff]`, `[system1]` and their
keys), exactly as without a grant. With no configuration for it, the session cannot open on it, and says the free
credit is used up and to add a key: `intern free status` shows what is left, and `intern secrets` or the setup screen
adds one.

So with your own keys and a fresh grant, everything runs free. When the agent credit is spent, the next run's agent
and hand-over are yours while System 1 stays free; when the System 1 credit is spent too, System 1 is yours.

A credit that runs out in the middle of a run is not retried. System 1 goes on with your own console key
(`NACE_API_KEY`) on the same model, and System 2 with your own Fireworks key (`FIREWORKS_API_KEY`) on the same model
(GLM 5.3 or DeepSeek V4.1 Flash), and the run carries on. Without that key, the run stops, saying which of your models
the next message will run on, or that you need to add a key; the conversation is kept. You are told once when a system
moves to your keys, and once each when a meter has 20% and 5% left.

While a system runs on free credit, it runs on the grant's models (its `bundle`), not on your configuration:

- System 2 is `bundle.system2`, auto mode is always on and hands over to `bundle.handoff` at the bundle's
  threshold, streak and first turn, and memory's steward writes notes with the hand-over model.
- System 1 is `bundle.system1`, on the console.
- The context window is capped at `bundle.window_tokens` and replies at `bundle.max_output_tokens`, the limits the
  console keeps to, so they are never what stops a run.
- Picking another model is refused: `--system2`, `--system1` and `--handoff-to`, and in the desktop app the model
  pickers, say "This runs on free credit with … To pick models, use your own keys (`intern free pause`)."

`intern models --json` shows the same: its `free` block has the grant's state and meters, the bundle, `use`, and
what each system runs on (`source`), and a system on free credit lists only the bundle's model. ACP clients also get
the meters on every usage update ([ACP](acp.md#free-credit)).

## Where the tokens are

The sign-in gives an access token, good for an hour, and a refresh token that renews it. The refresh token is kept
in the OS keychain (service `intern`, account `NACE_FREE_REFRESH_TOKEN`), or in `~/.intern/free.json` where there is
no keychain. The access token is kept in `~/.intern/free-access.json` with its expiry, so every Intern process on
the machine (the desktop app's, the terminal UI, `intern models` probes) shares one. Both files are readable by you
only.

The tokens:

- go only to `console.nace.ai`, `console.test.nace.ai` or `console.stage.nace.ai` over HTTPS. A `$NACE_BASE_URL`
  pointing anywhere else is a configuration error and nothing is sent;
- are never in the agent's environment, and are replaced with `[REDACTED]` in what its commands print, even a
  command that prints the token files ([secrets](secrets.md));
- are renewed by one process at a time: the console signs you out everywhere when a used refresh token is presented
  again, so a renewal holds a lock on `~/.intern/free.lock`, and the other processes take its result.

A sign-in lasts 90 days, then `intern free start` again gives back the same grant with what is left on it. When the
console refuses a renewal (the 90 days are over, or the grant was revoked), Intern forgets the sign-in.
