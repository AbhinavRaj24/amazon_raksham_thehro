"""Evaluation at real-world base rates.

Everything here is measured on the test weeks (days 42-59) against ground truth,
including scams that were never reported, with thresholds fixed on the validation
week before the test weeks begin. Customer memory starts empty on day 42.

Headline measures
* Victims warned: share of scam journeys that got at least one tier 2+ warning.
* Money protected: expected share of scam losses prevented, using the assumed
  response rates in config.STOP_PROB (to be tested in a pilot).
* Interruptions: genuine tier 2+ warnings per 10,000 customer payments (shop
  payments included), and the share of genuine customers never interrupted.
* Precision at real prevalence: what share of warnings would be real scams at
  India's reported (6.8 per million) and estimated true (13.6 per million) UPI
  fraud rates, rather than at the synthetic world's inflated rate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (ADJUSTED_FRAUD_RATE, ALERT_BUDGET_T2, GENUINE_ABANDON_PROB, MIN_PER_DAY,
                     NUDGE_BUDGET_T1, REPORTED_FRAUD_RATE, REVIEW_BUDGET_T4, STOP_PROB, TRAIN_END,
                     VALID_END)
from .models import WORKFLOW_CONFIDENCE_FLOOR, fit, score
from .policy import apply_memory, base_tiers, calibrate, evidence
from .simulate import AGE_BANDS, KIND, LANGS, REGIONS, WORKFLOWS

GROUP_LABELS = {"age_band": dict(enumerate(AGE_BANDS)), "lang": dict(enumerate(LANGS)),
                "region": dict(enumerate(REGIONS)), "consent": {False: "not opted in", True: "opted in"}}

def windows(f: pd.DataFrame) -> dict:
    t = f.t.values
    return {"val": (t >= TRAIN_END * MIN_PER_DAY) & (t < VALID_END * MIN_PER_DAY),
            "test": t >= VALID_END * MIN_PER_DAY}


def n_customer_payments(denom: dict, start_day: int, end_day: int | None = None) -> int:
    return int(denom["customer_payments_by_day"][start_day:end_day].sum())


def thresholds_from_validation(f, S, denom, budgets=None):
    w = windows(f)
    genuine = w["val"] & (f.is_scam.values == 0)
    return calibrate(S, f, genuine, n_customer_payments(denom, TRAIN_END, VALID_END), budgets)


def run_policy(f: pd.DataFrame, S: pd.DataFrame, thr) -> pd.DataFrame:
    """Final tiers (with customer memory) for the rows of f, processed in time order."""
    ev = evidence(S, f, thr)
    base = base_tiers(S, f, thr, ev)
    tier, esc, sup = apply_memory(f, base, ev)
    return pd.DataFrame({"tier": tier, "base_tier": base, "escalated": esc, "suppressed": sup,
                         "gate": ev["gate"], "payee_flag": ev["payee_flag"], "n_families": ev["n_families"],
                         "elev_journey": ev["journey"], "elev_payee": ev["payee"],
                         "elev_context": ev["context"]}, index=f.index)


def precision_at(tpr: float, fpr: float, prevalence: float) -> float:
    num = tpr * prevalence
    den = num + fpr * (1 - prevalence)
    return float(num / den) if den > 0 else float("nan")


def _expected_loss(amounts, tiers, wf, stop_scale=1.0):
    surv, exp = 1.0, 0.0
    for a, k in zip(amounts, tiers):
        stop = min(1.0, STOP_PROB[k][wf] * stop_scale) if k >= 2 else 0.0
        surv *= 1.0 - stop
        exp += a * surv
    return exp


def journey_table(world, ft: pd.DataFrame, tiers: np.ndarray, stop_scale: float = 1.0) -> pd.DataFrame:
    """One row per scam journey that starts in the test weeks."""
    test_start = VALID_END * MIN_PER_DAY
    J = world.journeys
    J = J[J.start_t >= test_start].set_index("journey")
    sc = ft[ft.is_scam.values == 1].assign(tier=tiers[ft.is_scam.values == 1])
    sc = sc[sc.journey.isin(J.index)].sort_values(["journey", "t"])
    rows = []
    for jid, g in sc.groupby("journey", sort=False):
        wf = WORKFLOWS[int(g.workflow.iloc[0])]
        amt, tr = g.amount.values, g.tier.values
        alerted = np.where(tr >= 2)[0]
        first = int(alerted[0]) if len(alerted) else -1
        total = float(amt.sum())
        exp_loss = _expected_loss(amt, tr, wf, stop_scale)
        rows.append(dict(journey=jid, workflow=wf, victim=int(g.payer.iloc[0]), n_steps=len(g), total=total,
                         warned=len(alerted) > 0, first_alert_step=first,
                         warned_first_payment=first == 0, max_tier=int(tr.max()),
                         loss_before_first_alert=float(amt[:first].sum()) if first >= 0 else total,
                         expected_loss=exp_loss, prevented=total - exp_loss,
                         reported=bool(J.loc[jid, "reported"])))
    return pd.DataFrame(rows)


def summarise(world, ft, pol, n_all_test, n_users, stop_scale: float = 1.0) -> dict:
    """Headline numbers for one policy run on the test weeks."""
    tiers = pol.tier.values
    scam = ft.is_scam.values == 1
    out = {}
    for k in (1, 2, 3, 4):
        g = (tiers >= k) & ~scam
        out[f"genuine_t{k}plus"] = int(g.sum())
        out[f"genuine_t{k}plus_per_10k"] = g.sum() / n_all_test * 1e4
        out[f"scam_payments_t{k}plus"] = float(((tiers >= k) & scam).sum() / scam.sum())
    for k in range(5):
        out[f"genuine_tier{k}"] = int(((tiers == k) & ~scam).sum())
        out[f"scam_tier{k}"] = int(((tiers == k) & scam).sum())
    tpr = out["scam_payments_t2plus"]
    fpr = out["genuine_t2plus"] / n_all_test
    out["precision_t2_reported_rate"] = precision_at(tpr, fpr, REPORTED_FRAUD_RATE)
    out["precision_t2_adjusted_rate"] = precision_at(tpr, fpr, ADJUSTED_FRAUD_RATE)
    tpr4 = out["scam_payments_t4plus"]
    fpr4 = out["genuine_t4plus"] / n_all_test
    out["precision_t4_reported_rate"] = precision_at(tpr4, fpr4, REPORTED_FRAUD_RATE)
    out["precision_t4_adjusted_rate"] = precision_at(tpr4, fpr4, ADJUSTED_FRAUD_RATE)
    # customers: who ever had a genuine payment interrupted
    out["customers_interrupted"] = int(pd.unique(ft.payer.values[(tiers >= 2) & ~scam]).size)
    out["customers_never_interrupted_share"] = 1 - out["customers_interrupted"] / n_users
    test_days = (ft.t.max() - ft.t.min()) / MIN_PER_DAY
    out["interruptions_per_customer_per_year"] = out["genuine_t2plus"] / n_users * 365 / test_days
    out["genuine_abandoned_per_10k"] = sum(
        ((tiers == k) & ~scam).sum() * GENUINE_ABANDON_PROB[k] for k in (2, 3, 4)) / n_all_test * 1e4
    jt = journey_table(world, ft, tiers, stop_scale)
    out["journeys"] = int(len(jt))
    out["victims_warned"] = float(jt.warned.mean())
    out["victims_warned_first_payment"] = float(jt.warned_first_payment.mean())
    out["money_at_risk"] = float(jt.total.sum())
    out["money_protected"] = float(jt.prevented.sum())
    out["money_protected_share"] = float(jt.prevented.sum() / jt.total.sum())
    out["loss_before_first_warning_share"] = float(jt.loss_before_first_alert.sum() / jt.total.sum())
    out["unreported_victims_warned"] = float(jt[~jt.reported].warned.mean())
    return out, jt


def per_workflow(jt: pd.DataFrame, ft: pd.DataFrame, tiers: np.ndarray) -> pd.DataFrame:
    scam = ft.is_scam.values == 1
    pay = pd.DataFrame({"workflow": ft.workflow.values[scam], "alert": tiers[scam] >= 2})
    pay_rec = pay.groupby("workflow").alert.mean().rename(lambda i: WORKFLOWS[i])
    g = jt.groupby("workflow")
    tab = pd.DataFrame({
        "journeys": g.size(),
        "median_loss": g.total.median(),
        "victims_warned": g.warned.mean(),
        "warned_at_first_payment": g.warned_first_payment.mean(),
        "money_protected_share": g.prevented.sum() / g.total.sum(),
    })
    tab["scam_payments_warned"] = pay_rec.reindex(tab.index)
    return tab.reindex([w for w in WORKFLOWS if w in tab.index])


def fairness(world, ft, tiers, jt, denom) -> dict:
    """Same model, no demographic inputs: do outcomes differ by group?"""
    users = world.users
    cust = denom["customer_payments_by_user_day"]
    cust = cust[cust.day >= VALID_END]
    scam = ft.is_scam.values == 1
    out = {}
    for col in ["age_band", "lang", "region", "consent"]:
        grp_pay = users[col].values[cust.payer.values]
        n_pay = pd.Series(1, index=grp_pay).groupby(level=0).sum()
        gen = pd.Series(((tiers >= 2) & ~scam).astype(int), index=users[col].values[ft.payer.values])
        gen_rate = gen.groupby(level=0).sum() / n_pay * 1e4
        vic = users[col].values[jt.victim.values]
        jt_g = jt.assign(g=vic).groupby("g")
        tab = pd.DataFrame({
            "victim_journeys": jt_g.size(),
            "victims_warned": jt_g.warned.mean(),
            "money_protected_share": jt_g.prevented.sum() / jt_g.total.sum(),
            "genuine_interruptions_per_10k": gen_rate,
        })
        out[col] = tab.rename(index=GROUP_LABELS[col])
    return out


# ---------------------------------------------------------------------------
# Baselines, curves, stress tests, held-out workflows
# ---------------------------------------------------------------------------
def naive_rules(ft: pd.DataFrame) -> dict:
    new = ft.is_new_payee.values == 1
    big = ft.amount.values >= 10_000
    call = (np.nan_to_num(ft.on_call.values) == 1) & (np.nan_to_num(ft.caller_unknown.values) == 1)
    listed = (ft.payee_registry.values >= 1) | (ft.payee_fri_band.values >= 2)
    return {
        "Pause every first payment of Rs 10,000+ to a new payee": new & big,
        "Same, only while on a call with an unknown number": new & big & call,
        "Pause when the payee is on a fraud list (registry or FRI)": listed,
    }


def rule_summary(world, ft, flagged, n_all_test, n_users):
    pol = pd.DataFrame({"tier": np.where(flagged, 2, 0).astype(np.int8)}, index=ft.index)
    s, _ = summarise(world, ft, pol, n_all_test, n_users)
    return s


def budget_curve(world, f, S, denom, budgets, n_users):
    w = windows(f)
    ft, St = f[w["test"]], S[w["test"]]
    n_all_test = n_customer_payments(denom, VALID_END)
    rows = []
    for b in budgets:
        thr = thresholds_from_validation(f, S, denom, {1: max(NUDGE_BUDGET_T1, b), 2: b,
                                                       4: min(REVIEW_BUDGET_T4, b)})
        pol = run_policy(ft, St, thr)
        s, _ = summarise(world, ft, pol, n_all_test, n_users)
        rows.append(dict(budget=b, genuine_per_10k=s["genuine_t2plus_per_10k"], victims_warned=s["victims_warned"],
                         money_protected_share=s["money_protected_share"],
                         precision_adjusted=s["precision_t2_adjusted_rate"]))
    return pd.DataFrame(rows)


def perturb(ft: pd.DataFrame, which: str) -> pd.DataFrame:
    """Adversarial versions of the test weeks: scammers change tactics, genuine payments untouched."""
    g = ft.copy()
    scam = g.is_scam.values == 1
    consent = g.consent.values.astype(bool)
    if which in ("no_calls", "all"):
        # coach victims by chat instead of calls; no screen sharing either
        for c in ["on_call", "caller_unknown", "call_ott", "call_min", "caller_fri_band", "screen_share", "remote_app"]:
            g.loc[scam & consent, c] = 0.0
    if which in ("clean_mules", "all"):
        # every payment to a fresh, aged-looking account with no history and no outside flags
        m = scam
        g.loc[m, ["payee_registry", "payee_fri_band", "payee_in_campaign"]] = 0
        g.loc[m, "payee_campaign_size"] = 1
        g.loc[m, ["payee_new_payers_24h", "payee_new_payers_7d", "payee_inflow_24h_log"]] = 0.0
        g.loc[m, "payee_passthrough_24h"] = np.nan
        g.loc[m, "payee_age_days"] = 900.0
        g.loc[m, "payee_idle_days"] = 2.0
    if which in ("no_hooks", "all"):
        # no fixed-deposit break and no small "credits" to the victim first
        g.loc[scam, ["fd_break_48h", "stranger_credits_24h"]] = 0
    return g


def stress_tests(world, f, S, models, thr, denom, n_users):
    w = windows(f)
    ft = f[w["test"]]
    n_all_test = n_customer_payments(denom, VALID_END)
    out = {}
    for which, label in [("none", "As simulated"),
                         ("no_calls", "Scammers stop calling (chat only, no screen share)"),
                         ("clean_mules", "Fresh, clean-looking mule for every payment, no outside flags"),
                         ("no_hooks", "No FD break and no small credits first"),
                         ("all", "All three at once")]:
        g = ft if which == "none" else perturb(ft, which)
        Sg = S[w["test"]] if which == "none" else score(models, g)
        pol = run_policy(g, Sg, thr)
        s, _ = summarise(world, g, pol, n_all_test, n_users)
        out[label] = {k: s[k] for k in ["victims_warned", "money_protected_share", "scam_payments_t2plus",
                                        "genuine_t2plus_per_10k"]}
    return out


def held_out_workflows(world, f, window, y, denom, n_users, seed=0, verbose=True):
    """Hide each scam type completely from training and see how well it is still caught."""
    w = windows(f)
    ft = f[w["test"]]
    n_all_test = n_customer_payments(denom, VALID_END)
    rows = []
    for wf in WORKFLOWS:
        m = fit(f, window, y, exclude_workflows=[wf], seed=seed, with_recogniser=False, verbose=False)
        S = score(m, f)
        thr = thresholds_from_validation(f, S, denom)
        pol = run_policy(ft, S[w["test"]], thr)
        s, jt = summarise(world, ft, pol, n_all_test, n_users)
        jw = jt[jt.workflow == wf]
        rows.append(dict(workflow=wf, journeys=len(jw), victims_warned_hidden=float(jw.warned.mean()),
                         money_protected_hidden=float(jw.prevented.sum() / jw.total.sum()),
                         genuine_per_10k=s["genuine_t2plus_per_10k"]))
        if verbose:
            print(f"  hidden {wf:15s} victims warned {rows[-1]['victims_warned_hidden']:.1%}", flush=True)
    return pd.DataFrame(rows).set_index("workflow")


def message_fit(ft, tiers):
    """Does the advice shown fit what is actually happening? (model-free, so it holds for new scams too)"""
    from .explain import ADVICE_FIT, advice_keys
    scam = ft.is_scam.values == 1
    a = scam & (tiers >= 2)
    keys = advice_keys(ft[a])
    wfs = [WORKFLOWS[i] for i in ft.workflow.values[a]]
    general = np.isin(keys, ["generic", "flagged_payee"])
    fits = np.array([k in ADVICE_FIT[w] for k, w in zip(keys, wfs)])
    d = pd.DataFrame({"wf": wfs, "fits": fits, "general": general, "other": ~fits & ~general})
    g = d.groupby("wf")[["fits", "general", "other"]].mean()
    return {"specific_and_fits": float(fits.mean()), "general": float(general.mean()),
            "specific_but_other": float(d.other.mean()), "by_workflow": g.reindex([w for w in WORKFLOWS if w in g.index])}


def recogniser_quality(ft, St, tiers):
    scam = ft.is_scam.values == 1
    a = scam & (tiers >= 2)
    conf = St.wf_conf.values[a]
    pred = St.wf_pred.values[a]
    true = ft.workflow.values[a]
    cov = conf >= WORKFLOW_CONFIDENCE_FLOOR
    by_wf = pd.DataFrame({"wf": [WORKFLOWS[i] for i in true], "named": cov, "right": (pred == true) & cov})
    g = by_wf.groupby("wf")
    return {"named_share": float(cov.mean()), "accuracy_when_named": float(((pred == true) & cov).sum() / max(cov.sum(), 1)),
            "by_workflow": pd.DataFrame({"named_share": g.named.mean(),
                                         "accuracy_when_named": g.right.sum() / g.named.sum().clip(lower=1)})}


def campaign_timing(world, components) -> dict:
    """How fast the label-free detector finds each scam campaign's mule network."""
    camp_of = world.accounts.campaign.values
    pay = world.payments
    sp = pay[pay.kind.values == KIND["scam"]]
    first_pay = sp.groupby("campaign").t.min()
    J = world.journeys
    first_report = J[J.reported].groupby("campaign").report_t.min()
    flag_t, false_components = {}, 0
    for comp in components:
        c = camp_of[comp["members"]]
        camps = set(c[c >= 0].tolist())
        if not camps:
            false_components += 1
        for cid in camps:
            flag_t[cid] = min(flag_t.get(cid, np.inf), comp["flag_t"])
    rows = []
    for cid, t0 in first_pay.items():
        ft_ = flag_t.get(cid, np.inf)
        fr = first_report.get(cid, np.inf)
        jt = J[J.campaign == cid]
        rows.append(dict(campaign=cid, workflow=world.campaigns.set_index("campaign").workflow.get(cid),
                         first_payment_t=t0, flag_t=ft_, first_report_t=fr,
                         hours_to_flag=(ft_ - t0) / 60, flagged=np.isfinite(ft_),
                         flagged_before_first_report=ft_ < fr,
                         lead_hours_vs_first_report=(fr - ft_) / 60 if np.isfinite(ft_) and np.isfinite(fr) else np.nan,
                         victims=len(jt), victims_started_before_flag=int((jt.start_t < ft_).sum())))
    ct = pd.DataFrame(rows)
    fl = ct[ct.flagged]
    return {"table": ct,
            "campaigns": int(len(ct)), "flagged": int(ct.flagged.sum()),
            "flagged_share": float(ct.flagged.mean()),
            "median_hours_to_flag": float(fl.hours_to_flag.median()),
            "flagged_before_first_complaint_share": float(ct.flagged_before_first_report.mean()),
            "median_lead_hours_vs_first_complaint": float(fl.lead_hours_vs_first_report.median()),
            "false_components": int(false_components), "components": int(len(components))}
