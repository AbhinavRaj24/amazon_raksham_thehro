**Measured, synthetic** (test days 42–59; model hgb-86it-677lab-nocallerid, policy v4; lines from the validation week each time). These are the two model-side choices made for the finale, kept here so they can be rerun.

**A. Adversarial training: tried and rejected.** Attacked copies of the training-day scams were added to the fused model's training data.

|  | Victims warned | At first payment | False pauses /10k | Chat-only | Split | Aged mules | Clean mules | All at once |
|---|---|---|---|---|---|---|---|---|
| shipped (no augmentation) | 96.0% | 74.4% | 1.04 | 95.4% | 97.5% | 88.2% | 79.4% | 88.2% |
| + all four attacked copies | 94.2% | 67.8% | 0.92 | 94.3% | 96.8% | 85.8% | 100.0% | 100.0% |
| + chat and hooks copies only (clean mules held out) | 94.9% | 68.6% | 1.14 | 94.9% | 97.1% | 87.6% | 80.3% | 90.0% |

With the clean-mule copies in training the clean-mule and combined attacks look solved (100% and 100%). That is the model learning the edit's fixed values (an account exactly 900 days old, idle exactly 2 days), not the scam: with those copies held out, clean mules move only from 79.4% to 80.3%. And both versions are worse on real, unattacked scams: victims warned -1.8 and -1.1 points, warned at the first payment -6.7 and -5.8 points. So the shipped model has no augmented training.

**B. The journey-only pause line: chosen (policy v4).** Share of the 1-in-10,000 pause budget given to the journey line, with the total fixed:

|  | Victims warned | At first payment | False pauses /10k | Chat-only | Split | Aged mules | Clean mules | All at once |
|---|---|---|---|---|---|---|---|---|
| share 0.0 | 95.1% | 75.6% | 1.03 | 94.6% | 96.2% | 85.6% | 73.3% | 78.8% |
| share 0.1 | 95.8% | 75.3% | 1.02 | 95.3% | 96.9% | 86.9% | 76.0% | 84.0% |
| share 0.2 | 95.7% | 74.3% | 0.94 | 95.1% | 97.2% | 86.8% | 77.1% | 85.0% |
| share 0.3 (shipped) | 96.0% | 74.4% | 1.04 | 95.4% | 97.5% | 88.2% | 79.4% | 88.2% |
| share 0.4 | 95.0% | 72.9% | 0.92 | 94.6% | 97.1% | 87.2% | 78.6% | 87.8% |

The shipped share is 0.3, picked after looking at these test-day numbers. Share 0.2 is the smallest that passes the slide-10 line, with the combined attack at 85.0%; the shipped 0.3 reaches 88.2%, a margin over the line, for 1.04 false pauses per 10,000 on the test days. Because the choice used the test days, it is checked again on a freshly simulated world before any number is quoted as final.
