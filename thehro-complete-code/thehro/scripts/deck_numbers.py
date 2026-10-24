"""Recompute every derived number shown in the deck from outputs/metrics.json.

    python scripts/deck_numbers.py

run_all.py saves the raw results (counts and shares). The deck also shows
numbers derived from them: 95% confidence intervals, "1 in N" precision,
the per-10-million-payments scenario and a few public-figure calculations.
This script prints each one with the formula used, so every figure on the
slides can be traced back to the saved output.
"""
from __future__ import annotations

import json
from pathlib import Path

from scipy.stats import chi2, norm

ROOT = Path(__file__).resolve().parents[1]
M = json.load(open(ROOT / "outputs" / "metrics.json"))
H = M["headline"]
Z = norm.ppf(0.975)  # 1.96 for a two-sided 95% interval


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson score interval for a share k/n (used for victim-level percentages)."""
    p = k / n
    den = 1 + Z**2 / n
    mid = (p + Z**2 / (2 * n)) / den
    half = Z * ((p * (1 - p) / n + Z**2 / (4 * n**2)) ** 0.5) / den
    return mid - half, mid + half


def poisson(k: int) -> tuple[float, float]:
    """Exact (Garwood) 95% interval for a Poisson count k (used for rare events)."""
    lo = 0.0 if k == 0 else chi2.ppf(0.025, 2 * k) / 2
    hi = chi2.ppf(0.975, 2 * k + 2) / 2
    return lo, hi


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def line(label: str, value: str, how: str) -> None:
    print(f"{label:<46} {value:<22} {how}")


print("=== Measured on the synthetic test days (days 42-59) ===")
n_vic = H["journeys"]
k_warn = round(H["victims_warned"] * n_vic)
lo, hi = wilson(k_warn, n_vic)
line("Victims warned (tier 2+)", f"{pct(k_warn / n_vic)} [{pct(lo)}-{pct(hi)}]", f"{k_warn}/{n_vic} victims, Wilson")

k_first = round(H["victims_warned_first_payment"] * n_vic)
lo, hi = wilson(k_first, n_vic)
line("Warned at the first scam payment", f"{pct(k_first / n_vic)} [{pct(lo)}-{pct(hi)}]", f"{k_first}/{n_vic}, Wilson")

N = H["test_customer_payments"]
k_fp = H["genuine_t2plus"]
lo, hi = poisson(k_fp)
line("False pauses per 10,000 genuine payments", f"{k_fp / N * 1e4:.2f} [{lo / N * 1e4:.2f}-{hi / N * 1e4:.2f}]",
     f"{k_fp} pauses / {N:,} customer payments, exact Poisson")

k_hold = H["genuine_t4plus"]
_, hi = poisson(k_hold)
line("Genuine payments held", f"{k_hold} (bound 1 in {N / hi:,.0f})", f"0 events: upper bound {hi:.3f} / {N:,}")

line("Scam payments paused (tier 2+)", pct(H["scam_payments_t2plus"]), f"of {H['test_scam_payments']:,} scam payments")
line("Scam payments reaching a hold (tier 4)", pct(H["scam_payments_t4plus"]), "payment level")
line("Customers interrupted in 18 days", f"{100 * H['customers_interrupted'] / H['customers']:.2f}%",
     f"{H['customers_interrupted']} of {H['customers']:,} customers")
c = M["campaigns"]
lo, hi = wilson(c["flagged"], c["campaigns"])
line("Mule campaigns flagged (no labels)", f"{c['flagged']}/{c['campaigns']} [{pct(lo)}-{pct(hi)}]", "Wilson")
line("  median hours to flag", f"{c['median_hours_to_flag']:.1f} h", "first victim payment -> network flagged")
line("  flagged before first complaint", pct(c["flagged_before_first_complaint_share"]), "")
line("  flagged groups with no scam account", f"{c['false_components']} of {c['components']}", "")
mk = M["held_out_workflows"]["marketplace"]
k_mk = round(mk["victims_warned_hidden"] * mk["journeys"])
lo, hi = wilson(k_mk, mk["journeys"])
line("Marketplace, hidden from training", f"{pct(k_mk / mk['journeys'])} [{pct(lo)}-{pct(hi)}]",
     f"{k_mk}/{mk['journeys']}, Wilson")
L = H["latency_ms"]
line("Decision latency", f"{L['p50']:.1f} ms median, {L['p99']:.1f} ms p99", f"{L['n']} decisions, 2 vCPU")

print("\n=== Estimated at India's fraud rate (not measured) ===")
rep, adj = H["reported_fraud_rate"], H["adjusted_fraud_rate"]
line("Reported UPI fraud rate", f"{rep * 1e6:.1f} per million", "12.64 lakh frauds / 18,586.60 crore payments")
line("Estimated true rate", f"{adj * 1e6:.1f} per million", "x2: 51% of victims never complain")
for name, key in [("Real scams per Thehro pause (true rate)", "precision_t2_adjusted_rate"),
                  ("Real scams per Thehro pause (reported rate)", "precision_t2_reported_rate")]:
    line(name, f"1 in {1 / H[key]:.1f}", "precision = tpr*p / (tpr*p + fpr*(1-p))")
r1 = M["naive_rules"]["Pause every first payment of Rs 10,000+ to a new payee"]
line("Same, for the new-payee Rs 10k+ rule", f"1 in {1 / r1['precision_t2_adjusted_rate']:,.0f}", "true rate")
per_m_scam = adj * 1e6 * H["scam_payments_t2plus"]
per_m_gen = H["genuine_t2plus_per_10k"] * 100
line("Per million payments: scams paused", f"{per_m_scam:.1f}", "13.6 x share of scam payments paused")
line("Per million payments: genuine paused", f"{per_m_gen:.0f}", "0.89 per 10,000 x 100")
line("Per 10 million a day: genuine / scam paused", f"{per_m_gen * 10:.0f} / {per_m_scam * 10:.0f}", "x 10")
line("Per 10 million a day: scam holds (T4)", f"{adj * 1e7 * H['scam_payments_t4plus']:.0f}",
     "136 scams x share reaching tier 4")
line("Synthetic scams vs reality", f"{H['sim_enrichment_vs_adjusted']:.0f}x-{H['sim_enrichment_vs_reported']:.0f}x",
     "simulated scam rate / true and reported rates")

print("\n=== Modelled (depends on assumed stop rates) ===")
line("Money protected", pct(H["money_protected_share"]), "config.STOP_PROB, anchored on CeDEx")
line("Money protected if half as many stop", pct(H["money_protected_share_if_half_as_many_stop"]), "STOP_PROB x 0.5")

print("\n=== Public figures used on slide 2 ===")
line("Senior (55+) loss per complaint, 2025", f"Rs {4005e7 / 103488 / 1e5:.2f} lakh",
     "Rs 4,005 crore / 1,03,488 complaints (Rajya Sabha)")
line("False alarms per million at 1 in 10,000", f"{1e6 * 1e-4:.0f}", "vs about 7 reported frauds per million")
