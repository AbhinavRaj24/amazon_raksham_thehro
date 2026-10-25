"""P2-1: name what we get wrong, in plain words, with a real example of each.

    python scripts/failure_cases.py

On the frozen model and lines in outputs/, over the synthetic test days (42-59):
* the 41 scam victims who were never warned, each placed in exactly one named group by cause
* the 70 false pauses (genuine payments at tier 2+), each placed in exactly one group by genuine kind,
  with cross-tabs by "on a call" and by the top reason shown
* the weak spots measured by run_all.py (read from outputs/, never recomputed or edited)

Writes test_kit/results/failure_cases.json and the "Named failure cases" section of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import kit  # noqa: E402
from thehro.config import TIER_NAMES  # noqa: E402
from thehro.explain import inr, top_reasons  # noqa: E402
from thehro.features import ALL_FEATURES  # noqa: E402
from thehro.simulate import KIND_NAME  # noqa: E402

SHOWN = ["amount", "is_new_payee", "amt_vs_usual", "recent_payee_payments_7d", "payee_age_days", "payee_idle_days",
         "payee_new_payers_24h", "payee_passthrough_24h", "payee_registry", "payee_fri_band", "payee_in_campaign",
         "on_call", "caller_unknown", "call_ott", "call_min", "screen_share", "remote_app", "consent"]

# Missed victims: the first rule that matches decides the group, so every victim lands in exactly one.
MISSED_GROUPS = [
    ("flagged_payee_under_line",
     "Payee was on a fraud list, but the overall risk stayed under the pause line",
     lambda j: j["outside_flag"]),
    ("one_small_payment",
     "One small payment (under ₹10,000) and nothing else on file",
     lambda j: j["n_payments"] == 1 and j["all_under_10k"]),
    ("one_large_payment_clean_payee",
     "One larger payment to a payee with no outside flag",
     lambda j: j["n_payments"] == 1),
    ("run_of_small_payments",
     "A short run of small payments (all under ₹10,000), payee never flagged",
     lambda j: j["all_under_10k"]),
    ("several_larger_payments",
     "Several larger payments that stayed just under the pause line, payee never flagged",
     lambda j: True),
]

MISSED_TEXT = {
    "flagged_payee_under_line": (
        "The payee was already on the cybercrime registry, rated high risk by the telecom department, or in a "
        "live mule network, but the rest of the payment looked ordinary, so the fused risk stayed under the pause "
        "line. This is by design: one outside flag on its own raises the payee family, it does not interrupt. "
        "The cost is that these victims got at most a one-line note."),
    "one_small_payment": (
        "The whole scam was a single small payment. With no history building up and no flag on the payee, "
        "there is almost nothing to separate it from the many genuine first payments of that size."),
    "one_large_payment_clean_payee": (
        "A single larger payment to an account that had never been reported and was not in a detected mule "
        "network. Without a second payment, the journey signals (rising amounts, several new payees) never "
        "appear, and the payee family has nothing to go on."),
    "run_of_small_payments": (
        "Two to five small payments, often 'fees' or 'test' amounts, to clean-looking accounts. Each one is "
        "small enough to look like everyday spending, and the run ended before the series features or the "
        "mule-network detector caught up. Loan-fee and marketplace scams make up most of this group."),
    "several_larger_payments": (
        "Several larger payments, but each step stayed just under the pause line: the payee was never flagged "
        "and the growth from one payment to the next was gradual. The memory rule only escalates after a "
        "warning, so it never started."),
}


def _flag(s: pd.DataFrame) -> pd.Series:
    return ((s.payee_registry >= 1) | (s.payee_fri_band >= 2) | (s.payee_in_campaign >= 1))


def _feat_row(r: pd.Series) -> dict:
    return {c: (None if pd.isna(r[c]) else float(r[c])) for c in ALL_FEATURES}


def _shown(r: pd.Series, s_fused: float, tier: int, t2: float) -> dict:
    d = {c: (None if pd.isna(r[c]) else (bool(r[c]) if c == "consent" else float(r[c]))) for c in SHOWN}
    d["fused_risk"] = round(float(s_fused), 4)
    d["pause_line"] = round(float(t2), 4)
    d["tier"] = f"{tier} {TIER_NAMES[tier]}"
    return d


def missed_victims(base: dict) -> dict:
    ft, pol, jt, St, w = base["ft"], base["pol"], base["jt"], base["St"], base["bundle"]["reason_weights"]
    t2 = base["thr"].t2
    rows = []
    for _, j in jt[~jt.warned].iterrows():
        s = ft[(ft.journey.values == j.journey) & (ft.is_scam.values == 1)].sort_values("t", kind="mergesort")
        p = pol.loc[s.index]
        consent = bool(s.consent.iloc[0])
        rec = {
            "journey": int(j.journey), "victim": int(j.victim), "scam_type": j.workflow,
            "n_payments": int(len(s)), "total": float(s.amount.sum()), "largest": float(s.amount.max()),
            "highest_tier": int(p.tier.max()), "opted_in": consent,
            "on_a_call": bool(consent and (np.nan_to_num(s.on_call.values) == 1).any()),
            "outside_flag": bool(_flag(s).any()), "all_under_10k": bool((s.amount < 10_000).all()),
            "highest_fused_risk": float(St.loc[s.index, "s_fused"].max()),
        }
        rec["group"] = next(g for g, _, rule in MISSED_GROUPS if rule(rec))
        i = St.loc[s.index, "s_fused"].idxmax()                # the step that came closest
        rec["closest_step"] = _shown(ft.loc[i], St.at[i, "s_fused"], int(pol.at[i, "tier"]), t2)
        rec["reasons_it_would_show"] = [{"code": x["code"], "en": x["en"]} for x in top_reasons(_feat_row(ft.loc[i]), w)]
        rows.append(rec)
    d = pd.DataFrame(rows)
    groups = []
    for g, title, _ in MISSED_GROUPS:
        m = d[d.group == g]
        if m.empty:
            continue
        ex = m.sort_values("total", ascending=False).iloc[0]   # the costliest miss in the group
        groups.append({
            "group": g, "title": title, "count": int(len(m)),
            "scam_types": m.scam_type.value_counts().to_dict(),
            "not_opted_in": int((~m.opted_in).sum()), "on_a_call": int(m.on_a_call.sum()),
            "got_a_note": int((m.highest_tier == 1).sum()), "got_nothing": int((m.highest_tier == 0).sum()),
            "money": float(m.total.sum()), "explanation": MISSED_TEXT[g],
            "example": {k: ex[k] for k in ["journey", "scam_type", "n_payments", "total", "largest", "highest_tier",
                                           "opted_in", "on_a_call", "outside_flag", "closest_step",
                                           "reasons_it_would_show"]},
        })
    assert d.group.notna().all() and sum(g["count"] for g in groups) == len(d), "a missed victim has no group"
    return {"n": int(len(d)), "of": int(len(jt)), "groups": groups, "victims": rows}


def false_pauses(base: dict) -> dict:
    ft, pol, St, w = base["ft"], base["pol"], base["St"], base["bundle"]["reason_weights"]
    t2 = base["thr"].t2
    gen = ft.is_scam.values == 0
    fp = ft[gen & (pol.tier.values >= kit.INTERRUPT)]
    kind_n = pd.Series(ft.kind.values[gen]).map(KIND_NAME).value_counts()
    rows = []
    for i, r in fp.iterrows():
        rs = top_reasons(_feat_row(r), w)
        consent = bool(r.consent)
        rows.append({"pid": int(r.pid), "kind": KIND_NAME[int(r.kind)], "amount": float(r.amount),
                     "tier": int(pol.at[i, "tier"]), "escalated_by_memory": bool(pol.at[i, "escalated"]),
                     "opted_in": consent, "on_a_call": bool(consent and r.on_call == 1),
                     "top_reason": rs[0]["code"] if rs else "NONE", "reasons": [x["code"] for x in rs],
                     "fused_risk": float(St.at[i, "s_fused"]), "_i": i})
    d = pd.DataFrame(rows)
    groups = []
    for kind, m in d.groupby("kind"):
        ex = m.sort_values(["tier", "fused_risk"], ascending=False).iloc[0]
        i = ex["_i"]
        groups.append({
            "group": kind, "count": int(len(m)),
            "scored_payments_of_kind": int(kind_n.get(kind, 0)),
            "false_pauses_per_10k_of_kind": kit.per10k(len(m), int(kind_n.get(kind, 0))),
            "trusted_checks": int((m.tier == 3).sum()), "holds": int((m.tier == 4).sum()),
            "on_a_call": int(m.on_a_call.sum()), "escalated_by_memory": int(m.escalated_by_memory.sum()),
            "top_reasons": m.top_reason.value_counts().head(3).to_dict(),
            "example": {"pid": int(ex.pid), "amount": float(ex.amount), "tier": int(ex.tier),
                        "features": _shown(ft.loc[i], St.at[i, "s_fused"], int(ex.tier), t2),
                        "reasons_shown": [{"code": x["code"], "en": x["en"]} for x in top_reasons(_feat_row(ft.loc[i]), w)]},
        })
    groups.sort(key=lambda g: -g["count"])
    assert sum(g["count"] for g in groups) == len(d) == base["head"]["genuine_t2plus"]
    tiers = d.tier.value_counts().to_dict()
    way_out = {
        "pauses": int(tiers.get(2, 0)), "trusted_checks": int(tiers.get(3, 0)), "holds": int(tiers.get(4, 0)),
        "escalated_by_memory": int(d.escalated_by_memory.sum()),
        "text": (f"{tiers.get(2, 0)} of the {len(d)} were a pause: the customer sees three facts, waits 30 seconds "
                 f"and can press Continue, so the cost is about half a minute. {tiers.get(3, 0)} reached a trusted "
                 f"check (₹50,000+ with two kinds of evidence, or within 7 days of continuing past a warning): the "
                 f"payment waits for the trusted person or up to an hour, then the customer decides. "
                 f"{tiers.get(4, 0)} reached a hold, so no genuine payment was stopped for an officer."),
    }
    by_call = d.groupby("on_a_call").size().rename({True: "on a call", False: "not on a call"}).to_dict()
    by_reason = d.top_reason.value_counts().to_dict()
    return {"n": int(len(d)), "customer_payments": int(base["n_all"]),
            "rate": kit.per10k(len(d), base["n_all"]), "groups": groups, "way_out": way_out,
            "by_call": by_call, "by_top_reason": by_reason,
            "payments": d.drop(columns="_i").to_dict(orient="records")}


def round1_weak_spots() -> list[dict]:
    """Weak spots measured by run_all.py, read from the committed outputs/ (never recomputed here)."""
    o = kit.ROOT / "outputs"
    pw = pd.read_csv(o / "tables" / "per_workflow.csv", index_col=0)
    ho = pd.read_csv(o / "tables" / "held_out_workflows.csv", index_col=0)
    st = pd.read_csv(o / "tables" / "stress_tests.csv", index_col=0)
    rec = json.load(open(o / "metrics.json"))["recogniser"]["by_workflow"]
    mk = pw.loc["marketplace"]
    return [
        {"what": "Marketplace ('scan to receive') scams",
         "measured": f"{mk.victims_warned:.0%} of victims warned, {mk.warned_at_first_payment:.0%} at the first "
                     f"payment, {ho.loc['marketplace', 'victims_warned_hidden']:.0%} when the type is hidden from "
                     f"training (n = {int(mk.journeys)})",
         "why": "small amounts to clean accounts, often without a call; the 'pay to receive' QR trick is not modelled",
         "source": "outputs/tables/per_workflow.csv, held_out_workflows.csv"},
        {"what": "All three scammer tactics at once (no calls, clean mules, no hooks)",
         "measured": f"{st.loc['All three at once', 'victims_warned']:.0%} of victims warned (model not retrained)",
         "why": "each tactic silences one kind of evidence; together they leave mostly the journey family",
         "source": "outputs/tables/stress_tests.csv"},
        {"what": "Scam-type guesser on the new loan-fee scam",
         "measured": f"right {rec['loan_fee']['accuracy_when_named']:.1%} of the time when it names a type "
                     f"(it names one for {rec['loan_fee']['named_share']:.0%} of warned loan-fee payments)",
         "why": "it had only 2 loan-fee labels, so it confidently names a known type instead of saying "
                "'unfamiliar'; officers would see a wrong label exactly when a new scam appears. It never "
                "changes the tier or the advice the customer sees",
         "source": "outputs/metrics.json: recogniser.by_workflow.loan_fee"},
    ]


def section(res: dict) -> str:
    mv, fp = res["missed"], res["false_pauses"]
    md = [f"**Measured, synthetic** (frozen {kit.system()}, test days 42–59). Every missed victim and every "
          "false pause belongs to exactly one group below; the full lists are in `results/failure_cases.json`.", ""]
    md += [f"### Victims we never warned: {mv['n']} of {mv['of']}", "",
           "Groups are assigned in this order, first match wins.", ""]
    md.append(kit.md_table(
        ["Group", "Victims", "Scam types", "Got a note / nothing", "Not opted in", "On a call", "Money lost"],
        [[g["title"], g["count"], ", ".join(f"{k} {v}" for k, v in g["scam_types"].items()),
          f"{g['got_a_note']} / {g['got_nothing']}", g["not_opted_in"], g["on_a_call"], inr(g["money"])]
         for g in mv["groups"]]))
    md.append("")
    for g in mv["groups"]:
        ex = g["example"]
        cs = ex["closest_step"]
        why = "; ".join(x["en"] for x in ex["reasons_it_would_show"]) or "no reason codes were true"
        md += [f"**{g['title']}** ({g['count']}). {g['explanation']}", "",
               f"*Example:* journey {ex['journey']}, {ex['scam_type']}, {ex['n_payments']} payment(s), "
               f"{inr(ex['total'])} in total. Its closest step had fused risk {cs['fused_risk']} against a pause line "
               f"of {cs['pause_line']} and ended at tier {cs['tier']}. Reasons it would show: {why}.", ""]
    md += [f"### False pauses: {fp['n']} genuine payments interrupted", "",
           f"{kit.fmt_rate(fp['rate'])} customer payments, shop payments included.", ""]
    md.append(kit.md_table(
        ["Genuine kind", "False pauses", "Per 10,000 of that kind (95% CI)", "Trusted checks", "Holds",
         "On a call", "After Continue (memory)", "Top reasons"],
        [[g["group"], g["count"], f"{g['false_pauses_per_10k_of_kind']['value']:.1f} "
          f"({g['false_pauses_per_10k_of_kind']['lo']:.1f}–{g['false_pauses_per_10k_of_kind']['hi']:.1f}) "
          f"of {g['scored_payments_of_kind']:,}", g["trusted_checks"], g["holds"], g["on_a_call"],
          g["escalated_by_memory"], ", ".join(f"{k} {v}" for k, v in g["top_reasons"].items())]
         for g in fp["groups"]]))
    md += ["", f"By call: {', '.join(f'{k} {v}' for k, v in fp['by_call'].items())}. "
           f"By the top reason shown: {', '.join(f'{k} {v}' for k, v in fp['by_top_reason'].items())}.", ""]
    for g in fp["groups"]:
        ex = g["example"]
        why = "; ".join(x["en"] for x in ex["reasons_shown"])
        md.append(f"- *{g['group']}*, e.g. payment {ex['pid']}, {inr(ex['amount'])}, tier {ex['features']['tier']}: "
                  f"\"{why}\".")
    md += ["", f"**The way out for these customers.** {fp['way_out']['text']} "
           f"{fp['way_out']['escalated_by_memory']} of them were stricter than they would otherwise have been "
           f"because the customer had continued past an earlier warning in the last 7 days.", ""]
    md += ["### Weak spots measured by `run_all.py`", ""]
    md.append(kit.md_table(["Weak spot", "Measured (synthetic)", "Why", "Source"],
                           [[w["what"], w["measured"], w["why"], f"`{w['source']}`"] for w in res["round1"]]))
    return "\n".join(md)


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    base = kit.get_baseline(a.cache)
    res = {"data": f"synthetic test days 42-59, frozen {kit.system(base)}",
           "missed": missed_victims(base), "false_pauses": false_pauses(base), "round1": round1_weak_spots()}
    path = kit.save_json(res, out / "failure_cases.json")
    sec = kit.write_section(out, "failure_cases", section(res))
    kit.log_run(out, kit.new_run_id("failure_cases"), None, [path, sec],
                command=kit.cmd(__file__, argv))
    for g in res["missed"]["groups"]:
        print(f"missed  {g['count']:3d}  {g['title']}")
    for g in res["false_pauses"]["groups"]:
        print(f"false   {g['count']:3d}  {g['group']}")
    print(f"saved {path}")
    return res


if __name__ == "__main__":
    main()
