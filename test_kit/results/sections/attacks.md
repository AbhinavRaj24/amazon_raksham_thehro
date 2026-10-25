**Measured, synthetic; money protected is modelled.** Scammers' events on the test days are edited, the features rebuilt with the training code (`features.build_features`) and scored with the **frozen** model and lines (model hgb-86it-677lab-nocallerid, policy v4). Genuine customers are not edited. Seed 7 for every random choice (`redteam.py`).

| Attack | Victims warned | Warned at first payment | Scam payments paused | False pauses | Money protected (modelled) | Slide-10 pass line |
|---|---|---|---|---|---|---|
| No edits (rebuild check) | 96.0% (94.3%–97.2%), 691/720 | 74.4% (71.1%–77.5%), 536/720 | 88.7% (87.4%–89.8%), 2,376/2,680 | 1.04 per 10,000 (0.83–1.29; 82 of 786,121) | 93.2% |  |
| Chat-only coaching (no calls, no screen share) | 95.4% (93.6%–96.7%), 687/720 | 71.9% (68.6%–75.1%), 518/720 | 87.8% (86.5%–89.0%), 2,353/2,680 | 1.04 per 10,000 (0.83–1.29; 82 of 786,121) | 91.7% | pass: ≥ 75% warned |
| Split payments just under Rs 10,000 | 97.5% (96.1%–98.4%), 702/720 | 69.4% (66.0%–72.7%), 500/720 | 87.8% (87.4%–88.2%), 20,193/23,003 | 0.99 per 10,000 (0.79–1.23; 80 of 806,444) | 96.6% | pass: ≥ 75% warned |
| Aged mules (old, recently active, forward 50-70% after 6-48 h) | 88.2% (85.6%–90.4%), 635/720 | 47.9% (44.3%–51.6%), 345/720 | 71.7% (70.0%–73.4%), 1,922/2,680 | 1.02 per 10,000 (0.81–1.27; 80 of 786,121) | 82.3% | pass: ≥ 75% warned |
| Fresh, clean mule for every payment (feature-level) | 79.4% (76.3%–82.2%), 572/720 | 28.6% (25.4%–32.0%), 206/720 | 58.1% (56.3%–60.0%), 1,558/2,680 | 1.00 per 10,000 (0.80–1.25; 79 of 786,121) | 73.6% | pass: ≥ 75% warned |
| All of the above at once | 88.2% (85.6%–90.4%), 635/720 | 17.2% (14.6%–20.2%), 124/720 | 51.0% (50.3%–51.6%), 11,724/23,003 | 0.98 per 10,000 (0.78–1.22; 79 of 806,444) | 90.9% | pass: ≥ 85% warned at ≤ 1 false pause per 10,000 |
| Cancel and retry smaller to another payee (line probing) | – | – | – | – | – | pass: retries stopped 80.7% with Round 1's policy, 98.8% with policy v4 (n = 658; next section) |

Rebuild check: with no edits the rebuilt world gives 691/720 victims warned and 82 false pauses (identical to the baseline).

What each attack changes:

- *Chat-only coaching (no calls, no screen share)*: chat_only: scam_payments_edited 2680
- *Split payments just under Rs 10,000*: split: payments_split 1763, parts_created 22086, median_parts_per_payment 4, max_parts_per_payment 506, parts_9000_to_9900_share 0.863, parts_dropped_after_day_60 0, amount_dropped_after_day_60 0
- *Aged mules (old, recently active, forward 50-70% after 6-48 h)*: aged_mules: mule_accounts_aged 1297, forwards_delayed_and_cut 3398, forwards_pushed_past_day_60 135
- *Fresh, clean mule for every payment (feature-level)*: clean_mules: scam_payments_edited 2680, note feature-level: evaluate.perturb(ft, 'clean_mules')
- *All of the above at once*: chat_only: scam_payments_edited 2680; split: payments_split 1763, parts_created 22086, median_parts_per_payment 4, max_parts_per_payment 506, parts_9000_to_9900_share 0.863, parts_dropped_after_day_60 0, amount_dropped_after_day_60 0; aged_mules: mule_accounts_aged 1297, forwards_delayed_and_cut 3398, forwards_pushed_past_day_60 135; clean_mules: scam_payments_edited 23003, note feature-level: evaluate.perturb(ft, 'clean_mules')

The feature-level stress tests from `run_all.py`, for comparison (`outputs/tables/stress_tests.csv`, model not retrained):

| Stress test | Victims warned | False pauses per 10,000 |
|---|---|---|
| As simulated | 96.0% | 1.04 |
| Scammers stop calling (chat only, no screen share) | 95.4% | 1.04 |
| Fresh, clean-looking mule for every payment, no outside flags | 79.4% | 1.00 |
| No FD break and no small credits first | 94.2% | 1.04 |
| All three at once | 65.3% | 0.97 |

**Reading the results.** False pauses stay between 0.98 and 1.04 per 10,000 (baseline 1.04), because only the scammers change. The weakest single tactic is "Fresh, clean mule for every payment (feature-level)" at 79.4% of victims warned (baseline 96.0%): it silences the payee family, including every outside flag. Splitting under ₹10,000 does not help the scammer (97.5% warned): every extra part to the same new payee adds to the 7-day series features, so the run is seen sooner. All tactics together give 88.2%, which passes. This combination does not include "no FD break and no small credits", so it is not the same test as Round 1's "all three at once" (58.3%), which did. Cancel and retry smaller (line probing) needs customers who cancel, which this replay never has, so it is measured on the live path in the next section: 80.7% of retries were stopped with Round 1's policy and 98.8% with policy v4's retry memory.
