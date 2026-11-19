# Free credit

Intern can run on free credit from the Nace console, with no keys: one sign-in in the browser with GitHub or
Google, and the console pays for a fixed set of models until the credit is spent. Signing in is only for the free
credit; with your own keys you never sign in.

This release adds the sign-in and the credential. Sessions do not run on free credit yet: a later release picks it
per system (System 2 and the hand-over, and System 1), free first and then your own keys.

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

`grant` is null when the sign-in worked but the grant could not be read; `intern free status` reads it again.
`status --json` prints `{signed_in, use, status, grant, error}`: `status` is the console's
`{grants, inference, message}` (null when it could not be asked), `grant` as above, and `error` a refusal like
those above when the grant could not be read (a revoked grant also signs you out).

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
