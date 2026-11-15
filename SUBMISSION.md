# Quorum submission

Quorum is an adversarial evaluation lab for invoice-processing agents. The complete application is under [projects/quorum](projects/quorum/README.md). Intern's existing implementation remains at the repository root.

## Where to start

- [Architecture, setup and implementation](projects/quorum/README.md)
- [Arena report](projects/quorum/results/arena/report.md) and [experiment notes](projects/quorum/results/arena/NOTES.md)
- [Separate 95-case benchmark](projects/quorum/backend/BENCHMARK.md)
- [Seven-slide presentation](projects/quorum/results/presentation/Quorum-Pitch-7-Slides.pptx)
- [Two-minute presentation script and detailed judge Q&A](projects/quorum/datasets/scripts/presentation-script.md)
- [Two-minute application demo script](projects/quorum/datasets/scripts/scripted-demo.md)

## Evidence

The snapshot covers 27 valid rounds with 528 hostile and 132 clean invoices. Four entirely failed rounds are excluded and documented.

For shared redirect rounds 22–31, every defender saw 200 hostile and 50 clean invoices. Drex Finance finished with 30 hostile approvals versus 156 for the LLM guard, with zero clean flags and failures for both. Contextual memory reduced Finance hostile approvals to 17, with 4 clean invoices held. This is context supplied to the same model, not weight training.

The same cohort yielded roughly 6.1 times lower median call latency and 65 times lower estimated decision cost for Finance. These figures exclude NDI extraction. The LLM guard prompt explicitly rejects invoice instructions, while the Drex questions lack that line. Both systems matched 95/95 expected actions on a separate fixed benchmark. The inputs are invoice PDFs from a synthetic corpus with generated adversarial notes.

## Try the application

Run from `projects/quorum/web`:

```sh
npm ci
npm run dev
```

Open `http://localhost:5173/?demo=1` for the labelled sample walkthrough. To inspect the recorded snapshot, follow the backend setup in the project README and open `http://localhost:5173/?tour=1`. Recorded-results replay reads saved files and does not launch model requests. Private source datasets, generated PDFs, extraction caches, credentials and local dependencies are not committed, so PDF images require locally generated data.

## Validation

The application uses its own backend pytest suite and TypeScript/Vite build, documented in its README. Root-level Ruff excludes this independent submission instead of applying Intern's formatting rules to a separate application. Intern source, defaults and package version are unchanged. No Intern release is requested.

Verified in the isolated submission checkout: **354 passed, 1 warning** with the source dataset mounted read-only; TypeScript and the production web build passed. Dataset-dependent tests require the ignored source parquet at the documented path.
