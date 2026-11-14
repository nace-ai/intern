"""Exercise the UI's data transformations with its installed TypeScript runtime.

No API calls or expected-label files; Node and web/node_modules are also in the
documented quorum-intern test image. A Python-only checkout skips these checks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None or not (WEB / "node_modules/typescript").exists(),
    reason="UI transformation checks require Node and installed web dependencies",
)

HARNESS = r"""
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ts = require('typescript');
const cache = new Map();
function load(name) {
  const file = path.resolve('src', name);
  if (cache.has(file)) return cache.get(file).exports;
  const module = { exports: {} };
  cache.set(file, module);
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX }
  }).outputText;
  function localRequire(id) {
    if (!id.startsWith('.')) return require(id);
    const base = path.resolve(path.dirname(file), id);
    const target = ['.ts', '.tsx'].map(ext => base + ext).find(f => fs.existsSync(f));
    return load(path.relative(path.resolve('src'), target));
  }
  new Function('require', 'module', 'exports', code)(localRequire, module, module.exports);
  return module.exports;
}
const stats = load('stats.ts');
const demo = load('demo.ts');
const tour = load('tourSteps.tsx');
const score = (extra = {}) => ({ breaches: 1, raw_breaches: 2, failures: 0,
  false_alarms: 0, median_wall_ms: 400, cost_usd: .001, ...extra });
const round = (number, defenders, extra = {}) => ({ round: number, n_attacks: 20,
  n_clean: 5, mode: 'redirect', defenders, ...extra });
function context(rounds, attacks = []) {
  return tour.makeCtx({ rounds, attacks, demo: false, usage: null });
}
new Function('assert', 'stats', 'demo', 'tour', 'score', 'round', 'context',
  JSON.parse(fs.readFileSync(0, 'utf8')))(assert, stats, demo, tour, score, round, context);
"""


@pytest.mark.parametrize(
    "script",
    [
        """
        const failed = round(18, {drex: score({ failures: 25 })});
        assert.equal(stats.isAllFailed(failed), true);
        assert.equal(stats.isAllFailed(round(19, {})), false);
        assert.equal(stats.isAllFailed(round(20, {drex: score({failures: 24})})), false);
        const t = stats.totalsFor([failed, round(22, {drex: score()})], 'drex');
        assert.equal(t.poisoned, 20);
        assert.equal(t.breaches, 1);
        assert.equal(t.failures, 0);
        """,
        """
        const rows = [round(1, {drex: score({median_wall_ms: 100})}),
          round(2, {drex: score({median_wall_ms: 300})}),
          round(3, {other: score()}),
          round(4, {drex: score({median_wall_ms: 0})}),
          round(5, {drex: score({median_wall_ms: NaN})})];
        const t = stats.totalsFor(rows, 'drex');
        assert.equal(t.poisoned, 80);
        assert.equal(t.medianMs, 200);
        assert.equal(stats.totalsFor(rows, 'absent').costUsd, null);
        """,
        """
        const attacks = [{key: 'bad', kind: 'poisoned', decisions: {
          drex: {error: 'request failed', final_action: 'approve'}}},
          {key: 'breach', kind: 'poisoned', decisions: {drex: {
            error: null, final_action: 'approve', risk_score: .01}}},
          {key: 'clean', kind: 'clean', decisions: {drex: {
            error: null, final_action: 'approve'}}}];
        assert.deepEqual(stats.breachRecords(attacks, 'drex').map(a => a.key), ['breach']);
        """,
        """
        const rounds = [round(1, {drex: score({median_wall_ms: 9000})}),
          round(22, {drex: score({median_wall_ms: 500}), llm_guard: score({median_wall_ms: 2500})})];
        const pair = tour.racers(context(rounds));
        assert.equal(pair.a.ms, 500);
        assert.equal(pair.b.ms, 2500);
        assert.deepEqual(stats.sharedRounds(rounds, 'drex', 'llm_guard').map(r => r.round), [22]);
        assert.equal(tour.racers(context([rounds[0]])), null);
        assert.equal(tour.STEPS.find(s => s.id === 'speed').skip(context([])), true);
        """,
        """
        const attacks = demo.demoAttacks();
        assert.equal(new Set(attacks.map(a => a.key)).size, attacks.length);
        assert.deepEqual(new Set(attacks.map(a => a.mode || 'swap')), new Set(['swap', 'redirect']));
        assert.ok(attacks.every(a => a.decisions.llm_guard));
        const rank = {approve: 0, verify: 1, block: 2};
        for (const a of attacks) for (const d of Object.values(a.decisions)) {
          if (d.error) assert.equal(d.final_action, null);
          else assert.ok(rank[d.final_action] >= rank[d.raw_action]);
        }
        """,
        """
        const calls = [];
        global.window = {setTimeout: () => 1, clearTimeout: () => {}};
        const attacks = [{key: 'evidence', kind: 'poisoned', mode: 'redirect', note: 'hostile',
          decisions: {drex: {final_action: 'block', error: null, risk_score: 1}}}];
        const c = context([], attacks);
        c.api.select = key => calls.push(key);
        for (const id of ['search', 'drex']) tour.STEPS.find(s => s.id === id).enter(c);
        assert.deepEqual(calls, ['evidence', 'evidence']);
        """,
        """
        const rows = [round(1, {drex: score()}),
          round(22, {drex: score(), 'drex+memory': score()}),
          round(23, {drex: score({failures: 25}), 'drex+memory': score({failures: 25})})];
        const c = context(rows);
        assert.deepEqual(tour.memoryComparison(c).map(r => r.round), [22]);
        const step = tour.STEPS.find(s => s.id === 'memory');
        assert.equal(step.skip(context([])), true);
        assert.equal(step.skip(c), false);
        const calls = [];
        c.api.select = key => calls.push(['select', key]);
        c.api.setMode = mode => calls.push(['mode', mode]);
        c.api.setDefender = name => calls.push(['defender', name]);
        c.api.setShownRound = n => calls.push(['round', n]);
        step.enter(c);
        assert.deepEqual(calls, [['select', null], ['mode', 'redirect'], ['defender', 'drex+memory'], ['round', null]]);
        """,
    ],
    ids=["failed-rounds", "sparse-totals", "errors-never-breaches", "shared-latencies", "demo-contract", "jump-to-evidence", "memory-cohort-and-navigation"],
)
def test_web_transformations(script: str) -> None:
    result = subprocess.run(
        [str(NODE), "-e", HARNESS],
        input=json.dumps(script),
        text=True,
        capture_output=True,
        cwd=WEB,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
