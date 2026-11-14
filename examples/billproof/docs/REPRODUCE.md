# Reproduce

Commands are the script arguments, from each script's argument parser, or from the usage string in the file when the script takes a positional command instead of argparse. Run them from the repository root.

The appeal files already on disk are described in [../data/real/LICENSE_NOTE.md](../data/real/LICENSE_NOTE.md) and [../data/real/SOURCES.md](../data/real/SOURCES.md). Those two notes are not the same set as the 400-appeal benchmark in [METHODS.md](METHODS.md).

A fresh prior-auth model call needs `data/raw/criteria_gym/development.parquet`, which is not in this repository, and no dataset URL is stored. Recorded-answer rescoring does not need that file.

Two commands rewrite [RESULTS.md](RESULTS.md) from recorded files. Running either one overwrites the wording on that page, including the note that the hard-bill system row receives code-computed facts and the Qwen row does not.

- `PYTHONPATH=src .venv/bin/python scripts/g_build_results.py` takes no arguments. It rebuilds `data/metrics/all_benchmarks.json` and `docs/RESULTS.md`.
- `PYTHONPATH=src .venv/bin/python scripts/qcombo.py` rescores Drex-then-model from recorded answers and then rebuilds the same two files. `PYTHONPATH=src .venv/bin/python scripts/qcombo.py --no-hub` writes the combo and dial files only.

## Rescore recorded files, no model call

| Command | What the script does |
|---|---|
| `PYTHONPATH=src .venv/bin/python scripts/hard_outcomes.py` | No arguments. Scores recorded hard answers and does not call a model. |
| `PYTHONPATH=src .venv/bin/python scripts/hard_metrics.py --split test` | `--split` is required: `dev` or `test`. Writes `data/metrics/hard_*.json` from recorded hard JSONL. |
| `PYTHONPATH=src .venv/bin/python scripts/pa_report.py --split development` | Summarizes recorded `pa_*_<split>.jsonl`. The default `--split` is `validation`, which is not the headline panel. The headline 370 uses `--split development`: those rows come from the publisher's file named development, and here they are the held-out panel, scored after the GPT cutoffs were frozen on a validation sample ([METHODS.md](METHODS.md)). Optional: `--columns` (default `gpt,drex,hybrid,checked`), `--metrics`, `--tag`. |
| `PYTHONPATH=src .venv/bin/python scripts/di_lanes.py score` | Scores recorded DeepInfra JSONL. Optional `--bench` (`hard`, `pa`, or `persius`) and `--model`. This subcommand does not call a model. |
| `PYTHONPATH=src .venv/bin/python scripts/qcombo.py --no-hub` | Rescores recorded Drex and DeepInfra answers. See the overwrite warning above. |
| `PYTHONPATH=src .venv/bin/python scripts/g_persius_combo.py tune` | Positional command, not argparse. Picks one frozen rule on the recorded development sample. Does not call a model. |
| `PYTHONPATH=src .venv/bin/python scripts/g_persius_combo.py apply` | Scores that frozen rule once on the recorded 400 test rows. Does not call a model. |
| `PYTHONPATH=src .venv/bin/python -m billproof.dial.build` | No model calls. Optional bench names: `hard`, `pa_criteria_gym`, `persius`, `synthetic_v1`. With no name it builds every bench in that list. |
| `PYTHONPATH=src .venv/bin/python scripts/pa_demo_record.py --assemble-only` | Rebuilds `evidence.json` and `check.png` from `run.json`. The script's help says this flag makes no live calls. Optional `--case`. |

## Fresh call, needs keys

These call a model or an external API. They are listed so the flags are visible. Fresh-call commands were not executed for this page.

