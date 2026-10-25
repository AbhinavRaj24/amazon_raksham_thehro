**Measured, synthetic, on a world no design choice ever saw** (seed 25, fixed before the first run; same generator and size as seed 7; 788,013 customer payments on its test days, 868 scam victims). *Frozen* systems keep their seed-7 model, lines and policy, untouched: the deployment question. *Retrained* recipes rerun the same code from scratch on this world: the "did our choices fit seed 7?" question. Nothing is tuned here.

| System | Policy | Victims warned (95% CI) | At first payment | False pauses /10k | Genuine trusted checks | Genuine holds | Money protected (modelled) |
|---|---|---|---|---|---|---|---|
| Round 1 system, frozen | v1 | 92.3% (90.3%–93.9%) | 66.6% | 0.96 | 6 | 0 | 91.3% |
| Finale system, frozen | v4 | 94.4% (92.6%–95.7%) | 69.7% | 0.98 | 3 | 0 | 91.7% |
| Round 1 recipe, retrained here | v1 | 94.0% (92.2%–95.4%) | 68.1% | 1.24 | 19 | 0 | 92.2% |
| Finale recipe, retrained here | v4 | 94.1% (92.4%–95.5%) | 69.2% | 1.37 | 9 | 0 | 91.1% |

Under the attacks (victims warned; the attack seed is the same as on seed 7):

| System | Chat-only | Split | Aged mules | Clean mules | All at once | False pauses /10k (all at once) | Slide-10 lines: combined / single |
|---|---|---|---|---|---|---|---|
| Round 1 system, frozen | 90.4% | 94.5% | 70.3% | 67.7% | 70.9% | 0.87 | FAIL / FAIL |
| Finale system, frozen | 93.2% | 96.8% | 81.3% | 72.4% | 82.6% | 0.94 | FAIL / FAIL |
| Round 1 recipe, retrained here | 92.7% | 95.4% | 70.2% | 73.5% | 79.6% | 1.29 | FAIL / FAIL |
| Finale recipe, retrained here | 93.3% | 96.1% | 82.4% | 75.7% | 85.8% | 1.47 | FAIL / pass |

**Reading.** round 1 system, frozen: 70.9% of victims warned under the combined attack at 0.87 false pauses per 10,000 (fails: under 85% of victims); finale system, frozen: 82.6% of victims warned under the combined attack at 0.94 false pauses per 10,000 (fails: under 85% of victims); round 1 recipe, retrained here: 79.6% of victims warned under the combined attack at 1.29 false pauses per 10,000 (fails: under 85% of victims and over 1 false pause per 10,000); finale recipe, retrained here: 85.8% of victims warned under the combined attack at 1.47 false pauses per 10,000 (fails: over 1 false pause per 10,000). Frozen, the finale system warns +2.1 points more victims than Round 1's on this world and +2.3 to +11.8 points more under each attack. These, not the seed-7 test days (which were used to make choices), are the numbers to quote as final, including the misses.
