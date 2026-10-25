"""L-2: the PaySim benchmark. Does our METHOD work on public data we did not create?

    python scripts/paysim_benchmark.py                          # reads data/paysim/*.csv
    python scripts/paysim_benchmark.py --csv path/to/PS_20174392719_1491204439457_log.csv

PaySim (Lopez-Rojas, Elmir and Axelsson, EMSS 2016; Kaggle ealaxi/paysim1) is a public synthetic mobile-money
dataset: about 6.3 M transactions over 744 hourly steps, about 0.13% fraud. Its fraud is ACCOUNT TAKEOVER
(a TRANSFER then a CASH_OUT that empties the account), not coached scams, so this tests the method, not the scam
signals:
  * point-in-time features (only what is known before each transaction),
  * gradient-boosted trees with the same settings as thehro/models.py _hgb() (one model, no families),
  * alert lines set on GENUINE validation traffic to a fixed false-alarm budget, counted against ALL
    transactions, exactly as policy.calibrate does: 1 in 1,000 and 1 in 10,000.

Time split: train on steps 1-400, set the lines on steps 401-500, test on steps 501-744.
Never used: newbalanceOrig and newbalanceDest (known only after the transaction, a known leak), isFlaggedFraud
(a baseline) and oldbalanceDest (left out; --with-dest-balance adds it as a labelled variant).

Writes test_kit/results/paysim/paysim_results.json, paysim_table.csv and the PaySim section of test_kit/report.md.
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
from thehro.features import EventIndex  # noqa: E402
from thehro.models import _hgb  # noqa: E402

TYPES = ["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"]
TRAIN = (1, 400)
VALID = (401, 500)
TEST = (501, 744)
BUDGETS = [1e-3, 1e-4]
FEATURES = ["type", "amount_log", "hour", "amt_vs_balance", "empties_account", "orig_zero_balance",
            "dest_is_merchant", "dest_prev_in_count", "dest_new_payers_24", "dest_inflow_24_log",
            "dest_steps_since_first_seen"]
USECOLS = ["step", "type", "amount", "nameOrig", "oldbalanceOrg", "nameDest", "oldbalanceDest", "isFraud",
           "isFlaggedFraud"]


def find_csv(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    d = kit.DATA / "paysim"
    files = sorted(d.glob("*.csv")) if d.exists() else []
    return files[0] if files else None


def load(path: Path) -> pd.DataFrame:
    p = pd.read_csv(path, usecols=USECOLS, dtype={"step": np.int32, "type": "category", "amount": np.float64,
                                                   "oldbalanceOrg": np.float64, "oldbalanceDest": np.float64,
                                                   "isFraud": np.int8, "isFlaggedFraud": np.int8})
    return p.reset_index(drop=True)


def features(p: pd.DataFrame, with_dest_balance: bool = False) -> pd.DataFrame:
    """Only information known before each transaction. The receiving account's history uses earlier steps only:
    transactions in the same hourly step have no order in PaySim, so none of them counts."""
    f = pd.DataFrame(index=p.index)
    f["type"] = pd.Categorical(p["type"].astype(str), categories=TYPES).codes.astype(np.int8)
    amt = p.amount.values
    bal = p.oldbalanceOrg.values
    f["amount_log"] = np.log10(np.maximum(amt, 1))
    f["hour"] = (p.step.values % 24).astype(np.int8)
    f["amt_vs_balance"] = np.where(bal > 0, amt / np.where(bal > 0, bal, 1), np.nan)
    f["empties_account"] = ((bal > 0) & (amt >= bal)).astype(np.int8)
    f["orig_zero_balance"] = (bal == 0).astype(np.int8)
    f["dest_is_merchant"] = p.nameDest.str.startswith("M").values.astype(np.int8)

    dest = pd.factorize(p.nameDest)[0].astype(np.int64)
    orig = pd.factorize(p.nameOrig)[0].astype(np.int64)
    t = p.step.values.astype(float) * 60.0                  # one step = one hour, in minutes
    pair = orig * (int(dest.max()) + 1) + dest
    new_pair = ~pd.Series(pair).duplicated().values         # first time this payer pays this receiver
    E_in = EventIndex(dest, t, {"amount": amt, "new": new_pair.astype(float)})
    # every earlier step: a window longer than the whole dataset (it stays inside each account's own events,
    # because EventIndex keys are entity * 10^7 + seconds and 800 hours is far less than 10^7 seconds)
    f["dest_prev_in_count"] = E_in.window("count", dest, t, 800 * 60.0)
    f["dest_new_payers_24"] = E_in.window("new", dest, t, 24 * 60.0)
    f["dest_inflow_24_log"] = np.log10(E_in.window("amount", dest, t, 24 * 60.0) + 1)
    first = pd.Series(p.step.values).groupby(dest).transform("min").values
    f["dest_steps_since_first_seen"] = np.where(first < p.step.values, p.step.values - first, np.nan)
    if with_dest_balance:
        f["dest_old_balance_log"] = np.log10(np.maximum(p.oldbalanceDest.values, 0) + 1)
    return f


def line(scores_genuine: np.ndarray, n_all: int, budget: float) -> float:
    """Same rule as policy.calibrate: genuine validation transactions cross the line at most budget x n_all times."""
    allowed = int(np.floor(budget * n_all))
    v = np.sort(scores_genuine)[::-1]
    return float(np.nextafter(v[allowed], np.inf)) if allowed < len(v) else 0.0


def measure(flag: np.ndarray, y: np.ndarray, amount: np.ndarray) -> dict:
    tp = int((flag & (y == 1)).sum())
    fp = int((flag & (y == 0)).sum())
    return {"frauds_caught": kit.share(tp, int((y == 1).sum())),
            "false_alarms_per_10k": kit.per10k(fp, len(y)),
            "precision": kit.share(tp, tp + fp),
            "money_caught_share": float(amount[flag & (y == 1)].sum() / max(amount[y == 1].sum(), 1)),
            "alerts": int(flag.sum())}


def run(p: pd.DataFrame, with_dest_balance: bool = False, seed: int = 0) -> dict:
    t0 = time.time()
    f = features(p, with_dest_balance)
    cols = FEATURES + (["dest_old_balance_log"] if with_dest_balance else [])
    s = p.step.values
    y = p.isFraud.values
    tr = (s >= TRAIN[0]) & (s <= TRAIN[1])
    va = (s >= VALID[0]) & (s <= VALID[1])
    te = (s >= TEST[0]) & (s <= TEST[1])
    model = _hgb(cols, seed).set_params(categorical_features=["type"])
    model.fit(f.loc[tr, cols], y[tr])
    sv = model.predict_proba(f.loc[va, cols])[:, 1]
    st = model.predict_proba(f.loc[te, cols])[:, 1]
    yv, yt = y[va], y[te]
    amt_t = p.amount.values[te]
    out = {"rows": {"train": int(tr.sum()), "valid": int(va.sum()), "test": int(te.sum())},
           "frauds": {"train": int(y[tr].sum()), "valid": int(yv.sum()), "test": int(yt.sum())},
           "features": cols, "n_iter": int(model.n_iter_), "budgets": {}, "baselines": {}}
    for b in BUDGETS:
        thr = line(sv[yv == 0], int(va.sum()), b)
        out["budgets"][f"{b:.0e}"] = {"budget": b, "line": thr, **measure(st >= thr, yt, amt_t)}
    pt = p[te]
    typ = pt["type"].astype(str).values
    bal = pt.oldbalanceOrg.values
    rules = {
        "PaySim's own isFlaggedFraud (transfers over 200,000)": pt.isFlaggedFraud.values == 1,
        "TRANSFER or CASH_OUT that empties the account": np.isin(typ, ["TRANSFER", "CASH_OUT"]) & (bal > 0)
        & (pt.amount.values >= bal),
        "Amount over 200,000": pt.amount.values > 200_000,
    }
    for name, flag in rules.items():
        out["baselines"][name] = measure(flag, yt, amt_t)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def section(res: dict) -> str:
    r = res["main"]
    rows = []
    for k, b in r["budgets"].items():
        rows.append([f"Thehro method, line at 1 in {1 / b['budget']:,.0f}", kit.fmt_share(b["frauds_caught"]),
                     kit.fmt_rate(b["false_alarms_per_10k"]), kit.fmt_share(b["precision"]),
                     f"{b['money_caught_share']:.1%}"])
    for name, b in r["baselines"].items():
        rows.append([name, kit.fmt_share(b["frauds_caught"]), kit.fmt_rate(b["false_alarms_per_10k"]),
                     kit.fmt_share(b["precision"]), f"{b['money_caught_share']:.1%}"])
    var = ""
    if res.get("with_dest_balance"):
        v = res["with_dest_balance"]["budgets"]["1e-04"]
        var = (f"\n\nLabelled variant with the receiver's balance before the transaction (`oldbalanceDest`): at 1 in "
               f"10,000, {kit.fmt_share(v['frauds_caught'])} of frauds caught. PaySim's receiver balances are known to "
               "be unreliable for merchants, so the main result leaves them out.")
    return (f"**Measured on PaySim** (public synthetic mobile-money data, `{res['file']}`). Trained on steps 1–400 "
            f"({r['rows']['train']:,} transactions, {r['frauds']['train']:,} frauds), lines set on steps 401–500 using "
            f"genuine transactions only, tested on steps 501–744 ({r['rows']['test']:,} transactions, "
            f"{r['frauds']['test']:,} frauds). Features use only what is known before each transaction: never the "
            "after-balances (`newbalanceOrig`, `newbalanceDest`, a known leak) and never `isFlaggedFraud`.\n\n"
            + kit.md_table(["Method", "Frauds caught", "False alarms", "Precision", "Money caught"], rows) + var +
            "\n\n**What this shows and what it can't.** It shows the method (point-in-time features, gradient-boosted "
            "trees, alert lines set to a fixed false-alarm budget on genuine traffic) works on data we did not make, "
            "and beats simple rules at the same or lower false-alarm rate. PaySim fraud is account takeover by the "
            "fraudster, so it cannot test the scam signals: there are no calls, no coaching of a victim who pays "
            "with their own PIN, no customer history (`nameOrig` almost never repeats) and no mule networks across "
            "banks. Those are tested only on our synthetic data.")


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--with-dest-balance", action="store_true", help="also run the labelled oldbalanceDest variant")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    path = find_csv(a.csv)
    if path is None or not path.exists():
        msg = ("PaySim not found. Download it from Kaggle (ealaxi/paysim1) into data/paysim/ (git-ignored); see the "
               "P2-4 step in test_kit/README.md.")
        print(msg)
        kit.write_section(out, "paysim", f"_Not run: {msg}_")
        return {}
    print(f"loading {path} ...", flush=True)
    p = load(path)
    print(f"{len(p):,} transactions, {int(p.isFraud.sum()):,} frauds, steps {p.step.min()}-{p.step.max()}", flush=True)
    res = {"file": path.name, "data": "PaySim (public synthetic, Kaggle ealaxi/paysim1)", "main": run(p)}
    if a.with_dest_balance:
        res["with_dest_balance"] = run(p, with_dest_balance=True)
    d = out / "paysim"
    jpath = kit.save_json(res, d / "paysim_results.json")
    flat = [{"method": f"thehro_line_1_in_{1 / b['budget']:.0f}", "frauds_caught": b["frauds_caught"]["value"],
             "false_alarms_per_10k": b["false_alarms_per_10k"]["value"], "precision": b["precision"]["value"],
             "money_caught_share": b["money_caught_share"]} for b in res["main"]["budgets"].values()]
    flat += [{"method": n, "frauds_caught": b["frauds_caught"]["value"],
              "false_alarms_per_10k": b["false_alarms_per_10k"]["value"], "precision": b["precision"]["value"],
              "money_caught_share": b["money_caught_share"]} for n, b in res["main"]["baselines"].items()]
    pd.DataFrame(flat).to_csv(d / "paysim_table.csv", index=False)
    sec = kit.write_section(out, "paysim", section(res))
    kit.log_run(out, kit.new_run_id("paysim"), 0, [jpath, d / "paysim_table.csv", sec],
                command=kit.cmd(__file__, argv))
    for row in flat:
        print(f"{row['method']:55s} caught {row['frauds_caught']:.1%}  false alarms/10k "
              f"{row['false_alarms_per_10k']:.2f}  precision {row['precision']:.1%}")
    print(f"saved {jpath} ({res['main']['seconds']}s)")
    return res


if __name__ == "__main__":
    main()
