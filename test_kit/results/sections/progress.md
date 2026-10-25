**Measured, synthetic** (both on test days 42–59 of the seed-7 world), except where marked. Round 1 is `reference/round1/` (committed at `b84dbb3`); now is `outputs/`. Each number is the one its own run reported. The test days have been studied closely since Round 1, so changes designed from them are also checked on a freshly simulated world (next section), and those are the numbers to quote as final.

| Measure | Round 1 | Now |  |
|---|---|---|---|
| Model | hgb-92it-677lab | hgb-86it-677lab-nocallerid |  |
| Policy | v1 | v4 |  |
| Victims warned | 94.3% (679/720) | 96.0% (691/720) | higher is better |
| Warned at their first scam payment | 69.6% (501/720) | 74.4% (536/720) | higher is better |
| Scam payments paused | 86.0% | 88.7% | higher is better |
| False pauses per 10,000 payments | 0.89 (70) | 1.04 (82) | lower is better |
| Genuine trusted checks (up to an hour's wait in Round 1) | 15 | 6 | lower is better |
| Genuine holds | 0 | 0 | lower is better |
| Pauses that are real scams at India's fraud rate (estimated) | 1 in 9 | 1 in 10 | lower is better |
| Money protected (modelled) | 93.1% | 93.2% | higher is better |
| Decision latency p99 inside the service | 40.5 ms | 32.8 ms | timing, varies by machine |

Feature-level stress tests from `run_all.py` (victims warned; model not retrained):

| Stress test | Round 1 | Now |
|---|---|---|
| As simulated | 94.3% | 96.0% |
| Scammers stop calling (chat only, no screen share) | 92.8% | 95.4% |
| Fresh, clean-looking mule for every payment, no outside flags | 75.0% | 79.4% |
| No FD break and no small credits first | 92.8% | 94.2% |
| All three at once | 58.3% | 65.3% |

Each scam type hidden from training (victims of that type warned):

| Hidden scam type | Round 1 | Now |
|---|---|---|
| investment | 91.6% | 95.3% |
| digital_arrest | 95.1% | 94.2% |
| task | 81.4% | 92.4% |
| customer_care | 85.9% | 90.1% |
| marketplace | 61.8% | 76.5% |
| voice_clone | 80.0% | 91.1% |
| loan_fee | 93.7% | 93.7% |
