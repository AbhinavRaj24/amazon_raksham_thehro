"""Export typical journeys from the test weeks, with real model outputs, for the demo.

Nothing in a scenario is hand-written except the one-line story: amounts, times,
signals, scores, tiers, reasons, messages and evidence records all come from the
pipeline run.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MIN_PER_DAY, TIER_NAMES, VALID_END
from .explain import (EvidenceLog, advice_keys, build_message, decision_record, inr, ncrp_draft,
                      top_reasons, workflow_guess)
from .features import ALL_FEATURES
from .models import WORKFLOW_CONFIDENCE_FLOOR
from .simulate import (ACCOUNT_TYPES, AGE_BANDS, CHANNELS, KIND, LANGS, REGIONS, SEGMENTS, WORKFLOW_LABEL,
                       WORKFLOWS)

STORIES = {
    "investment": "Added to a WhatsApp 'stock tips' group run by a fake SEBI-registered adviser. A trading app "
                  "shows big paper profits; every withdrawal needs another 'tax'.",
    "digital_arrest": "A video caller in uniform says a parcel in her name held drugs. She must stay on the call "
                      "and move her savings to a 'safe account' for verification.",
    "task": "A part-time job: like videos, get paid. Small commissions arrive first; then 'prepaid tasks' need "
            "ever larger top-ups to unlock the earnings.",
    "customer_care": "Searched online for a refund helpline and called the number. The 'agent' asks him to "
                     "install a support app and share his screen.",
    "marketplace": "Listed an old sofa online. The 'buyer' sends a QR code 'to receive the payment' and keeps "
                   "asking for one more try.",
    "voice_clone": "A call from a new number in his son's voice: an accident, police, money needed right now, "
                   "don't tell anyone.",
    "loan_fee": "An instant-loan app approves Rs 2 lakh but first needs a 'processing fee', then 'insurance', then "
                "'GST'. A new scam type: it first appeared in week 5 and the model had seen only a couple of "
                "reports of it.",
}
GENUINE_STORIES = {
    "new_landlord": "Moving flats: first rent and deposit to a new landlord, a large first payment to a stranger.",
    "medical": "Paying a hospital's account for a parent's treatment while on the phone with the hospital.",
    "false_alarm": "A genuine payment that still looked risky. This is what a false alarm costs.",
}

DISPLAY_FEATURES = [
    "amount_log", "is_new_payee", "n_prev_to_payee", "amt_vs_usual", "new_payees_7d", "recent_payee_payments_7d",
    "recent_payee_sum_7d_log", "push_out_24h_vs_usual", "fd_break_48h", "stranger_credits_24h", "is_night",
    "payee_type", "payee_age_days", "payee_idle_days", "payee_new_payers_24h", "payee_inflow_24h_log",
    "payee_passthrough_24h", "payee_registry", "payee_fri_band", "payee_in_campaign", "payee_campaign_size",
    "on_call", "caller_unknown", "call_ott", "call_min", "caller_fri_band", "screen_share", "remote_app",
    "app_switches",
]


def _clean(x):
    if isinstance(x, (np.floating, float)):
        return None if np.isnan(x) else round(float(x), 4)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _acct(payee: int) -> str:
    return f"A/c ••{(payee * 7919) % 10000:04d}"


def _when(t: float) -> str:
    day = int(t // MIN_PER_DAY)
    mins = int(t % MIN_PER_DAY)
    return f"Day {day + 1} · {mins // 60:02d}:{mins % 60:02d}"


def _step(i, f, S, pol, weights, log: EvidenceLog, versions):
    row = f.loc[i]
    rd = {c: _clean(row[c]) for c in ALL_FEATURES + ["amount"]}
    tier = int(pol.loc[i, "tier"])
    reasons = top_reasons(rd, weights, k=3)
    adv = advice_keys(f.loc[[i]])[0]
    guess = workflow_guess(S.loc[i, "wf_pred"], S.loc[i, "wf_conf"], WORKFLOW_CONFIDENCE_FLOOR)
    payment = {"payment_id": f"P{int(row.pid)}", "customer_ref": f"C{int(row.payer)}", "payee_ref": _acct(int(row.payee)),
               "amount": float(row.amount), "channel": CHANNELS[int(row.channel)], "time": _when(row.t)}
    scores = {k: float(S.loc[i, k]) for k in ["s_fused", "s_journey", "s_payee", "s_context"]}
    decision = {"tier": tier,
                "families": {"journey": bool(pol.loc[i, "elev_journey"]), "payee": bool(pol.loc[i, "elev_payee"]),
                             "context": bool(pol.loc[i, "elev_context"])},
                "gate": bool(pol.loc[i, "gate"]), "payee_flag": bool(pol.loc[i, "payee_flag"]),
                "memory_note": ("customer continued past a warning in the last 7 days"
                                if bool(pol.loc[i, "escalated"]) else None)}
    rec = None
    if tier >= 2:
        rec = log.append("decision", decision_record(payment, scores, decision, reasons, guess, versions),
                         at=f"simulated {payment['time']}")
    return {
        "payment": payment,
        "amount_text": inr(row.amount),
        "payee_kind": ACCOUNT_TYPES[int(row.payee_type)],
        "features": {c: _clean(row[c]) for c in DISPLAY_FEATURES},
        "consent": bool(row.consent),
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "families": decision["families"],
        "gate": decision["gate"],
        "payee_flag": decision["payee_flag"],
        "tier": tier, "tier_name": TIER_NAMES[tier],
        "base_tier": int(pol.loc[i, "base_tier"]),
        "escalated": bool(pol.loc[i, "escalated"]),
        "reasons": reasons,
        "advice": adv,
        "message": {"en": build_message(tier, reasons, adv, "en"), "hi": build_message(tier, reasons, adv, "hi")},
        "workflow_guess": guess,
        "workflow_confidence": round(float(S.loc[i, "wf_conf"]), 3),
        "evidence": rec,
    }


def _persona(world, user: int) -> dict:
    u = world.users.iloc[user]
    return {"age_band": AGE_BANDS[int(u.age_band)], "lang": LANGS[int(u.lang)], "region": REGIONS[int(u.region)],
            "segment": SEGMENTS[int(u.segment)], "consent": bool(u.consent), "trusted_contact": bool(u.trusted_contact)}


SELECTION_NOTE = ("Each scam scenario is a real journey from the test weeks, chosen to be typical for its type: "
                  "warned (as most were), close to the median loss, first warned at the most common step, "
                  "2-6 payments, and a customer who opted in to call signals so every signal family is visible.")


def pick_journeys(jt: pd.DataFrame, f: pd.DataFrame) -> dict:
    """One typical, warned journey per scam type (see SELECTION_NOTE)."""
    from .explain import ADVICE_FIT, advice_keys
    consent = f.groupby("payer").consent.first()
    scam = f[f.is_scam.values == 1]
    adv = pd.Series(advice_keys(scam), index=scam.index)
    picks = {}
    for wf in WORKFLOWS:
        g = jt[(jt.workflow == wf) & jt.warned].copy()
        if g.empty:
            continue
        g["consent"] = consent.reindex(g.victim).values
        fits = adv[scam.journey.isin(g.journey)].groupby(scam.journey).apply(
            lambda a: float(np.mean([k in ADVICE_FIT[wf] for k in a])))
        g["fit"] = fits.reindex(g.journey).fillna(0).values
        mode_step = int(g.first_alert_step.mode().iloc[0])
        med = g.total.median()
        g["score"] = (np.abs(np.log(g.total / med)) + 0.6 * np.abs(g.first_alert_step - mode_step)
                      + 0.8 * (~g.consent.astype(bool)) + 0.3 * np.abs(g.n_steps.clip(upper=6) - 3)
                      + 2.0 * (g.n_steps < 2) + 2.0 * (g.n_steps > 6) + 0.5 * (1 - g.fit))
        picks[wf] = int(g.sort_values("score").journey.iloc[0])
    return picks


def export(world, f, S, pol_test, jt, weights, metrics: dict, versions: dict) -> dict:
    """Everything the demo shows. f/S cover the test weeks; pol_test is the policy output for them."""
    out = {"meta": {"versions": versions, "note": "All data synthetic. " + SELECTION_NOTE},
           "scenarios": []}
    picks = pick_journeys(jt, f)
    for wf, jid in picks.items():
        log = EvidenceLog()
        idx = f.index[(f.journey.values == jid) & (f.is_scam.values == 1)]
        idx = f.loc[idx].sort_values("t").index
        steps = [_step(i, f, S, pol_test, weights, log, versions) for i in idx]
        user = int(f.loc[idx[0], "payer"])
        jrow = jt.set_index("journey").loc[jid]
        first = next((s for s in steps if s["tier"] >= 2), None)
        out["scenarios"].append({
            "id": wf, "kind": "scam", "title": WORKFLOW_LABEL[wf], "story": STORIES[wf],
            "persona": _persona(world, user), "steps": steps,
            "total": float(jrow.total), "total_text": inr(jrow.total),
            "first_warning_step": int(jrow.first_alert_step),
            "expected_protected": float(jrow.prevented), "expected_protected_text": inr(jrow.prevented),
            "ncrp_draft": ncrp_draft(first["payment"] | {"channel": CHANNELS.index(first["payment"]["channel"])},
                                     WORKFLOWS[int(f.loc[idx[0], "workflow"])], first["reasons"]) if first else None,
            "evidence_chain_ok": log.verify(),
        })
    # genuine scenarios
    gen = (f.is_scam.values == 0)
    tiers = pol_test.tier.values
    cand = {
        "new_landlord": gen & (f.kind.values == KIND["new_landlord"]) & (f.is_new_payee.values == 1)
                        & (f.amount.values >= 20_000) & (tiers <= 1),
        "medical": gen & (f.kind.values == KIND["medical"]) & (np.nan_to_num(f.on_call.values) == 1) & (tiers <= 1),
        "false_alarm": gen & (tiers == 2),
    }
    for key, m in cand.items():
        idx = f.index[m]
        if len(idx) == 0:
            continue
        sub = S.loc[idx, "s_fused"]
        # typical rather than extreme: the payment closest to the median score of its group
        i = (sub - sub.median()).abs().idxmin() if key != "false_alarm" else sub.idxmax()
        log = EvidenceLog()
        out["scenarios"].append({
            "id": key, "kind": "genuine", "title": {"new_landlord": "New landlord", "medical": "Hospital bill",
                                                    "false_alarm": "A false alarm"}[key],
            "story": GENUINE_STORIES[key], "persona": _persona(world, int(f.loc[i, "payer"])),
            "steps": [_step(i, f, S, pol_test, weights, log, versions)],
        })
    out["results"] = metrics
    return out
