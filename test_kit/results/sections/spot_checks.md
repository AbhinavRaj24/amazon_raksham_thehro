**Measured, synthetic.** Random labelled draws from the test days, run through the live `DecisionService` after replaying each customer's earlier test-day payments. Rerun with any seed: `python scripts/spot_check.py --seed <N>` (payment level) or `--journeys <M>` (victim level).

Right means: a scam is warned (tier 2+); a genuine payment is not interrupted (tier 0–1).

| Seed | Level | n | Right (95% CI) | Scams right | Genuine right | Live = evaluation | File |
|---|---|---|---|---|---|---|---|
| 2026 | payment | 60 | 57/60 (86%–98%) | 23/25 (75%–98%) | 34/35 (85%–99%) | 60 of 60 | `results/spot_check_seed2026.json` |
| 42 | payment | 60 | 58/60 (89%–99%) | 23/25 (75%–98%) | 35/35 (90%–100%) | 60 of 60 | `results/spot_check_seed42.json` |
| 42 | journey | 30 | 28/30 (79%–98%) | 28/30 (79%–98%) | – | 30 of 30 | `results/spot_check_seed42_journeys30.json` |
| 7 | payment | 60 | 56/60 (84%–97%) | 22/25 (70%–96%) | 34/35 (85%–99%) | 60 of 60 | `results/spot_check_seed7.json` |

Payment-level draws contain about 25 scam payments; about 86% of all scam payments are paused, because a scam's first step is often small. Victim-level draws (`--journeys`) are the fairer reading of "was this victim protected": 94.3% over all 720 test-day victims. The kit samples are small, so their intervals are wide; the headline accuracy claims come from the full held-out set (Error rates below).
