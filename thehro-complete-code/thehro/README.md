# Thehro (ठहरो, "wait")

A scam-journey guard for UPI and bank apps. Prototype for the Amazon AI Cyber Security Hackathon, Round 1,
Track 2: AI-Driven Scam Pattern Recognition.

**The problem.** In an authorised push payment scam the victim pays with their own phone and PIN. Checked one
payment at a time, every step looks normal. The scam only shows up when three views are joined: the payer's
journey, the account receiving the money, and the live situation on the phone.

**What Thehro does.** Before the PIN screen, the bank app asks Thehro about the payment. Thehro joins three
families of signals, scores the risk and picks one of five responses, from "allow" to "hold this payment for a
person to review". Each family of interruptions has a fixed budget of genuine payments it may touch. Every
warning shows facts the customer can check and advice that fits those facts, in their language. Every
decision of tier 2 or above is written to a tamper-evident log.

All data here is synthetic. No real customer, bank or platform data was used.

**Finale (25 Oct 2026).** The system has moved on from Round 1. The results to quote now are in
`../../test_kit/report.md` (one command, `python scripts/run_test_kit.py`), led by the *Round 1 → now* table and
the *fresh world* test: every system on a newly simulated world no design choice ever saw. What changed:
- policy v4 (`thehro/policy.py`, versions v1 to v4)
- no caller identity
- a bank call-back for customers with no trusted person
- bank-wide flags from officer-confirmed scams
- judge mode with live features (`bank/`, `thehro/live.py`)

Round 1's results below are kept exactly in `reference/round1/`.

## Results on the synthetic test weeks (Round 1, as submitted)

Thresholds were set on one validation week (days 35-41). Results are from the next 18 days (days 42-59), with
no retraining, scored against ground truth including scams that were never reported. The figures come from
`outputs/metrics.json`.

| Measure | Thehro | Pause every first ₹10,000+ payment to a new payee |
|---|---|---|
| Scam victims warned (tier 2+) | **94.3%** (69.6% at their first payment) | 69.0% |
| Money protected, modelled* | **93.1%** (75.5% if half as many victims stop) | 71.9% |
| Genuine payments interrupted per 10,000 | **0.89** (budget 1.0) | 59.8 |
| Customers interrupted in 18 days | **0.17%** | 10.0% |
| Real scams per warning, at India's fraud rate** | **1 in 9** to 1 in 16 | 1 in 1,076 to 1 in 2,152 |
| Genuine payments held for review | **0** of 786,121 | n/a |

\* Expected share of scam losses prevented, using the response rates assumed in `thehro/config.py` (`STOP_PROB`).
These are assumptions to test in a pilot, anchored on the CeDEx experiment below.
\** The synthetic world has 158-315x more scams than reality, so that there are enough to measure. Precision is
converted to India's reported UPI fraud rate (6.8 per million payments, FY 2024-25) and to an estimated true rate
of 13.6 per million (about half of victims never complain).

**New scam types.** With each scam type hidden from training entirely, 62-95% of its victims are still warned
(`outputs/tables/held_out_workflows.csv`). The fake loan fee type first appears in week 5; the model saw 2
reported cases and warned 92% of its victims.

**Mule networks without complaints.** The label-free detector flagged 148 of 157 campaigns (94%), a median 16
hours after the first victim paid; 78% were flagged before the first complaint. 1 of 170 flagged groups held no
scam account.

**When scammers adapt** (model not retrained): no calls or screen sharing 92.8% of victims warned; a clean new
mule for every payment 75.0%; no FD break and no small credits first 92.8%; all three at once 58.3%.

**Fairness.** Age, language and region are never model inputs. Victims warned ranged from 91% to 97% across age
bands, and false interruptions from 0.78 to 1.10 per 10,000 payments. Customers who did not opt in to call signals
were warned as often (94.3% vs 94.3%).

**Speed.** 25 ms median, 40 ms p99 for the full decision (all models, policy, reasons, message), in Python on 2 vCPU.

## How it works

```
 payment initiated in the bank app
        │
        ├─ Journey signals   bank's own records: first payment to this payee, amount vs usual, a rising series
        │                    to new payees this week, FD broken in 48 h, small credits from strangers
        ├─ Payee signals     NPCI / MuleHunter.AI risk, I4C Suspect Registry, DoT FRI band, live mule-network flag
        └─ Context signals   opt-in, on the phone: on a call with an unknown number and for how long, screen
                             sharing, remote-access app (yes/no flags only)
        │
        ▼
 three family models + one fused model (gradient-boosted trees, monotonic where it matters)
        ▼
 policy: tier 0-4 within fixed budgets; a delay needs two families to agree; a hold also needs an
         outside flag on the payee and a person; 7-day memory after the customer continues past a warning
        ▼
 customer screen (facts + advice, EN/HI)  ──►  evidence log (hash-chained)  ──►  fraud officer (holds)
                                                                               └─►  1930 / NCRP complaint draft
```

