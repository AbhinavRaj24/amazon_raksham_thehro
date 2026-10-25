# Thehro test kit

The organisers asked for this:

> The test workflow is a part of your submission. You may choose any open (publicly available) dataset, and submit
> reproducibility instructions along with your results. Completeness of tests is just as important as accuracy.

This folder is that workflow. It holds the data, one command that reruns everything, the results, and these
instructions. A judge can run it, get our numbers, and draw samples of their own.

- **Results:** [`report.md`](report.md). Every number there is labelled *measured*, *estimated* or *modelled*, and
  says whether it comes from our synthetic data or from PaySim.
- **Raw results:** `results/` (JSON and CSV), with every run listed in `results/runs.csv`.

## 1. Reproduce everything

You need Python 3.13 and about 3–4 GB of free RAM.

```bash
git clone git@github.com:AbhinavRaj24/amazon_raksham_thehro.git && cd amazon_raksham_thehro
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r thehro-complete-code/thehro/requirements-lock.txt
cd thehro-complete-code/thehro
python -m pytest -q                                                # all tests pass
python scripts/run_all.py --quick --out /tmp/thehro_quick --cache .cache/thehro_world.pkl   # builds the cache once
python scripts/run_test_kit.py                                     # the whole kit, into test_kit/results
```

- **Building the cache.** The first command simulates the world and saves it with all 854,912 feature rows to
  `.cache/thehro_world.pkl` (git-ignored, about 500 MB). It takes 1.5–3 minutes and about 2.4 GB of RAM. Every
  kit script, and the judge page's Kit tab, loads this file instead of re-simulating. If you put it elsewhere, pass
  `--cache <path>` or set `THEHRO_CACHE`. `--out` keeps `run_all.py` away from `outputs/`, which holds the
  committed results of the current system (Round 1's are kept exactly in `reference/round1/`).
- **The policy-versions step** also needs the bank server's world cache, `.cache/live_world.pkl`. The first
  `bash scripts/demo_up.sh` builds it (about 2 minutes), or `scripts/eval_policy.py` builds it itself.
- **Running the kit.** `run_test_kit.py` took about 10 minutes on a 16-core laptop with PaySim, about
  20 seconds of it loading and scoring the baseline once. The policy versions step (with the cancel-and-retry
  attack) takes the longest (about 5 minutes), then the attacks (about 1.5 minutes) and PaySim (about 45 seconds);
  each spot-check takes about 3 seconds.
- **The baseline check.** Every kit script first checks that the frozen model, lines and policy in `outputs/`
  reproduce the committed `outputs/metrics.json`: now **691 of 720 victims warned** and **82 false pauses** in
  786,121 customer payments (policy v4, no caller identity). If either check fails, the cache or `outputs/` is stale: rebuild.

### Expected key numbers

All are synthetic, measured on the test days (42–59) of the seed-7 world, with the frozen model and lines in `outputs/`
and policy v4. The numbers to quote as final are the fresh-world ones (last rows), because the seed-7 test days
were used to make choices.
Your run should print exactly these. Round 1's own numbers are in the report's "Round 1 → now" section.

| Measure | Expected |
|---|---|
| Victims warned | 691 / 720 = 96.0% (95% CI 94.3–97.2%) |
| Warned at their first scam payment | 536 / 720 = 74.4% |
| False pauses | 82 in 786,121 = 1.04 per 10,000 (95% CI 0.83–1.29) |
| Genuine trusted checks | 6 (each offered a bank call-back) |
| Genuine holds | 0 (95% upper bound 1 in 213,000) |
| Spot-check seed 7 / 42 / 2026 (payment level, 60 samples) | 56 / 58 / 57 right; live = evaluation 60 of 60 every time |
| Rebuild with no edits (attacks.py) | identical to the baseline |
| Chat-only coaching (attacks.py) | 687 / 720 = 95.4% warned, 1.04 false pauses per 10,000 |
| All attacks at once (attacks.py) | 635 / 720 = 88.2% warned, 0.98 false pauses per 10,000 |
| Phone signals switched off (signal_switchoff.py) | 95.7% warned, 1.26 false pauses per 10,000 |
| Cancel-and-retry attack (eval_policy.py) | retries stopped 80.7% with Round 1's policy, 98.8% with v4 (100% after a warning v4 gave; n = 658) |
| K1 hand-written cases (k1_run.py, bank server running) | 28 / 30 right |
| Fresh world, finale system frozen (fresh_world.py) | 94.4% warned, 0.98 false pauses per 10,000; combined attack 82.6% (fails the 85% line) |
| Fresh world, Round 1 system frozen | 92.3% warned, 0.96 false pauses per 10,000; combined attack 70.9% |
| PaySim, line at 1 in 10,000 (paysim_benchmark.py) | 2,425 / 2,652 = 91.4% of frauds caught, 0.80 false alarms per 10,000 |

### Run one piece at a time

```bash
python scripts/spot_check.py --seed <any number>          # 25 scam, 25 risky-looking genuine, 10 ordinary genuine
python scripts/spot_check.py --seed <N> --journeys 30     # 30 victims: right if any of their payments was warned
python scripts/failure_cases.py                           # every missed victim and false pause, grouped by cause
python scripts/eval_false_positives.py                    # what the falsely interrupted customers saw, and for how long
python scripts/eval_policy.py                             # policy versions v1 (Round 1), v2, v3 + cancel-and-retry
python scripts/round1_vs_now.py                           # Round 1's committed results against outputs/
python scripts/kit_metrics.py --run-tests                 # error rates, confusion matrices, completeness table
python scripts/export_test_set.py                         # dataset A (below)
python scripts/signal_switchoff.py                        # each signal switched off in turn
python scripts/attacks.py --seed 7                        # event-level attacks on the held-out weeks
python scripts/fairness_check.py                          # by age, language, region, consent; baseline and attack
python scripts/caller_identity.py                         # what caller identity would add (the shipped system has none)
python scripts/paysim_benchmark.py                        # dataset B, once downloaded
python scripts/k1_run.py                                  # hand-written cases, with the bank server running;
                                                          # refuses any case not matching cases/FROZEN.sha256
```

## 2. What counts as right

| Term | Meaning |
|---|---|
| test days | days 42–59, never used for training or for setting the alert lines |
| interrupted / warned | tier 2 or higher (pause, trusted check or hold). Tier 1, a one-line note, is *not* an interruption |
| a scam sample is right | it reaches tier 2+ |
| a genuine sample is right | it stays at tier 0–1 |
| a `never_hold` case is right | it stays below tier 4 |
| false pause rate | genuine tier 2+ payments ÷ **all** customer payments on test days (786,121, shop payments included) × 10,000 |
| victim warned | at least one tier 2+ payment in a scam journey that starts on the test days (720 journeys) |
| risky-looking genuine | genuine payments of kinds new individual, new landlord, medical bill or informal investment |

Intervals are 95%: Wilson for shares (victims warned, samples right), exact Poisson for rare counts (false pauses,
holds).

**Payment level vs victim level.** About 86% of all scam *payments* are paused, because a scam's first step is
often small. At the *victim* level 94.3% are warned. So a payment-level spot-check will usually show 2–4 "missed"
scam payments whose victims were warned later in the same journey.

## 3. The datasets

### A. Thehro synthetic (ours, published here)

| | |
|---|---|
| Generator | `thehro/simulate.py`, seed 7. The generator is the source of truth: same code and seed, same world |
| Size | 40,000 customers, 60 days, 2.78 M payments, 1,675 scam journeys in 157 campaigns |
| Held-out export | `data/thehro_heldout.csv.gz` (git-ignored; attach to a GitHub release): the 250,793 scored payments on the test days, of which 2,680 are scam payments from 720 victims. It's written with a fixed gzip timestamp, so the same code gives a byte-identical file (about 26 MB). SHA-256: `6cf6f5ee22433064054c661dc42450af226ed88e3c89032e39e3bb8bcb9e8a47` (policy v4 tiers), also in `test_kit/data/heldout_full.sha256` |
| Committed sample | `test_kit/data/heldout_sample.csv`: 2,000 rows (500 scam, 500 risky-looking genuine, 1,000 other genuine, seed 7) |
| Columns | `test_kit/data/columns.csv`: the 39 model features in three families, ground truth, complaint timing, frozen-model scores and tiers, and three `audit_` columns (age, language, region) that are **never model inputs** and are used only for the fairness check |
| Labels | `is_scam` is ground truth, including scams never reported. Training used only scams *reported before day 38*, the way a bank learns; that rule gives 0 for every test-day row, so the export carries `bank_label_by_day_60` (reported by the end of the data) instead |
| Scored payments | customer payments to people or businesses. Shop payments to registered merchants are fast-pathed and not scored, but they count in every false-pause denominator |
| Scam enrichment | scams are 0.214% of payments, **158–315× more common than in reality**, so there are enough to measure. Shares of pauses that are real scams are therefore converted to India's real fraud rate and labelled *estimated* |

**What is realistic:**
- Indian scam workflows: fake trading, digital arrest, task jobs, fake customer care, marketplace, cloned voice,
  and a new loan-fee scam from day 30.
- Victims who complain late or never (about half never do).
- Outside flags that appear only days after complaints.
- Mule accounts that forward money within minutes.
- Many genuine payments that look risky: new landlords, medical emergencies, payments made during calls.

**What isn't realistic:**
- The world is cleaner than reality.
- Parameters are illustrative, loosely calibrated to public Indian figures (sources in `thehro/README.md`).
- Customer behaviour doesn't change over time.
- There are no account takeovers or merchant fraud.

### B. PaySim (public)

PaySim is a public synthetic mobile-money dataset: Lopez-Rojas, Elmir and Axelsson, *PaySim: a financial mobile
money simulator for fraud detection*, EMSS 2016. It's on Kaggle as `ealaxi/paysim1`, with about 6.3 M transactions
over 744 hourly steps and about 0.13% fraud. Our team plan lists the licence as CC BY-SA 4.0; check it on the
Kaggle page and record it in `DISCLOSURES.md`.

**Download it** into `data/paysim/` at the repository root (git-ignored), either:
- in the browser from the Kaggle page, or
- with the Kaggle CLI in its **own** small venv, so the project's lock file stays clean:

```bash
python3 -m venv ~/.venvs/kaggle && ~/.venvs/kaggle/bin/pip install kaggle      # needs ~/.kaggle/kaggle.json
~/.venvs/kaggle/bin/kaggle datasets download -d ealaxi/paysim1 -p data/paysim --unzip
python scripts/paysim_benchmark.py                                             # about 3-6 minutes
```

**What it tests:** our *method* on data we didn't make. That method is:
- point-in-time features,
- gradient-boosted trees with the same settings as `models._hgb()`,
- alert lines set on genuine traffic to 1 in 1,000 and 1 in 10,000 of all transactions.

The time split is train on steps 1–400, set lines on 401–500, test on 501–744. It's compared with PaySim's own
`isFlaggedFraud` flag and two simple rules. After-balances (`newbalanceOrig`, `newbalanceDest`) are never used,
because they're a known leak.

**What it can't test:** PaySim fraud is account takeover, so there are no calls, no coaching, no customer history
and no cross-bank mule networks.

## 4. Kit samples

- **K1, hand-written cases** (`cases/K1-01.json` to `K1-30.json`: 10 clear scams, 10 risky-looking genuine, 10
  hard). These were written by P4, who didn't build the model, together with the expected outcome, and frozen in
  `cases/FROZEN.sha256` (one SHA-256 per file, identical to P4's package as received) *before* anything ran on
  them. The freeze is enforced: `scripts/k1_run.py` and the bank server's `/kit/run` refuse any case file that is
  missing from that list or has changed since.
  Each file is a judge-mode case (the same fields as `POST /judge/case`) plus who wrote it, when, the true answer and the
  expected outcome:
  ```json
  {"id": "K1-01", "title": "Digital arrest: FD broken, then a transfer during a long video call",
   "group": "clear_scam", "truth": "scam", "story": "A 'CBI officer' on a WhatsApp video call ...",
   "written_by": "P4", "written_at": "2026-10-24T19:30+05:30",
   "customer": "C23724", "payee_type": "mule_network", "amount": 85000, "channel": "IMPS",
   "history": [{"kind": "fd_break", "hours_ago": 3}], "consent": true,
   "context": {"on_call": 1, "call_ott": 1, "call_min": 95, "caller_unknown": 1}, "lang": "en",
   "expected": "interrupt", "why": "A first payment far above anything she has ever sent ..."}
  ```
  `expected` is one of `interrupt`, `no_interrupt` or `never_hold`. `python scripts/check_cases.py` checks the
  format without running anything. `scripts/k1_run.py` runs them through the live bank server, and the judge
  page's Kit tab runs them one at a time or all together. Every miss stays in the report.
- **K2, random labelled draws** (`scripts/spot_check.py`). The judge picks the seed. Samples come from the test days
  and run through the same `DecisionService` the judge page uses, after replaying that customer's earlier
  test-day payments, so memory is right. Each live tier is cross-checked against the offline evaluation; any
  disagreement is a bug. The risky-looking genuine draws are split evenly across their four kinds; a plain random
  draw would be almost all "new individual". The judge page's Kit tab calls the same function,
  `thehro.kit.spot_check(seed)`, through the bank server's `/kit/spot_check`: the judge types any seed and sees
  every sample, right or wrong. The first draw loads the baseline (about a minute, once per server).

## 5. Rules that keep the numbers honest

1. Only `run_all.py` writes `thehro-complete-code/thehro/outputs/`, and only when the team deliberately re-baselines.
   Round 1's results are kept exactly as committed in `reference/round1/`.
2. Nothing is fitted on the test days: models train on days 14–34 and the alert lines are set on the validation
   week (35–41), model v2 included. Since Round 1 the test days have also guided design choices (policy v3 was
   checked on the validation week first), so the final numbers are also reported on a freshly simulated world.
3. Every rate has a 95% interval and its sample size.
4. Every number is labelled measured, estimated or modelled, and synthetic or PaySim.
5. Failures stay in. A pass line that fails is reported as failed, and attacks and lines are never changed to make
   something pass.
6. Hand-written cases are pre-registered: frozen in `cases/FROZEN.sha256` before any run, never edited after.
7. Every run appends a row to `results/runs.csv`: run ID, time, git commit, command, seed and the files it wrote.
8. Data never goes into git: PaySim, the full export and the model v2 bundle live in the repository's `data/`
   folder, which is git-ignored. The small result files (CSV, JSON, MD) are committed.

## 6. Layout

```text
test_kit/
  README.md          this file
  report.md          every result, generated (sections are rewritten by the script that owns them)
  cases/             K1 hand-written cases (P4, pre-registered; scripts/check_cases.py checks the format)
  data/              heldout_sample.csv, columns.csv, heldout_full.sha256 (the full export is in ../data/)
  results/           every JSON/CSV the scripts write, sections/*.md, runs.csv
```

The code lives in `thehro-complete-code/thehro/`:
- `thehro/kit.py`: baseline, intervals, the right rules, the spot-check, the run log and the report builder.
- `thehro/redteam.py`: the event-level attacks.
- `scripts/`: one script per test, plus `run_test_kit.py`.
- `tests/test_kit.py`: unit tests for the kit itself.
