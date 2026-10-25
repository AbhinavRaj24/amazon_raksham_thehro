"""Round 1 against now: the headline numbers of the committed Round 1 results and of outputs/, side by side.

    python scripts/round1_vs_now.py

Reads reference/round1/metrics.json (the Round 1 results, exactly as committed at b84dbb3) and outputs/metrics.json
(whatever run_all.py last wrote). Computes nothing itself, so every number is the one its own run reported. Both
are measured on the same synthetic test days (days 42-59, seed 7). Writes test_kit/results/round1_vs_now.json and
the "progress" section of test_kit/report.md, and adds a row to runs.csv.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import kit  # noqa: E402
from thehro.service import load_bundle  # noqa: E402

REF = ROOT / "reference" / "round1"


def load(folder: Path) -> dict:
    m = json.load(open(folder / "metrics.json"))
    m["_model"] = load_bundle(folder / "model_bundle.pkl")["versions"]["model"]
    return m


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now", default=str(ROOT / "outputs"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    r1, now = load(REF), load(Path(a.now))
    h1, h2 = r1["headline"], now["headline"]

    def pct(x):
        return f"{x:.1%}"

    def n_of(h, share):
        return f"{pct(h[share])} ({round(h[share] * h['journeys'])}/{h['journeys']})"

    rows = [
        ("Model", r1["_model"], now["_model"], ""),
        ("Policy", r1.get("policy", "v1"), now.get("policy", "v1"), ""),
        ("Victims warned", n_of(h1, "victims_warned"), n_of(h2, "victims_warned"), "higher is better"),
        ("Warned at their first scam payment", n_of(h1, "victims_warned_first_payment"),
         n_of(h2, "victims_warned_first_payment"), "higher is better"),
        ("Scam payments paused", pct(h1["scam_payments_t2plus"]), pct(h2["scam_payments_t2plus"]), "higher is better"),
        ("False pauses per 10,000 payments", f"{h1['genuine_t2plus_per_10k']:.2f} ({h1['genuine_t2plus']})",
         f"{h2['genuine_t2plus_per_10k']:.2f} ({h2['genuine_t2plus']})", "lower is better"),
        ("Genuine trusted checks (up to an hour's wait in Round 1)", str(h1["genuine_tier3"]), str(h2["genuine_tier3"]),
         "lower is better"),
        ("Genuine holds", str(h1["genuine_t4plus"]), str(h2["genuine_t4plus"]), "lower is better"),
        ("Pauses that are real scams at India's fraud rate (estimated)",
         f"1 in {1 / h1['precision_t2_adjusted_rate']:.0f}", f"1 in {1 / h2['precision_t2_adjusted_rate']:.0f}",
         "lower is better"),
        ("Money protected (modelled)", pct(h1["money_protected_share"]), pct(h2["money_protected_share"]),
         "higher is better"),
        ("Decision latency p99 inside the service", f"{h1['latency_ms']['p99']:.1f} ms",
         f"{h2['latency_ms']['p99']:.1f} ms", "timing, varies by machine"),
    ]
    stress = [(k, pct(v["victims_warned"]), pct(now["stress_tests"].get(k, {}).get("victims_warned", float("nan"))))
              for k, v in r1["stress_tests"].items()]
    held = [(wf, pct(v["victims_warned_hidden"]),
             pct(now["held_out_workflows"].get(wf, {}).get("victims_warned_hidden", float("nan"))))
            for wf, v in (r1.get("held_out_workflows") or {}).items()]
    res = {"round1": {"model": r1["_model"], "policy": r1.get("policy", "v1"), "headline": h1,
                      "stress_tests": r1["stress_tests"], "held_out_workflows": r1.get("held_out_workflows")},
           "now": {"model": now["_model"], "policy": now.get("policy", "v1"), "headline": h2,
                   "stress_tests": now["stress_tests"], "held_out_workflows": now.get("held_out_workflows")}}
    path = kit.save_json(res, out / "round1_vs_now.json")
    md = ["**Measured, synthetic** (both on test days 42–59 of the seed-7 world), except where marked. Round 1 is "
          "`reference/round1/` (committed at `b84dbb3`); now is `outputs/`. Each number is the one its own run "
          "reported. The test days have been studied closely since Round 1, so changes designed from them are also "
          "checked on a freshly simulated world (next section), and those are the numbers to quote as final.", "",
          kit.md_table(["Measure", "Round 1", "Now", ""], [list(r) for r in rows]), "",
          "Feature-level stress tests from `run_all.py` (victims warned; model not retrained):", "",
          kit.md_table(["Stress test", "Round 1", "Now"], [list(r) for r in stress])]
    if held:
        md += ["", "Each scam type hidden from training (victims of that type warned):", "",
               kit.md_table(["Hidden scam type", "Round 1", "Now"], [list(r) for r in held])]
    sec = kit.write_section(out, "progress", "\n".join(md))
    kit.log_run(out, kit.new_run_id("round1_vs_now"), None, [path, sec], command=kit.cmd(__file__, argv))
    print("\n".join(md))
    return res


if __name__ == "__main__":
    main()
