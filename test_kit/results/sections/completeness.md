Every kind of test we run, what it checks and where it lives. Rows that are not done stay in the table, with who owns them.

| Test | Where | Status | Result |
|---|---|---|---|
| Unit tests: point-in-time features, policy safety rules, messages, evidence tampering, API, kit helpers | `tests/` (test_features.py, test_policy.py, test_explain.py, test_api.py, test_kit.py) | done | 75 passed, 1 warning in 12.27s |
| Live = batch feature parity (judge mode uses the training feature code) | `tests/test_live.py` | done | 3 tests |
| Bank-server rules: consent drop, 30-second wait, holds, officer release, fallback | `tests/test_bank.py` | done | 18 tests |
| Failure paths, run on purpose: feeds time out, decision service down, no officer, call-back not reached, no consent; a confirmed scam reaching every customer | `tests/test_failures.py` | done | 7 tests |
| Reproducibility: a clean rebuild reproduces outputs/ exactly | `scripts/compare_outputs.py`; `kit.load_baseline()` asserts on every run | done | every kit run checks 691/720 victims and 82 false pauses against outputs/metrics.json |
| Held-out test days (whole evaluation, all error rates with CIs) | `scripts/kit_metrics.py` | done | matches outputs/metrics.json |
| Scam types hidden from training (7 refits) | `outputs/tables/held_out_workflows.csv` (`run_all.py`) | done | 76%–95% of victims still warned |
| Named failure cases (every miss and false pause grouped) | `scripts/failure_cases.py` | done | 29 missed victims in 4 groups, 82 false pauses in 5 groups |
| Genuine customers flagged by mistake: what they saw, how long it took, repeats | `scripts/eval_false_positives.py` | done | 82 interrupted (81 customers), 0 held; 14 raised by memory |
| K2 spot-checks: random labelled draws through the live decision service | `scripts/spot_check.py` | done | seed 2026: 57/60 right, live = evaluation 60/60; seed 42: 58/60 right, live = evaluation 60/60; seed 42 journeys: 28/30 right, live = evaluation 30/30; seed 7: 56/60 right, live = evaluation 60/60 |
| K1 hand-written, pre-registered cases | `test_kit/cases/`, `scripts/k1_run.py` | done | 28/30 right |
| Attacks on the held-out weeks (event-level, frozen model) | `scripts/attacks.py` | done | No edits (rebuild check): 96.0%; Chat-only coaching (no calls, no screen share): 95.4%; Split payments just under Rs 10,000: 97.5%; Aged mules (old, recently active, forward 50-70% after 6-48 h): 88.2%; Fresh, clean mule for every payment (feature-level): 79.4%; All of the above at once: 88.2% |
| Policy versions side by side, and cancel and retry to another payee (live path) | `scripts/eval_policy.py` | done | retries stopped 80.7% (Round 1 policy) → 98.8% (policy v4), n = 658; victims warned 94.3% → 96.0% |
| Signals switched off in turn (ablations) | `scripts/signal_switchoff.py` | done | 6 switch-offs |
| Fairness by age, language, region, consent (baseline and combined attack) | `scripts/fairness_check.py` | done | worst age group 1.30× the overall pause rate (pass) |
| Caller identity: the shipped system without it vs a counterfactual with it | `scripts/caller_identity.py` | done | victims warned 96.0% shipped vs 95.7% with caller id; false pauses 1.04 vs 1.00 |
| Design choices tried (adversarial training; journey-line share sweep) | `scripts/design_choices.py` | done | adversarial training rejected; journey-line share 0.3 gives the combined attack 88.2% |
| Public benchmark: PaySim (method on outside data) | `scripts/paysim_benchmark.py` | done | at 1 in 10,000: 91.4% of frauds caught, 0.80 false alarms per 10,000 |
| Dataset A published: held-out export + data card | `scripts/export_test_set.py`, `test_kit/README.md` | done | 6cf6f5ee22433064… |
| Latency and load: decision service and bank server under concurrent requests | `scripts/loadtest.py` | done | decision service 12.6 ms median, 18.2 ms p99 over HTTP, 77/s per process; judge case end to end 26.6 ms median; 0 errors |
| A freshly simulated world no design choice saw (frozen systems and retrained recipes) | `scripts/fresh_world.py` | done | seed 25: frozen finale system warns 94.4%, 0.98 false pauses per 10,000; combined attack 82.6% |
