**Measured, synthetic.** The shipped system (model hgb-86it-677lab-nocallerid, policy v4) uses no caller identity, because a bank app can't read the caller's number. The counterfactual is the same models trained *with* `caller_unknown` and `caller_fri_band` (same settings, labels and training days), the policy allowed to count the caller's DoT rating as context evidence, and lines set on the validation week only.

| Data | Victims warned (shipped) | Victims warned (with caller id) | False pauses (shipped) | False pauses (with caller id) |
|---|---|---|---|---|
| Test days (as simulated) | 96.0% (94.3%–97.2%), 691/720 | 95.7% (94.0%–97.0%), 689/720 | 1.04 per 10,000 (0.83–1.29; 82 of 786,121) | 1.00 per 10,000 (0.80–1.25; 79 of 786,121) |
| Chat-only coaching (no calls, no screen share) | 95.4% (93.6%–96.7%), 687/720 | 95.3% (93.5%–96.6%), 686/720 | 1.04 per 10,000 (0.83–1.29; 82 of 786,121) | 1.00 per 10,000 (0.80–1.25; 79 of 786,121) |
| Split payments just under Rs 10,000 | 97.5% (96.1%–98.4%), 702/720 | 97.4% (95.9%–98.3%), 701/720 | 0.99 per 10,000 (0.79–1.23; 80 of 806,444) | 1.02 per 10,000 (0.81–1.26; 82 of 806,444) |
| Aged mules (old, recently active, forward 50-70% after 6-48 h) | 88.2% (85.6%–90.4%), 635/720 | 88.3% (85.8%–90.5%), 636/720 | 1.02 per 10,000 (0.81–1.27; 80 of 786,121) | 0.98 per 10,000 (0.77–1.22; 77 of 786,121) |
| Fresh, clean mule for every payment (feature-level) | 79.4% (76.3%–82.2%), 572/720 | 80.1% (77.1%–82.9%), 577/720 | 1.00 per 10,000 (0.80–1.25; 79 of 786,121) | 0.97 per 10,000 (0.76–1.21; 76 of 786,121) |
| All of the above at once | 88.2% (85.6%–90.4%), 635/720 | 88.9% (86.4%–91.0%), 640/720 | 0.98 per 10,000 (0.78–1.22; 79 of 806,444) | 1.00 per 10,000 (0.80–1.25; 81 of 806,444) |

Caller identity would change victims warned by -0.3 points and false pauses by -0.04 per 10,000 on the test days. That is the measured price of staying deployable on a phone.

| Scam type | Victims warned (shipped) | Victims warned (with caller id) |
|---|---|---|
| investment | 96.9% | 96.9% |
| digital_arrest | 97.1% | 96.1% |
| task | 100.0% | 100.0% |
| customer_care | 95.8% | 95.8% |
| marketplace | 82.4% | 82.4% |
| voice_clone | 91.1% | 93.3% |
| loan_fee | 95.6% | 94.3% |

**Messages.** The advice fits the actual scam for 61% of warned scam payments in the shipped system (the call reason says only that a call is on, and for how long), and would for 61% with caller identity.
