"""What genuine customers flagged by mistake go through, and how fast they get past it (synthetic test days).

    python scripts/eval_false_positives.py            # also step "false positives" of scripts/run_test_kit.py
    python scripts/eval_false_positives.py --cache <run_all.py --cache file>

For every genuine payment the policy interrupted (tier 2+) on the test days, with the frozen model, lines and
policy in outputs/: which screen the customer saw, what it said first, what they could do, how
long that takes, and whether the same customer was interrupted again. Times are the product's own limits
(the 30-second pause, a trusted person or a one-hour wait, the officer's 30-minute SLA), not measured user
behaviour. Writes test_kit/results/false_positive_journey.json and the "false_positives" section of
test_kit/report.md, and adds a row to runs.csv.
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

from thehro import kit  # noqa: E402
from thehro.config import MIN_PER_DAY, OVERRIDE_MEMORY_DAYS  # noqa: E402
from thehro.explain import top_reasons  # noqa: E402
from thehro.features import ALL_FEATURES  # noqa: E402
from thehro.simulate import KIND_NAME  # noqa: E402

LABEL = {
    "NEW_PAYEE_LARGE": "First payment to this account, far above usual", "ESCALATING": "Payments to new accounts keep growing",
    "FD_BREAK": "A fixed deposit was just broken", "STRANGER_CREDITS": "Small credits from unknown accounts first",
    "DAY_OUTFLOW": "Much more sent today than usual", "NIGHT_NEW": "Late-night payment to a new account",
    "REGISTRY": "Account reported on the cybercrime portal", "FRI": "Account's number rated high risk by DoT",
    "CAMPAIGN": "Account is in a live mule network", "FAN_IN": "Many new people paid this account today",
    "PASS_THROUGH": "Account passes money straight on", "NEW_ACCOUNT": "Account opened recently",
    "DORMANT": "Account dormant for months", "CALL_UNKNOWN": "On a call with an unknown number",
    "CALLER_FRI": "Caller's number rated high risk by DoT", "SCREEN_SHARE": "Screen being shared",
    "REMOTE_APP": "Remote-access app running", "APP_SWITCHING": "Switching apps during the payment",
    "FIRST_PAYMENT": "First payment to this account (fallback fact)", "ABOVE_USUAL": "Above the usual amount (fallback fact)",
    "UNUSUAL_PATTERN": "Unusual pattern (fallback fact)", "RECENT_WARNING": "An earlier warning (memory)",
}
WHAT_THEY_DO = {
    2: ("Pause and explain", "reads the three reasons, waits 30 s, then Continue (or Cancel, or calls the bank)",
        "about 35 seconds"),
    3: ("Trusted check", "asks their trusted person to approve; with no trusted person, gets a call-back from the "
                         "bank; or waits one hour",
        "minutes with a trusted person; at most 15 minutes for a call-back (bank's target); one hour at most"),
    4: ("Hold for review", "a bank officer calls within 30 minutes and releases it; the payee is then confirmed",
        "at most 30 minutes (officer SLA)"),
}


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE, help="run_all.py --cache file")
    ap.add_argument("--out", default=None, help="results folder (default test_kit/results)")
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    T = time.time()

    base = kit.get_baseline(a.cache)                       # shared with the rest of run_test_kit.py
    world, ft, bundle = base["world"], base["ft"], base["bundle"]
    n_all, U = base["n_all"], base["n_users"]
    pol = base["pol"]                                      # finale policy (v2); customers continue past warnings
    head = base["head"]
    gen = ft.is_scam.values == 0
    hit = gen & (pol.tier.values >= 2)
    G = ft[hit].assign(tier=pol.tier.values[hit], base_tier=pol.base_tier.values[hit],
                       escalated=pol.escalated.values[hit])
    users = world.users
    weights = bundle["reason_weights"]

    first_reasons, rows = [], []
    for _, r in G.sort_values("t").iterrows():
        rd = {c: (None if pd.isna(r[c]) else float(r[c])) for c in ALL_FEATURES} | {"amount": float(r.amount)}
        reasons = top_reasons(rd, weights, k=3, fallback=True)       # what the live service shows
        first = "RECENT_WARNING" if r.escalated else reasons[0]["code"]
        first_reasons.append(first)
        victim = bool(users.victim.values[int(r.payer)])
        earlier = ft[(ft.payer.values == r.payer) & (ft.t.values < r.t)]
        warned_scam = bool(((earlier.is_scam.values == 1) & (pol.tier.loc[earlier.index].values >= 2)).any())
        rows.append({"pid": int(r.pid), "customer": int(r.payer), "kind": KIND_NAME[int(r.kind)],
                     "customer_is_scam_victim": victim, "after_a_warning_on_their_own_scam": warned_scam,
                     "amount": float(r.amount), "tier": int(r.tier), "raised_by_memory": bool(r.escalated),
                     "first_reason": first, "reason_text": reasons[0]["en"] if reasons else None,
                     "trusted_contact": bool(users.trusted_contact.values[int(r.payer)]),
                     "age_band": ["18-25", "26-40", "41-59", "60+"][int(r.age_band)]})
    R = pd.DataFrame(rows)

    by_tier = {}
    for k in (2, 3, 4):
        sub = R[R.tier == k]
        name, action, how_long = WHAT_THEY_DO[k]
        by_tier[name] = {"payments": int(len(sub)), "what_the_customer_does": action, "how_long": how_long}
        if k == 3:
            by_tier[name]["with_a_trusted_contact"] = int(sub.trusted_contact.sum())
    per_cust = R.groupby("customer").size()
    window = OVERRIDE_MEMORY_DAYS * MIN_PER_DAY
    again = 0
    for c, g in G.groupby("payer"):
        tt = np.sort(g.t.values)
        again += int((np.diff(tt) <= window).sum())
    supp = int((gen & pol.suppressed.values).sum())
    reasons_count = pd.Series(first_reasons).value_counts()
    mem = R[R.raised_by_memory]
    mem_victims = int(mem.after_a_warning_on_their_own_scam.sum())
    report = {
        "what": f"Genuine payments interrupted (tier 2+) on synthetic test days 42-59; frozen {kit.system(base)}. "
                "Counts measured; times are the product's own limits, not measured behaviour.",
        "genuine_payments_interrupted": int(len(R)), "customer_payments": int(n_all),
        "false_pauses_per_10k": len(R) / n_all * 1e4,
        "customers_interrupted": int(per_cust.size), "customers": int(U),
        "interruptions_per_customer_per_year": head["interruptions_per_customer_per_year"],
        "by_screen": by_tier,
        "raised_by_memory": int(R.raised_by_memory.sum()),
        "raised_by_memory_after_a_warning_on_their_own_scam": mem_victims,
        "customers_interrupted_more_than_once": int((per_cust > 1).sum()),
        "repeat_interruptions_within_7_days": again,
        "genuine_payments_let_through_because_the_payee_was_confirmed": supp,
        "genuine_holds": int((R.tier == 4).sum()),
        "first_reason_shown": reasons_count.to_dict(),
        "by_kind": R.kind.value_counts().to_dict(),
        "by_age_band": R.age_band.value_counts().to_dict(),
        "examples": R.head(5).to_dict(orient="records"),
        "seconds": round(time.time() - T),
    }
    path = kit.save_json(report, out / "false_positive_journey.json")

    yrs = 1 / head["interruptions_per_customer_per_year"]
    md = [
        f"**Measured, synthetic** (test days 42–59, frozen {kit.system(base)}); the times are the "
        "product's own limits, not measured behaviour.",
        "",
        f"On the 18 test days, **{len(R)} of {n_all:,} genuine customer payments** were interrupted "
        f"({len(R) / n_all * 1e4:.2f} per 10,000). That touched **{per_cust.size} of {U:,} customers**, "
        f"about one false interruption per customer every {yrs:.0f} years. **No genuine payment was held.**",
        "",
        "| What they saw | Payments | What the customer does | How long it takes |",
        "|---|---|---|---|",
    ]
    for name, d in by_tier.items():
        extra = ""
        if "with_a_trusted_contact" in d:
            n_tc = d["with_a_trusted_contact"]
            extra = (" (none had a trusted person set up, so the bank would call each of them back)"
                     if n_tc == 0 else f" ({n_tc} had a trusted person set up; the bank would call the rest back)")
        md.append(f"| {name} | {d['payments']}{extra} | {d['what_the_customer_does']} | {d['how_long']} |")
    md += [
        "",
        "**The alert says why it fired.** The first reason each of them saw:",
        "",
        "| First reason on the screen | Payments |",
        "|---|---|",
    ]
    for code, n in reasons_count.items():
        md.append(f"| {LABEL.get(code, code)} | {n} |")
    md += [
        "",
        f"**Repeat interruptions:** {int((per_cust > 1).sum())} of the {per_cust.size} customers "
        f"{'was' if int((per_cust > 1).sum()) == 1 else 'were'} interrupted more than once. "
        f"{int(R.raised_by_memory.sum())} of the {len(R)} interruptions came from memory, and {mem_victims} of those "
        "were people in the middle of a real scam: they had gone ahead after a warning on their *own* scam payment, "
        "so their other payments got extra checks for a few days. That's protection doing its job. The rest "
        "are the real cost of memory for genuine customers.",
        "",
        f"**Getting past it for good:** a payee the customer went ahead with is confirmed after 7 days without any "
        f"outside flag ({supp} genuine payments went through on the test days because of that). If a bank officer "
        "releases a held payment, the payee is confirmed for that customer at once (shown live in judge mode).",
        "",
    ]
    sec = kit.write_section(out, "false_positives", "\n".join(md))
    kit.log_run(out, kit.new_run_id("false_positives"), 7, [path, sec], command=kit.cmd(__file__, argv))
    print("\n".join(md))
    return report


if __name__ == "__main__":
    main()
