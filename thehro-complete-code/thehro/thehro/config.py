"""Shared constants: time split, alert policy budgets and real-world base rates.

Every number here is a design choice or a public figure; the README lists the
source or the reasoning for each.
"""

MIN_PER_DAY = 1440.0

# ---------------------------------------------------------------------------
# Time split (simulated days). Labels arrive late, the way victim complaints do.
# ---------------------------------------------------------------------------
WARMUP_END = 14            # days 0-13: build each customer's normal profile, no scoring
TRAIN_END = 35             # train on payments from days 14-34 ...
LABEL_CUTOFF_TRAIN = 38    # ... labelled only by complaints filed before day 38
VALID_END = 42             # days 35-41: set alert thresholds against the alert budget
# days 42-59: test, scored against ground truth (including scams never reported)
#
# Thresholds are set on the validation week's genuine payments. In the real world
# more than 99.99% of payments are genuine, so a bank simply sets them on all of
# last week's payments. Our synthetic world holds 150-300x more scams than reality
# (so there are enough to measure), and leaving them in would distort the
# thresholds, so they are set on its genuine payments: what "all payments" means
# at real-world prevalence.

# ---------------------------------------------------------------------------
# Alert policy
# ---------------------------------------------------------------------------
# Budgets are shares of ALL payments a customer makes (shop payments included),
# measured on genuine payments. 1e-4 = one interruption per 10,000 payments.
ALERT_BUDGET_T2 = 1e-4     # Tier 2+ (pause and explain, or stronger)
JOURNEY_LINE_SHARE = 0.3            # policy v4: 30% of the tier-2 budget goes to a journey-only pause line, so
                                    # silencing the payee and phone signals can't hide a scam-like journey; the
                                    # fused line gets the other 70%, so the total budget is unchanged
NUDGE_BUDGET_T1 = 1e-3     # Tier 1 (one-line, non-blocking note)
REVIEW_BUDGET_T4 = 1e-5    # Tier 4 (payment held for human review)
TRUSTED_CHECK_MIN_AMOUNT = 50_000   # Tier 3 applies to high-risk payments of Rs 50,000+
                                    # (same line as RBI's April 2026 trusted-person option)
CALLBACK_SLA_MINUTES = 15           # tier 3 without a trusted person: the bank calls back within 15 min
                                    # (if it can't reach them, they can still wait the hour)
HOLD_MIN_AMOUNT = 10_000            # Tier 4 holds only payments of Rs 10,000+ (or any amount
                                    # after the customer continued past a warning)
FAMILY_ELEVATED_PCTL = 99.0         # a signal family is "elevated" above this percentile
                                    # of genuine scored payments in validation
OVERRIDE_MEMORY_DAYS = 7            # after a warning is overridden, keep escalating for 7 days
RETRY_MEMORY_HOURS = 24.0           # policy v2 (finale): after a tier 2+ warning the customer did NOT go ahead
                                    # with (cancelled, called the bank, trusted person declined, or no answer),
                                    # a payment to a new payee, or to the payee they cancelled, within 24 h is at
                                    # least a pause. 0 gives Round 1's policy (v1).

TIER_NAMES = {
    0: "Allow",
    1: "Note",
    2: "Pause and explain",
    3: "Trusted check",
    4: "Hold for review",
}

# ---------------------------------------------------------------------------
# Real-world base rates used to translate simulation results
# ---------------------------------------------------------------------------
# UPI FY2024-25: 12.64 lakh reported frauds / 18,586.60 crore payments (Lok Sabha replies).
REPORTED_FRAUD_RATE = 12.64e5 / 18586.60e7      # ~6.8 per million payments
# LocalCircles: 51% of UPI-fraud victims never complain, so true rate is ~2x reported.
ADJUSTED_FRAUD_RATE = 2 * REPORTED_FRAUD_RATE   # ~13.6 per million payments

# Assumed share of scam victims who stop when shown each tier. Anchored on the
# CeDEx experiment (risk-based screening + one-tap actions cut scam payments by
# 81%) and discounted for scammers coaching victims live. Used only to estimate
# "value protected"; they are assumptions to be tested in an A/B pilot.
STOP_PROB = {
    2: {"investment": 0.55, "digital_arrest": 0.35, "task": 0.60, "customer_care": 0.70,
        "marketplace": 0.75, "voice_clone": 0.65, "loan_fee": 0.60},
    3: {"investment": 0.85, "digital_arrest": 0.75, "task": 0.85, "customer_care": 0.90,
        "marketplace": 0.90, "voice_clone": 0.90, "loan_fee": 0.85},
    4: {"investment": 0.95, "digital_arrest": 0.92, "task": 0.95, "customer_care": 0.97,
        "marketplace": 0.97, "voice_clone": 0.97, "loan_fee": 0.95},
}
GENUINE_ABANDON_PROB = {2: 0.08, 3: 0.03, 4: 0.01}  # genuine payments dropped after an alert
