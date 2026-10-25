**Measured, synthetic** (frozen model hgb-86it-677lab-nocallerid, policy v4, test days 42–59). Every missed victim and every false pause belongs to exactly one group below; the full lists are in `results/failure_cases.json`.

### Victims we never warned: 29 of 720

Groups are assigned in this order, first match wins.

| Group | Victims | Scam types | Got a note / nothing | Not opted in | On a call | Money lost |
|---|---|---|---|---|---|---|
| One small payment (under ₹10,000) and nothing else on file | 10 | investment 4, marketplace 4, customer_care 1, loan_fee 1 | 3 / 7 | 2 | 3 | ₹40,701 |
| One larger payment to a payee with no outside flag | 9 | digital_arrest 3, voice_clone 3, customer_care 2, investment 1 | 7 / 2 | 5 | 2 | ₹8,78,344 |
| A short run of small payments (all under ₹10,000), payee never flagged | 7 | loan_fee 5, marketplace 2 | 5 / 2 | 1 | 4 | ₹54,489 |
| Several larger payments that stayed just under the pause line, payee never flagged | 3 | investment 1, voice_clone 1, loan_fee 1 | 3 / 0 | 0 | 3 | ₹2,11,248 |

**One small payment (under ₹10,000) and nothing else on file** (10). The whole scam was a single small payment. With no history building up and no flag on the payee, there is almost nothing to separate it from the many genuine first payments of that size.

*Example:* journey 391, investment, 1 payment(s), ₹7,109 in total. Its closest step had fused risk 0.0008 against a pause line of 0.1058 and ended at tier 0 Allow. Reasons it would show: First payment to this account, 10x your usual payment; You switched between apps 4 times during this payment; This account had no activity for 6 months before today.

**One larger payment to a payee with no outside flag** (9). A single larger payment to an account that had never been reported and was not in a detected mule network. Without a second payment, the journey signals (rising amounts, several new payees) never appear, and the payee family has nothing to go on.

*Example:* journey 508, digital_arrest, 1 payment(s), ₹5,03,724 in total. Its closest step had fused risk 0.0807 against a pause line of 0.1058 and ended at tier 1 Note. Reasons it would show: First payment to this account, 760x your usual payment; This account had no activity for 27 months before today.

**A short run of small payments (all under ₹10,000), payee never flagged** (7). Two to five small payments, often 'fees' or 'test' amounts, to clean-looking accounts. Each one is small enough to look like everyday spending, and the run ended before the series features or the mule-network detector caught up. Loan-fee and marketplace scams make up most of this group.

*Example:* journey 1597, loan_fee, 2 payment(s), ₹11,355 in total. Its closest step had fused risk 0.0862 against a pause line of 0.1058 and ended at tier 1 Note. Reasons it would show: no reason codes were true.

**Several larger payments that stayed just under the pause line, payee never flagged** (3). Several larger payments, but each step stayed just under the pause line: the payee was never flagged and the growth from one payment to the next was gradual. The memory rule only escalates after a warning, so it never started.

*Example:* journey 1421, voice_clone, 2 payment(s), ₹1,36,794 in total. Its closest step had fused risk 0.0964 against a pause line of 0.1058 and ended at tier 1 Note. Reasons it would show: no reason codes were true.

### False pauses: 82 genuine payments interrupted

1.04 per 10,000 (0.83–1.29; 82 of 786,121) customer payments, shop payments included.

| Genuine kind | False pauses | Per 10,000 of that kind (95% CI) | Trusted checks | Holds | On a call | After Continue (memory) | Top reasons |
|---|---|---|---|---|---|---|---|
| new_individual | 55 | 38.2 (28.8–49.7) of 14,410 | 2 | 0 | 24 | 11 | NEW_PAYEE_LARGE 32, STRANGER_CREDITS 9, NONE 3 |
| supplier | 14 | 4.3 (2.3–7.2) of 32,732 | 0 | 0 | 1 | 2 | NEW_PAYEE_LARGE 9, STRANGER_CREDITS 3, FRI 1 |
| medical | 6 | 705.9 (259.0–1536.4) of 85 | 2 | 0 | 5 | 0 | NEW_PAYEE_LARGE 3, FD_BREAK 2, STRANGER_CREDITS 1 |
| new_landlord | 4 | 168.1 (45.8–430.3) of 238 | 2 | 0 | 3 | 1 | NEW_PAYEE_LARGE 3, APP_SWITCHING 1 |
| gray_invest | 3 | 566.0 (116.7–1654.2) of 53 | 0 | 0 | 1 | 0 | NEW_PAYEE_LARGE 2, FD_BREAK 1 |

By call: not on a call 48, on a call 34. By the top reason shown: NEW_PAYEE_LARGE 49, STRANGER_CREDITS 13, FD_BREAK 4, APP_SWITCHING 3, NONE 3, FRI 2, CAMPAIGN 2, DORMANT 2, PASS_THROUGH 1, ESCALATING 1, DAY_OUTFLOW 1, NIGHT_NEW 1.

- *new_individual*, e.g. payment 2770698, ₹51,288, tier 3 Trusted check: "First payment to this account, 43x your usual payment; You are on an internet call (9 min so far)".
- *supplier*, e.g. payment 2008294, ₹18,444, tier 2 Pause and explain: "First payment to this account, 20x your usual payment; You have sent 43x your usual daily amount to people today".
- *medical*, e.g. payment 2080729, ₹64,759, tier 3 Trusted check: "First payment to this account, 20x your usual payment; You are on an internet call (23 min so far)".
- *new_landlord*, e.g. payment 2007574, ₹66,626, tier 3 Trusted check: "First payment to this account, 8x your usual payment; You are on a phone call (32 min so far)".
- *gray_invest*, e.g. payment 2009572, ₹1,45,049, tier 2 Pause and explain: "First payment to this account, 158x your usual payment".

**The way out for these customers.** 76 of the 82 were a pause: the customer sees three facts, waits 30 seconds and can press Continue, so the cost is about half a minute. 6 reached a trusted check (₹50,000+ with two kinds of evidence, or within 7 days of continuing past a warning): the payment waits for the trusted person or up to an hour, then the customer decides. 0 reached a hold, so no genuine payment was stopped for an officer. 14 of them were stricter than they would otherwise have been because the customer had continued past an earlier warning in the last 7 days.

### Weak spots measured by `run_all.py`

| Weak spot | Measured (synthetic) | Why | Source |
|---|---|---|---|
| Marketplace ('scan to receive') scams | 82% of victims warned, 35% at the first payment, 76% when the type is hidden from training (n = 34) | small amounts to clean accounts, often without a call; the 'pay to receive' QR trick is not modelled | `outputs/tables/per_workflow.csv, held_out_workflows.csv` |
| All three scammer tactics at once (no calls, clean mules, no hooks) | 65% of victims warned (model not retrained) | each tactic silences one kind of evidence; together they leave mostly the journey family | `outputs/tables/stress_tests.csv` |
| Scam-type guesser on the new loan-fee scam | right 0.3% of the time when it names a type (it names one for 72% of warned loan-fee payments) | it had only 2 loan-fee labels, so it confidently names a known type instead of saying 'unfamiliar'; officers would see a wrong label exactly when a new scam appears. It never changes the tier or the advice the customer sees | `outputs/metrics.json: recogniser.by_workflow.loan_fee` |
