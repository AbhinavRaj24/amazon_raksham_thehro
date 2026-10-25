"""K2 spot-check: fresh labelled samples from data the model never trained on, through the live decision service.

    python scripts/spot_check.py --seed 7                   # 25 scam, 25 risky-looking genuine, 10 ordinary genuine
    python scripts/spot_check.py --seed 42 --journeys 30    # 30 victims: right if any of their payments was warned

The judge picks the seed. Samples come from the test days (42-59) of the synthetic world. Before each sample,
that customer's earlier test-day payments are replayed through the same DecisionService the judge page uses
(pressing Continue after every warning, as the evaluation assumes), so memory is right. Each live tier is
cross-checked against the offline evaluation's tier: any disagreement is a bug.

Writes test_kit/results/spot_check_seed<N>.json (or ..._journeys<M>.json) and the Spot-checks section of
test_kit/report.md, and appends to runs.csv.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import kit  # noqa: E402


def print_result(res: dict) -> None:
    rows = []
    for s in res["samples"]:
        what = s["scam_type"] if s["label"] != "genuine" else s["kind"]
        rows.append([s["sample"], s["label"], what, s["amount_text"], s["expected"],
                     f"{s['got']['tier']} {s['got']['tier_name']}", "✓" if s["right"] else "✗",
                     (s.get("top_reason") or "")[:70]])
    widths = [max(len(str(r[i])) for r in rows + [["#", "label", "type", "amount", "expected", "got", "ok",
                                                       "top reason"]]) for i in range(8)]
    head = ["#", "label", "type", "amount", "expected", "got", "ok", "top reason"]
    print("  ".join(str(h).ljust(w) for h, w in zip(head, widths)))
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    sm = res["summary"]
    print()
    print(f"right overall : {kit.fmt_share(sm['right_share'])}")
    print(f"scams right   : {kit.fmt_share(sm['scam_right_share'])}")
    if sm["n_genuine"]:
        print(f"genuine right : {kit.fmt_share(sm['genuine_right_share'])}")
    print(f"live = evaluation: {sm['live_equals_evaluation']} of {sm['n']}"
          + ("" if sm["live_equals_evaluation"] == sm["n"] else "   <-- DISAGREEMENT: tell P1, this is a bug"))
    print(res["note"])


def spot_section(results: Path) -> str:
    """The Spot-checks section, from every spot-check file in results/."""
    import json
    files = sorted(results.glob("spot_check_seed*.json"))
    if not files:
        return "_Not run yet._"
    rows = []
    for p in files:
        r = json.load(open(p))
        sm = r["summary"]
        lvl = r.get("level", "payment")
        fmt = lambda k, n: f"{k}/{n} ({kit.wilson(k, n)[0]:.0%}–{kit.wilson(k, n)[1]:.0%})" if n else "–"  # noqa: E731
        rows.append([r["seed"], lvl, sm["n"], fmt(sm["right"], sm["n"]), fmt(sm["scam_right"], sm["n_scam"]),
                     fmt(sm["genuine_right"], sm["n_genuine"]), f"{sm['live_equals_evaluation']} of {sm['n']}",
                     f"`results/{p.name}`"])
    md = [
        "**Measured, synthetic.** Random labelled draws from the test days, run through the live `DecisionService` "
        "after replaying each customer's earlier test-day payments. Rerun with any seed: "
        "`python scripts/spot_check.py --seed <N>` (payment level) or `--journeys <M>` (victim level).",
        "",
        "Right means: a scam is warned (tier 2+); a genuine payment is not interrupted (tier 0–1).",
        "",
        kit.md_table(["Seed", "Level", "n", "Right (95% CI)", "Scams right", "Genuine right", "Live = evaluation",
                      "File"], rows),
        "",
        "Payment-level draws contain about 25 scam payments; about 86% of all scam payments are paused, because a "
        "scam's first step is often small. Victim-level draws (`--journeys`) are the fairer reading of "
        "\"was this victim protected\": 94.3% over all 720 test-day victims. The kit samples are small, so their "
        "intervals are wide; the headline accuracy claims come from the full held-out set (Error rates below).",
    ]
    return "\n".join(md)


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--journeys", type=int, default=None, help="draw N scam journeys instead of payments")
    ap.add_argument("--n-scam", type=int, default=25)
    ap.add_argument("--n-risky", type=int, default=25)
    ap.add_argument("--n-normal", type=int, default=10)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None, help="results folder (default test_kit/results)")
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    t0 = time.time()
    base = kit.get_baseline(a.cache)
    res = kit.spot_check(a.seed, a.n_scam, a.n_risky, a.n_normal, a.journeys, base=base)
    res["seconds"] = round(time.time() - t0, 1)
    print_result(res)
    name = f"spot_check_seed{a.seed}" + (f"_journeys{a.journeys}" if a.journeys else "") + ".json"
    path = kit.save_json(res, out / name)
    sec = kit.write_section(out, "spot_checks", spot_section(out))
    kit.log_run(out, kit.new_run_id("spot_check"), a.seed, [path, sec],
                command=kit.cmd(__file__, argv))
    print(f"saved {path} ({res['seconds']}s)")
    return res


if __name__ == "__main__":
    main()
