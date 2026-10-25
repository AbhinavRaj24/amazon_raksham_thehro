**Measured, synthetic** (test days 42–59, the same frozen model hgb-86it-677lab-nocallerid for every version; each version's alert lines are calibrated for it on the same validation week and budgets; only the policy rules differ). The cost to genuine customers who cancel is **modelled**.

- **v1** is Round 1's policy.
- **v2** adds retry memory: after a warning the customer didn't go ahead with (cancelled, called the bank, trusted person declined, or no answer), a payment to a new payee, or to the cancelled one, within 24 hours is at least a pause.
- **v3** adds two rules. A first payment to a payee with an outside flag (cybercrime registry, DoT high risk, or a live mule network) is at least a pause. And memory raises a payment to a trusted check only if it is ₹50,000+ or the payee is flagged; otherwise to a pause.
- **v4** (the finale) adds a journey-only pause line: 30% of the pause budget goes to the journey family's own score, so a scam-like journey is paused even when the payee looks clean and the phone signals are silent (the fused line keeps the other 70%, so the total budget is the same). It also drops caller identity, which a bank app can't read: the caller's DoT rating no longer counts as context evidence.

| | v1 (Round 1) | v2 (+ retry memory) | v3 (+ flagged payee, memory cap) | v4 (+ journey line, no caller id) |
|---|---|---|---|---|
| Victims warned | 94.3% (92.4–95.8%) | 94.3% (92.4–95.8%) | 95.1% (93.3–96.5%) | 96.0% (94.3–97.2%) |
| Warned at their first scam payment | 70.4% | 70.4% | 75.6% | 74.4% |
| Scam payments paused | 86.2% | 86.2% | 88.7% | 88.7% |
| Scam payments held | 48.1% | 48.1% | 48.4% | 46.9% |
| False pauses per 10,000 | 1.00 (0.80–1.25; 79) | 1.00 (0.80–1.25; 79) | 1.03 (0.82–1.28; 81) | 1.04 (0.83–1.29; 82) |
| Genuine trusted checks / holds | 17 / 0 | 17 / 0 | 7 / 0 | 6 / 0 |
| Money protected (modelled) | 92.6% | 92.6% | 93.3% | 93.2% |
| **Cancel-and-retry attack: retries stopped** (n = 658) | 80.7% (77.5–83.5%) | 100.0% (99.4–100.0%) | 100.0% (99.4–100.0%) | 98.8% (97.6–99.4%) |
| Retry money stopped | 90.8% | 100.0% | 100.0% | 99.7% |
| … of which the version itself warned the first payment | 658 of 658 | 658 of 658 | 658 of 658 | 640 of 658 |
| … retries stopped after a warning it gave | 80.7% | 100.0% | 100.0% | 100.0% |

When customers go ahead after warnings (the usual evaluation path) v1 and v2 give identical results on every test-day payment: retry memory only acts when a customer doesn't go ahead. The retries are planned from the payments Round 1's policy warned; a version with a stricter line may not warn some of them, and then there is no warning to cancel and no retry memory, which is why the last two rows are shown.

**Cost to genuine customers who cancel (v4, modelled):** of the 82 false alarms, 1 was followed by a payment to a new payee within 24 hours. If every one were cancelled, retry memory would add 1 pause (1.06 per 10,000 in all). At the assumed cancel rates (8% after a pause, 3% after a trusted check, 1% after a hold) it adds 0.08 (1.04 per 10,000).
