# Agent Heist development workflow

Project: Agent Heist. Official remote: https://github.com/vishalVasanthakumarPoornima/Agent-Heist.git

For every completed development milestone:

1. Inspect the working tree, branch, remotes and latest remote history; fetch before editing and again before pushing.
2. Preserve teammate changes and existing working integrations. Reconcile divergence safely; never force-push or discard unrelated work.
3. Implement, run the appropriate tests and production build, then audit credentials and sensitive files.
4. Stage only appropriate files; inspect the index, commit with a meaningful message, push and verify the remote commit.
5. For deployment changes, deploy and verify the actual public website as well. Do not declare the milestone delivered until synchronization and verification complete, unless a genuine blocker is explicitly reported.

Keep API keys, `.env`, private ledgers, sessions, runtime state, caches and generated private data out of Git. Preserve spending ledgers. Use offline tests/mocks by default; no paid Nace calls without explicit authorization. Public Live may be enabled only with a server-side Groq secret, BYO Nace keys, tested request quotas, credential audits, and recorded real-provider verification. Never turn model dialogue into proof of a successful tool action.

Update README.md and PROGRESS.md with verified behavior and exact remaining limits. Keep credentials and session flags out of command output, screenshots and reports.
