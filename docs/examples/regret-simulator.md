# Example: Regret Simulator — A Parliament of Future Selves

[Regret Simulator / BagelCal](https://github.com/sane-wu-berkeley/BagelCal) is a decision-support prototype developed for Drexathon 2026 using Intern, local Qwen3, Drex, and optional Nace Document Intelligence.

**The complete application source is included in this PR** under [`examples/regret-simulator/`](../../examples/regret-simulator/). The original project is maintained independently at [BagelCal](https://github.com/sane-wu-berkeley/BagelCal).

## What the code includes

- Next.js / React / TypeScript frontend with four advisor viewpoints, decision options, preference weights, and a results dashboard.
- Server-side `/api/decide`, `/api/analyze`, and `/api/profile` routes, plus orchestration and validation.
- Numerical utility, regret and downside-risk analysis using explicit user-reviewed scenarios.
- JSON schemas and sample decision fixtures, tests, setup scripts, lockfile, and development documentation.
- An `.env.example` template with **no credential values**. Never commit an actual `.env` or `.env.local` file.

Generated execution logs, presentations, and PDF files are **not duplicated** into the Intern repository. See the original project's [redacted verification evidence](https://github.com/sane-wu-berkeley/BagelCal/tree/main/frontend/evidence) and [verification notes](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/docs/verification.md).

## How Intern was used

- **System 2:** local `ollama/qwen3:8b` coding model.
- **System 1:** Drex supervising agent turns.
- The published [Intern verification summary](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/evidence/intern-summary.json) reports three successful System 1 asks and zero failures. That verification run did not modify source files, so it should not be represented as autonomous authorship of the entire application.
- User-facing Drex decisions are separate API calls made by the application, not the Intern development harness.

## Run locally

Prerequisites: Node.js 20+, Ollama with `qwen3:8b`, and a Nace API key for live decisions.

```bash
cd examples/regret-simulator
npm ci
cp .env.example .env.local
# Edit .env.local to set NACE_API_KEY locally; never commit it.
ollama pull qwen3:8b
npm test
npm run build
npm run start:local
```

Then open http://127.0.0.1:3019. The UI also has clearly labeled illustrative demo mode. The application is a local proof of concept; neither generated advisor prose nor scenario-regret calculations are validated forecasts of personal outcomes.

This is an **independent community example**, not an official feature of Intern.
