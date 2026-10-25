"""L-3a: switch each signal off in turn and report what it adds (slide 10: "switch off network and phone signals in
turn; each signal's gain reported").

    python scripts/signal_switchoff.py

Frozen model and lines in outputs/, on a copy of the test-day features (genuine and scam rows alike, since a
switch-off removes the signal for everyone). The "gain" of a signal is the baseline minus the switched-off value.

Writes test_kit/results/signal_switchoff.json and the Signals switched off section of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import evaluate as E  # noqa: E402
from thehro import kit  # noqa: E402
from thehro.features import C_FEATURES  # noqa: E402
from thehro.models import score  # noqa: E402
from thehro.policy import apply_memory, base_tiers, evidence  # noqa: E402

VARIANTS = [
    ("baseline", "Nothing switched off (baseline)"),
    ("phone_off", "Phone signals off (nobody opted in)"),
    ("network_off", "Mule-network flag off"),
    ("feeds_off", "Outside feeds off (registry, telecom FRI)"),
    ("hooks_off", "Hooks off (FD break, small credits from strangers)"),
    ("memory_off", "Memory off (no escalation after Continue)"),
    ("gate_off", "Two-family gate off (one kind of evidence can delay money)"),
]


def edited(ft: pd.DataFrame, which: str) -> pd.DataFrame:
    g = ft.copy()
    if which == "phone_off":
        for c in C_FEATURES:
            g[c] = np.nan
        g["consent"] = False
    elif which == "network_off":
        g["payee_in_campaign"] = 0
        g["payee_campaign_size"] = 1
    elif which == "feeds_off":
        g["payee_registry"] = 0
        g["payee_fri_band"] = 0
        g.loc[g.consent.values.astype(bool), "caller_fri_band"] = 0.0     # stays blank without consent
    elif which == "hooks_off":
        g["fd_break_48h"] = 0
        g["stranger_credits_24h"] = 0.0
    return g


def tiers_for(base: dict, which: str):
    ft, St, thr = base["ft"], base["St"], base["thr"]
    if which == "baseline":
        return ft, base["pol"].tier.values
    if which == "memory_off":
        return ft, base_tiers(St, ft, thr, rules=base["rules"])
    if which == "gate_off":
        ev = evidence(St, ft, thr)
        ev["gate"] = np.ones(len(ft), dtype=bool)
        ev["hold_ok"] = ev["payee_flag"] & (St["s_fused"].values >= thr.t4)
        tier, _, _ = apply_memory(ft, base_tiers(St, ft, thr, ev, base["rules"]), ev, rules=base["rules"])
        return ft, tier
    g = edited(ft, which)
    S = score(base["bundle"]["models"], g)
    return g, E.run_policy(g, S, thr, base["rules"]).tier.values


def memory_note(b: dict, m: dict) -> str:
    dv = 100 * (b["victims_warned"]["value"] - m["victims_warned"]["value"])
    dfp = b["false_pauses_per_10k"]["value"] - m["false_pauses_per_10k"]["value"]
    dmoney = 100 * (b["money_protected_share_modelled"] - m["money_protected_share_modelled"])
    return (f"- **Memory** (after Continue, the next 7 days are stricter): it changes victims warned by {dv:+.1f} "
            f"points, because it acts only on customers already warned once. Its job is later payments: without it, "
            f"money protected (modelled) is {m['money_protected_share_modelled']:.1%} instead of "
            f"{b['money_protected_share_modelled']:.1%} ({dmoney:+.1f} points). The cost is "
            f"{dfp:+.2f} false pauses per 10,000 and {b['genuine_trusted_checks_per_10k']['k'] - m['genuine_trusted_checks_per_10k']['k']:+d} "
            "genuine trusted checks, from genuine customers who had continued past an earlier warning.")


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    base = kit.get_baseline(a.cache)
    rows = []
    for which, label in VARIANTS:
        g, tiers = tiers_for(base, which)
        pol = pd.DataFrame({"tier": np.asarray(tiers, dtype=np.int8)}, index=g.index)
        head, jt = E.summarise(base["world"], g, pol, base["n_all"], base["n_users"])
        r = kit.headline_rates(head, jt, g, pol.tier.values, base["n_all"])
        rows.append({"variant": which, "label": label, **r})
        print(f"{label:62s} victims {r['victims_warned']['value']:.1%}  false pauses "
              f"{r['false_pauses_per_10k']['value']:.2f}  genuine trusted {r['genuine_trusted_checks_per_10k']['k']}  "
              f"genuine holds {r['genuine_holds_per_10k']['k']}", flush=True)
    b = rows[0]
    for r in rows:
        r["gain_victims_warned_points"] = 100 * (b["victims_warned"]["value"] - r["victims_warned"]["value"])
        r["gain_false_pauses_per_10k"] = b["false_pauses_per_10k"]["value"] - r["false_pauses_per_10k"]["value"]
    res = {"data": f"synthetic test days 42-59, frozen {kit.system(base)}", "rows": rows}
    path = kit.save_json(res, out / "signal_switchoff.json")
    tab = [[r["label"], kit.fmt_share(r["victims_warned"]), kit.fmt_rate(r["false_pauses_per_10k"]),
            r["genuine_trusted_checks_per_10k"]["k"], r["genuine_holds_per_10k"]["k"],
            f"{r['scam_payments_paused']['value']:.1%}",
            "–" if r["variant"] == "baseline" else f"{r['gain_victims_warned_points']:+.1f} points",
            "–" if r["variant"] == "baseline" else f"{r['gain_false_pauses_per_10k']:+.2f}"] for r in rows]
    by = {r["variant"]: r for r in rows}
    ph = by["phone_off"]
    no_opt = kit.group_rates(base["world"], base["ft"], base["pol"].tier.values, base["jt"],
                             base["denom"])["consent"]["not opted in"]["false_pauses"]["value"]
    notes = [
        f"- **Phone signals:** without them {ph['victims_warned']['value']:.1%} of victims are still warned, but false "
        f"pauses rise from {b['false_pauses_per_10k']['value']:.2f} to {ph['false_pauses_per_10k']['value']:.2f} per "
        "10,000. The model seems to use phone context to *clear* risky-looking genuine payments too (for example a "
        "large first payment made with no call in progress), the same effect seen for customers who never opted in "
        f"({no_opt:.2f} per 10,000 in the fairness table). That is a real cost of not opting in, borne by the customer.",
        f"- **Two-family gate:** with it off, {by['gate_off']['genuine_trusted_checks_per_10k']['k']} genuine payments "
        f"would wait for a trusted person and {by['gate_off']['genuine_holds_per_10k']['k']} would be held for an "
        f"officer, against {b['genuine_trusted_checks_per_10k']['k']} and {b['genuine_holds_per_10k']['k']} with it. "
        "The gate is what keeps a single kind of evidence from delaying money.",
        memory_note(b, by["memory_off"]),
        f"- **Mule-network flag:** without it {by['network_off']['victims_warned']['value']:.1%} of victims are warned "
        f"({by['network_off']['gain_victims_warned_points']:+.1f} points of gain"
        + (", the largest of any signal)." if max(rows, key=lambda r: r["gain_victims_warned_points"])["variant"]
           == "network_off" else ")."),
    ]
    md = ["**Measured, synthetic.** Each row switches one signal off for every payment on the test days and reruns "
          "the frozen model and lines. Gain = baseline minus switched-off: a positive victims gain means the signal "
          "helps warn victims; a negative false-pause gain means switching it off *adds* false pauses.", "",
          kit.md_table(["Switched off", "Victims warned", "False pauses", "Genuine trusted checks", "Genuine holds",
                        "Scam payments paused", "Gain: victims warned", "Gain: false pauses"], tab), ""] + notes
    sec = kit.write_section(out, "switchoff", "\n".join(md))
    kit.log_run(out, kit.new_run_id("signal_switchoff"), None, [path, sec],
                command=kit.cmd(__file__, argv))
    print(f"saved {path}")
    return res


if __name__ == "__main__":
    main()
