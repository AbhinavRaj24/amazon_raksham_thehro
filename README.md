# Thehro (ठहरो)

A pre-PIN scam check for UPI and bank apps. Team abhinavraj24 (IIT Kanpur), RAKSHAM AI Cybersecurity Hackathon
(IIT Delhi × Amazon), Track 2: AI-Driven Scam Pattern Recognition.

In a coached scam the victim pays with their own phone and PIN, so each payment looks normal on its own. Thehro
asks, before the PIN screen, about three things together:
- the customer's own journey
- the account receiving the money
- the live situation on the phone (only with consent)

It answers with one of five responses: allow, a one-line note, a 30-second pause with the reasons, a trusted-person
check or bank call-back, or a hold for a bank officer. A note or a pause can come from one strong signal. A trusted
check or a hold needs two kinds of evidence to agree, and a hold also needs an outside flag on the payee and a person
to decide.

**All data is synthetic.** No real customer, bank, telecom or platform data is used.

## Results

Measured on a freshly simulated world that no design choice ever saw (seed 25), with the frozen finale system:

| | Round 1 system | Finale system |
|---|---|---|
| Scam victims warned (95% CI) | 92.3% (90.3–93.9%) | **94.4%** (92.6–95.7%) |
| Genuine payments paused, per 10,000 | 0.96 | **0.98** |
| Genuine payments held | 0 | 0 |
| Victims warned under all attacks at once | 70.9% | **82.6%**: still under our 85% target |

Also:
- **PaySim** (public dataset): 91.4% of frauds caught at 0.80 false alarms per 10,000.
- **Hand-written cases** : 28 of 30 right.
- **Cancel-and-retry attack:** 98.8% of retries stopped (80.7% with Round 1's policy).

Every number, every miss and how to rerun it is in [`test_kit/report.md`](test_kit/report.md).

## Setup (about 5 minutes)

You need Linux or macOS, Python 3.13 and about 4 GB of free RAM.

```bash
git clone git@github.com:AbhinavRaj24/amazon_raksham_thehro.git
cd amazon_raksham_thehro
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r thehro-complete-code/thehro/requirements-lock.txt
cd thehro-complete-code/thehro          # every command below runs from here
```

## Run the live demo (judge mode)

```bash
bash scripts/demo_up.sh
```

The first start builds the bank's world (about 30 s); later starts take about 10 s. Then open:

| What | Where |
|---|---|
| Judge page: build any payment and see the decision, the phone alert and the bank's view | http://127.0.0.1:8080/ |
| Officer console: holds and call-backs | the judge page's **Officer** tab |
| The customer's screen on your own phone | under the phone on the judge page, **📱 Show this on a judge's own phone**, then scan the QR (same Wi-Fi or hotspot) |
| Test kit, live: random labelled samples with your seed, the hand-written cases, all results | the **Kit** and **Results** tabs |

Ctrl+C stops it. The step-by-step demo, with what each click should show, is in `docs/DEMO_RUNBOOK.md`.

## Run the tests and the test kit

```bash
python -m pytest -q                       # 76 tests, about 15 s

# the test kit: builds its cache the first time (about 3 min), then about 12 min
python scripts/run_all.py --quick --out /tmp/thehro_quick --cache .cache/thehro_world.pkl
python scripts/run_test_kit.py            # writes test_kit/report.md and test_kit/results/
```

- **K1 hand-written cases:** these run only while `demo_up.sh` is running.
- **PaySim** is a public dataset we don't redistribute. Download it from Kaggle (`ealaxi/paysim1`) into `data/paysim/` at the repository root, and the kit includes it.
- **Details** of every step and what each result means: [`test_kit/README.md`](test_kit/README.md).

## Reproduce

```bash
python scripts/run_all.py --out /tmp/now                 # simulate, train, evaluate (about 3 min)
python scripts/compare_outputs.py /tmp/now               # matches the committed outputs/ exactly
python scripts/run_all.py --policy v1 --out /tmp/r1
python scripts/compare_outputs.py /tmp/r1 --ref reference/round1   # Round 1, exactly as submitted
```

## What's where

| Path | What it holds |
|---|---|
| `thehro-complete-code/thehro/thehro/` | simulator, features, models, policy (versions v1 to v4), explanations, decision service, live features, test-kit helpers |
| `thehro-complete-code/thehro/bank/` | bank server, judge page, officer console, phone view |
| `thehro-complete-code/thehro/scripts/` | `run_all.py`, `demo_up.sh`, `run_test_kit.py` and one script per test |
| `thehro-complete-code/thehro/tests/` | unit and failure-path tests |
| `thehro-complete-code/thehro/outputs/` | results of the current system |
| `thehro-complete-code/thehro/reference/round1/` | Round 1's results, exactly as submitted |
| `test_kit/` | the test workflow: report, results, the 30 hand-written cases, the published data sample |
| `Thehro Round 1 Final.pdf` | the Round 1 submission |
