"""Policy versions side by side on the synthetic test days: v1 (Round 1), v2, v3 and v4 (the finale).

    python scripts/eval_policy.py                     # also step "policy versions" of scripts/run_test_kit.py
    python scripts/eval_policy.py --cache <run_all.py --cache file> --seed 2026

Same frozen model (outputs/) for every version; each version's alert lines are calibrated for it on the same
validation week with the same budgets; only the policy rules differ (thehro/policy.py):
  v1  Round 1's policy
  v2  + retry memory: after a warning the customer didn't go ahead with, a new payee within 24 h is a pause
  v3  + a first payment to a payee with an outside flag is at least a pause, and memory reaches a trusted check
        only for Rs 50,000+ or a flagged payee (otherwise a pause)
  v4  + a journey-only pause line from 30% of the pause budget, and no caller identity (the caller's DoT rating
        no longer counts as context evidence)

A. The whole test days, customers going ahead after every warning (the usual evaluation path). There v1 and v2
   must agree, because retry memory only starts when a customer does NOT go ahead. The retry rule's real cost is
   genuine customers who cancel a false alarm and then pay someone new within 24 h: counted exactly for every
   false alarm of the current version, then weighted by the assumed share of genuine customers who give up
   after each tier (config.GENUINE_ABANDON_PROB): modelled.
B. The cancel-and-retry attack. Every victim whose scam Round 1's policy warned cancels that first warned
   payment and, 30-120 minutes later, retries 60% of it to a different mule of the same campaign. The same
   retries go to every version. Retry features come from the live path (thehro/live.py) on a history in which
   the cancelled payment and the rest of that journey never happened; decisions come from the decision service,
   with each victim's earlier test-day payments replayed first so memory is right.
Writes test_kit/results/policy_versions.json and the "policy" section of test_kit/report.md, and adds a row to
runs.csv.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import evaluate as E  # noqa: E402
from thehro import kit  # noqa: E402
from thehro.config import GENUINE_ABANDON_PROB  # noqa: E402
from thehro.features import ALL_FEATURES  # noqa: E402
from thehro.live import CONTEXT_KEYS, MULE_KINDS, LiveWorld  # noqa: E402
from thehro.policy import CURRENT, V1, V2, V3, V4, apply_memory, base_tiers, evidence  # noqa: E402
from thehro.service import DecisionService  # noqa: E402
from thehro.simulate import CHANNELS, KIND, WORKFLOWS  # noqa: E402

VERSIONS = [V1, V2, V3, V4]
LABEL = {"v1": "v1 (Round 1)", "v2": "v2 (+ retry memory)", "v3": "v3 (+ flagged payee, memory cap)",
         "v4": "v4 (+ journey line, no caller id)"}


def headline(world, ft, pol, n_all, U) -> tuple[dict, pd.DataFrame]:
    h, jt = E.summarise(world, ft, pol, n_all, U)
    k = h["genuine_t2plus"]
    lo, hi = kit.poisson(k)
    vw = int(round(h["victims_warned"] * h["journeys"]))
    vf = int(round(h["victims_warned_first_payment"] * h["journeys"]))
    return {"victims_warned": h["victims_warned"], "victims_warned_ci": kit.wilson(vw, h["journeys"]),
            "victims_warned_first_payment": h["victims_warned_first_payment"],
            "victims_warned_first_payment_ci": kit.wilson(vf, h["journeys"]),
            "scam_payments_paused": h["scam_payments_t2plus"], "scam_payments_held": h["scam_payments_t4plus"],
            "genuine_pauses": k, "false_pauses_per_10k": k / n_all * 1e4,
            "false_pauses_per_10k_ci": (lo / n_all * 1e4, hi / n_all * 1e4),
            "genuine_trusted_checks": h["genuine_tier3"], "genuine_holds": h["genuine_t4plus"],
            "customers_interrupted": h["customers_interrupted"],
            "money_protected_modelled": h["money_protected_share"]}, jt


def decide_row(svc, ref, r, payee=None, amount=None, t=None, features=None):
    feats = features if features is not None else {c: (None if pd.isna(r[c]) else float(r[c])) for c in ALL_FEATURES}
    pay = {"payment_id": f"P{int(r.pid)}", "customer_ref": ref,
           "payee_ref": f"A{int(payee if payee is not None else r.payee)}",
           "amount": float(amount if amount is not None else r.amount),
           "channel": CHANNELS[int(r.channel)], "t_min": float(t if t is not None else r.t)}
    return svc.decide(pay, feats, bool(r.consent))


def cancel_cost(ft, St, thr, pol, rules, n_all) -> dict:
    """Genuine customers who cancel a false alarm: what retry memory adds for them in the next 24 h."""
    false_alarms = np.flatnonzero((ft.is_scam.values == 0) & (pol.tier.values >= 2))
    extra = []
    for pos in false_alarms:
        rows = np.flatnonzero(ft.payer.values == ft.payer.values[pos])
        Fc, Sc = ft.iloc[rows], St.iloc[rows]
        ev = evidence(Sc, Fc, thr, rules)
        cont = np.ones(len(rows), dtype=bool)
        cont[np.flatnonzero(rows == pos)] = False
        after, _, _ = apply_memory(Fc, base_tiers(Sc, Fc, thr, ev, rules), ev, rules=rules, continued=cont)
        newly = (after >= 2) & (pol.tier.values[rows] < 2) & (Fc.is_scam.values == 0)
        extra.append((int(pol.tier.values[pos]), int(newly.sum())))
    worst = sum(e for _, e in extra)
    expected = sum(GENUINE_ABANDON_PROB[t] * e for t, e in extra)
    k = int(((ft.is_scam.values == 0) & (pol.tier.values >= 2)).sum())
    return {"policy": rules.name, "false_alarms": len(extra),
            "false_alarms_followed_by_a_new_payee_within_24h": sum(e > 0 for _, e in extra),
            "extra_pauses_if_every_one_is_cancelled": worst, "extra_pauses_expected": round(expected, 2),
            "false_pauses_per_10k_with_expected_cancels": (k + expected) / n_all * 1e4,
            "false_pauses_per_10k_if_every_one_is_cancelled": (k + worst) / n_all * 1e4,
            "cancel_rates_assumed": GENUINE_ABANDON_PROB}


def retry_plans(world, ft, tiers_v1, jt, live, seed) -> list[dict]:
    rng = np.random.default_rng(seed)
    acc = world.accounts
    mules = acc[np.isin(acc.mule_category.values, MULE_KINDS)]
    mule_by_campaign = {c: g.index.values for c, g in mules.groupby("campaign")}
    scam = ft[ft.is_scam.values == 1].assign(tier=tiers_v1[ft.is_scam.values == 1])
    scam = scam[scam.journey.isin(jt.journey.values)]
    plans = []
    for jid, g in scam.sort_values("t").groupby("journey"):
        warned = g[g.tier.values >= 2]
        if warned.empty:
            continue
        wrow = warned.iloc[0]
        paid = set(world.payments.payee.values[world.payments.payer.values == wrow.payer].tolist())
        cands = [m for m in mule_by_campaign.get(int(wrow.campaign), []) if m != wrow.payee and m not in paid]
        if not cands:
            continue
        delay = float(rng.uniform(30, 120))
        plans.append(dict(journey=int(jid), workflow=WORKFLOWS[int(wrow.workflow)], w_idx=wrow.name,
                          cust=int(wrow.payer), payee=int(rng.choice(cands)), t=float(wrow.t) + delay,
                          amount=float(round(0.6 * wrow.amount)), delay=delay,
                          excluded=g.pid.values[g.t.values >= wrow.t].tolist()))
    plans.sort(key=lambda p: p["t"])                       # the network state only moves forward in time
    raw = world.payments
    for p in plans:
        wr = raw.iloc[int(ft.loc[p["w_idx"], "pid"])]
        ctx = {k: float(wr[k]) for k in CONTEXT_KEYS}
        if ctx["on_call"]:
            ctx["call_min"] += p["delay"]
        live.network.advance(p["t"])
        row = {"t": p["t"], "payer": p["cust"], "payee": p["payee"], "amount": p["amount"],
               "channel": 0 if p["amount"] <= 100_000 else 2, "kind": KIND["new_individual"], **ctx}
        new = live.payment_rows([row])
        fr = live.slice_features(p["cust"], p["payee"], p["t"], new, exclude_pids=p["excluded"])
        fr = fr.loc[fr.pid.values == int(new.pid.iloc[0])].iloc[0]
        p["features"] = {c: (None if pd.isna(fr[c]) else float(fr[c])) for c in ALL_FEATURES}
    return plans


def run_retries(bundle, rules, thr, plans, ft) -> dict:
    svc = DecisionService(dict(bundle, thresholds=thr), rules=rules)
    tiers, warned_first = [], []
    for p in plans:
        wrow = ft.loc[p["w_idx"]]
        ref = f"{rules.name}:{p['journey']}:C{p['cust']}"
        earlier = ft[(ft.payer.values == p["cust"]) & (ft.t.values < wrow.t)].sort_values("t")
        for _, e in earlier.iterrows():
            d = decide_row(svc, ref, e)
            if d["tier"] >= 2:
                svc.outcome(d["evidence_seq"], "continue", float(e.t))
        d = decide_row(svc, ref, wrow)
        warned_first.append(d["tier"] >= 2)
        if d["evidence_seq"] is not None:
            svc.outcome(d["evidence_seq"], "cancel", float(wrow.t))
        dr = decide_row(svc, ref, wrow, payee=p["payee"], amount=p["amount"], t=p["t"], features=p["features"])
        tiers.append(dr["tier"])
    tiers, warned_first = np.array(tiers), np.array(warned_first)
    amounts = np.array([p["amount"] for p in plans])
    stopped = tiers >= 2
    agree = int(warned_first.sum())
    by_type = (pd.DataFrame({"workflow": [p["workflow"] for p in plans], "stopped": stopped})
               .groupby("workflow").stopped.agg(["mean", "size"]).round(3))
    return {"retries": int(len(plans)), "retries_interrupted": float(stopped.mean()),
            "retries_interrupted_ci": kit.wilson(int(stopped.sum()), len(plans)),
            "retries_held": float((tiers == 4).mean()),
            "retry_money_stopped_share": float(amounts[stopped].sum() / amounts.sum()),
            "cancelled_payment_warned": f"{agree} of {len(plans)}",
            "warned_first": agree,
            "retries_interrupted_after_a_warning": float(stopped[warned_first].mean()) if agree else float("nan"),
            "tier_counts": {int(k): int(v) for k, v in zip(*np.unique(tiers, return_counts=True))},
            "by_scam_type": {wf: {"interrupted": float(r["mean"]), "n": int(r["size"])} for wf, r in by_type.iterrows()}}


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE, help="run_all.py --cache file")
    ap.add_argument("--live-cache", default=str(ROOT / ".cache" / "live_world.pkl"))
    ap.add_argument("--out", default=None, help="results folder (default test_kit/results)")
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    T = time.time()

    # ---------------- A. whole test days ----------------
    base = kit.get_baseline(a.cache)                       # shared with the rest of run_test_kit.py
    world, ft, St, bundle = base["world"], base["ft"], base["St"], base["bundle"]
    thr, n_all, U = base["thr"], base["n_all"], base["n_users"]
    thrs = {r.name: (thr if r.name == base["rules"].name else
                     E.thresholds_from_validation(base["f"], base["S"], base["denom"], rules=r)) for r in VERSIONS}
    pols = {r.name: E.run_policy(ft, St, thrs[r.name], r) for r in VERSIONS}
    heads, jts = {}, {}
    for r in VERSIONS:
        heads[r.name], jts[r.name] = headline(world, ft, pols[r.name], n_all, U)
    assert (pols[base["rules"].name].tier.values == base["pol"].tier.values).all(), "baseline policy mismatch"
    assert (pols["v1"].tier.values == pols["v2"].tier.values).all(), "customers who continue: v1 and v2 must agree"
    cost = cancel_cost(ft, St, thrs[CURRENT.name], pols[CURRENT.name], CURRENT, n_all)
    for r in VERSIONS:
        h = heads[r.name]
        print(f"A. {r.name}: victims warned {h['victims_warned']:.1%}, first payment "
              f"{h['victims_warned_first_payment']:.1%}, false pauses {h['genuine_pauses']}, genuine trusted checks "
              f"{h['genuine_trusted_checks']}")

    # ---------------- B. cancel-and-retry attack ----------------
    live = LiveWorld.build(cache=a.live_cache, verbose=False)
    assert len(live.world.payments) == len(world.payments), "the two caches must hold the same world (seed 7)"
    plans = retry_plans(world, ft, pols["v1"].tier.values, jts["v1"], live, a.seed)
    print(f"B. {len(plans)} victims cancel their first warned payment and retry")
    results = {}
    for r in VERSIONS:
        results[r.name] = run_retries(bundle, r, thrs[r.name], plans, ft)
        print(f"   {r.name}: retries interrupted {results[r.name]['retries_interrupted']:.1%} "
              f"(held {results[r.name]['retries_held']:.1%})")

    report = {
        "what": f"Policy versions v1 (Round 1) to v4 on the same frozen {kit.system(base).split(', policy')[0]}, each "
                "with lines calibrated for it on the validation week. Synthetic test days 42-59. Measured, except the "
                "genuine cancel cost (modelled).",
        "lines": {k: {"t2": v.t2, "tj": v.tj} for k, v in thrs.items()},
        "current": CURRENT.name, "outputs_policy": base["rules"].name,
        "test_days": {r.name: heads[r.name] for r in VERSIONS}, "v1_equals_v2_when_customers_continue": True,
        "genuine_cancel_path_modelled": cost,
        "cancel_and_retry_attack": {"setup": "each victim Round 1's policy warned cancels that first warned payment "
                                             "and retries 60% of it to another mule of the same campaign 30-120 min "
                                             "later; the same retries for every version; retry features from the "
                                             "live path with the cancelled payment removed from history",
                                    "seed": a.seed, **results},
        "seconds": round(time.time() - T),
    }
    path = kit.save_json(report, out / "policy_versions.json")

    pc = lambda c: f"{100 * c[0]:.1f}–{100 * c[1]:.1f}%"  # noqa: E731
    cols = [r.name for r in VERSIONS]
    H, R = heads, results

    def line(label, fn):
        return f"| {label} | " + " | ".join(fn(c) for c in cols) + " |"

    md = [
        f"**Measured, synthetic** (test days 42–59, the same frozen {kit.system(base).split(', policy')[0]} for every "
        "version; each version's alert lines are calibrated for it on the same validation week and budgets; only the "
        "policy rules differ). The cost to genuine customers who cancel is **modelled**.",
        "",
        "- **v1** is Round 1's policy.",
        "- **v2** adds retry memory: after a warning the customer didn't go ahead with (cancelled, called the bank, "
        "trusted person declined, or no answer), a payment to a new payee, or to the cancelled one, within 24 hours "
        "is at least a pause.",
        "- **v3** adds two rules. A first payment to a payee with an outside flag (cybercrime "
        "registry, DoT high risk, or a live mule network) is at least a pause. And memory raises a payment to a "
        "trusted check only if it is ₹50,000+ or the payee is flagged; otherwise to a pause.",
        "- **v4** (the finale) adds a journey-only pause line: 30% of the pause budget goes to the journey family's own "
        "score, so a scam-like journey is paused even when the payee looks clean and the phone signals are silent "
        "(the fused line keeps the other 70%, so the total budget is the same). It also drops caller identity, which "
        "a bank app can't read: the caller's DoT rating no longer counts as context evidence.",
        "",
        "| | " + " | ".join(LABEL[c] for c in cols) + " |",
        "|---|" + "---|" * len(cols),
        line("Victims warned", lambda c: f"{H[c]['victims_warned']:.1%} ({pc(H[c]['victims_warned_ci'])})"),
        line("Warned at their first scam payment", lambda c: f"{H[c]['victims_warned_first_payment']:.1%}"),
        line("Scam payments paused", lambda c: f"{H[c]['scam_payments_paused']:.1%}"),
        line("Scam payments held", lambda c: f"{H[c]['scam_payments_held']:.1%}"),
        line("False pauses per 10,000", lambda c: f"{H[c]['false_pauses_per_10k']:.2f} "
                                                   f"({H[c]['false_pauses_per_10k_ci'][0]:.2f}–"
                                                   f"{H[c]['false_pauses_per_10k_ci'][1]:.2f}; {H[c]['genuine_pauses']})"),
        line("Genuine trusted checks / holds", lambda c: f"{H[c]['genuine_trusted_checks']} / {H[c]['genuine_holds']}"),
        line("Money protected (modelled)", lambda c: f"{H[c]['money_protected_modelled']:.1%}"),
        line(f"**Cancel-and-retry attack: retries stopped** (n = {R['v1']['retries']})",
             lambda c: f"{R[c]['retries_interrupted']:.1%} ({pc(R[c]['retries_interrupted_ci'])})"),
        line("Retry money stopped", lambda c: f"{R[c]['retry_money_stopped_share']:.1%}"),
        line("… of which the version itself warned the first payment", lambda c: R[c]["cancelled_payment_warned"]),
        line("… retries stopped after a warning it gave", lambda c: f"{R[c]['retries_interrupted_after_a_warning']:.1%}"),
        "",
        "When customers go ahead after warnings (the usual evaluation path) v1 and v2 give identical results on "
        "every test-day payment: retry memory only acts when a customer doesn't go ahead. The retries are planned "
        "from the payments Round 1's policy warned; a version with a stricter line may not warn some of them, and "
        "then there is no warning to cancel and no retry memory, which is why the last two rows are shown.",
        "",
        f"**Cost to genuine customers who cancel ({CURRENT.name}, modelled):** of the {cost['false_alarms']} false "
        f"alarms, {cost['false_alarms_followed_by_a_new_payee_within_24h']} "
        f"{'was' if cost['false_alarms_followed_by_a_new_payee_within_24h'] == 1 else 'were'} followed by a payment "
        f"to a new payee within 24 hours. If every one were cancelled, retry memory would add "
        f"{cost['extra_pauses_if_every_one_is_cancelled']} "
        f"{'pause' if cost['extra_pauses_if_every_one_is_cancelled'] == 1 else 'pauses'} "
        f"({cost['false_pauses_per_10k_if_every_one_is_cancelled']:.2f} per 10,000 in all). At the assumed cancel "
        f"rates (8% after a pause, 3% after a trusted check, 1% after a hold) it adds {cost['extra_pauses_expected']:.2f} "
        f"({cost['false_pauses_per_10k_with_expected_cancels']:.2f} per 10,000).",
        "",
    ]
    sec = kit.write_section(out, "policy", "\n".join(md))
    kit.log_run(out, kit.new_run_id("policy"), a.seed, [path, sec], command=kit.cmd(__file__, argv))
    print(f"wrote {path} and {sec} in {report['seconds']}s")
    return report


if __name__ == "__main__":
    main()
