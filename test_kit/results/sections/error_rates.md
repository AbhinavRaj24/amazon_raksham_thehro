**Measured, synthetic**, unless marked. Payment-level confusion matrix (rows: truth; columns: the tier the payment reached):

| Truth | tier 0 Allow | tier 1 Note | tier 2 Pause and explain | tier 3 Trusted check | tier 4 Hold for review | total |
|---|---|---|---|---|---|---|
| scam | 154 | 150 | 933 | 185 | 1,258 | 2,680 |
| genuine | 247,474 | 557 | 76 | 6 | 0 | 248,113 |

Victim level (warned = at least one tier 2+ payment in the journey):

| Scam type | Warned | Not warned |
|---|---|---|
| investment | 185 | 6 |
| digital_arrest | 100 | 3 |
| task | 118 | 0 |
| customer_care | 68 | 3 |
| marketplace | 28 | 6 |
| voice_clone | 41 | 4 |
| loan_fee | 151 | 7 |
| all | 691 | 29 |

Error rates:

| Rate | Value (95% CI, n) | Interval | Kind |
|---|---|---|---|
| Victims warned (tier 2+ at least once) | 96.0% (94.3%–97.2%), 691/720 | Wilson | measured |
| Victims warned at their first scam payment | 74.4% (71.1%–77.5%), 536/720 | Wilson | measured |
| Scam payments paused (tier 2+) | 88.7% (87.4%–89.8%), 2,376/2,680 | Wilson | measured |
| False pauses (genuine tier 2+) per 10,000 customer payments | 1.04 per 10,000 (0.83–1.29; 82 of 786,121) | exact Poisson | measured |
| Genuine trusted checks (tier 3) | 0.08 per 10,000 (0.03–0.17; 6 of 786,121) | exact Poisson | measured |
| Genuine holds (tier 4) | 0 of 786,121; 95% upper bound 1 in 213,106 | exact Poisson | measured |
| Customers never interrupted in 18 days | 99.80% (81 of 40,000 interrupted) | – | measured |
| Share of pauses that are real scams at India's estimated true fraud rate (13.6 per million) | 10.4% (about 1 in 10) | – | estimated |
| Same, at the reported rate (6.8 per million) | 5.5% (about 1 in 18) | – | estimated |
| Money protected | 93.2% | – | modelled (assumed stop rates) |

By scam type:

| Scam type | Victims | Warned (95% CI) | Warned at first payment | Scam payments paused |
|---|---|---|---|---|
| investment | 191 | 96.9% (93.3%–98.6%), 185/191 | 66.0% (59.0%–72.3%), 126/191 | 87.6% |
| digital_arrest | 103 | 97.1% (91.8%–99.0%), 100/103 | 92.2% (85.4%–96.0%), 95/103 | 96.5% |
| task | 118 | 100.0% (96.8%–100.0%), 118/118 | 94.9% (89.3%–97.6%), 112/118 | 96.7% |
| customer_care | 71 | 95.8% (88.3%–98.6%), 68/71 | 81.7% (71.2%–89.0%), 58/71 | 90.8% |
| marketplace | 34 | 82.4% (66.5%–91.7%), 28/34 | 35.3% (21.5%–52.1%), 12/34 | 70.1% |
| voice_clone | 45 | 91.1% (79.3%–96.5%), 41/45 | 88.9% (76.5%–95.2%), 40/45 | 91.5% |
| loan_fee | 158 | 95.6% (91.1%–97.8%), 151/158 | 58.9% (51.1%–66.2%), 93/158 | 82.4% |

By group:

**Age** (never a model input; used only for this check)

| Group | False pauses per 10,000 (95% CI; count) | × overall | Victims warned (95% CI) |
|---|---|---|---|
| 18-25 | 0.93 per 10,000 (0.48–1.62; 12 of 129,375) | 0.89 | 95.5% (89.9%–98.1%), 106/111 |
| 26-40 | 0.89 per 10,000 (0.61–1.27; 31 of 346,456) | 0.86 | 94.9% (91.8%–96.8%), 296/312 |
| 41-59 | 1.36 per 10,000 (0.92–1.93; 31 of 228,212) | 1.30 | 96.9% (93.5%–98.6%), 189/195 |
| 60+ | 0.97 per 10,000 (0.42–1.92; 8 of 82,078) | 0.93 | 98.0% (93.1%–99.5%), 100/102 |

**Language** (never a model input; used only for this check)

| Group | False pauses per 10,000 (95% CI; count) | × overall | Victims warned (95% CI) |
|---|---|---|---|
| hi | 0.99 per 10,000 (0.69–1.38; 34 of 343,295) | 0.95 | 95.3% (92.4%–97.1%), 304/319 |
| en | 1.00 per 10,000 (0.50–1.79; 11 of 109,906) | 0.96 | 98.6% (94.9%–99.6%), 138/140 |
| ta | 0.95 per 10,000 (0.35–2.06; 6 of 63,465) | 0.91 | 97.1% (89.9%–99.2%), 66/68 |
| te | 1.21 per 10,000 (0.52–2.39; 8 of 66,050) | 1.16 | 100.0% (72.2%–100.0%), 10/10 |
| bn | 1.08 per 10,000 (0.43–2.22; 7 of 64,945) | 1.03 | 98.6% (92.4%–99.8%), 70/71 |
| mr | 1.58 per 10,000 (0.79–2.83; 11 of 69,555) | 1.52 | 91.8% (83.2%–96.2%), 67/73 |
| kn | 0.26 per 10,000 (0.01–1.45; 1 of 38,504) | 0.25 | 90.9% (76.4%–96.9%), 30/33 |
| gu | 1.32 per 10,000 (0.36–3.37; 4 of 30,401) | 1.26 | 100.0% (61.0%–100.0%), 6/6 |

**Region** (never a model input; used only for this check)

| Group | False pauses per 10,000 (95% CI; count) | × overall | Victims warned (95% CI) |
|---|---|---|---|
| metro | 1.20 per 10,000 (0.82–1.68; 33 of 275,931) | 1.15 | 96.9% (94.0%–98.4%), 251/259 |
| tier-2 | 1.00 per 10,000 (0.68–1.41; 31 of 311,513) | 0.95 | 93.6% (90.1%–95.9%), 262/280 |
| rural | 0.91 per 10,000 (0.54–1.43; 18 of 198,677) | 0.87 | 98.3% (95.2%–99.4%), 178/181 |

**Consent** (never a model input; used only for this check)

| Group | False pauses per 10,000 (95% CI; count) | × overall | Victims warned (95% CI) |
|---|---|---|---|
| not opted in | 1.10 per 10,000 (0.72–1.62; 26 of 235,580) | 1.06 | 96.2% (92.7%–98.1%), 204/212 |
| opted in | 1.02 per 10,000 (0.77–1.32; 56 of 550,541) | 0.98 | 95.9% (93.8%–97.3%), 487/508 |

Every value above was checked against `outputs/metrics.json` and `outputs/tables/` (the numbers the deck quotes) before this section was written. Small groups (Telugu 10 victims, Gujarati 6) have wide intervals.
