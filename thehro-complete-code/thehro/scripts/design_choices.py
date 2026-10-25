"""Design choices tried for the finale model, reproducibly: what was rejected, and how the chosen one was picked.

    python scripts/design_choices.py

A. Adversarial training (rejected). Copies of the training-day reported scams with the attack edits applied
   (phone signals silenced, payee made clean-looking, FD break and small credits removed, and all three) are added
   to the fused model's training data, labelled as scams; the family models are left as they are. Trained without
   caller identity, lines from the validation week, policy as in outputs/. Compared on the test days, as
   simulated and under the attacks. The clean-mule edit sets payee fields to fixed values, so a model trained on
   those copies can learn the edit's fingerprint rather than the scam: the "held out" row trains without the
   clean-mule copies to show what is left when the edit wasn't seen.
B. The journey-only pause line (chosen, policy v4): the share of the 1-in-10,000 pause budget given to the journey
   line, swept from 0 to 0.4 with the total budget fixed. The share in config.JOURNEY_LINE_SHARE was picked after
   looking at these test-day numbers, which is why the fresh-world test exists.
Writes test_kit/results/design_choices.json and the "design" section of test_kit/report.md.
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
from thehro import kit, redteam  # noqa: E402
from thehro.config import JOURNEY_LINE_SHARE  # noqa: E402
from thehro.features import ALL_FEATURES, FAMILIES  # noqa: E402
from thehro.models import CALLER_FEATURES, Models, _hgb, fit, score, training_labels  # noqa: E402

ATTACKS = ["chat_only", "split", "aged_mules", "clean_mules", "all"]
CTX = ["on_call", "caller_unknown", "call_ott", "call_min", "caller_fri_band", "screen_share", "remote_app"]


def attacked_copies(X: pd.DataFrame, kinds) -> list[pd.DataFrame]:
    out = []
    for k in kinds:
        g = X.copy()
        if k in ("chat", "all"):
            g[[c for c in CTX if c in g]] = 0.0          # only the columns this model uses (none of caller identity)
        if k in ("clean", "all"):                      # the same edit as evaluate.perturb(..., "clean_mules")
            g[["payee_registry", "payee_fri_band", "payee_in_campaign"]] = 0
            g["payee_campaign_size"] = 1
            g[["payee_new_payers_24h", "payee_new_payers_7d", "payee_inflow_24h_log"]] = 0.0
            g["payee_passthrough_24h"] = np.nan
            g["payee_age_days"] = 900.0
            g["payee_idle_days"] = 2.0
        if k in ("hooks", "all"):
            g[["fd_break_48h", "stranger_credits_24h"]] = 0
        out.append(g)
    return out


def fit_augmented(f, kinds, drop) -> Models:
    """models.fit's family models, with a fused model whose training data also has attacked scam copies."""
    window, y = training_labels(f)
    base = fit(f, window, y, with_recogniser=False, verbose=False, drop=drop)
    feats = base.features or ALL_FEATURES
    X, yy = f.loc[window, feats], y[window]
    pos = X[yy == 1]
    copies = attacked_copies(pos, kinds)
    Xa = pd.concat([X] + copies)
    ya = np.concatenate([yy] + [np.ones(len(pos), np.int8)] * len(copies))
    base.fused = _hgb(feats, 0).fit(Xa, ya)
    return base


def evaluate_model(base, models, thr, rules) -> dict:
    row = {}
    for name in ["rebuild"] + ATTACKS:
        r = redteam.run(dict(base, rules=rules), name, 7, models=models, thr=thr)
        h = r["head"]
        row[name] = {"victims_warned": h["victims_warned"], "false_pauses_per_10k": h["genuine_t2plus_per_10k"]}
        if name == "rebuild":
            row[name].update(first_payment=h["victims_warned_first_payment"], false_pauses=h["genuine_t2plus"],
                             money_protected_modelled=h["money_protected_share"])
    return row


def adversarial_text(adv: dict) -> str:
    """Why adversarial training was rejected, written from the numbers."""
    keys = list(adv)
    ship, full, held = (adv[k] for k in keys[:3])
    d = lambda r, k="victims_warned", a="rebuild": 100 * (r[a][k] - ship[a][k])  # noqa: E731
    return ("With the clean-mule copies in training the clean-mule and combined attacks look solved "
            f"({full['clean_mules']['victims_warned']:.0%} and {full['all']['victims_warned']:.0%}). That is the model "
            "learning the edit's fixed values (an account exactly 900 days old, idle exactly 2 days), not the scam: "
            "with those copies held out, clean mules move only from "
            f"{ship['clean_mules']['victims_warned']:.1%} to {held['clean_mules']['victims_warned']:.1%}. And both "
            f"versions are worse on real, unattacked scams: victims warned {d(full):+.1f} and {d(held):+.1f} points, "
            f"warned at the first payment {d(full, 'first_payment'):+.1f} and {d(held, 'first_payment'):+.1f} points. "
            "So the shipped model has no augmented training.")


