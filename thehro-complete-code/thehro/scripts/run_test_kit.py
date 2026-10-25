"""The whole test kit in one command.

    python scripts/run_test_kit.py --out ../../test_kit/results
    python scripts/run_test_kit.py --out ../../test_kit/results --no-tests     # skip pytest in the completeness table

Runs, in one process (the baseline is loaded once and shared):
  1. baseline check        reproduces outputs/metrics.json (victims warned, false pauses) or stops
  2. Round 1 → now         reference/round1/ against outputs/ (round1_vs_now.py; reads both, computes nothing)
     fresh world           every system on a newly simulated world, seed 25 (fresh_world.py; about 5-8 min)
  3. failure cases         every missed victim and false pause, grouped
  4. false positives       what the falsely interrupted customers saw and how they got past it (eval_false_positives.py)
  5. spot-checks           seeds 7, 42 and 2026 (payment level) and seed 42 at victim level
  6. metrics               error rates with CIs, confusion matrices, unseen data
  7. dataset export        data/thehro_heldout.csv.gz, test_kit/data/ sample and checksum
  8. signal switch-offs
  9. policy versions       v1 (Round 1), v2, v3 side by side + the cancel-and-retry attack (eval_policy.py;
                           needs .cache/live_world.pkl)
 10. attacks               event-level, frozen model (its cancel-and-retry row reads step 9)
 11. fairness              baseline and combined attack
 12. caller identity       what it would add if a bank app could read it (caller_identity.py)
     design choices        adversarial training (rejected) and the journey-line share sweep (design_choices.py)
 13. PaySim                if data/paysim/*.csv exists
     load test             decision service and bank server under concurrent requests (loadtest.py; starts its own)
 14. K1 cases              if test_kit/cases/K1-*.json are committed in git and the bank server is running
 15. completeness table    last, so it sees everything above (runs pytest unless --no-tests)
Every step appends to runs.csv and rewrites its section of test_kit/report.md. A failing step is reported and
the rest still run; the exit code is non-zero if anything failed.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import kit  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent


def script(name: str):
    spec = importlib.util.spec_from_file_location(f"kit_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="results folder (default test_kit/results)")
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--seed", type=int, default=7, help="attack seed")
    ap.add_argument("--with-v2", action="store_true", help="accepted for old commands; the caller-identity step always runs")
    ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--server", default="http://127.0.0.1:8080", help="bank server for the K1 cases")
    ap.add_argument("--quiet", action="store_true", help="hide each step's own printout")
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    common = ["--out", str(out)]
    cache = ["--cache", a.cache]
    T = time.time()

    steps = [
        ("Round 1 → now", "round1_vs_now", common),
        ("fresh world", "fresh_world", common),
        ("failure cases", "failure_cases", common + cache),
        ("false positives", "eval_false_positives", common + cache),
        ("spot-check seed 7", "spot_check", ["--seed", "7"] + common + cache),
        ("spot-check seed 42", "spot_check", ["--seed", "42"] + common + cache),
        ("spot-check seed 2026", "spot_check", ["--seed", "2026"] + common + cache),
        ("spot-check seed 42, 30 victims", "spot_check", ["--seed", "42", "--journeys", "30"] + common + cache),
        ("metrics", "kit_metrics", common + cache),
        ("dataset export", "export_test_set", common + cache),
        ("signal switch-offs", "signal_switchoff", common + cache),
        ("policy versions", "eval_policy", common + cache),
        ("attacks", "attacks", ["--seed", str(a.seed)] + common + cache),
        ("fairness", "fairness_check", ["--seed", str(a.seed)] + common + cache),
    ]
    steps.append(("caller identity", "caller_identity", ["--seed", str(a.seed)] + common + cache))
    steps.append(("design choices", "design_choices", common + cache))
    steps += [
        ("PaySim", "paysim_benchmark", common),
        ("load test", "loadtest", common + cache),
        ("K1 cases", "k1_run", ["--server", a.server] + common),
        ("completeness", "kit_metrics", ["--completeness-only"] + ([] if a.no_tests else ["--run-tests"]) + common),
    ]

    print("1. baseline check ...", flush=True)
    t0 = time.time()
    base = kit.get_baseline(a.cache)
    print(f"   ok: {int(base['jt'].warned.sum())}/{len(base['jt'])} victims warned, {base['head']['genuine_t2plus']} "
          f"false pauses, as in outputs/metrics.json ({time.time() - t0:.0f}s)", flush=True)
    status = []
    for n, (label, name, args) in enumerate(steps, start=2):
        t0 = time.time()
        print(f"{n}. {label} ...", flush=True)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf) if a.quiet else contextlib.nullcontext():
                script(name).main(args)
            status.append((label, "ok", time.time() - t0))
            print(f"   ok ({time.time() - t0:.0f}s)", flush=True)
        except SystemExit as e:
            status.append((label, f"stopped (exit {e.code})", time.time() - t0))
            print(f"   STOPPED: exit {e.code}", flush=True)
        except Exception:  # noqa: BLE001
            status.append((label, "FAILED", time.time() - t0))
            print("   FAILED:\n" + traceback.format_exc(), flush=True)
    total = time.time() - T
    report = kit.build_report(out)
    kit.log_run(out, kit.new_run_id("run_test_kit"), a.seed, [report],
                command="python scripts/run_test_kit.py " + " ".join(argv if argv is not None else sys.argv[1:]))
    print("\nsummary:")
    for label, st, sec in status:
        print(f"  {label:34s} {st:22s} {sec:6.0f}s")
    print(f"total {total / 60:.1f} min; report: {report}")
    return 0 if all(st == "ok" for _, st, _ in status) else 1


if __name__ == "__main__":
    sys.exit(main())
