**Measured, synthetic.** Each row switches one signal off for every payment on the test days and reruns the frozen model and lines. Gain = baseline minus switched-off: a positive victims gain means the signal helps warn victims; a negative false-pause gain means switching it off *adds* false pauses.

| Switched off | Victims warned | False pauses | Genuine trusted checks | Genuine holds | Scam payments paused | Gain: victims warned | Gain: false pauses |
|---|---|---|---|---|---|---|---|
| Nothing switched off (baseline) | 96.0% (94.3%–97.2%), 691/720 | 1.04 per 10,000 (0.83–1.29; 82 of 786,121) | 6 | 0 | 88.7% | – | – |
| Phone signals off (nobody opted in) | 95.7% (94.0%–97.0%), 689/720 | 1.26 per 10,000 (1.02–1.53; 99 of 786,121) | 6 | 0 | 89.2% | +0.3 points | -0.22 |
| Mule-network flag off | 93.2% (91.1%–94.8%), 671/720 | 0.98 per 10,000 (0.77–1.22; 77 of 786,121) | 6 | 0 | 79.9% | +2.8 points | +0.06 |
| Outside feeds off (registry, telecom FRI) | 95.1% (93.3%–96.5%), 685/720 | 1.02 per 10,000 (0.81–1.27; 80 of 786,121) | 6 | 0 | 86.6% | +0.8 points | +0.03 |
| Hooks off (FD break, small credits from strangers) | 94.2% (92.2%–95.7%), 678/720 | 0.84 per 10,000 (0.65–1.07; 66 of 786,121) | 6 | 0 | 84.9% | +1.8 points | +0.20 |
| Memory off (no escalation after Continue) | 96.0% (94.3%–97.2%), 691/720 | 0.87 per 10,000 (0.67–1.10; 68 of 786,121) | 6 | 0 | 84.6% | +0.0 points | +0.18 |
| Two-family gate off (one kind of evidence can delay money) | 96.0% (94.3%–97.2%), 691/720 | 1.04 per 10,000 (0.83–1.29; 82 of 786,121) | 15 | 0 | 88.7% | +0.0 points | +0.00 |

- **Phone signals:** without them 95.7% of victims are still warned, but false pauses rise from 1.04 to 1.26 per 10,000. The model seems to use phone context to *clear* risky-looking genuine payments too (for example a large first payment made with no call in progress), the same effect seen for customers who never opted in (1.10 per 10,000 in the fairness table). That is a real cost of not opting in, borne by the customer.
- **Two-family gate:** with it off, 15 genuine payments would wait for a trusted person and 0 would be held for an officer, against 6 and 0 with it. The gate is what keeps a single kind of evidence from delaying money.
- **Memory** (after Continue, the next 7 days are stricter): it changes victims warned by +0.0 points, because it acts only on customers already warned once. Its job is later payments: without it, money protected (modelled) is 92.7% instead of 93.2% (+0.5 points). The cost is +0.18 false pauses per 10,000 and +0 genuine trusted checks, from genuine customers who had continued past an earlier warning.
- **Mule-network flag:** without it 93.2% of victims are warned (+2.8 points of gain, the largest of any signal).
