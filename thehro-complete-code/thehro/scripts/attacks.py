"""L-3b: attacks on the held-out weeks (the slide-10 "harder world"), with the model FROZEN.

    python scripts/attacks.py                 # all attacks, seed 7
    python scripts/attacks.py --seed 11 --only chat_only split

Each attack edits the scammers' events on the test days, rebuilds the features with the training code and scores
with the frozen model and lines in outputs/ (see thehro/redteam.py). A no-edit rebuild runs first: it must reproduce
the baseline exactly, which proves the rebuild path itself changes nothing.

Pass lines from slide 10:
  * the combined attack ("harder world"): at least 85% of victims warned at no more than 1 false pause per 10,000
  * no single tactic below 75% of victims warned
Failures are reported as failures. Nothing is tuned until it passes.

Writes test_kit/results/attacks.json, attacks.csv and the Attacks section of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import kit, redteam  # noqa: E402

ORDER = ["rebuild", "chat_only", "split", "aged_mules", "clean_mules", "all"]
SINGLE = ["chat_only", "split", "aged_mules", "clean_mules"]
PASS_SINGLE = 0.75
PASS_ALL = (0.85, 1.0)


def row_for(res: dict) -> dict:
    tiers = res["pol"].tier.values
    r = kit.headline_rates(res["head"], res["jt"], res["ft"], tiers, res["n_all"])
    return {"attack": res["name"], "label": res["label"], "ran": True, **r, "info": res["info"],
            "scam_payments": int((res["ft"].is_scam.values == 1).sum())}


def verdict(name: str, r: dict) -> str:
    v = r["victims_warned"]
    if name in SINGLE:
        ok = v["value"] >= PASS_SINGLE
        edge = " (interval crosses the line)" if v["lo"] < PASS_SINGLE <= v["hi"] or v["lo"] <= PASS_SINGLE < v["hi"] else ""
        return ("pass" if ok else "FAIL") + f": ≥ 75% warned{edge}"
    if name == "all":
        ok = v["value"] >= PASS_ALL[0] and r["false_pauses_per_10k"]["value"] <= PASS_ALL[1]
        return ("pass" if ok else "FAIL") + ": ≥ 85% warned at ≤ 1 false pause per 10,000"
    return ""


def section(rows: list[dict], base_rates: dict, seed: int, stress: pd.DataFrame) -> str:
    md = ["**Measured, synthetic; money protected is modelled.** Scammers' events on the test days are edited, the "
          "features rebuilt with the training code (`features.build_features`) and scored with the **frozen** "
          f"model and lines ({kit.system()}). Genuine customers are not edited. Seed {seed} for every random choice "
          "(`redteam.py`).", ""]
    tab = []
    for r in rows:
        if not r.get("ran"):
            tab.append([r["label"], "–", "–", "–", "–", "–", r["verdict"]])
            continue
        tab.append([r["label"], kit.fmt_share(r["victims_warned"]), kit.fmt_share(r["victims_warned_first_payment"]),
                    kit.fmt_share(r["scam_payments_paused"]), kit.fmt_rate(r["false_pauses_per_10k"]),
                    f"{r['money_protected_share_modelled']:.1%}", r.get("verdict", "")])
    md.append(kit.md_table(["Attack", "Victims warned", "Warned at first payment", "Scam payments paused",
                            "False pauses", "Money protected (modelled)", "Slide-10 pass line"], tab))
    reb = next((r for r in rows if r["attack"] == "rebuild" and r.get("ran")), None)
    if reb:
        same = (reb["victims_warned"]["k"] == base_rates["victims_warned"]["k"]
                and reb["false_pauses_per_10k"]["k"] == base_rates["false_pauses_per_10k"]["k"])
        md += ["", f"Rebuild check: with no edits the rebuilt world gives {reb['victims_warned']['k']}/"
               f"{reb['victims_warned']['n']} victims warned and {reb['false_pauses_per_10k']['k']} false pauses "
               + ("(identical to the baseline)." if same else "(**different from the baseline: the rebuild path is broken**).")]
    md += ["", "What each attack changes:", ""]
    for r in rows:
        if r.get("ran") and r["attack"] not in ("rebuild",):
            info = "; ".join(f"{k}: " + ", ".join(f"{kk} {vv:.3g}" if isinstance(vv, float) else f"{kk} {vv}"
                                                    for kk, vv in v.items()) for k, v in r["info"].items())
            md.append(f"- *{r['label']}*: {info}")
    md += ["", "The feature-level stress tests from `run_all.py`, for comparison (`outputs/tables/stress_tests.csv`, model not "
           "retrained):", "",
           kit.md_table(["Stress test", "Victims warned", "False pauses per 10,000"],
                        [[i, f"{s.victims_warned:.1%}", f"{s.genuine_t2plus_per_10k:.2f}"] for i, s in stress.iterrows()]),
           "", "**Reading the results.** " + reading(rows, base_rates)]
    return "\n".join(md)


def cancel_retry_row(out) -> dict:
    """Cancel-and-retry needs customers who cancel, which this replay never has (everyone continues), so it is
    measured by scripts/eval_policy.py on the live path; its row here quotes that run for the current policy."""
    row = {"attack": "cancel_retry", "label": "Cancel and retry smaller to another payee (line probing)", "ran": False}
    try:
        pv = json.load(open(out / "policy_versions.json"))
        cr, cur = pv["cancel_and_retry_attack"], pv["outputs_policy"]
    except (OSError, KeyError, ValueError):
        row["verdict"] = "not run: run scripts/eval_policy.py (step \"policy versions\" of run_test_kit.py) first"
        return row
    v1, v2 = cr["v1"], cr[cur]
    row.update(measured_in="policy", retries=v2["retries"], retries_stopped_v1=v1["retries_interrupted"],
               retries_stopped_v2=v2["retries_interrupted"], retries_stopped_v2_ci=v2["retries_interrupted_ci"],
               verdict=("pass" if v2["retries_interrupted"] >= PASS_SINGLE else "FAIL")
               + f": retries stopped {v1['retries_interrupted']:.1%} with Round 1's policy, "
                 f"{v2['retries_interrupted']:.1%} with policy {cur} (n = {v2['retries']}; next section)",
               policy=cur)
    return row


def reading(rows: list[dict], base_rates: dict) -> str:
    """Plain-language reading, generated from the numbers so it can never contradict the table."""
    ran = {r["attack"]: r for r in rows if r.get("ran")}
    base_v = base_rates["victims_warned"]["value"]
    base_fp = base_rates["false_pauses_per_10k"]["value"]
    out = []
    fps = [r["false_pauses_per_10k"]["value"] for k, r in ran.items() if k != "rebuild"]
    if fps:
        out.append(f"False pauses stay between {min(fps):.2f} and {max(fps):.2f} per 10,000 (baseline {base_fp:.2f}), "
                   "because only the scammers change.")
    singles = {k: ran[k] for k in SINGLE if k in ran}
    if singles:
        worst = min(singles, key=lambda k: singles[k]["victims_warned"]["value"])
        why = {"chat_only": "it silences the phone-context family",
               "split": "each part looks like an ordinary small payment",
               "aged_mules": "old, slow mules no longer look young or pass money straight on, so the payee family "
                             "and the mule-network flag go quiet",
               "clean_mules": "it silences the payee family, including every outside flag"}[worst]
        out.append(f"The weakest single tactic is \"{singles[worst]['label']}\" at "
                   f"{singles[worst]['victims_warned']['value']:.1%} of victims warned (baseline {base_v:.1%}): "
                   f"{why}.")
        below = [singles[k]["label"] for k in singles if singles[k]["victims_warned"]["lo"] < PASS_SINGLE]
        if below:
            out.append("The 95% interval reaches below the 75% line for: " + "; ".join(below) + ".")
    if "split" in ran:
        sv = ran["split"]["victims_warned"]["value"]
        if sv >= base_v:
            out.append(f"Splitting under ₹10,000 does not help the scammer ({sv:.1%} warned): every extra part to the "
                       "same new payee adds to the 7-day series features, so the run is seen sooner.")
        else:
            out.append(f"Splitting under ₹10,000 lowers victims warned to {sv:.1%}.")
    if "all" in ran:
        av = ran["all"]["victims_warned"]["value"]
        out.append(f"All tactics together give {av:.1%}, "
                   + ("which fails the 85% pass line for the harder world. " if av < PASS_ALL[0] else "which passes. ")
                   + "This combination does not include \"no FD break and no small credits\", so it is not the same "
                   "test as Round 1's \"all three at once\" (58.3%), which did.")
    cr = next((r for r in rows if r["attack"] == "cancel_retry"), {})
    if cr.get("measured_in"):
        out.append("Cancel and retry smaller (line probing) needs customers who cancel, which this replay never has, so "
                   f"it is measured on the live path in the next section: {cr['retries_stopped_v1']:.1%} of retries "
                   f"were stopped with Round 1's policy and {cr['retries_stopped_v2']:.1%} with policy "
                   f"{cr['policy']}'s retry memory.")
    else:
        out.append("Cancel and retry smaller (line probing) is measured by `scripts/eval_policy.py`, which has not "
                   "run yet.")
    return " ".join(out)


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--only", nargs="*", default=None, choices=ORDER)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    base = kit.get_baseline(a.cache)
    base_rates = kit.headline_rates(base["head"], base["jt"], base["ft"], base["pol"].tier.values, base["n_all"])
    names = a.only or ORDER
    rows = []
    for name in ORDER:
        if name not in names:
            continue
        t0 = time.time()
        r = row_for(redteam.run(base, name, a.seed))
        r["verdict"] = verdict(name, r)
        r["seconds"] = round(time.time() - t0, 1)
        rows.append(r)
        print(f"{r['label']:65s} victims warned {kit.fmt_share(r['victims_warned'])}  "
              f"false pauses {r['false_pauses_per_10k']['value']:.2f}  {r['verdict']}  ({r['seconds']}s)", flush=True)
    rows.append(cancel_retry_row(out))
    stress = pd.read_csv(kit.ROOT / "outputs" / "tables" / "stress_tests.csv", index_col=0)
    res = {"seed": a.seed, "data": f"synthetic test days 42-59, events edited, frozen {kit.system()}",
           "baseline": base_rates, "rows": rows}
    path = kit.save_json(res, out / "attacks.json")
    flat = [{"attack": r["label"], "victims_warned": r["victims_warned"]["value"] if r.get("ran") else None,
             "victims_lo": r["victims_warned"]["lo"] if r.get("ran") else None,
             "victims_hi": r["victims_warned"]["hi"] if r.get("ran") else None,
             "false_pauses_per_10k": r["false_pauses_per_10k"]["value"] if r.get("ran") else None,
             "verdict": r["verdict"]} for r in rows]
    pd.DataFrame(flat).to_csv(out / "attacks.csv", index=False)
    sec = kit.write_section(out, "attacks", section(rows, base_rates, a.seed, stress))
    kit.log_run(out, kit.new_run_id("attacks"), a.seed, [path, out / "attacks.csv", sec],
                command=kit.cmd(__file__, argv))
    print(f"saved {path}")
    return res


if __name__ == "__main__":
    main()
