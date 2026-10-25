**Measured, synthetic** (test days 42–59, frozen model hgb-86it-677lab-nocallerid, policy v4); the times are the product's own limits, not measured behaviour.

On the 18 test days, **82 of 786,121 genuine customer payments** were interrupted (1.04 per 10,000). That touched **81 of 40,000 customers**, about one false interruption per customer every 24 years. **No genuine payment was held.**

| What they saw | Payments | What the customer does | How long it takes |
|---|---|---|---|
| Pause and explain | 76 | reads the three reasons, waits 30 s, then Continue (or Cancel, or calls the bank) | about 35 seconds |
| Trusted check | 6 (none had a trusted person set up, so the bank would call each of them back) | asks their trusted person to approve; with no trusted person, gets a call-back from the bank; or waits one hour | minutes with a trusted person; at most 15 minutes for a call-back (bank's target); one hour at most |
| Hold for review | 0 | a bank officer calls within 30 minutes and releases it; the payee is then confirmed | at most 30 minutes (officer SLA) |

**The alert says why it fired.** The first reason each of them saw:

| First reason on the screen | Payments |
|---|---|
| First payment to this account, far above usual | 41 |
| An earlier warning (memory) | 14 |
| Small credits from unknown accounts first | 12 |
| A fixed deposit was just broken | 4 |
| Switching apps during the payment | 2 |
| Account's number rated high risk by DoT | 2 |
| First payment to this account (fallback fact) | 2 |
| Account is in a live mule network | 2 |
| Payments to new accounts keep growing | 1 |
| Much more sent today than usual | 1 |
| Late-night payment to a new account | 1 |

**Repeat interruptions:** 1 of the 81 customers was interrupted more than once. 14 of the 82 interruptions came from memory, and 14 of those were people in the middle of a real scam: they had gone ahead after a warning on their *own* scam payment, so their other payments got extra checks for a few days. That's protection doing its job. The rest are the real cost of memory for genuine customers.

**Getting past it for good:** a payee the customer went ahead with is confirmed after 7 days without any outside flag (0 genuine payments went through on the test days because of that). If a bank officer releases a held payment, the payee is confirmed for that customer at once (shown live in judge mode).