| Tier | What the customer sees | When | Genuine budget |
|---|---|---|---|
| 0 Allow | nothing | below every line | |
| 1 Note | one line under the Pay button | fused risk above the note line | 1 in 1,000 payments |
| 2 Pause and explain | full screen: facts, advice, Cancel / Call my bank / Continue after 30 s | risk above the pause line | 1 in 10,000 (tiers 2-4) |
| 3 Trusted check | trusted person approves, or a one-hour wait | tier 2 + two families agree + ₹50,000 or more | inside tier 2's |
| 4 Hold for review | this payment only is held; a fraud officer calls within 30 minutes | two families + outside flag on payee + hold line + ₹10,000 or more | 1 in 100,000 |

After a customer continues past a warning, for 7 days the amount lines are waived and a note counts as a pause.
A payee they chose to pay after a warning, still unflagged 7 days later, counts as confirmed and stops
triggering warnings.

## Run it

```bash
pip install -r requirements-lock.txt     # exact versions (requirements.txt keeps the loose ranges)
python scripts/run_all.py --out /tmp/run  # simulate, train, evaluate: ~2-5 min, ~3 GB RAM (policy v4)
python scripts/compare_outputs.py /tmp/run  # check the run reproduces outputs/ (the current system) exactly
python scripts/run_all.py --policy v1 --out /tmp/r1   # Round 1's recipe ...
python scripts/compare_outputs.py /tmp/r1 --ref reference/round1   # ... reproduces Round 1 exactly
python scripts/run_test_kit.py     # the whole test kit -> ../../test_kit/report.md (about 25 min)
python scripts/build_demo.py       # outputs/thehro_demo.html, the Round 1 replay demo
python -m pytest -q                # tests: point-in-time features, policy rules, messages, API,
                                   # live-vs-batch features, the bank server's rules
uvicorn thehro.api:app --port 8000 # POST /v1/score, POST /v1/outcome, GET /v1/evidence/{seq}
bash scripts/demo_up.sh            # finale judge mode: decision service :8000 + bank server :8080
```

`outputs/sample_requests.json` holds example requests for the API. `run_all.py` without `--out` rewrites `outputs/`, the committed results of the current system: do that only to re-baseline on purpose.

| Path | What it holds |
|---|---|
| `thehro/simulate.py` | synthetic world: 40,000 customers, 60 days, 2.78 million payments, 7 scam workflows, mule networks, late and missing complaints |
| `thehro/features.py` | point-in-time features for the three signal families |
| `thehro/campaign.py` | label-free mule-network detector (streaming union-find) |
| `thehro/models.py` | family models, fused model, workflow recogniser (for analysts only) |
| `thehro/policy.py` | tiers, the two-family gate, budgets, customer memory |
| `thehro/explain.py` | reason codes, fact-based advice, EN/HI messages, hash-chained evidence log, complaint draft |
| `thehro/evaluate.py` | metrics at real base rates, baselines, stress tests, held-out scam types, fairness |
| `thehro/service.py`, `thehro/api.py` | decision service and FastAPI app |
| `thehro/live.py` | live features for judge mode: the training feature code run on the customer's and payee's events |
| `bank/server.py`, `bank/static/` | the bank server: judge page, officer console, phone mode, call-backs, drills, the Kit tab |
| `thehro/kit.py`, `thehro/redteam.py`, `scripts/` | the test kit: baseline, intervals, spot-checks, attacks, fresh world, report |
| `reference/round1/` | Round 1's outputs exactly as committed (`b84dbb3`) |
| `demo/template.html` | the clickable demo |

## Choices worth knowing

- **Labels arrive late and incomplete, as in real life.** Training uses only scams reported before the cut-off;
  unreported scams count as genuine during training.
- **No single signal can delay money.** A warning can come from risk alone; a trusted check or a hold needs two
  signal families to agree, and a hold also needs an outside flag on the payee and a person to decide.
- **Messages are facts, not scores.** Up to three reasons, ranked by how much more often each appears in reported
  scams than in other payments. The advice is chosen from those facts, not from a guess about the scam type, so a
  new scam still gets advice that fits.
- **Minimum data.** No call audio, messages, contacts, photos or location. The calling number is used only for a
  one-off fraud-risk lookup. Context signals are opt-in; without consent that family does not vote.
- **Thresholds are budgets.** Each tier's line is set so genuine payments cross it at a fixed rate, measured on all
  customer payments, shop payments included.

## Assumptions and sources