def choice_text(sweep: dict) -> str:
    """Why the shipped share, written from the numbers so it can't contradict the table."""
    passing = [k for k, v in sweep.items() if v["all"]["victims_warned"] >= 0.85 and v["all"]["false_pauses_per_10k"] <= 1.0]
    c = sweep.get(JOURNEY_LINE_SHARE)
    if c is None:
        return f"The shipped share, {JOURNEY_LINE_SHARE}, is not in this sweep."
    first = min(passing) if passing else None
    if first is None:
        verdict = "No share in the sweep passes the slide-10 line (85% at no more than 1 false pause per 10,000)."
    elif first == JOURNEY_LINE_SHARE:
        verdict = (f"{JOURNEY_LINE_SHARE} is the smallest share that passes the slide-10 line (85% of victims warned "
                   "under the combined attack, at no more than 1 false pause per 10,000).")
    else:
        verdict = (f"Share {first} is the smallest that passes the slide-10 line, with the combined attack at "
                   f"{sweep[first]['all']['victims_warned']:.1%}; the shipped {JOURNEY_LINE_SHARE} reaches "
                   f"{c['all']['victims_warned']:.1%}, a margin over the line, for "
                   f"{c['rebuild']['false_pauses_per_10k']:.2f} false pauses per 10,000 on the test days.")
    return (f"The shipped share is {JOURNEY_LINE_SHARE}, picked after looking at these test-day numbers. {verdict} "
            "Because the choice used the test days, it is checked again on a freshly simulated world before any "
            "number is quoted as final.")


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    T = time.time()
    base = kit.get_baseline(a.cache)
    f, denom, rules = base["f"], base["denom"], base["rules"]
    drop = () if rules.caller_id else CALLER_FEATURES

    # A. adversarial training
    adv = {"shipped (no augmentation)": evaluate_model(base, base["bundle"]["models"], base["thr"], rules)}
    for label, kinds in [("+ all four attacked copies", ("chat", "clean", "hooks", "all")),
                         ("+ chat and hooks copies only (clean mules held out)", ("chat", "hooks"))]:
        m = fit_augmented(f, kinds, drop)
        thr = E.thresholds_from_validation(f, score(m, f), denom, rules=rules)
        adv[label] = evaluate_model(base, m, thr, rules)
        print(f"A. {label}: victims {adv[label]['rebuild']['victims_warned']:.1%}, combined attack "
              f"{adv[label]['all']['victims_warned']:.1%}", flush=True)

    # B. journey-line share, total budget fixed
    S = base["S"]
    sweep = {}
    for share in (0.0, 0.1, 0.2, 0.3, 0.4):
        thr = E.thresholds_from_validation(f, S, denom, rules=rules, journey_share=share)
        sweep[share] = evaluate_model(base, base["bundle"]["models"], thr, rules)
        print(f"B. share {share}: victims {sweep[share]['rebuild']['victims_warned']:.1%}, combined attack "
              f"{sweep[share]['all']['victims_warned']:.1%} at {sweep[share]['all']['false_pauses_per_10k']:.2f}/10k",
              flush=True)

    res = {"data": f"synthetic test days 42-59; {kit.system(base)}", "adversarial_training": adv,
           "journey_line_share": {str(k): v for k, v in sweep.items()}, "chosen_share": JOURNEY_LINE_SHARE,
           "seconds": round(time.time() - T)}
    path = kit.save_json(res, out / "design_choices.json")
    pct = lambda x: f"{x:.1%}"  # noqa: E731
    head = ["", "Victims warned", "At first payment", "False pauses /10k", "Chat-only", "Split", "Aged mules",
            "Clean mules", "All at once"]

    def line(label, r):
        b = r["rebuild"]
        return [label, pct(b["victims_warned"]), pct(b["first_payment"]), f"{b['false_pauses_per_10k']:.2f}"] + \
               [pct(r[x]["victims_warned"]) for x in ATTACKS]

    md = [f"**Measured, synthetic** (test days 42–59; {kit.system(base)}; lines from the validation week each time). "
          "These are the two model-side choices made for the finale, kept here so they can be rerun.", "",
          "**A. Adversarial training: tried and rejected.** Attacked copies of the training-day scams were added to "
          "the fused model's training data.", "",
          kit.md_table(head, [line(k, v) for k, v in adv.items()]), "",
          adversarial_text(adv), "",
          "**B. The journey-only pause line: chosen (policy v4).** Share of the 1-in-10,000 pause budget given to "
          "the journey line, with the total fixed:", "",
          kit.md_table(head, [line(f"share {k}" + (" (shipped)" if k == JOURNEY_LINE_SHARE else ""), v)
                              for k, v in sweep.items()]), "",
          choice_text(sweep)]
    sec = kit.write_section(out, "design", "\n".join(md))
    kit.log_run(out, kit.new_run_id("design_choices"), 7, [path, sec], command=kit.cmd(__file__, argv))
    print(f"saved {path} ({res['seconds']}s)")
    return res


if __name__ == "__main__":
    main()
