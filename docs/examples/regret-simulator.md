# Example: Regret Simulator — A Parliament of Future Selves

[Regret Simulator (BagelCal)](https://github.com/sane-wu-berkeley/BagelCal) is a Drexathon 2026 decision-support prototype built using Intern, a local System 2 coding model, Drex decisions, and optional Nace Document Intelligence (NDI). The complete Next.js application lives in its own repository; this example links to it rather than copying an application into Intern's Python package.

## What it does

A person describes a difficult decision, lists two to five feasible choices, and adjusts preference weights for security, growth, autonomy, connection, and meaning. Four distinct advisor roles (Future, Rational, Wisdom, and Relationship) generate preliminary qualitative perspectives using local Ollama/Qwen3. Drex makes typed selections for the advisors, then a separate Drex call arbitrates their proposals.

A separate, transparent scenario engine calculates weighted utility, regret, and downside risk over user-reviewed assumptions at 1-, 5-, and 20-year horizons. Its numbers are *illustrative scenario calculations*, not empirical forecasts or probabilities of a person's future life outcomes. Optional, consent-based NDI PDF extraction suggests profile details for the user to review.

## How Intern was used

The development and verification workflow ran Intern 0.30.1 with:

- **System 2:** `ollama/qwen3:8b`, a locally hosted coding agent.
- **System 1:** Nace Drex, supervising tool-using agent turns.

The project publishes a [redacted Intern execution log](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/evidence/intern-verification.jsonl), [run summary](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/evidence/intern-summary.json), and [verification notes](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/docs/verification.md). The recorded Intern verification run reports three successful System 1 asks and no failed asks; it did **not** itself edit project files. This evidence demonstrates actual harness execution, not that Intern autonomously authored the entire application.

The application also makes direct Drex System One API calls for the user-facing decision workflow. Those calls are separate from Intern's development-time System 1 supervision.

## Run the application locally

Prerequisites: Node.js 20+, Ollama running locally, an installed `qwen3:8b` model for live advisor mode, and a Nace API key for live Drex decisions.

```bash
git clone https://github.com/sane-wu-berkeley/BagelCal.git
cd BagelCal/frontend
npm ci
cp .env.example .env.local
# Edit .env.local and set NACE_API_KEY locally; never commit it.
ollama pull qwen3:8b
npm run build
npm run start:local
```

Then visit http://127.0.0.1:3019. The app also offers labeled demo mode without live model calls. The key remains server-side; do not use a `NEXT_PUBLIC_` variable for credentials.

## Further reading

- [Application README and numerical scope](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/README.md)
- [API and tests](https://github.com/sane-wu-berkeley/BagelCal/tree/main/frontend)
- [Verification evidence and limitations](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/docs/verification.md)
- [Recorded example run](https://github.com/sane-wu-berkeley/BagelCal/blob/main/frontend/evidence/final-browser-evaluation.json)

This is an independent user project, not an official Intern example, a validated decision-science model, or a hosted service.
