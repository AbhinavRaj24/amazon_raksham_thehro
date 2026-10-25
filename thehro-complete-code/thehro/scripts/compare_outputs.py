"""Check that a fresh pipeline run reproduces the committed outputs/ (or another reference folder).

    python scripts/run_all.py --out /tmp/v1
    python scripts/compare_outputs.py /tmp/v1                # compares with outputs/
    python scripts/compare_outputs.py /tmp/v1 --quick        # for a run made with run_all.py --quick

Compares metrics.json, demo_scenarios.json and sample_requests.json value by value (timings ignored, since
they differ between machines), every table byte for byte, and the saved model bundle by its thresholds and
by scoring the sample requests with both. Exits with status 1 on any difference.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # the model bundle refers to thehro's own classes

from thehro.features import ALL_FEATURES  # noqa: E402
from thehro.models import score  # noqa: E402

TIMING_KEYS = {"latency_ms", "seconds"}
QUICK_SKIPS = {"held_out_workflows"}          # not produced by run_all.py --quick


def walk(a, b, path, diffs, skip, added=None):
    """Value-by-value differences into `diffs`. Fields that exist only in the new run (bookkeeping added since the
    reference was made, e.g. the policy version) go to `added` instead: they change no reproduced value."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in skip:
                continue
            if k not in a and added is not None:
                added.append(f"{path}/{k} = {str(b[k])[:40]}")
            elif k not in a or k not in b:
                diffs.append(f"{path}/{k}: only in the {'new run' if k in b else 'reference'}")
            else:
                walk(a[k], b[k], f"{path}/{k}", diffs, skip, added)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: {len(a)} items vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", diffs, skip, added)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
            diffs.append(f"{path}: {a} vs {b}")
    elif a != b:
        diffs.append(f"{path}: {str(a)[:60]!r} vs {str(b)[:60]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("new", type=Path, help="output folder of the fresh run")
    ap.add_argument("--ref", type=Path, default=ROOT / "outputs", help="reference folder (default: outputs/)")
    ap.add_argument("--quick", action="store_true", help="the new run used run_all.py --quick")
    a = ap.parse_args()
    skip = TIMING_KEYS | (QUICK_SKIPS if a.quick else set())
    failed = False

    for name in ["metrics.json", "demo_scenarios.json", "sample_requests.json"]:
        diffs, added = [], []
        walk(json.load(open(a.ref / name, encoding="utf-8")), json.load(open(a.new / name, encoding="utf-8")),
             "", diffs, skip, added)
        failed |= bool(diffs)
        print(f"{name:24s} {'identical (timings ignored)' if not diffs else f'{len(diffs)} differences'}"
              + (f"; {len(added)} new fields" if added else ""))
        for d in diffs[:8]:
            print("    ", d)
        for d in added[:4]:
            print("     new field:", d)

    tables = sorted(p.name for p in (a.ref / "tables").glob("*.csv"))
    if a.quick:
        tables = [t for t in tables if t.removesuffix(".csv") not in QUICK_SKIPS]
    same = [t for t in tables if (a.new / "tables" / t).exists()
            and filecmp.cmp(a.ref / "tables" / t, a.new / "tables" / t, shallow=False)]
    failed |= len(same) != len(tables)
    print(f"{'tables/*.csv':24s} {len(same)} of {len(tables)} byte-identical")
    for t in sorted(set(tables) - set(same)):
        print("     differs or missing:", t)

    old, new = (pickle.load(open(p / "model_bundle.pkl", "rb")) for p in (a.ref, a.new))
    to, tn = vars(old["thresholds"]), vars(new["thresholds"])
    same_thr = all(to[k] == tn[k] for k in ("t1", "t2", "t4", "family")) and tn.get("tj") == to.get("tj") \
        and all(tn["info"].get(k) == v for k, v in to["info"].items())
    reqs = json.load(open(a.ref / "sample_requests.json"))
    X = pd.DataFrame([{c: (np.nan if r["features"][c] is None else r["features"][c]) for c in ALL_FEATURES}
                      for r in reqs])
    cols = ["s_fused", "s_journey", "s_payee", "s_context"]
    gap = float(np.abs(score(old["models"], X)[cols].values - score(new["models"], X)[cols].values).max())
    failed |= (not same_thr) or gap > 0 or old["versions"] != new["versions"]
    print(f"{'model bundle':24s} thresholds {'identical' if same_thr else 'DIFFER'}; "
          f"versions {'identical' if old['versions'] == new['versions'] else 'DIFFER'}; "
          f"max score difference on samples {gap:.3g}")

    print("RESULT:", "DIFFERENCES FOUND" if failed else "reproduces the reference exactly")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
