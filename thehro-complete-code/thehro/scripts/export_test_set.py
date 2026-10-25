"""Dataset A: export the synthetic held-out test days so anyone can check our numbers.

    python scripts/export_test_set.py

Writes
  data/thehro_heldout.csv.gz              every scored test-day payment (git-ignored; attach to a GitHub release)
  test_kit/data/heldout_sample.csv        a 2,000-row stratified sample (committed)
  test_kit/data/heldout_full.sha256       SHA-256 of the full file (committed)
  test_kit/data/columns.csv               what every column means (committed)

The generator is the source of truth: `simulate.py` with seed 7 rebuilds the same world, and the gzip is
written with a fixed timestamp, so the same code gives a byte-identical file and the same SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import evaluate as E  # noqa: E402
from thehro import kit  # noqa: E402
from thehro.config import MIN_PER_DAY  # noqa: E402
from thehro.features import C_FEATURES, J_FEATURES, P_FEATURES  # noqa: E402
from thehro.simulate import AGE_BANDS, KIND_NAME, LANGS, REGIONS, WORKFLOWS  # noqa: E402

SAMPLE = {"scam": 500, "risky-looking genuine": 500, "other genuine": 1000}


def frame(base: dict) -> pd.DataFrame:
    ft, St, pol = base["ft"], base["St"], base["pol"]
    d = pd.DataFrame({"pid": ft.pid.values, "t_min": ft.t.values, "day": ft.day.values,
                      "payer": ft.payer.values, "payee": ft.payee.values, "amount": ft.amount.values,
                      "kind": ft.kind.map(KIND_NAME).values}, index=ft.index)
    for c in J_FEATURES + P_FEATURES + C_FEATURES:
        d[c] = ft[c].values
    d["consent"] = ft.consent.values.astype(bool)
    d["is_scam"] = ft.is_scam.values
    d["scam_type"] = [WORKFLOWS[w] if w >= 0 else "" for w in ft.workflow.values]
    d["journey"] = ft.journey.values
    d["step"] = ft.step.values
    d["campaign"] = ft.campaign.values
    rep = ft.report_t.values
    d["reported"] = np.isfinite(rep)
    d["report_day"] = np.where(np.isfinite(rep), rep / MIN_PER_DAY, np.nan)
    d["bank_label_by_day_60"] = ((ft.is_scam.values == 1) & (rep < 60 * MIN_PER_DAY)).astype(np.int8)
    d["audit_age_band"] = [AGE_BANDS[i] for i in ft.age_band.values]
    d["audit_lang"] = [LANGS[i] for i in ft.lang.values]
    d["audit_region"] = [REGIONS[i] for i in ft.region.values]
    for c in ["s_fused", "s_journey", "s_payee", "s_context"]:
        d[f"thehro_{c}"] = St[c].values
    d["thehro_base_tier"] = pol.base_tier.values
    d["thehro_tier"] = pol.tier.values
    return d.reset_index(drop=True)


COLUMNS = [
    ("pid, t_min, day", "payment id, minutes since day 0, day number (42-59)"),
    ("payer, payee, amount", "pseudonymous customer and receiving account ids; amount in rupees"),
    ("kind", "what the payment really is (simulator ground truth): scam, new_individual, new_landlord, medical, ..."),
    (", ".join(J_FEATURES), "journey family: the payer's own history before this payment (model inputs)"),
    (", ".join(P_FEATURES), "payee family: the receiving account before this payment (model inputs)"),
    (", ".join(C_FEATURES), "context family: phone flags, blank when the customer did not opt in (model inputs)"),
    ("consent", "customer opted in to phone signals; without it the context family does not vote"),
    ("is_scam, scam_type, journey, step, campaign", "ground truth labels; journey links the payments of one victim"),
    ("reported, report_day", "whether and when the victim complained (half never do)"),
    ("bank_label_by_day_60", "the label a bank would hold at the end of the data: a scam reported by day 60. The "
     "training rule (reported before day 38) gives 0 for every test-day row, so it is not repeated here"),
    ("audit_age_band, audit_lang, audit_region", "AUDIT ONLY, never a model input: used for the fairness check"),
    ("thehro_s_fused, thehro_s_journey, thehro_s_payee, thehro_s_context", "frozen model scores (outputs/)"),
    ("thehro_base_tier, thehro_tier", "tier before and after customer memory (0 allow ... 4 hold), frozen lines"),
]


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None, help="results folder (for runs.csv)")
    ap.add_argument("--seed", type=int, default=7, help="seed for the 2,000-row sample")
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    base = kit.get_baseline(a.cache)
    d = frame(base)
    kit.DATA.mkdir(parents=True, exist_ok=True)
    full = kit.DATA / "thehro_heldout.csv.gz"
    d.to_csv(full, index=False, compression={"method": "gzip", "mtime": 0})
    sha = hashlib.sha256(full.read_bytes()).hexdigest()
    kd = kit.KIT / "data"
    kd.mkdir(parents=True, exist_ok=True)
    (kd / "heldout_full.sha256").write_text(f"{sha}  thehro_heldout.csv.gz\n")
    rng = np.random.default_rng(a.seed)
    risky = d.kind.isin(["new_individual", "new_landlord", "medical", "gray_invest"]).values
    pools = {"scam": d.index[d.is_scam.values == 1],
             "risky-looking genuine": d.index[(d.is_scam.values == 0) & risky],
             "other genuine": d.index[(d.is_scam.values == 0) & ~risky]}
    pick = np.concatenate([rng.choice(p, size=min(SAMPLE[k], len(p)), replace=False) for k, p in pools.items()])
    sample = d.loc[np.sort(pick)]
    sample.to_csv(kd / "heldout_sample.csv", index=False)
    pd.DataFrame(COLUMNS, columns=["columns", "meaning"]).to_csv(kd / "columns.csv", index=False)
    w = E.windows(base["f"])
    info = {"rows": int(len(d)), "scam_rows": int(d.is_scam.sum()), "victims": int(len(base["jt"])),
            "sha256": sha, "size_mb": round(full.stat().st_size / 1e6, 1), "sample_rows": int(len(sample)),
            "validation_rows": int(w["val"].sum())}
    kit.log_run(out, kit.new_run_id("export_test_set"), a.seed,
                [full, kd / "heldout_full.sha256", kd / "heldout_sample.csv", kd / "columns.csv"],
                command=kit.cmd(__file__, argv))
    print(f"wrote {full} ({info['size_mb']} MB, {info['rows']:,} rows, {info['scam_rows']:,} scam payments)")
    print(f"sha256 {sha}")
    print(f"wrote {kd / 'heldout_sample.csv'} ({len(sample):,} rows: 500 scam, 500 risky-looking genuine, 1,000 other)")
    return info


if __name__ == "__main__":
    main()
