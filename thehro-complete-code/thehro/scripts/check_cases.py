"""Check the hand-written test cases (test_kit/cases/*.json) WITHOUT running them.

    python scripts/check_cases.py                 # checks ../../test_kit/cases
    python scripts/check_cases.py --dir <folder>

The cases are pre-registered: their expected outcomes are written and committed before the system sees them.
So this script only checks format and consistency. It never calls the decision service and prints no
decisions. Exit status 1 if any case has an error; warnings don't fail.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bank.server import HISTORY_KINDS  # noqa: E402
from thehro.live import CONTEXT_KEYS, PAYEE_TYPES  # noqa: E402

CHANNELS = ("UPI to person", "IMPS", "NEFT", "RTGS")
EXPECTED = ("interrupt", "no_interrupt", "never_hold")
GROUPS = ("clear_scam", "risky_genuine", "hard")
SCAM_SIDE = {"mule_network", "mule_unflagged", "registry", "fri_high", "new_account"}
GENUINE_SIDE = {"saved_contact", "shop", "regular_vendor", "new_landlord", "new_individual", "dormant",
                "flagged_genuine"}
REQUIRED = ("id", "title", "group", "truth", "story", "written_by", "written_at", "customer", "payee_type",
            "amount", "expected", "why")


def demo_customers() -> set[str]:
    jm = json.loads((ROOT / "bank" / "judge_mode.json").read_text(encoding="utf-8"))
    return {c["id"] for c in jm["customers"]}


def check(case: dict, path: Path, customers: set[str]) -> tuple[list[str], list[str]]:
    err, warn = [], []
    for k in REQUIRED:
        if k not in case:
            err.append(f"missing '{k}'")
    if err:
        return err, warn
    if not re.fullmatch(r"K1-\d{2}", str(case["id"])):
        err.append("id must look like K1-01")
    if path.stem != case["id"]:
        err.append(f"file name must be {case['id']}.json")
    if case["group"] not in GROUPS:
        err.append(f"group must be one of {GROUPS}")
    if case["truth"] not in ("scam", "genuine"):
        err.append("truth must be 'scam' or 'genuine'")
    if case["expected"] not in EXPECTED:
        err.append(f"expected must be one of {EXPECTED}")
    if case["truth"] == "scam" and case["expected"] != "interrupt":
        err.append("a scam's expected outcome is 'interrupt' (even if you think we'll miss it: say so in 'why')")
    if case["truth"] == "genuine" and case["expected"] == "interrupt":
        err.append("a genuine payment's expected outcome is 'no_interrupt' or 'never_hold'")
    if case["customer"] not in customers:
        err.append(f"customer must be one of {sorted(customers)} (the judge-mode customers)")
    if case["payee_type"] not in PAYEE_TYPES:
        err.append(f"payee_type must be one of {list(PAYEE_TYPES)}")
    elif case["truth"] == "scam" and case["payee_type"] in GENUINE_SIDE - {"new_individual", "dormant"}:
        warn.append(f"a scam paying a '{case['payee_type']}' account: in the simulation that account is genuine")
    elif case["truth"] == "genuine" and case["payee_type"] in SCAM_SIDE - {"new_account"}:
        warn.append(f"a genuine payment to a '{case['payee_type']}' account: in the simulation that account is a mule")
    if not isinstance(case["amount"], (int, float)) or case["amount"] <= 0:
        err.append("amount must be a positive number (rupees)")
    if case.get("channel", "UPI to person") not in CHANNELS:
        err.append(f"channel must be one of {CHANNELS}")
    if case.get("lang", "en") not in ("en", "hi"):
        err.append("lang must be 'en' or 'hi'")
    kinds = {k["id"]: set(k["fields"]) for k in HISTORY_KINDS}
    for i, h in enumerate(case.get("history", [])):
        if h.get("kind") not in kinds:
            err.append(f"history[{i}].kind must be one of {list(kinds)}")
            continue
        for fld in kinds[h["kind"]]:
            if fld in h and not isinstance(h[fld], (int, float)):
                err.append(f"history[{i}].{fld} must be a number")
    ctx = case.get("context") or {}
    bad = sorted(set(ctx) - set(CONTEXT_KEYS))
    if bad:
        err.append(f"context has unknown fields {bad} (allowed: {list(CONTEXT_KEYS)})")
    if any(not isinstance(v, (int, float)) for v in ctx.values()):
        err.append("context values must be numbers (1/0 for yes/no, minutes for call_min)")
    if ctx and not case.get("consent", True):
        warn.append("context given with consent off: the bank server will drop it (fine if that is the point)")
    try:
        datetime.fromisoformat(str(case["written_at"]))
    except ValueError:
        err.append("written_at must be an ISO time, e.g. 2026-10-24T19:30+05:30")
    if len(str(case["why"])) < 15:
        warn.append("'why' is very short: say what a good system should do and why")
    return err, warn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT.parents[1] / "test_kit" / "cases")
    a = ap.parse_args()
    files = sorted(a.dir.glob("*.json"))
    if not files:
        print(f"no cases in {a.dir}")
        return 1
    customers = demo_customers()
    ids, groups, truths, n_err = Counter(), Counter(), Counter(), 0
    for p in files:
        try:
            case = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"✗ {p.name}: not valid JSON ({exc})")
            n_err += 1
            continue
        err, warn = check(case, p, customers)
        ids[case.get("id")] += 1
        groups[case.get("group")] += 1
        truths[case.get("truth")] += 1
        mark = "✗" if err else ("!" if warn else "✓")
        print(f"{mark} {p.name}: {case.get('title', '')}")
        for e in err:
            print(f"    error:   {e}")
        for w in warn:
            print(f"    warning: {w}")
        n_err += bool(err)
    dupes = [i for i, n in ids.items() if n > 1]
    if dupes:
        print(f"✗ duplicate ids: {dupes}")
        n_err += 1
    print(f"\n{len(files)} cases: {dict(groups)}; truth {dict(truths)}. Target: 10 clear_scam, 10 risky_genuine, 10 hard.")
    print("No decisions were run: the expected outcomes stay pre-registered until the cases are committed.")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
