# Schedules

Run a task later, or on a repeat: "every morning, summarise the failing tests", "in 20 minutes, check CI".
Intern has no scheduler of its own that keeps running when it is closed. Use the one that fits where you work:

| Where you work | How to schedule | Keeps running when |
|---|---|---|
| The Intern desktop app | Its scheduled tasks | the app is running (its window may be closed) |
| Nothing open | Your OS scheduler (launchd, systemd) runs `intern -p` | you are logged in |
| The terminal UI | A one-off wait, or timers (`/caps schedule`): ask the agent | the session is open |

Words used here: the **frontier model** does the work (`--system2`); the **decision model** watches it
(`--system1`). A **headless** run (`intern -p`) does one task with no terminal UI and exits.

## In the desktop app

Open **Settings → Scheduled tasks**. A task runs once at a date and time, every N minutes or hours (at least one
minute), at a fixed time (for example every weekday at 09:00, in your Mac's time zone), or when a webhook calls it.
Each run goes to a new thread, or to one thread that continues the conversation or starts fresh each time.

The agent can create, list and delete these tasks itself when you ask it to ("every morning at 9, check the
nightly build"). It does not ask you first: the task shows up in **Settings → Scheduled tasks**, where you can pause,
edit or delete it.

On macOS, closing the window does not stop the app, so tasks keep running until you quit it. A task that came due
while the app was not running runs once when it starts again, except a fixed-time run more than 10 minutes late,
which is skipped until its next time.

## With your OS scheduler

The OS starts a headless run on a timer. Each run is a fresh session: it remembers nothing of earlier runs, so a
task that needs to (for example "report only new failures") should keep its own notes in a file.

The command, the same on macOS and Linux:

```bash
intern -p --system2 <provider/model> -- "<task>"
```

Before you schedule it:

1. **Find the full path** with `command -v intern` (for a `uv tool install`, `~/.local/bin/intern`). The OS
   scheduler does not read your shell profile, so it knows neither your `PATH` nor the keys you `export` there.
2. **Give it its keys** another way (below), then check them with `intern secrets test`.
3. **Run the command once by hand** from the folder the task should work in.
4. **Decide what it may do unwatched.** Nobody approves anything during a scheduled run. Turn on the
   [tool-call guard](../capability-settings.md#guard) (`[capabilities.guard] enabled = true` in
   `~/.intern/config.toml`), and if the task sends anything out (a message, an email, a push), say so in
   `~/.intern/SYSTEM1.md` so the guard knows it is wanted. For untrusted work, use the
   [Docker workspace](docker-workspace.md).

Add `--no-memory` if each run should neither recall nor save [memory](../memory.md).

### macOS: a launchd agent

A launchd agent runs in your login session, so keys stored with `intern secrets set` load:

```bash
intern secrets set FIREWORKS_API_KEY
intern secrets set NACE_API_KEY
```

Save this as `~/Library/LaunchAgents/com.example.intern-report.plist`, with your own paths, model and task:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.intern-report</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/.local/bin/intern</string>
    <string>-p</string>
    <string>--system2</string>
    <string>fireworks/accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b</string>
    <string>--</string>
    <string>Summarise the failing tests in tests/ and write the summary to report.md</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/you/projects/app</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/Users/you/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key>
  <integer>10800</integer>
  <key>StandardOutPath</key>
  <string>/Users/you/Library/Logs/intern-report.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/you/Library/Logs/intern-report.log</string>
</dict>
</plist>
```

`StartInterval` is in seconds (10800 is every 3 hours). For a time of day, replace it with
`StartCalendarInterval` (for example `Hour` 9, `Minute` 0). launchd does not start the task again while a run is
still going.

Turn it on, run it once now, and turn it off:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.intern-report.plist
launchctl kickstart gui/$(id -u)/com.example.intern-report
launchctl bootout gui/$(id -u)/com.example.intern-report
```

A Mac that is asleep runs a missed `StartInterval` task once when it wakes.

### Linux: a systemd user timer

On Linux the keychain needs a desktop session, which a timer may not have. Put the keys in a file only you can
read instead:

```bash
mkdir -p ~/.config/intern
printf 'FIREWORKS_API_KEY=...\nNACE_API_KEY=...\n' > ~/.config/intern/schedule.env
chmod 600 ~/.config/intern/schedule.env
```

`~/.config/systemd/user/intern-report.service`:

```ini
[Unit]
Description=Intern: summarise failing tests

[Service]
Type=oneshot
WorkingDirectory=%h/projects/app
EnvironmentFile=%h/.config/intern/schedule.env
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/flock -n %t/intern-report.lock %h/.local/bin/intern -p --system2 fireworks/accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b -- "Summarise the failing tests in tests/ and write the summary to report.md"
```

`~/.config/systemd/user/intern-report.timer`:

```ini
[Unit]
Description=Run intern-report every 3 hours

[Timer]
OnBootSec=5min
OnUnitActiveSec=3h
Persistent=true

[Install]
WantedBy=timers.target
```

`flock -n` skips a run while the last one is still going. Turn it on, run it once now, and read the log:

```bash
systemctl --user daemon-reload
systemctl --user enable --now intern-report.timer
systemctl --user start intern-report.service
journalctl --user -u intern-report.service
```

A user timer runs while you are logged in. To keep it running after you log out:
`loginctl enable-linger $USER`.

## In the terminal UI

For a one-off wait, ask: "in 20 minutes, check whether CI passed". The agent starts a background `sleep` and
hands the session back to you; when it ends, the session wakes the agent, which then checks. The wait ends when
you close the session.

For repeats, turn on timers with `/caps schedule` (off by default). Then ask: "every hour, check the nightly
build". The agent sets a timer with its `schedule` tool; each time it is due, the session runs the prompt with no
message from you, shown as a `⚑` notice. A repeat counts its next interval from the end of its run, so runs
never pile up. Timers are at least one minute apart, at most five per session by default
(`[capabilities.schedule] max_timers`), and `/schedule` lists them and stops one.

Timers live only in the open session: closing it, or anything that reopens it (`/caps`, `/runner`, `/handover`,
`/setup`, `/reload`, `/clear`), stops them all and the transcript says how many. Other clients (`-p`, JSON-RPC,
ACP) never get them. See [timers](../tui.md#timers).

## Limits

- Nothing runs while the desktop app is quit or the computer is off. A sleeping computer runs missed tasks late,
  except the desktop app's fixed-time runs more than 10 minutes late.
- `intern -p`, JSON-RPC and other [ACP](../acp.md) clients (Agent Client Protocol: editors and apps that run
  `intern acp`) get no scheduling from the harness. The app or script that runs Intern schedules it.
- Scheduled runs act with nobody watching. Keep their tasks narrow, and use the guard.
- Intern has no built-in way to send a chat message or email. A task that needs one uses a command or an
  [MCP server](mcp.md) you set up, with its key in the secrets store or the key file above.
