# Contributing to PixelDrex

Thanks for helping. PixelDrex is small on purpose: one Python package, a web app, and saved worlds.

## Development setup

```bash
git clone https://github.com/mauryantitans/PixelDrex.git && cd PixelDrex
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env   # NACE_API_KEY and DEEPINFRA_API_KEY, only needed to generate GIFs
```

Windows (PowerShell) steps are in [docs/SETUP.md](docs/SETUP.md#windows-setup-powershell).

## Checks

```bash
.venv/bin/pytest          # offline tests: no API keys or network needed
.venv/bin/ruff check . && .venv/bin/ruff format --check .   # lint and format
```

CI runs both on every push and pull request.

## Where things live

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains both engines, Scenes and Drex Life, with diagrams and a file map.
In short: `pixeldrex/parts.py` and `pixeldrex/pixels.py` paint scenes, `pixeldrex/life.py` runs worlds,
`pixeldrex/cli.py` and `pixeldrex/web.py` are the two front ends.

## Guidelines

- **Ask Drex better questions before adding post-processing.** Most quality gains came from changing the question
  (parts instead of colors, local neighbourhoods instead of coordinates).
- **Visual changes:** include a before/after GIF or frame strip in the pull request.
- **Saved worlds** (`worlds/*.json`) need a unique `title`, 2–8 `states` with palette colors, and plain-English
  `rules`; the tests load every one.
- **Experiments** go in `scripts/experiments/` and write to `out/` (git-ignored).
- **Never commit keys.** `.env` is git-ignored; use `.env.example` for new settings.
