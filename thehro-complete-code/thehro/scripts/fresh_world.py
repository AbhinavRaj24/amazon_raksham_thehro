"""The fresh-world test: every system on a newly simulated world that no design choice ever saw.

    python scripts/fresh_world.py                 # about 8 minutes the first time (simulates the world), then 5
    python scripts/fresh_world.py --seed 25       # the seed is fixed in advance; changing it is a different test

Since Round 1 the seed-7 test days have been studied closely, and some finale choices (policy v3's rules, the
journey-line share) were picked while looking at them. This test simulates a new world with the same generator and
size but seed 25, chosen before the first run and never used for anything else, and reports on it:

  1. Round 1's system, frozen (reference/round1: model, lines, policy v1)
  2. The finale system, frozen (outputs/: model, lines, policy v4)
  3. Round 1's recipe retrained on the fresh world (training days, validation-week lines, policy v1)
  4. The finale recipe retrained on the fresh world (no caller identity, policy v4)
Frozen means not refitted and no line moved: the deployment question. Retrained means the same code from scratch:
the "were our choices fitted to seed 7?" question. Each is measured as simulated and under the attacks, with the
slide-10 pass lines. Nothing is tuned on this world. Writes test_kit/results/fresh_world.json and the "fresh"
section of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import evaluate as E  # noqa: E402
from thehro import kit, redteam  # noqa: E402
from thehro.config import VALID_END  # noqa: E402
from thehro.features import build_features  # noqa: E402
from thehro.models import CALLER_FEATURES, fit, score, training_labels  # noqa: E402
from thehro.policy import RULES, V1, V4  # noqa: E402
from thehro.service import load_bundle  # noqa: E402
from thehro.simulate import SimConfig, simulate  # noqa: E402

ATTACKS = ["chat_only", "split", "aged_mules", "clean_mules", "all"]
PASS_SINGLE, PASS_ALL = 0.75, (0.85, 1.0)


def world_for(seed: int, cache: Path):
    if cache.exists():
        return pickle.load(open(cache, "rb"))
    world = simulate(SimConfig(seed=seed, n_users=40_000))
    f, denom = build_features(world, verbose=False)
    pickle.dump((world, f, denom), open(cache, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    return world, f, denom


def measure(world, f, denom, models, thr, rules, seed) -> dict:
    w = E.windows(f)
    ft = f[w["test"]]
    base = {"world": world, "ft": ft, "denom": denom, "bundle": {"models": models}, "thr": thr, "rules": rules,
            "n_users": denom["n_users"]}
    out = {}
    for name in ["rebuild"] + ATTACKS:
        r = redteam.run(base, name, seed, models=models, thr=thr)
        h = r["head"]
        k = round(h["victims_warned"] * h["journeys"])
        out[name] = {"victims_warned": kit.share(k, h["journeys"]),
                     "false_pauses_per_10k": kit.per10k(h["genuine_t2plus"], r["n_all"])}
        if name == "rebuild":
            out[name].update(first_payment=h["victims_warned_first_payment"], genuine_trusted_checks=h["genuine_tier3"],
                             genuine_holds=h["genuine_t4plus"], money_protected_modelled=h["money_protected_share"],
                             journeys=h["journeys"], customer_payments=r["n_all"])
    singles = [out[x]["victims_warned"]["value"] for x in ATTACKS if x != "all"]
    out["passes"] = {"single_tactics": all(v >= PASS_SINGLE for v in singles),
                     "combined": out["all"]["victims_warned"]["value"] >= PASS_ALL[0]
                     and out["all"]["false_pauses_per_10k"]["value"] <= PASS_ALL[1]}
    return out


def verdict_text(systems: dict) -> str:
    """The reading, generated from the numbers: both halves of the combined pass line, for every system."""
    def one(label):
        r = systems[label]
        v, fp = r["all"]["victims_warned"]["value"], r["all"]["false_pauses_per_10k"]["value"]
        why = [] if r["passes"]["combined"] else (["under 85% of victims"] if v < PASS_ALL[0] else []) + \
            (["over 1 false pause per 10,000"] if fp > PASS_ALL[1] else [])
        return (f"{label.lower()}: {v:.1%} of victims warned under the combined attack at {fp:.2f} false pauses per "
                f"10,000 ({'passes' if r['passes']['combined'] else 'fails: ' + ' and '.join(why)})")
    fin, r1 = systems["Finale system, frozen"], systems["Round 1 system, frozen"]
    gains = [100 * (fin[x]["victims_warned"]["value"] - r1[x]["victims_warned"]["value"]) for x in ["rebuild"] + ATTACKS]
    return ("**Reading.** " + "; ".join(one(k) for k in systems) + ". "
            f"Frozen, the finale system warns {gains[0]:+.1f} points more victims than Round 1's on this world and "
            f"{min(gains[1:]):+.1f} to {max(gains[1:]):+.1f} points more under each attack. These, not the seed-7 test "
            "days (which were used to make choices), are the numbers to quote as final, including the misses.")


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=25, help="fresh world seed (fixed in advance)")
    ap.add_argument("--attack-seed", type=int, default=7)
    ap.add_argument("--cache", default=None, help="default .cache/fresh_world_s<seed>.pkl")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    T = time.time()
    world, f, denom = world_for(a.seed, Path(a.cache or ROOT / ".cache" / f"fresh_world_s{a.seed}.pkl"))
    print(f"fresh world seed {a.seed}: {len(world.payments):,} payments ({time.time() - T:.0f}s)", flush=True)
    window, y = training_labels(f)
    systems = {}
    for label, folder, default_rules in [("Round 1 system, frozen", ROOT / "reference" / "round1", V1),
                                         ("Finale system, frozen", ROOT / "outputs", None)]:
        b = load_bundle(folder / "model_bundle.pkl")
        rules = RULES[b.get("policy", "v1")] if default_rules is None else default_rules
        systems[label] = measure(world, f, denom, b["models"], b["thresholds"], rules, a.attack_seed)
        systems[label]["system"] = {"model": b["versions"]["model"], "policy": rules.name, "lines": "frozen (seed 7)"}
        print(f"{label}: victims {systems[label]['rebuild']['victims_warned']['value']:.1%}, combined attack "
              f"{systems[label]['all']['victims_warned']['value']:.1%} ({time.time() - T:.0f}s)", flush=True)
    for label, rules in [("Round 1 recipe, retrained here", V1), ("Finale recipe, retrained here", V4)]:
        m = fit(f, window, y, verbose=False, drop=() if rules.caller_id else CALLER_FEATURES)
        thr = E.thresholds_from_validation(f, score(m, f, recogniser=False), denom, rules=rules)
        systems[label] = measure(world, f, denom, m, thr, rules, a.attack_seed)
        systems[label]["system"] = {"model": f"refit, {m.meta['n_pos']} labels", "policy": rules.name,
                                    "lines": "validation week of this world"}
        print(f"{label}: victims {systems[label]['rebuild']['victims_warned']['value']:.1%}, combined attack "
              f"{systems[label]['all']['victims_warned']['value']:.1%} ({time.time() - T:.0f}s)", flush=True)
    res = {"seed": a.seed, "attack_seed": a.attack_seed, "world": {"payments": int(len(world.payments)),
           "test_customer_payments": int(E.n_customer_payments(denom, VALID_END))}, "systems": systems,
           "seconds": round(time.time() - T)}
    path = kit.save_json(res, out / "fresh_world.json")

    pct = lambda d: f"{d['value']:.1%}"  # noqa: E731
    rows = []
    for label, r in systems.items():
        b = r["rebuild"]
        rows.append([label, r["system"]["policy"], f"{b['victims_warned']['value']:.1%} "
                     f"({b['victims_warned']['lo']:.1%}–{b['victims_warned']['hi']:.1%})", f"{b['first_payment']:.1%}",
                     f"{b['false_pauses_per_10k']['value']:.2f}", str(b["genuine_trusted_checks"]), str(b["genuine_holds"]),
                     f"{b['money_protected_modelled']:.1%}"])
    att = [[label] + [pct(r[x]["victims_warned"]) for x in ATTACKS] +
           [f"{r['all']['false_pauses_per_10k']['value']:.2f}",
            ("pass" if r["passes"]["combined"] else "FAIL") + " / " + ("pass" if r["passes"]["single_tactics"] else "FAIL")]
           for label, r in systems.items()]
    fin, fin_r = systems["Finale system, frozen"], systems["Finale recipe, retrained here"]
    md = [f"**Measured, synthetic, on a world no design choice ever saw** (seed {a.seed}, fixed before the first run; "
          f"same generator and size as seed 7; {res['world']['test_customer_payments']:,} customer payments on its "
          f"test days, {fin['rebuild']['journeys']} scam victims). *Frozen* systems keep their seed-7 model, lines "
          "and policy, untouched: the deployment question. *Retrained* recipes rerun the same code from scratch on "
          "this world: the \"did our choices fit seed 7?\" question. Nothing is tuned here.", "",
          kit.md_table(["System", "Policy", "Victims warned (95% CI)", "At first payment", "False pauses /10k",
                        "Genuine trusted checks", "Genuine holds", "Money protected (modelled)"], rows), "",
          "Under the attacks (victims warned; the attack seed is the same as on seed 7):", "",
          kit.md_table(["System", "Chat-only", "Split", "Aged mules", "Clean mules", "All at once",
                        "False pauses /10k (all at once)", "Slide-10 lines: combined / single"], att), "",
          verdict_text(systems)]
    sec = kit.write_section(out, "fresh", "\n".join(md))
    kit.log_run(out, kit.new_run_id("fresh_world"), a.seed, [path, sec], command=kit.cmd(__file__, argv))
    print(f"saved {path} ({res['seconds']}s)")
    return res


if __name__ == "__main__":
    main()
