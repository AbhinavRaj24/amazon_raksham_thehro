"""L-3c: fairness re-check by age, language, region and consent, on the baseline and on the combined attack.

    python scripts/fairness_check.py            # baseline + combined attack (seed 7)
    python scripts/fairness_check.py --no-attack

For each group: false pauses per 10,000 of that group's customer payments (exact Poisson interval, counted here),
the ratio to the overall rate, and victims warned (Wilson interval). Age, language and region are never model
inputs; they are used only for this check.

Pass line (slide 10): no age group above 1.5x the overall pause rate. Groups with fewer than 20 victims, or
fewer than 10 false pauses, are flagged as too small to read.

Writes test_kit/results/fairness.json and the Fairness section of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import kit, redteam  # noqa: E402

PASS_RATIO = 1.5
SMALL_VICTIMS = 20
SMALL_COUNT = 10
TITLES = [("age_band", "Age"), ("lang", "Language"), ("region", "Region"), ("consent", "Consent")]


def summarise(groups: dict) -> dict:
    age = groups["age_band"]
    worst_age = max(age, key=lambda g: age[g]["ratio_to_overall"])
    flags = []
    for col, rows in groups.items():
        for g, r in rows.items():
            if r["ratio_to_overall"] > PASS_RATIO:
                small = r["victims_warned"]["n"] < SMALL_VICTIMS or r["false_pauses"]["k"] < SMALL_COUNT
                flags.append({"column": col, "group": g, "ratio": r["ratio_to_overall"],
                              "false_pauses": r["false_pauses"]["k"], "victims": r["victims_warned"]["n"],
                              "small_sample": small})
    return {"worst_age_group": worst_age, "worst_age_ratio": age[worst_age]["ratio_to_overall"],
            "age_pass": age[worst_age]["ratio_to_overall"] <= PASS_RATIO, "over_line_any_group": flags}


def table(groups: dict) -> str:
    md = []
    for col, title in TITLES:
        rows = []
        for g, r in groups[col].items():
            small = r["victims_warned"]["n"] < SMALL_VICTIMS or r["false_pauses"]["k"] < SMALL_COUNT
            rows.append([g, kit.fmt_rate(r["false_pauses"]), f"{r['ratio_to_overall']:.2f}×" + (" ⚠" if
                         r["ratio_to_overall"] > PASS_RATIO else ""), kit.fmt_share(r["victims_warned"]),
                         "small sample" if small else ""])
        md += [f"*{title}*", "", kit.md_table(["Group", "False pauses", "× overall", "Victims warned", ""], rows), ""]
    return "\n".join(md)


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7, help="seed of the combined attack")
    ap.add_argument("--no-attack", action="store_true")
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    base = kit.get_baseline(a.cache)
    g0 = kit.group_rates(base["world"], base["ft"], base["pol"].tier.values, base["jt"], base["denom"])
    res = {"data": f"synthetic test days 42-59, frozen {kit.system(base)}",
           "baseline": {**summarise(g0), "groups": g0}}
    md = ["**Measured, synthetic.** False pauses are per 10,000 of that group's own customer payments; × overall is "
          "the group's rate divided by everyone's. Pass line (slide 10): no age group above 1.5× the overall pause "
          "rate. ⚠ marks any group above 1.5×; \"small sample\" means fewer than 20 victims or fewer than 10 false "
          "pauses, so the interval is too wide to read much into.", ""]
    s0 = res["baseline"]
    md += [f"### Baseline: worst age group {s0['worst_age_group']} at {s0['worst_age_ratio']:.2f}× "
           f"({'pass' if s0['age_pass'] else 'FAIL'})", "", table(g0)]
    if not a.no_attack:
        r = redteam.run(base, "all", a.seed)
        g1 = kit.group_rates(base["world"], r["ft"], r["pol"].tier.values, r["jt"], r["denom"])
        res["combined_attack"] = {**summarise(g1), "groups": g1, "seed": a.seed}
        s1 = res["combined_attack"]
        md += [f"### Combined attack (seed {a.seed}): worst age group {s1['worst_age_group']} at "
               f"{s1['worst_age_ratio']:.2f}× ({'pass' if s1['age_pass'] else 'FAIL'})", "",
               "Only scammers change in the attack, so false pauses barely move; what changes is victims warned "
               "per group.", "", table(g1)]
    over = [f"{f['group']} ({f['column']}) {f['ratio']:.2f}×, {f['false_pauses']} false pauses, {f['victims']} victims"
            + (" — small sample" if f["small_sample"] else "") for f in s0["over_line_any_group"]]
    if over:
        md += ["", "Groups above 1.5× at baseline (any column): " + "; ".join(over) + "."]
    path = kit.save_json(res, out / "fairness.json")
    sec = kit.write_section(out, "fairness", "\n".join(md))
    kit.log_run(out, kit.new_run_id("fairness"), None if a.no_attack else a.seed, [path, sec],
                command=kit.cmd(__file__, argv))
    print(f"baseline worst age group {s0['worst_age_group']} {s0['worst_age_ratio']:.2f}x "
          f"({'pass' if s0['age_pass'] else 'FAIL'}); saved {path}")
    return res


if __name__ == "__main__":
    main()
