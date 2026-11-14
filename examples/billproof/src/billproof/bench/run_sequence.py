"""Sequential live runs, with an exit gate and conservative cumulative spend limit.

Run explicitly: python -m billproof.bench.run_sequence --execute
Existing synthetic outputs are read or changed only after the recorded synthetic log exits.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from billproof.config import PKG_ROOT, RUNS_DIR

LOGS = PKG_ROOT / 'out/logs'
HISTORY = RUNS_DIR / 'history/t1_pre_dev_tune'
RESERVE = 1.0  # unrecorded warmups, original pilot, and failed billed attempts


def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []


def spend():
    total = nace_upper = 0.0
    for path in list(RUNS_DIR.glob('real*.jsonl')) + list(HISTORY.glob('*.jsonl')):
        for row in rows(path):
            if row.get('simulated') or row.get('shared_from'):
                continue
            total += float(row.get('cost_usd') or 0)
            if row.get('lane') in ('drex', 'hybrid'):
                # Hybrid input tokens include verifier input too: conservative bound.
                nace_upper += int(row.get('input_tokens') or 0) * .03 / 1e6
    return total, nace_upper


def archive_hybrid(run_id):
    """Preserve old hybrid predictions/spend before recording dev-tuned replacements."""
    path = RUNS_DIR / f'{run_id}.jsonl'
    old = rows(path)
    hybrid = [r for r in old if r.get('lane') == 'hybrid']
    if not hybrid:
        return
    HISTORY.mkdir(parents=True, exist_ok=True)
    archived = HISTORY / f'{run_id}_old_hybrid.jsonl'
    if archived.exists():
        return  # resume: never delete the newly recorded replacements
    archived.write_text(''.join(json.dumps(r) + '\n' for r in hybrid))
    meta = RUNS_DIR / f'{run_id}.meta.json'
    if meta.exists():
        (HISTORY / meta.name).write_text(meta.read_text())
    path.write_text(''.join(json.dumps(r) + '\n' for r in old if r.get('lane') != 'hybrid'))


def command(args, model, log_name):
    total, nace = spend()
    if total >= 15 - RESERVE or nace >= .45:
        raise RuntimeError(f'spend stop: recorded=${total:.4f}, Nace upper=${nace:.4f}')
    cap = 15 - RESERVE - total
    env = dict(os.environ, DREX_MODEL=model, PYTHONPATH=str(PKG_ROOT / 'src'))
    cmd = [sys.executable, '-u', 'scripts/event_day.py', 'run', *args, '--max-usd', str(cap)]
    print(f'START {log_name}; recorded=${total:.4f}, available=${cap:.4f}', flush=True)
    with (LOGS / log_name).open('a') as log:
        result = subprocess.run(cmd, cwd=PKG_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        log.write(f'\nexit {result.returncode}\n')
    print(f'END {log_name}: exit {result.returncode}', flush=True)
    if result.returncode:
        raise RuntimeError(f'run stopped; inspect {log_name} before resuming')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    if not args.execute:
        p.error('--execute required for live calls')
    LOGS.mkdir(parents=True, exist_ok=True)
    meta_log = LOGS / 'run_real_synthetic_drexmax.log'
    print('Waiting for the recorded synthetic log to exit; no shared outputs touched.', flush=True)
    while not any(line.strip().startswith('exit') for line in meta_log.read_text().splitlines()):
        time.sleep(10)
    os.environ['DREX_MODEL'] = 'drex-max-v1.1'
    from billproof.config import settings
    settings.cache_clear()
    from billproof.bench.runner import tune_bands
    from billproof.bench.export_metrics import load_run
    dev = load_run('real_synthetic_dev')
    if dev['partial'] or dev['n_error_rows']:
        raise RuntimeError('synthetic dev incomplete; resume baseline first')
    tune_bands(dev['rows'], bench='synthetic', model='drex-max-v1.1', source_run='real_synthetic_dev')
    marker = HISTORY / 'synthetic_repaired'
    if not marker.exists():
        archive_hybrid('real_synthetic_test')
        command(['--bench', 'synthetic', '--skip-dev'], 'drex-max-v1.1', 't1_synthetic_repaired.log')
        marker.write_text('Hybrid re-recorded using observed-dev proxy tuning.\n')
    # Structured IMR dropped by operator; preserve every recorded row untouched.
    command(['--bench', 'synthetic', '--prefix', 'realfin_', '--lanes', 'drex,hybrid'],
            'drex-max-finance', 't1_finance.log')
    total, nace = spend()
    print(f'COMPLETE recorded=${total:.6f}; Nace token upper=${nace:.6f}; reserve=${RESERVE}', flush=True)
    subprocess.run([sys.executable, '-m', 'billproof.bench.export_metrics', '--runs', 'real_synthetic_test', 'realfin_synthetic_test'], cwd=PKG_ROOT, check=True)


if __name__ == '__main__':
    main()
