"""Run the whole prototype end to end.

    python scripts/run_all.py            # ~10 minutes on 2 CPUs, ~3 GB RAM
    python scripts/run_all.py --quick    # skips the held-out scam-type tests

simulate -> features -> train -> set thresholds -> evaluate -> outputs/
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import __version__  # noqa: E402
from thehro import evaluate as E  # noqa: E402
from thehro import figures, scenarios  # noqa: E402
from thehro.config import (ADJUSTED_FRAUD_RATE, ALERT_BUDGET_T2, HOLD_MIN_AMOUNT, NUDGE_BUDGET_T1,  # noqa: E402
                           OVERRIDE_MEMORY_DAYS, REPORTED_FRAUD_RATE, REVIEW_BUDGET_T4, TRUSTED_CHECK_MIN_AMOUNT,
                           VALID_END)
from thehro.explain import fit_reason_weights  # noqa: E402
from thehro.features import ALL_FEATURES, build_features  # noqa: E402
from thehro.models import CALLER_FEATURES, fit, score, training_labels  # noqa: E402
from thehro.policy import CURRENT, RULES  # noqa: E402
from thehro.service import DecisionService  # noqa: E402
from thehro.simulate import WORKFLOWS, SimConfig, simulate  # noqa: E402


def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, pd.DataFrame):
        return {str(i): jsonable(r) for i, r in x.to_dict(orient="index").items()}
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(f"{float(x):.6g}")
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=40_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "outputs"))
    ap.add_argument("--cache", default=None, help="pickle of (world, features, denominators) to reuse")
    ap.add_argument("--quick", action="store_true", help="skip held-out scam-type tests")
    ap.add_argument("--policy", default=CURRENT.name, choices=sorted(RULES),
                    help="policy version (v1 reproduces Round 1's results; default: the current policy)")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    T = time.time()

    # 1. world and features ---------------------------------------------------------------
    if a.cache and Path(a.cache).exists():
        world, f, denom = pickle.load(open(a.cache, "rb"))
        print(f"loaded cached world: {len(world.payments):,} payments")
    else:
        world = simulate(SimConfig(seed=a.seed, n_users=a.users))
        f, denom = build_features(world)
        if a.cache:
            pickle.dump((world, f, denom), open(a.cache, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    U = denom["n_users"]

    # 2. models and thresholds ---------------------------------------------------------------
    policy = RULES[a.policy]
    drop = () if policy.caller_id else CALLER_FEATURES     # policy v4: no caller identity anywhere
    window, y = training_labels(f)
    models = fit(f, window, y, drop=drop)
    S = score(models, f)
    thr = E.thresholds_from_validation(f, S, denom, rules=policy)
    weights = fit_reason_weights(f[window], y[window], caller_id=policy.caller_id)
    tag = "" if policy.caller_id else "-nocallerid"
    versions = {"thehro": __version__,
                "model": f"hgb-{models.meta['n_iter_fused']}it-{models.meta['n_pos']}lab{tag}",
                "thresholds": f"val-days-35-41-t2-{thr.t2:.4f}" + (f"-tj-{thr.tj:.4f}" if thr.tj is not None else "")}
    print(f"thresholds: note {thr.t1:.4f}  pause {thr.t2:.4f}  hold {thr.t4:.4f}  family {thr.family}"
          + (f"  journey line {thr.tj:.4f}" if thr.tj is not None else ""))
    bundle = {"models": models, "thresholds": thr, "reason_weights": weights, "versions": versions,
              "features": [c for c in ALL_FEATURES if c not in drop], "policy": policy.name}
    pickle.dump(bundle, open(out / "model_bundle.pkl", "wb"), protocol=pickle.HIGHEST_PROTOCOL)

    # 3. evaluation on the test weeks ----------------------------------------------------------------
    w = E.windows(f)
    ft, St = f[w["test"]], S[w["test"]]
    n_all_test = E.n_customer_payments(denom, VALID_END)
    pol = E.run_policy(ft, St, thr, policy)
    head, jt = E.summarise(world, ft, pol, n_all_test, U)
    head_half, _ = E.summarise(world, ft, pol, n_all_test, U, stop_scale=0.5)
    head["money_protected_share_if_half_as_many_stop"] = head_half["money_protected_share"]
    head["escalated_scam"] = int(pol.escalated[ft.is_scam == 1].sum())
    head["escalated_genuine"] = int(pol.escalated[ft.is_scam == 0].sum())
    head["test_customer_payments"] = n_all_test
    head["test_scored_payments"] = int(len(ft))
    head["test_scam_payments"] = int(ft.is_scam.sum())
    head["test_days"] = int(world.cfg.days - VALID_END)
    head["customers"] = int(U)
    head["thresholds"] = {"t1": thr.t1, "t2": thr.t2, "t4": thr.t4, "family": thr.family, "tj": thr.tj}
    head["budgets"] = {"t1": NUDGE_BUDGET_T1, "t2": ALERT_BUDGET_T2, "t4": REVIEW_BUDGET_T4}
    head["policy"] = {"trusted_min": TRUSTED_CHECK_MIN_AMOUNT, "hold_min": HOLD_MIN_AMOUNT,
                      "memory_days": OVERRIDE_MEMORY_DAYS}
    head["reported_fraud_rate"] = REPORTED_FRAUD_RATE
    head["adjusted_fraud_rate"] = ADJUSTED_FRAUD_RATE
    sim_rate = world.payments.kind.eq(20).sum() / denom["customer_payments_by_day"].sum()
    head["sim_scam_rate"] = float(sim_rate)
    head["sim_enrichment_vs_reported"] = float(sim_rate / REPORTED_FRAUD_RATE)
    head["sim_enrichment_vs_adjusted"] = float(sim_rate / ADJUSTED_FRAUD_RATE)
    print("headline:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in head.items()
                        if k in ("victims_warned", "money_protected_share", "genuine_t2plus_per_10k",
                                 "precision_t2_adjusted_rate", "customers_never_interrupted_share")})

    wf_tab = E.per_workflow(jt, ft, pol.tier.values)
    fair = E.fairness(world, ft, pol.tier.values, jt, denom)
    rules = {name: E.rule_summary(world, ft, m, n_all_test, U) for name, m in E.naive_rules(ft).items()}
    curve = E.budget_curve(world, f, S, denom, [1e-5, 3e-5, 1e-4, 3e-4, 1e-3, 3e-3, 1e-2], U, policy)
    stress = E.stress_tests(world, f, S, models, thr, denom, U, policy)
    rq = E.recogniser_quality(ft, St, pol.tier.values)
    mfit = E.message_fit(ft, pol.tier.values, policy.caller_id)
    camp = E.campaign_timing(world, denom["campaign_components"])
    lowo = None
    if not a.quick:
        print("held-out scam types:")
        lowo = E.held_out_workflows(world, f, window, y, denom, U, rules=policy)

    # labels the model actually had, per scam type
    labels = pd.Series(f.workflow.values[window & (y == 1)]).map(dict(enumerate(WORKFLOWS))).value_counts()

    # 4. latency of the decision path (models + policy + reasons + message + evidence) ------
    svc = DecisionService(bundle, rules=policy)
    rng = np.random.default_rng(0)
    rows = ft.sample(1000, random_state=1)
    lat = []
    for _, r in rows.iterrows():
        feats = {c: (None if pd.isna(r[c]) else float(r[c])) for c in ALL_FEATURES}
        pay = {"payment_id": f"P{int(r.pid)}", "customer_ref": f"C{int(r.payer)}", "payee_ref": f"A{int(r.payee)}",
               "amount": float(r.amount), "channel": "UPI to person", "t_min": float(r.t)}
        t0 = time.perf_counter()
        svc.decide(pay, feats, bool(r.consent))
        lat.append((time.perf_counter() - t0) * 1000)
    lat = np.array(lat)
    head["latency_ms"] = {"p50": float(np.percentile(lat, 50)), "p95": float(np.percentile(lat, 95)),
                          "p99": float(np.percentile(lat, 99)), "n": int(len(lat)),
                          "note": "single process, 2 vCPU, Python; includes all models, policy, reasons, message"}
    print(f"latency p50 {head['latency_ms']['p50']:.1f} ms  p99 {head['latency_ms']['p99']:.1f} ms")

    # 5. write outputs ----------------------------------------------------------------------------
    metrics = {
        "policy": policy.name,             # which policy version produced these numbers (absent = Round 1's v1)
        "headline": head,
        "per_workflow": wf_tab,
        "fairness": fair,
        "naive_rules": rules,
        "budget_curve": curve.set_index("budget"),
        "stress_tests": stress,
        "held_out_workflows": lowo,
        "recogniser": {"named_share": rq["named_share"], "accuracy_when_named": rq["accuracy_when_named"],
                       "by_workflow": rq["by_workflow"]},
        "message_fit": mfit,
        "campaigns": {k: v for k, v in camp.items() if k != "table"},
        "campaigns_by_workflow": camp["table"].groupby("workflow").agg(
            campaigns=("campaign", "size"), flagged=("flagged", "mean"), median_hours=("hours_to_flag", "median"),
            before_first_complaint=("flagged_before_first_report", "mean")),
        "training_labels_by_workflow": labels.to_dict(),
        "model_meta": models.meta,
    }
    json.dump(jsonable(metrics), open(out / "metrics.json", "w"), indent=1)
    wf_tab.to_csv(out / "tables" / "per_workflow.csv")
    curve.to_csv(out / "tables" / "budget_curve.csv", index=False)
    camp["table"].to_csv(out / "tables" / "campaigns.csv", index=False)
    pd.DataFrame(stress).T.to_csv(out / "tables" / "stress_tests.csv")
    pd.DataFrame(rules).T.to_csv(out / "tables" / "naive_rules.csv")
    for k, v in fair.items():
        v.to_csv(out / "tables" / f"fairness_{k}.csv")
    if lowo is not None:
        lowo.to_csv(out / "tables" / "held_out_workflows.csv")

    short = {"Pause every first payment of Rs 10,000+ to a new payee": "Pause every first Rs 10k+ to a new payee",
             "Same, only while on a call with an unknown number": "...only while on a call with an unknown number",
             "Pause when the payee is on a fraud list (registry or FRI)": "Pause when payee is on a fraud list"}
    figures.budget_curve(curve, {short[k]: v for k, v in rules.items()}, head, out / "figures" / "budget_curve.png")
    figures.bars(pd.DataFrame(stress).T[["victims_warned", "money_protected_share"]],
                 ["victims_warned", "money_protected_share"], ["Victims warned", "Money protected (modelled)"],
                 "If scammers change tactics (model not retrained)", out / "figures" / "stress_tests.png")
    if lowo is not None:
        cmp_ = pd.DataFrame({"full": wf_tab.victims_warned, "hidden": lowo.victims_warned_hidden})
        figures.bars(cmp_, ["full", "hidden"], ["Scam type seen in training", "Scam type hidden from training"],
                     "Victims warned, by scam type", out / "figures" / "held_out.png")

    demo = scenarios.export(world, ft, St, pol, jt, weights, jsonable(metrics), versions, caller_id=policy.caller_id)
    json.dump(jsonable(demo), open(out / "demo_scenarios.json", "w"), ensure_ascii=False, indent=1)
    sample = []
    for _, r in ft[ft.is_scam == 1].head(3).iterrows():
        sample.append({"payment": {"payment_id": f"P{int(r.pid)}", "customer_ref": f"C{int(r.payer)}",
                                   "payee_ref": f"A{int(r.payee)}", "amount": float(r.amount), "t_min": float(r.t)},
                       "features": {c: (None if pd.isna(r[c]) else float(r[c])) for c in ALL_FEATURES},
                       "consent": bool(r.consent)})
    json.dump(sample, open(out / "sample_requests.json", "w"), indent=1)
    print(f"done in {time.time() - T:.0f}s -> {out}")


if __name__ == "__main__":
    main()