| Assumption | Value | Source |
|---|---|---|
| Reported UPI fraud rate | 12.64 lakh frauds / 18,587 crore payments in FY 2024-25 = 6.8 per million | [Lok Sabha unstarred question 2481](https://eparlib.sansad.in/bitstream/123456789/3018042/1/AU2481_5bIzKk.pdf), [IANS](https://ianslive.in/upi-transactions-register-22-pc-growth-in-july-value-hits-rs-2988-lakh-crore--20260801093238) |
| Share of victims who never complain | 51%, so true rate ~2x reported | [LocalCircles via Business Standard](https://www.business-standard.com/finance/news/upi-transaction-fraud-india-survey-one-in-five-users-hit-localcircles-125062601141_1.html) |
| Victims who stop when warned | 35-75% (pause), 75-90% (trusted check), 92-97% (hold), by scam type | anchored on [CeDEx 2023](https://www.nottingham.ac.uk/cedex/documents/papers/cedex-discussion-paper-2023-08.pdf): risk-based screening with one-tap safe actions cut scam payments by 81%; discounted for live coaching |
| Trusted check at ₹50,000; ₹10,000 line | | [RBI discussion paper, April 2026](https://www.rbi.org.in/SCRIPTs/PublicationsView.aspx?id=23810) (one-hour lag at ₹10,000+, trusted person above ₹50,000) |
| Hold only the disputed amount, with review | | RBI draft mule-account procedure, Sep 2026 ([Taxguru](https://taxguru.in/?p=1078928)) |
| Decision before the PIN screen | | NPCI 15-second request-pay limit since 16 June 2025 ([MediaNama](https://www.medianama.com/2025/06/223-npci-upi-changes-transaction-speed-fraud-prevention/)) |
| Scam mix and losses | investment and digital arrest dominate losses | [ThePrint](https://theprint.in/india/cybercrime-saw-24-spike-in-2025-indians-lost-rs-22495-crore-mainly-in-investment-scams/2859930/), [The Wire](https://thewire.in/tech/india-lost-rs-11333-crore-to-cyber-fraud-in-2024) |

Simulated median losses per victim: investment ₹1.95 lakh, digital arrest ₹1.93 lakh, customer care ₹58,000,
voice clone ₹48,000, task ₹35,000, loan fee ₹19,000, marketplace ₹16,000.

Other sources cited in the deck:
[Asianet, citing a Rajya Sabha reply](https://newsable.asianetnews.com/india/senior-citizens-women-lost-rs-7-769-crore-to-cyber-fraud-in-2025-articleshow-u9oad8w)
(seniors lost ₹4,005 crore across 1,03,488 complaints in 2025);
[ELP summary of RBI's customer-liability directions, June 2026](https://elplaw.in/wp-content/uploads/2026/06/RBI-Tightens-Customer-Protection-Framework-for-Fraudulent-Electronic-Banking-Transactions.pdf);
[AM Legals on the RBI mule-account procedure](https://amlegals.com/mule-accounts-and-cyber-fraud-supreme-courts-directions-on-the-proposed-rbi-sop-and-their-fintech-implications/);
[TechCrunch on Google Pay's scam warnings in India](https://techcrunch.com/2025/11/20/google-steps-up-ai-scam-protection-in-india-but-gaps-remain);
[DoT Financial Fraud Risk Indicator](https://www.sancharsaathi.gov.in/SancharSaathiDocuments/ImportantDocuments/DoT%20Introduces%20Financial%20Fraud%20Risk%20Indicator%20%28FRI%29%20to%20strengthen%20Cyber%20Fraud%20Prevention.pdf);
[MuleHunter.AI and DPIP, Parliament reply via Taxguru](https://taxguru.in/?p=1063495).
"16 false alarms per scam caught" and "about 8,800 UPI payments a second" are our calculations from the
figures above and [IANS](https://ianslive.in/upi-transactions-register-22-pc-growth-in-july-value-hits-rs-2988-lakh-crore--20260801093238).

## Limits

- Synthetic data is cleaner than reality, so real results will be lower. The stress tests show how much is left
  when scammers change tactics; a shadow-mode pilot on real data is the first step of the rollout.
- Money protected depends on assumed response rates, to be measured in an A/B pilot.
- Payee signals (NPCI, MuleHunter.AI, IDPIC, I4C registry, DoT FRI) are simulated; production needs data-sharing
  agreements with their owners.
- The workflow recogniser is for analysts and the complaint draft only. It names the right scam type for 78% of the
  warned scam payments it names, and it confidently mislabels scam types it has barely seen (loan fee).

## Data, models and AI-generated content

- **Data:** fully synthetic, generated by `thehro/simulate.py`, calibrated to the public Indian figures above. No
  real customer, bank or platform data.
- **Models:** scikit-learn `HistGradientBoostingClassifier`, trained from scratch on the synthetic data. No
  pre-trained or third-party models.
- **Libraries:** numpy, pandas, scikit-learn, FastAPI, Pydantic, uvicorn, matplotlib, pytest (all open source).
  The demo loads IBM Plex fonts from Google Fonts (SIL Open Font License).
- **AI assistance:** the code, the demo and this README were written with an AI assistant (Claude, by Anthropic)
  and reviewed by the team.