| Command | Arguments the script accepts |
|---|---|
| `PYTHONPATH=src .venv/bin/python scripts/pa_run.py --split development --columns drex` | `--split` is required: `validation` or `development`. It reads `data/raw/criteria_gym/development.parquet` (loader: `src/billproof/bench/pa/cases.py`, path `CRITERIA_GYM_DIR / "{split}.parquet"`). That parquet is not in the repository. No dataset URL is stored. Recorded-answer rescoring does not need it. `--columns` defaults to `gpt,drex,hybrid,checked`. Also `--limit`, `--sample`, `--seed` (default 0), `--cases-file`, `--drex-model`, `--cutoff3` (default 0.75), `--cutoff4` (default 0.75), `--stages` (default `initial_packet`), `--out-suffix`. |
| `PYTHONPATH=src .venv/bin/python scripts/c_persius_fair.py test` | Positional command: `fair20`, `test`, or `all`. No argparse. `test` is the 400-appeal comparison and requires live adapters. |
| `PYTHONPATH=src .venv/bin/python scripts/hard_run.py --split test --lane drex-v1` | `--split` is required: `dev` or `test`. `--lane` is required: `drex-v1`, `drex-finance`, `drex-both`, `gpt`, `hybrid`, or `gpt-one`. The script refuses `gpt-one` unless `--split` is `test`. Optional `--limit` (default 0) and `--tau`. |
| `PYTHONPATH=src .venv/bin/python scripts/ladder_run.py --model openai/gpt-6.1-sol --test hard` | `--model` and `--test` are required. `--test` is `hard`, `pa`, or `persius`. Optional `--prompt-only` and `--limit` (default 0, a prefix of the frozen order). |
| `PYTHONPATH=src .venv/bin/python scripts/di_lanes.py run --bench hard --model Qwen/Qwen3.8-Max` | `run` requires `--bench` (`hard`, `pa`, or `persius`) and `--model`. Optional `--limit` and `--workers` (default 4, allowed 1 through 16). The hard-bill state this command sends is `visible_state(record)`. It does not add `state["computed"]`. |
| `PYTHONPATH=src .venv/bin/python scripts/run_bench.py --bench synthetic --split test` | `--bench` is required: `synthetic` or `imr`. `--split` is required: `dev` or `test`. `--lanes` defaults to `frontier,drex,hybrid`. Also `--run-id`, `--limit`, `--seed` (default 0), and `--assume-human-correct`. The script's own description is a simulated or live benchmark. |
| `PYTHONPATH=src .venv/bin/python scripts/g_persius_combo.py run` | Draws a development sample and calls the models, then `tune` and `apply`. |
| `PYTHONPATH=src .venv/bin/python scripts/cc_queue.py` | Optional `--limit`, `--gpt-n`, `--max-questions` (default 200), `--allow-sim`. The docstring starts a live queue. |
| `PYTHONPATH=src .venv/bin/python scripts/cc_rule_change.py` | Optional `--limit`, `--max-questions` (default 200), `--allow-sim`. Reuses a queue row when that case is already decided, and can call Drex otherwise. |
| `PYTHONPATH=src .venv/bin/python scripts/cc_what_decides.py` | Optional `--limit`, `--max-questions` (default 200), `--allow-sim`. The docstring re-asks Drex with each record removed. |
| `PYTHONPATH=src .venv/bin/python scripts/pa_demo_record.py` | Live Nace and frontier calls. Optional `--case`, `--steps` (default `ground,search,drex,ablate,gpt`), and `--browser`. Use `--assemble-only` for the no-call rebuild in the table above. |

## Local files and downloads, no model call

| Command | Arguments the script accepts |
|---|---|
| `PYTHONPATH=src .venv/bin/python scripts/make_synthetic.py` | Optional `--seed` (default 20261114) and `--demo-only`. Builds synthetic cases locally. |
| `PYTHONPATH=src .venv/bin/python scripts/render_pdfs.py` | Optional `--cases-dir`, `--pdf-dir`, `--fixtures-dir`, `--case`, `--no-write-cases`, `--no-verify`. Renders PDFs from case JSON. |
| `PYTHONPATH=src .venv/bin/python scripts/hard_generate.py --split dev` | `--split` is `dev` or `test` (default `dev`). Optional `--force`. Refuses the test split until knobs are frozen. |
| `PYTHONPATH=src .venv/bin/python scripts/fetch_cms.py` | No arguments. Downloads the CMS MUE, Medicaid NCCI, and fee-schedule files named in the script. |
| `PYTHONPATH=src .venv/bin/python scripts/pa_demo_prepare.py` | Offline unless `--fetch` is set. Optional `--case` (one slug; default is every showcase case). `--fetch` downloads the policy PDFs named in the script. |

`.venv/bin/pytest -q` runs the tests. It is not a benchmark.
