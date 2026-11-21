"""Generate ~60 synthetic-but-realistic CI failure logs + ground-truth labels.

Deterministic (seeded). Logs never contain the label itself; signals are the ones
a human triager would read: error type, retry history, changed files, fail rate.
Run:  python data/generate.py
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
rng = random.Random(1114)

MODULES = [
    ("billing", "invoice"), ("auth", "session"), ("search", "indexer"), ("checkout", "cart"),
    ("notifications", "email_queue"), ("api", "rate_limiter"), ("reports", "exporter"),
    ("users", "profile"), ("payments", "stripe_webhook"), ("inventory", "stock"),
]
JS_MODULES = [("web", "CartDrawer"), ("web", "LoginForm"), ("web", "SearchBar"), ("web", "InvoiceTable"),
              ("web", "useWebsocket"), ("web", "DatePicker")]


def sha():
    return "%07x" % rng.getrandbits(28)


def header(case_id, fw, test_name, test_file, changed, duration, runner="ci-runner"):
    lines = [
        f"=== CI run #{rng.randint(41000, 49999)}  job=unit-tests  runner={runner}-{rng.randint(1, 24):02d}",
        f"commit {sha()}  branch=feature/{rng.choice(['perf','refactor','fix','deps','ui'])}-{rng.randint(100,999)}",
        "changed files in this PR:",
    ] + [f"  M {c}" for c in changed] + [
        f"framework: {fw}",
        f"failing test: {test_name}",
        f"test file: {test_file}",
        f"duration: {duration:.2f}s",
        "",
    ]
    return lines


def history(fails, window=50):
    return [f"history: {test_name_placeholder} failed {fails}/{window} recent runs on main (recent_fail_rate={fails/window:.2f})"]


test_name_placeholder = "this test"


def retry_block(results):
    out = ["retry history:"]
    for i, r in enumerate(results, 1):
        out.append(f"  attempt {i + 1}: {r} ({rng.uniform(0.4, 9.5):.2f}s)")
    if not results:
        out.append("  (no automatic retries configured for this job)")
    return out


def py_trace(test_file, test_func, frames, err_lines):
    out = ["=================================== FAILURES ===================================",
           f"___________________________ {test_func} ___________________________", ""]
    for fpath, ln, code in frames:
        out += [f"{fpath}:{ln}: in {code.split('(')[0].split()[-1] if '(' in code else 'call'}", f"    {code}"]
    out += err_lines
    out += ["=========================== short test summary info ============================",
            f"FAILED {test_file}::{test_func} - {err_lines[-1].lstrip('E ').strip()[:90]}"]
    return out


def js_trace(test_file, title, err_lines, frames):
    out = [f" FAIL  {test_file}", f"  ● {title}", ""] + [f"    {l}" for l in err_lines] + [""]
    out += [f"      at {f}" for f in frames]
    out += ["", "Tests:       1 failed, %d passed, %d total" % ((n := rng.randint(20, 140)), n + 1)]
    return out


# ---------------- case builders ----------------
# each returns (log_lines, meta) ; meta: category, truth, ambiguous

def flaky_timeout(amb=False):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}.py"; fn = f"test_{mod}_{rng.choice(['sync','roundtrip','bulk_update','refresh'])}"
    other_pkg = rng.choice([m for m in MODULES if m[0] != pkg])[0]
    changed = [f"src/{pkg}/{mod}.py"] if amb else [f"src/{other_pkg}/utils.py", "README.md"]
    dur = rng.uniform(30, 31) if not amb else rng.uniform(60, 61)
    L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, changed, dur)
    L += py_trace(tf, fn, [(tf, rng.randint(20, 200), f"resp = client.get('/{pkg}/{mod}', timeout={int(dur)})"),
                         ("venv/lib/python3.12/site-packages/httpx/_client.py", 1014, "raise mapped_exc(message) from exc")],
                  ["E   httpx.ReadTimeout: timed out", f"E   (waited {dur:.1f}s for postgres-test.internal:5432 fixture warm-up)"])
    L += retry_block(["FAILED (timeout)", "PASSED"] if amb else ["PASSED"])
    L += [f"history: failed {(f:=rng.randint(2,6) if amb else rng.randint(4,12))}/50 recent runs on main (recent_fail_rate={f/50:.2f})"]
    return L, ("timeout", "flaky")


def flaky_race(amb=False):
    if rng.random() < 0.5:
        pkg, mod = rng.choice(JS_MODULES)
        tf = f"src/{pkg}/__tests__/{mod}.test.tsx"; title = f"{mod} › updates after async fetch"
        changed = [f"src/{pkg}/{mod}.tsx"] if amb else ["package-lock.json", "docs/CHANGELOG.md"]
        L = header(0, "jest 29.7.0", title, tf, changed, rng.uniform(4, 6))
        L += js_trace(tf, title, ["expect(screen.getByText('Total: $42.00')).toBeInTheDocument()", "",
                                  "TestingLibraryElementError: Unable to find an element with the text: Total: $42.00",
                                  "Warning: An update to CartDrawer inside a test was not wrapped in act(...)"],
                      [f"Object.<anonymous> ({tf}:{rng.randint(30,90)}:27)", "processTicksAndRejections (node:internal/process/task_queues:95:5)"])
    else:
        pkg, mod = rng.choice(MODULES)
        tf = f"tests/{pkg}/test_{mod}_async.py"; fn = f"test_concurrent_{mod}_writes"
        changed = [f"src/{pkg}/{mod}.py"] if amb else [f"tests/conftest.py"]
        L = header(0, "pytest 8.3.2 (pytest-asyncio, -n 8 xdist)", f"{tf}::{fn}", tf, changed, rng.uniform(1, 3))
        L += py_trace(tf, fn, [(tf, rng.randint(40, 120), "await asyncio.sleep(0.05)  # wait for worker"),
                             (tf, rng.randint(121, 160), "assert len(events) == 3")],
                      ["E   assert 2 == 3", "E    +  where 2 = len([<Event id=1>, <Event id=2>])"])
    L += retry_block(["PASSED", "PASSED"] if not amb else ["FAILED", "PASSED"])
    L += [f"history: failed {(f:=rng.randint(3,9))}/50 recent runs on main (recent_fail_rate={f/50:.2f})"]
    return L, ("race_condition_in_test", "flaky")


def flaky_network(amb=False):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/integration/test_{mod}_client.py"; fn = f"test_{mod}_fetch_remote"
    changed = [f"src/{pkg}/http_config.py"] if amb else [f"src/{pkg}/{mod}.py"]
    L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, changed, rng.uniform(0.2, 2))
    err = rng.choice([
        ["E   requests.exceptions.ConnectionError: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))"],
        ["E   socket.gaierror: [Errno -3] Temporary failure in name resolution", "E   (host: registry.npmjs-mirror.internal)"],
        ["E   urllib3.exceptions.MaxRetryError: HTTPSConnectionPool(host='sandbox.stripe-mock.internal', port=443): Max retries exceeded", "E   Caused by NewConnectionError: [Errno 111] Connection refused"],
    ])
    L += py_trace(tf, fn, [(tf, rng.randint(10, 80), f"r = session.get(REMOTE_URL + '/{mod}')")], err)
    L += retry_block(["FAILED (same error)", "PASSED"] if amb else ["PASSED"])
    L += [f"history: failed {(f:=rng.randint(1,7))}/50 recent runs on main (recent_fail_rate={f/50:.2f})",
          "note: 3 other jobs on this runner pool reported network errors in the same 5-minute window" if not amb else ""]
    return L, ("network_blip", "flaky")


def flaky_order(amb=False):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}_settings.py"; fn = f"test_default_{mod}_config"
    changed = [f"tests/{pkg}/test_{mod}_cache.py"] if not amb else [f"src/{pkg}/settings.py"]
    L = header(0, "pytest 8.3.2 (-p randomly, seed=%d)" % rng.randint(1000, 99999), f"{tf}::{fn}", tf, changed, rng.uniform(0.01, 0.3))
    L += py_trace(tf, fn, [(tf, rng.randint(5, 50), f"assert get_settings().{mod}_ttl == 300")],
                  [f"E   assert 5 == 300", f"E    +  where 5 = Settings({mod}_ttl=5, cache='memory').{mod}_ttl",
                   "E   (module-level singleton mutated by an earlier test in this worker)" if not amb else "E"])
    L += ["rerun in isolation: `pytest %s::%s` -> 1 passed" % (tf, fn)] if not amb else []
    L += retry_block(["PASSED"] if not amb else ["FAILED", "FAILED"])
    L += [f"history: failed {(f:=rng.randint(2,8))}/50 recent runs on main (recent_fail_rate={f/50:.2f})"]
    return L, ("order_dependence", "flaky")


def flaky_env(amb=False):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}_server.py"; fn = f"test_{mod}_server_boots"
    L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, [f"src/{pkg}/{mod}.py"] if amb else ["Makefile"], rng.uniform(0.5, 1.5))
    err = rng.choice([["E   OSError: [Errno 98] Address already in use: ('127.0.0.1', 8081)"],
                      ["E   AssertionError: expected report date 2026-11-14, got 2026-11-15",
                       "E   (test executed at 23:59:59.871 UTC; fixture computed 'today' before midnight)"],
                      ["E   OSError: [Errno 28] No space left on device: '/tmp/pytest-of-ci/pytest-311'"]])
    L += py_trace(tf, fn, [(tf, rng.randint(10, 60), "server = start_server(port=8081)" if 'Address' in err[0] else "result = run_job()")], err)
    L += retry_block(["PASSED"] if not amb else ["FAILED", "PASSED"])
    L += [f"history: failed {(f:=rng.randint(1,5))}/50 recent runs on main (recent_fail_rate={f/50:.2f})"]
    return L, ("environment_resource", "flaky")


def real_assert(amb=False):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}.py"; fn = f"test_{mod}_total_includes_tax"
    L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, [f"src/{pkg}/{mod}.py", f"src/{pkg}/rounding.py"], rng.uniform(0.01, 0.2))
    exp = rng.choice(["107.50", "42.00", "1299.99"]); got = f"{float(exp) - rng.choice([0.01, 7.5, 0.5]):.2f}"
    L += py_trace(tf, fn, [(tf, rng.randint(20, 90), f"assert {mod}.total() == Decimal('{exp}')")],
                  [f"E   AssertionError: assert Decimal('{got}') == Decimal('{exp}')", f"E     - {exp}", f"E     + {got}"])
    L += retry_block(["FAILED (identical assertion)", "FAILED (identical assertion)"])
    L += [f"history: failed 0/50 recent runs on main (recent_fail_rate=0.00)", "first failure of this test on any branch: this PR"]
    return L, ("assertion_regression", "real")


def real_import(amb=False):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}_api.py"
    L = header(0, "pytest 8.3.2", f"{tf}", tf, [f"src/{pkg}/__init__.py", f"src/{pkg}/{mod}.py"], 0.0)
    L += ["==================================== ERRORS ====================================",
          f"_____________ ERROR collecting {tf} _____________",
          f"ImportError while importing test module '{tf}'.",
          "Traceback:", f"{tf}:3: in <module>", f"    from src.{pkg}.{mod} import {mod.title().replace('_','')}Service",
          f"E   ImportError: cannot import name '{mod.title().replace('_','')}Service' from 'src.{pkg}.{mod}' (did you mean '{mod.title().replace('_','')}Client'?)",
          "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!"]
    L += retry_block(["ERROR (collection)", "ERROR (collection)"])
    L += ["history: failed 0/50 recent runs on main (recent_fail_rate=0.00)"]
    return L, ("import_error", "real")


def real_type(amb=False):
    if rng.random() < 0.5:
        pkg, mod = rng.choice(JS_MODULES)
        tf = f"src/{pkg}/__tests__/{mod}.test.tsx"; title = f"{mod} › renders with default props"
        L = header(0, "jest 29.7.0", title, tf, [f"src/{pkg}/{mod}.tsx", f"src/{pkg}/types.ts"], rng.uniform(0.3, 1))
        L += js_trace(tf, title, ["TypeError: Cannot read properties of undefined (reading 'map')", "",
                                  f"> 41 |   {{props.items.map((item) => <Row key={{item.id}} {{...item}} />)}}"],
                      [f"{mod} (src/{pkg}/{mod}.tsx:41:22)", "renderWithHooks (node_modules/react-dom/cjs/react-dom.development.js:16305:18)"])
    else:
        pkg, mod = rng.choice(MODULES)
        tf = f"tests/{pkg}/test_{mod}_service.py"; fn = f"test_create_{mod}"
        L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, [f"src/{pkg}/{mod}.py"], rng.uniform(0.01, 0.1))
        L += py_trace(tf, fn, [(tf, rng.randint(10, 60), f"svc.create_{mod}(user_id=7, currency='USD')")],
                      [f"E   TypeError: create_{mod}() got an unexpected keyword argument 'currency'"])
    L += retry_block(["FAILED", "FAILED"])
    L += ["history: failed 0/50 recent runs on main (recent_fail_rate=0.00)"]
    return L, ("signature_change", "real")


def real_snapshot(amb=False):
    pkg, mod = rng.choice(JS_MODULES)
    tf = f"src/{pkg}/__tests__/{mod}.snap.test.tsx"; title = f"{mod} › matches snapshot"
    L = header(0, "jest 29.7.0", title, tf, [f"src/{pkg}/{mod}.tsx", "src/web/theme/tokens.ts"], rng.uniform(0.2, 0.8))
    L += js_trace(tf, title, ["expect(received).toMatchSnapshot()", "", "Snapshot name: `matches snapshot 1`", "",
                              "- Snapshot  - 2", "+ Received  + 2", "", '-   className="btn btn-primary"', '+   className="btn btn-primary-v2"',
                              '-   aria-label="Checkout"', '+   aria-label={undefined}'],
                  [f"Object.<anonymous> ({tf}:12:31)"])
    L += retry_block(["FAILED", "FAILED"])
    L += ["history: failed 0/50 recent runs on main (recent_fail_rate=0.00)"]
    return L, ("snapshot_regression", "real")


# ---- deliberately ambiguous REAL regressions ----
def real_amb_product_race(amb=True):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}_concurrency.py"; fn = f"test_parallel_{mod}_updates_are_atomic"
    L = header(0, "pytest 8.3.2 (-n 8 xdist)", f"{tf}::{fn}", tf, [f"src/{pkg}/{mod}.py", f"src/{pkg}/locks.py"], rng.uniform(1, 4))
    L += py_trace(tf, fn, [(tf, rng.randint(40, 100), "results = await asyncio.gather(*[svc.increment() for _ in range(20)])"),
                         (tf, rng.randint(101, 130), "assert svc.count == 20")],
                  [f"E   assert {rng.randint(17,19)} == 20"])
    L += retry_block(["PASSED", "FAILED"])  # intermittent but caused by the PR removing a lock
    L += [f"history: failed 0/50 recent runs on main (recent_fail_rate=0.00)"]
    return L, ("race_condition_in_product_code", "real")


def real_amb_perf(amb=True):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}_list.py"; fn = f"test_list_{mod}s_paginated"
    L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, [f"src/{pkg}/queries.py", f"src/{pkg}/serializers.py"], 30.0 + rng.uniform(0, 0.5))
    L += py_trace(tf, fn, [(tf, rng.randint(20, 70), f"resp = client.get('/{pkg}/{mod}s?page=1', timeout=30)"),
                         ("venv/lib/python3.12/site-packages/httpx/_client.py", 1014, "raise mapped_exc(message) from exc")],
                  ["E   httpx.ReadTimeout: timed out", f"E   (SQL log: 1,204 queries issued for 50 rows)"])
    L += retry_block(["FAILED (timeout)", "FAILED (timeout)"])
    L += [f"history: failed {(f:=rng.randint(1,3))}/50 recent runs on main (recent_fail_rate={f/50:.2f})"]
    return L, ("timeout_from_perf_regression", "real")


def real_amb_config_net(amb=True):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/integration/test_{mod}_client.py"; fn = f"test_{mod}_fetch_remote"
    L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, [f"src/{pkg}/http_config.py", "config/test.yaml"], rng.uniform(0.1, 1))
    L += py_trace(tf, fn, [(tf, rng.randint(10, 80), f"r = session.get(REMOTE_URL + '/{mod}')")],
                  ["E   requests.exceptions.ConnectionError: HTTPConnectionPool(host='localhost', port=9090): Max retries exceeded",
                   "E   Caused by NewConnectionError: [Errno 111] Connection refused"])
    L += retry_block(["FAILED (same error)"])
    L += [f"history: failed {(f:=rng.randint(1,4))}/50 recent runs on main (recent_fail_rate={f/50:.2f})"]
    return L, ("network_error_from_config_change", "real")


def real_amb_float(amb=True):
    pkg, mod = rng.choice(MODULES)
    tf = f"tests/{pkg}/test_{mod}_stats.py"; fn = f"test_{mod}_average_latency"
    L = header(0, "pytest 8.3.2", f"{tf}::{fn}", tf, [f"src/{pkg}/stats.py"], rng.uniform(0.01, 0.1))
    L += py_trace(tf, fn, [(tf, rng.randint(10, 60), f"assert avg == pytest.approx(12.5, abs=1e-3)")],
                  [f"E   assert 12.50390625 == 12.5 ± 1.0e-03"])
    L += retry_block(["FAILED"])
    L += [f"history: failed {(f:=rng.randint(1,3))}/50 recent runs on main (recent_fail_rate={f/50:.2f})"]
    return L, ("numeric_drift", "real")


PLAN = (
    [(flaky_timeout, False)] * 6 + [(flaky_timeout, True)] * 2 +
    [(flaky_race, False)] * 5 + [(flaky_race, True)] * 2 +
    [(flaky_network, False)] * 6 + [(flaky_network, True)] * 2 +
    [(flaky_order, False)] * 4 + [(flaky_order, True)] * 2 +
    [(flaky_env, False)] * 6 + [(flaky_env, True)] * 1 +
    [(real_assert, False)] * 6 + [(real_import, False)] * 4 + [(real_type, False)] * 4 + [(real_snapshot, False)] * 3 +
    [(real_amb_product_race, True)] * 2 + [(real_amb_perf, True)] * 2 + [(real_amb_config_net, True)] * 2 + [(real_amb_float, True)] * 1
)


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    for p in LOGS.glob("*.log"):
        p.unlink()
    plan = PLAN[:]
    rng.shuffle(plan)
    rows = []
    for i, (fn, amb) in enumerate(plan, 1):
        cid = f"case_{i:03d}"
        lines, (cat, truth) = fn(amb)
        (LOGS / f"{cid}.log").write_text("\n".join(l for l in lines if l is not None) + "\n")
        test_line = next(l for l in lines if l.startswith("failing test:"))
        rows.append({"case_id": cid, "test_name": test_line.split(": ", 1)[1], "category": cat,
                     "truth": truth, "ambiguous": int(amb)})
    # feedback set: the first 16 cases (chronological "day 1"), what a human would label first.
    for r in rows:
        r["feedback_set"] = int(int(r["case_id"][-3:]) <= 16)
    with open(HERE / "labels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    n_f = sum(r["truth"] == "flaky" for r in rows)
    print(f"wrote {len(rows)} logs: {n_f} flaky, {len(rows)-n_f} real, {sum(r['ambiguous'] for r in rows)} ambiguous, "
          f"{sum(r['feedback_set'] for r in rows)} in feedback set")


if __name__ == "__main__":
    main()
