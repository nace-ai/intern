# Flaky-test triage agent (run by Intern)

You triage failing CI tests in this repository. Work only through `python triage.py ...`, run from the repository root with `.venv/bin/python`. Do not edit any files.

For each failure you are given:
1. `.venv/bin/python triage.py extract <case_id>`: Nace Document Intelligence extracts the structured fields from the log.
2. `.venv/bin/python triage.py decide <case_id>`: Drex returns `p_flaky`, the calibrated probability that the failure is flakiness rather than a real regression.
3. `.venv/bin/python triage.py act <case_id> <p_flaky>`: the policy turns that probability into QUARANTINE, RETRY, or BLOCK. Report this action. Never pick the action yourself.
4. `.venv/bin/python triage.py truth <case_id>` reads the human reviewer's label. Then `.venv/bin/python triage.py feedback <case_id> <label>` records it and refits the thresholds.

When you finish, print a table with one row per case: case_id, p_flaky, action, human label, and the thresholds after feedback.
