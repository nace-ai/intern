# Local scores

Everything in `web/public/scores/local/` is gitignored and never committed. These are local-only files: you supply them yourself.

- Drop `imperial_march.mid` in `local/` and it becomes the first and default piece in the picker, shown as **"Imperial March (local)"**.
- Optionally add `local/scores.json` (same entry format as `../scores.json`, with `file` paths relative to `local/`) to add more local pieces. They are listed after the built-in entries.
- Any missing or broken local file is simply ignored.
