"""What caller identity would add, and why the shipped system doesn't use it.

    python scripts/caller_identity.py
    python scripts/caller_identity.py --no-attacks

A bank app on Android can't read the caller's number (call-log access is restricted to the phone app), so the
shipped models and policy (outputs/, policy v4) use no caller identity: no `caller_unknown` ("caller not in
contacts") and no `caller_fri_band` (the caller's DoT fraud-risk rating). This script trains the counterfactual:
the same models WITH those two columns (same settings, labels and training days), with the v4 policy allowed to use
the caller's DoT rating as context evidence and the call reasons allowed to name an unknown caller, and lines set on
the validation week only. It reports what that would change on the test days and under the attacks, so the cost of
staying deployable is a measured number. (Before the finale this was P2's "model v2" check, run the other way
round.)

Writes test_kit/results/caller_identity.json and the "caller_id" section of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import evaluate as E  # noqa: E402
from thehro import kit, redteam  # noqa: E402
from thehro.models import CALLER_FEATURES, fit, model_features, score, training_labels  # noqa: E402

ATTACKS = ["chat_only", "split", "aged_mules", "clean_mules", "all"]


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7, help="attack seed")
    ap.add_argument("--no-attacks", action="store_true")
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    base = kit.get_baseline(a.cache)
    t0 = time.time()
    shipped_feats, _ = model_features(base["bundle"]["models"])
    if any(c in shipped_feats for c in CALLER_FEATURES) or base["rules"].caller_id:
        msg = "outputs/ already uses caller identity, so there is no counterfactual to train"
        kit.write_section(out, "caller_id", f"_Not run: {msg}._")
        print(msg)
        return {}
    f, denom = base["f"], base["denom"]
    rules_c = replace(base["rules"], name=base["rules"].name + "+caller", caller_id=True)
    window, y = training_labels(f)
    m_c = fit(f, window, y, verbose=False)                       # all 39 columns, caller identity included
    S_c = score(m_c, f)
    thr_c = E.thresholds_from_validation(f, S_c, denom, rules=rules_c)
    w = E.windows(f)
    ft, St_c = f[w["test"]], S_c[w["test"]]
    pol_c = E.run_policy(ft, St_c, thr_c, rules_c)
    head_c, jt_c = E.summarise(base["world"], ft, pol_c, base["n_all"], base["n_users"])
    shipped = kit.headline_rates(base["head"], base["jt"], base["ft"], base["pol"].tier.values, base["n_all"])
    with_c = kit.headline_rates(head_c, jt_c, ft, pol_c.tier.values, base["n_all"])
    msg = {"shipped": E.message_fit(base["ft"], base["pol"].tier.values, caller_id=False)["specific_and_fits"],
           "with_caller_id": E.message_fit(ft, pol_c.tier.values, caller_id=True)["specific_and_fits"]}
    pw_s = E.per_workflow(base["jt"], base["ft"], base["pol"].tier.values)
    pw_c = E.per_workflow(jt_c, ft, pol_c.tier.values)
    res = {"data": "synthetic; the counterfactual is refit on days 14-34, lines on days 35-41, tested on days 42-59",
           "caller_features": list(CALLER_FEATURES), "shipped": shipped, "with_caller_id": with_c,
           "lines": {"shipped": {"t2": base["thr"].t2, "tj": base["thr"].tj},
                     "with_caller_id": {"t2": thr_c.t2, "tj": thr_c.tj}},
           "per_type": {"shipped": pw_s.victims_warned, "with_caller_id": pw_c.victims_warned},
           "message_fit": msg, "attacks": []}
    print(f"shipped victims {shipped['victims_warned']['value']:.1%} fp {shipped['false_pauses_per_10k']['value']:.2f} | "
          f"with caller id {with_c['victims_warned']['value']:.1%} fp {with_c['false_pauses_per_10k']['value']:.2f} "
          f"({time.time() - t0:.0f}s)", flush=True)
    if not a.no_attacks:
        for name in ATTACKS:
            r_s = redteam.run(base, name, a.seed)
            r_c = redteam.run(dict(base, rules=rules_c), name, a.seed, models=m_c, thr=thr_c)
            row = {"attack": name, "label": redteam.ATTACKS[name],
                   "shipped": kit.headline_rates(r_s["head"], r_s["jt"], r_s["ft"], r_s["pol"].tier.values, r_s["n_all"]),
                   "with_caller_id": kit.headline_rates(r_c["head"], r_c["jt"], r_c["ft"], r_c["pol"].tier.values,
                                                        r_c["n_all"])}
            res["attacks"].append(row)
            print(f"{row['label']:62s} shipped {row['shipped']['victims_warned']['value']:.1%}  "
                  f"with caller id {row['with_caller_id']['victims_warned']['value']:.1%}", flush=True)
    path = kit.save_json(res, out / "caller_identity.json")
    rows = [["Test days (as simulated)", kit.fmt_share(shipped["victims_warned"]), kit.fmt_share(with_c["victims_warned"]),
             kit.fmt_rate(shipped["false_pauses_per_10k"]), kit.fmt_rate(with_c["false_pauses_per_10k"])]]
    rows += [[r["label"], kit.fmt_share(r["shipped"]["victims_warned"]), kit.fmt_share(r["with_caller_id"]["victims_warned"]),
              kit.fmt_rate(r["shipped"]["false_pauses_per_10k"]), kit.fmt_rate(r["with_caller_id"]["false_pauses_per_10k"])]
             for r in res["attacks"]]
    dv = 100 * (with_c["victims_warned"]["value"] - shipped["victims_warned"]["value"])
    dfp = with_c["false_pauses_per_10k"]["value"] - shipped["false_pauses_per_10k"]["value"]
    md = [f"**Measured, synthetic.** The shipped system ({kit.system(base)}) uses no caller identity, because a bank app "
          "can't read the caller's number. The counterfactual is the same models trained *with* `caller_unknown` and "
          "`caller_fri_band` (same settings, labels and training days), the policy allowed to count the caller's DoT "
          "rating as context evidence, and lines set on the validation week only.", "",
          kit.md_table(["Data", "Victims warned (shipped)", "Victims warned (with caller id)",
                        "False pauses (shipped)", "False pauses (with caller id)"], rows),
          "", f"Caller identity would change victims warned by {dv:+.1f} points and false pauses by {dfp:+.2f} per "
          "10,000 on the test days. That is the measured price of staying deployable on a phone.", "",
          kit.md_table(["Scam type", "Victims warned (shipped)", "Victims warned (with caller id)"],
                       [[wf, f"{pw_s.victims_warned[wf]:.1%}", f"{pw_c.victims_warned[wf]:.1%}"] for wf in pw_s.index]),
          "", f"**Messages.** The advice fits the actual scam for {msg['shipped']:.0%} of warned scam payments in the "
          f"shipped system (the call reason says only that a call is on, and for how long), and would for "
          f"{msg['with_caller_id']:.0%} with caller identity."]
    sec = kit.write_section(out, "caller_id", "\n".join(md))
    kit.log_run(out, kit.new_run_id("caller_identity"), None if a.no_attacks else a.seed, [path, sec],
                command=kit.cmd(__file__, argv))
    print(f"saved {path}")
    return res


if __name__ == "__main__":
    main()
