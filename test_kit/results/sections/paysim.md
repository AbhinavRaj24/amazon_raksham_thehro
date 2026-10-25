**Measured on PaySim** (public synthetic mobile-money data, `PS_20174392719_1491204439457_log.csv`). Trained on steps 1–400 (5,787,030 transactions, 4,477 frauds), lines set on steps 401–500 using genuine transactions only, tested on steps 501–744 (300,813 transactions, 2,652 frauds). Features use only what is known before each transaction: never the after-balances (`newbalanceOrig`, `newbalanceDest`, a known leak) and never `isFlaggedFraud`.

| Method | Frauds caught | False alarms | Precision | Money caught |
|---|---|---|---|---|
| Thehro method, line at 1 in 1,000 | 99.4% (99.1%–99.7%), 2,637/2,652 | 9.61 per 10,000 (8.53–10.78; 289 of 300,813) | 90.1% (89.0%–91.2%), 2,637/2,926 | 100.0% |
| Thehro method, line at 1 in 10,000 | 91.4% (90.3%–92.4%), 2,425/2,652 | 0.80 per 10,000 (0.51–1.19; 24 of 300,813) | 99.0% (98.5%–99.3%), 2,425/2,449 | 99.3% |
| PaySim's own isFlaggedFraud (transfers over 200,000) | 0.4% (0.2%–0.7%), 10/2,652 | 0.00 per 10,000 (0.00–0.12; 0 of 300,813) | 100.0% (72.2%–100.0%), 10/10 | 1.2% |
| TRANSFER or CASH_OUT that empties the account | 97.2% (96.5%–97.8%), 2,578/2,652 | 1961.28 per 10,000 (1945.49–1977.18; 58,998 of 300,813) | 4.2% (4.0%–4.3%), 2,578/61,576 | 84.5% |
| Amount over 200,000 | 67.1% (65.3%–68.9%), 1,780/2,652 | 2458.20 per 10,000 (2440.52–2475.99; 73,946 of 300,813) | 2.4% (2.2%–2.5%), 1,780/75,726 | 98.2% |

**What this shows and what it can't.** It shows the method (point-in-time features, gradient-boosted trees, alert lines set to a fixed false-alarm budget on genuine traffic) works on data we did not make, and beats simple rules at the same or lower false-alarm rate. PaySim fraud is account takeover by the fraudster, so it cannot test the scam signals: there are no calls, no coaching of a victim who pays with their own PIN, no customer history (`nameOrig` almost never repeats) and no mule networks across banks. Those are tested only on our synthetic data.
