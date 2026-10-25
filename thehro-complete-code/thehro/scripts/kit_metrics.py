"""P2-3: error rates with intervals, confusion matrices, data the model never saw, and the completeness table.

    python scripts/kit_metrics.py                  # error rates, confusion matrices, unseen data, completeness
    python scripts/kit_metrics.py --run-tests      # also runs the pytest suite for the completeness table
    python scripts/kit_metrics.py --completeness-only

Everything is computed on ALL test days (42-59) of the synthetic world with the frozen model and
lines, and every value is checked against the committed outputs/metrics.json and outputs/tables/*.csv: if
anything disagrees the script stops, because the deck quotes those numbers.

Writes test_kit/results/kit_metrics.json, confusion_payments.csv, confusion_victims.csv and the Summary,
Error rates, Unseen data and Completeness sections of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import evaluate as E  # noqa: E402
from thehro import kit  # noqa: E402
from thehro.config import ADJUSTED_FRAUD_RATE, REPORTED_FRAUD_RATE, TIER_NAMES  # noqa: E402
from thehro.simulate import WORKFLOWS  # noqa: E402

OUT = kit.ROOT / "outputs"


def close(a, b, tol=1e-5) -> bool:
    a, b = float(a), float(b)
    return abs(a - b) <= tol * max(1.0, abs(b))


def check_reference(base: dict, rates: dict, per_type: pd.DataFrame, groups: dict) -> list[str]:
    """Every value must match outputs/metrics.json and outputs/tables (the numbers the deck quotes)."""
    ref = json.load(open(OUT / "metrics.json"))["headline"]
    head = base["head"]
    bad = []
    for k in ["victims_warned", "victims_warned_first_payment", "genuine_t2plus", "genuine_t2plus_per_10k",
              "genuine_t4plus", "scam_payments_t2plus", "precision_t2_adjusted_rate", "precision_t2_reported_rate",
              "money_protected_share", "journeys", "customers_never_interrupted_share"]:
        if not close(head[k], ref[k]):
            bad.append(f"headline.{k}: {head[k]} vs {ref[k]}")
    if base["n_all"] != ref["test_customer_payments"]:
        bad.append(f"test_customer_payments: {base['n_all']} vs {ref['test_customer_payments']}")
    pw = pd.read_csv(OUT / "tables" / "per_workflow.csv", index_col=0)
    for wf, r in per_type.iterrows():
        for col in ["victims_warned", "warned_at_first_payment", "scam_payments_warned"]:
            if not close(r[col], pw.loc[wf, col]):
                bad.append(f"per_workflow.{wf}.{col}: {r[col]} vs {pw.loc[wf, col]}")
    for col, fname in [("age_band", "fairness_age_band"), ("lang", "fairness_lang"), ("region", "fairness_region"),
                       ("consent", "fairness_consent")]:
        tab = pd.read_csv(OUT / "tables" / f"{fname}.csv", index_col=0)
        for g, r in groups[col].items():
            key = str(g)
            if key not in tab.index:
                bad.append(f"fairness.{col}: group {key} missing from {fname}.csv")
                continue
            if not close(r["false_pauses"]["value"], tab.loc[key, "genuine_interruptions_per_10k"]):
                bad.append(f"fairness.{col}.{key}: {r['false_pauses']['value']} vs "
                           f"{tab.loc[key, 'genuine_interruptions_per_10k']}")
            if not close(r["victims_warned"]["value"], tab.loc[key, "victims_warned"]):
                bad.append(f"fairness.{col}.{key} victims: {r['victims_warned']['value']} vs "
                           f"{tab.loc[key, 'victims_warned']}")
    return bad


def confusion(base: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    head, jt = base["head"], base["jt"]
    pay = pd.DataFrame({f"tier {k} {TIER_NAMES[k]}": [head[f"scam_tier{k}"], head[f"genuine_tier{k}"]] for k in range(5)},
                       index=["scam", "genuine"])
    pay["total"] = pay.sum(axis=1)
    g = jt.groupby("workflow")
    vic = pd.DataFrame({"warned": g.warned.sum().astype(int), "not_warned": (g.size() - g.warned.sum()).astype(int)})
    vic = vic.reindex([w for w in WORKFLOWS if w in vic.index])
    vic.loc["all"] = vic.sum()
    return pay, vic


def per_type_table(base: dict) -> pd.DataFrame:
    return E.per_workflow(base["jt"], base["ft"], base["pol"].tier.values)


def unseen_rows(base: dict, rates: dict) -> list[list]:
    ho = pd.read_csv(OUT / "tables" / "held_out_workflows.csv", index_col=0)
    m = json.load(open(OUT / "metrics.json"))
    labels = m["training_labels_by_workflow"]
    jt = base["jt"]
    rows = [["Held-out test days 42–59 (the whole evaluation)", "all 7 types, model frozen",
             kit.fmt_share(rates["victims_warned"]), kit.fmt_rate(rates["false_pauses_per_10k"]), "measured"]]
    for wf, r in ho.iterrows():
        n = int(r.journeys)
        k = int(round(r.victims_warned_hidden * n))
        rows.append([f"Scam type hidden from training: {wf}", "models refit 7 times, one type removed each time",
                     kit.fmt_share(kit.share(k, n)), f"{r.genuine_per_10k:.2f} per 10,000 (that refit)", "measured"])
    lf = jt[jt.workflow == "loan_fee"]
    rows.append(["Brand-new type: loan fee (starts day 30)", f"only {labels.get('loan_fee', 0)} reported labels in "
                 "training", kit.fmt_share(kit.share(int(lf.warned.sum()), len(lf))), "–", "measured"])
    return rows


def error_rate_rows(base: dict, rates: dict) -> list[list]:
    head = base["head"]
    n_all = base["n_all"]
    hold = rates["genuine_holds_per_10k"]
    bound = f"0 of {n_all:,}; 95% upper bound 1 in {n_all / kit.poisson(0)[1]:,.0f}" if hold["k"] == 0 else kit.fmt_rate(hold)
    return [
        ["Victims warned (tier 2+ at least once)", kit.fmt_share(rates["victims_warned"]), "Wilson", "measured"],
        ["Victims warned at their first scam payment", kit.fmt_share(rates["victims_warned_first_payment"]), "Wilson",
         "measured"],
        ["Scam payments paused (tier 2+)", kit.fmt_share(rates["scam_payments_paused"]), "Wilson", "measured"],
        ["False pauses (genuine tier 2+) per 10,000 customer payments", kit.fmt_rate(rates["false_pauses_per_10k"]),
         "exact Poisson", "measured"],
        ["Genuine trusted checks (tier 3)", kit.fmt_rate(rates["genuine_trusted_checks_per_10k"]), "exact Poisson",
         "measured"],
        ["Genuine holds (tier 4)", bound, "exact Poisson", "measured"],
        ["Customers never interrupted in 18 days", f"{head['customers_never_interrupted_share']:.2%} "
         f"({head['customers_interrupted']} of {base['n_users']:,} interrupted)", "–", "measured"],
        ["Share of pauses that are real scams at India's estimated true fraud rate (13.6 per million)",
         f"{head['precision_t2_adjusted_rate']:.1%} (about 1 in {1 / head['precision_t2_adjusted_rate']:.0f})", "–",
         "estimated"],
        ["Same, at the reported rate (6.8 per million)",
         f"{head['precision_t2_reported_rate']:.1%} (about 1 in {1 / head['precision_t2_reported_rate']:.0f})", "–",
         "estimated"],
        ["Money protected", f"{head['money_protected_share']:.1%}", "–", "modelled (assumed stop rates)"],
    ]


# ---------------------------------------------------------------------------------------------
# Completeness table
# ---------------------------------------------------------------------------------------------
def run_pytest() -> dict:
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=kit.ROOT, capture_output=True, text=True,
                           timeout=900)
        line = [x for x in r.stdout.strip().splitlines() if re.search(r"passed|failed|error", x)]
        return {"ok": r.returncode == 0, "summary": line[-1] if line else r.stdout[-200:]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "summary": f"could not run pytest: {e}"}


def _count_tests(path: Path) -> int:
    return len(re.findall(r"^def test_", path.read_text(), flags=re.M)) if path.exists() else 0


def completeness(results: Path, pytest_result: dict | None = None) -> list[list]:
    T = kit.ROOT / "tests"
    S = kit.ROOT / "scripts"

    def have(p: Path) -> bool:
        return p.exists()

    def jload(p: Path):
        try:
            return json.load(open(p))
        except Exception:  # noqa: BLE001
            return None

    rows = []
    unit_files = ["test_features.py", "test_policy.py", "test_explain.py", "test_api.py", "test_kit.py"]
    n_unit = sum(_count_tests(T / f) for f in unit_files)
    if pytest_result:
        res = pytest_result["summary"] + ("" if pytest_result["ok"] else "  <-- FAILING")
        st = "done" if pytest_result["ok"] else "failing"
    else:
        res, st = f"{n_unit} tests found; run `python -m pytest -q`", "done (not rerun in this pass)"
    rows.append(["Unit tests: point-in-time features, policy safety rules, messages, evidence tampering, API, kit "
                 "helpers", "`tests/` (" + ", ".join(f for f in unit_files if (T / f).exists()) + ")", st, res])
    for f, what, owner in [("test_live.py", "Live = batch feature parity (judge mode uses the training feature code)", "P1"),
                           ("test_bank.py", "Bank-server rules: consent drop, 30-second wait, holds, officer release, "
                            "fallback", "P1"),
                           ("test_failures.py", "Failure paths, run on purpose: feeds time out, decision service down, no officer, call-back not reached, no consent; a confirmed scam reaching every customer", "P1")]:
        rows.append([what, f"`tests/{f}`", "done" if have(T / f) else f"not done ({owner}, Phase 1–2)",
                     f"{_count_tests(T / f)} tests" if have(T / f) else "–"])
    ref = json.load(open(OUT / "metrics.json"))["headline"]
    rows.append(["Reproducibility: a clean rebuild reproduces outputs/ exactly", "`scripts/compare_outputs.py`; "
                 "`kit.load_baseline()` asserts on every run",
                 "done" if have(S / "compare_outputs.py") else "partly: baseline asserts only (compare_outputs.py is P1-1)",
                 f"every kit run checks {round(ref['victims_warned'] * ref['journeys'])}/{ref['journeys']} victims and "
                 f"{ref['genuine_t2plus']} false pauses against outputs/metrics.json"])
    km = jload(results / "kit_metrics.json")
    rows.append(["Held-out test days (whole evaluation, all error rates with CIs)", "`scripts/kit_metrics.py`",
                 "done" if km else "not run", "matches outputs/metrics.json" if km and not km.get("mismatches") else "–"])
    ho = pd.read_csv(OUT / "tables" / "held_out_workflows.csv", index_col=0)
    rows.append(["Scam types hidden from training (7 refits)", "`outputs/tables/held_out_workflows.csv` (`run_all.py`)",
                 "done", f"{ho.victims_warned_hidden.min():.0%}–{ho.victims_warned_hidden.max():.0%} of victims still warned"])
    fc = jload(results / "failure_cases.json")
    rows.append(["Named failure cases (every miss and false pause grouped)", "`scripts/failure_cases.py`",
                 "done" if fc else "not run",
                 f"{fc['missed']['n']} missed victims in {len(fc['missed']['groups'])} groups, "
                 f"{fc['false_pauses']['n']} false pauses in {len(fc['false_pauses']['groups'])} groups" if fc else "–"])
    fp = jload(results / "false_positive_journey.json")
    rows.append(["Genuine customers flagged by mistake: what they saw, how long it took, repeats",
                 "`scripts/eval_false_positives.py`", "done" if fp else "not run",
                 f"{fp['genuine_payments_interrupted']} interrupted ({fp['customers_interrupted']} customers), "
                 f"{fp['genuine_holds']} held; {fp['raised_by_memory']} raised by memory" if fp else "–"])
    sc = sorted(results.glob("spot_check_seed*.json"))
    rows.append(["K2 spot-checks: random labelled draws through the live decision service", "`scripts/spot_check.py`",
                 "done" if sc else "not run",
                 "; ".join(f"seed {jload(p)['seed']}{' journeys' if 'journeys' in p.name else ''}: "
                           f"{jload(p)['summary']['right']}/{jload(p)['summary']['n']} right, live = evaluation "
                           f"{jload(p)['summary']['live_equals_evaluation']}/{jload(p)['summary']['n']}" for p in sc) or "–"])
    k1 = jload(results / "k1_results.json")
    k1_status = ("done" if k1 and k1.get("status") == "ran" else
                 f"not done: {k1.get('reason')}" if k1 else "not done (cases from P4, bank server from P1)")
    rows.append(["K1 hand-written, pre-registered cases", "`test_kit/cases/`, `scripts/k1_run.py`", k1_status,
                 f"{k1['summary']['right']}/{k1['summary']['n']} right" if k1 and k1.get("status") == "ran" else "–"])
    at = jload(results / "attacks.json")
    rows.append(["Attacks on the held-out weeks (event-level, frozen model)", "`scripts/attacks.py`",
                 "done" if at else "not run",
                 "; ".join(f"{r['label']}: {r['victims_warned']['value']:.1%}" for r in at["rows"] if r.get("ran"))
                 if at else "–"])
    pv = jload(results / "policy_versions.json")
    if pv:
        cr, cur = pv["cancel_and_retry_attack"], pv["outputs_policy"]
        pv_res = (f"retries stopped {cr['v1']['retries_interrupted']:.1%} (Round 1 policy) → "
                  f"{cr[cur]['retries_interrupted']:.1%} (policy {cur}), n = {cr[cur]['retries']}; victims warned "
                  f"{pv['test_days']['v1']['victims_warned']:.1%} → {pv['test_days'][cur]['victims_warned']:.1%}")
    rows.append(["Policy versions side by side, and cancel and retry to another payee (live path)",
                 "`scripts/eval_policy.py`", "done" if pv else "not run", pv_res if pv else "–"])
    so = jload(results / "signal_switchoff.json")
    rows.append(["Signals switched off in turn (ablations)", "`scripts/signal_switchoff.py`",
                 "done" if so else "not run", f"{len(so['rows']) - 1} switch-offs" if so else "–"])
    fa = jload(results / "fairness.json")
    rows.append(["Fairness by age, language, region, consent (baseline and combined attack)",
                 "`scripts/fairness_check.py`", "done" if fa else "not run",
                 f"worst age group {fa['baseline']['worst_age_ratio']:.2f}× the overall pause rate "
                 f"({'pass' if fa['baseline']['age_pass'] else 'FAIL'})" if fa else "–"])
    ci = jload(results / "caller_identity.json")
    rows.append(["Caller identity: the shipped system without it vs a counterfactual with it", "`scripts/caller_identity.py`",
                 "done" if ci else "not run",
                 f"victims warned {ci['shipped']['victims_warned']['value']:.1%} shipped vs "
                 f"{ci['with_caller_id']['victims_warned']['value']:.1%} with caller id; false pauses "
                 f"{ci['shipped']['false_pauses_per_10k']['value']:.2f} vs {ci['with_caller_id']['false_pauses_per_10k']['value']:.2f}"
                 if ci else "–"])
    dc = jload(results / "design_choices.json")
    if dc:
        ch = dc["journey_line_share"].get(str(dc["chosen_share"]), {})
        dc_res = (f"adversarial training rejected; journey-line share {dc['chosen_share']} gives the combined attack "
                  f"{ch.get('all', {}).get('victims_warned', float('nan')):.1%}")
    rows.append(["Design choices tried (adversarial training; journey-line share sweep)", "`scripts/design_choices.py`",
                 "done" if dc else "not run", dc_res if dc else "–"])
    ps = jload(results / "paysim" / "paysim_results.json")
    rows.append(["Public benchmark: PaySim (method on outside data)", "`scripts/paysim_benchmark.py`",
                 "done" if ps else "not done (needs data/paysim/, see P2-4)",
                 f"at 1 in 10,000: {ps['main']['budgets']['1e-04']['frauds_caught']['value']:.1%} of frauds caught, "
                 f"{ps['main']['budgets']['1e-04']['false_alarms_per_10k']['value']:.2f} false alarms per 10,000" if ps else "–"])
    ex = (kit.KIT / "data" / "heldout_full.sha256")
    rows.append(["Dataset A published: held-out export + data card", "`scripts/export_test_set.py`, `test_kit/README.md`",
                 "done" if ex.exists() else "not run", ex.read_text().split()[0][:16] + "…" if ex.exists() else "–"])
    lt = jload(results / "loadtest.json")
    if lt:
        d1, b1 = lt["decision_service"][0], lt["bank_server"][0]
        lat_res = (f"decision service {d1['p50_ms']:.1f} ms median, {d1['p99_ms']:.1f} ms p99 over HTTP, "
                   f"{d1['throughput_per_s']:.0f}/s per process; judge case end to end {b1['p50_ms']:.1f} ms median; "
                   f"{sum(r['errors'] for r in lt['decision_service'] + lt['bank_server'])} errors")
    else:
        L = json.load(open(OUT / "metrics.json"))["headline"].get("latency_ms", {})
        lat_res = (f"{L.get('p50', float('nan')):.1f} ms median, {L.get('p99', float('nan')):.1f} ms p99 inside the "
                   "service, 1,000 decisions (`run_all.py`)")
    rows.append(["Latency and load: decision service and bank server under concurrent requests",
                 "`scripts/loadtest.py`", "done" if lt else "not run", lat_res])
    fw = jload(results / "fresh_world.json")
    if fw:
        fz = fw["systems"]["Finale system, frozen"]
        fw_res = (f"seed {fw['seed']}: frozen finale system warns {fz['rebuild']['victims_warned']['value']:.1%}, "
                  f"{fz['rebuild']['false_pauses_per_10k']['value']:.2f} false pauses per 10,000; combined attack "
                  f"{fz['all']['victims_warned']['value']:.1%}")
    rows.append(["A freshly simulated world no design choice saw (frozen systems and retrained recipes)",
                 "`scripts/fresh_world.py`", "done" if fw else "not run", fw_res if fw else "–"])
    return rows


def completeness_section(results: Path, pytest_result=None) -> str:
    rows = completeness(results, pytest_result)
    return ("Every kind of test we run, what it checks and where it lives. Rows that are not done stay in the "
            "table, with who owns them.\n\n" + kit.md_table(["Test", "Where", "Status", "Result"], rows))


def summary_section(base: dict, rates: dict) -> str:
    head = base["head"]
    rows = [
        ["Victims warned", kit.fmt_share(rates["victims_warned"]), "measured, synthetic"],
        ["False pauses per 10,000 payments", kit.fmt_rate(rates["false_pauses_per_10k"]), "measured, synthetic"],
        ["Genuine holds", f"0 of {base['n_all']:,}", "measured, synthetic"],
        ["Pauses that are real scams at India's fraud rate", f"about 1 in {1 / head['precision_t2_adjusted_rate']:.0f}",
         "estimated"],
        ["Money protected", f"{head['money_protected_share']:.0%}", "modelled"],
    ]
    return (f"Frozen {kit.system(base)}, synthetic test days 42–59 ({len(base['jt'])} scam victims, "
            f"{base['n_all']:,} customer payments). "
            "Sections below add the spot-checks, failure cases, attacks, switch-offs, fairness and the public PaySim "
            "benchmark.\n\n" + kit.md_table(["Measure", "Value (95% CI, n)", "Kind"], rows))


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    ap.add_argument("--run-tests", action="store_true")
    ap.add_argument("--completeness-only", action="store_true")
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    pyres = run_pytest() if a.run_tests else None
    if a.completeness_only:
        sec = kit.write_section(out, "completeness", completeness_section(out, pyres))
        kit.log_run(out, kit.new_run_id("completeness"), None, [sec], command=kit.cmd(__file__, argv))
        return {}
    base = kit.get_baseline(a.cache)
    rates = kit.headline_rates(base["head"], base["jt"], base["ft"], base["pol"].tier.values, base["n_all"])
    per_type = per_type_table(base)
    groups = kit.group_rates(base["world"], base["ft"], base["pol"].tier.values, base["jt"], base["denom"])
    bad = check_reference(base, rates, per_type, groups)
    if bad:
        print("MISMATCH with outputs/ (the deck's numbers):\n  " + "\n  ".join(bad))
        sys.exit(1)
    pay, vic = confusion(base)
    pay.to_csv(out / "confusion_payments.csv")
    vic.to_csv(out / "confusion_victims.csv")
    jt = base["jt"]
    type_rows = []
    for wf, r in per_type.iterrows():
        m = jt.workflow.values == wf
        type_rows.append([wf, int(r.journeys), kit.fmt_share(kit.share(int(jt.warned.values[m].sum()), int(m.sum()))),
                          kit.fmt_share(kit.share(int(jt.warned_first_payment.values[m].sum()), int(m.sum()))),
                          f"{r.scam_payments_warned:.1%}"])
    group_md = []
    for col, title in [("age_band", "Age"), ("lang", "Language"), ("region", "Region"), ("consent", "Consent")]:
        group_md.append(f"**{title}** (never a model input; used only for this check)")
        group_md.append("")
        group_md.append(kit.md_table(["Group", "False pauses per 10,000 (95% CI; count)", "× overall",
                                      "Victims warned (95% CI)"],
                                     [[g, kit.fmt_rate(r["false_pauses"]), f"{r['ratio_to_overall']:.2f}",
                                       kit.fmt_share(r["victims_warned"])] for g, r in groups[col].items()]))
        group_md.append("")
    res = {"data": f"synthetic test days 42-59, frozen {kit.system(base)}", "rates": rates,
           "precision_estimated": {"adjusted_rate": base["head"]["precision_t2_adjusted_rate"],
                                   "reported_rate": base["head"]["precision_t2_reported_rate"],
                                   "fraud_rates": {"reported": REPORTED_FRAUD_RATE, "adjusted": ADJUSTED_FRAUD_RATE}},
           "confusion_payments": pay, "confusion_victims": vic, "per_type": per_type, "groups": groups,
           "mismatches": bad}
    path = kit.save_json(res, out / "kit_metrics.json")
    err = ["**Measured, synthetic**, unless marked. Payment-level confusion matrix (rows: truth; columns: the tier "
           "the payment reached):", "",
           kit.md_table(["Truth"] + list(pay.columns), [[i] + [f"{int(v):,}" for v in r] for i, r in pay.iterrows()]),
           "", "Victim level (warned = at least one tier 2+ payment in the journey):", "",
           kit.md_table(["Scam type", "Warned", "Not warned"], [[i, int(r.warned), int(r.not_warned)] for i, r in vic.iterrows()]),
           "", "Error rates:", "",
           kit.md_table(["Rate", "Value (95% CI, n)", "Interval", "Kind"], error_rate_rows(base, rates)),
           "", "By scam type:", "",
           kit.md_table(["Scam type", "Victims", "Warned (95% CI)", "Warned at first payment", "Scam payments paused"],
                        type_rows),
           "", "By group:", ""] + group_md + [
           "Every value above was checked against `outputs/metrics.json` and `outputs/tables/` (the numbers the deck "
           "quotes) before this section was written. Small groups (Telugu 10 victims, Gujarati 6) have wide intervals."]
    secs = [kit.write_section(out, "summary", summary_section(base, rates)),
            kit.write_section(out, "error_rates", "\n".join(err)),
            kit.write_section(out, "unseen", "**Measured, synthetic.**\n\n" + kit.md_table(
                ["Unseen data", "How", "Victims warned (95% CI)", "False pauses", "Kind"], unseen_rows(base, rates))),
            kit.write_section(out, "completeness", completeness_section(out, pyres))]
    if not (out / "sections" / "latency.md").exists():      # P1 owns this section; never overwrite theirs
        L = json.load(open(OUT / "metrics.json"))["headline"]["latency_ms"]
        secs.append(kit.write_section(out, "latency", (
            f"**Measured, synthetic (`run_all.py`).** Inside the decision service: {L['p50']:.1f} ms median, {L['p95']:.1f} ms "
            f"p95, {L['p99']:.1f} ms p99 over {L['n']:,} full decisions (models, policy, reasons, message, evidence), "
            "single process, 2 vCPU (`run_all.py` step 4). Not yet measured: load test, the phone round trip over LAN "
            "and 4G, and the failure paths (feed timeout, no consent, no officer, API down). Those come from P1's "
            "`scripts/loadtest.py` and `tests/test_failures.py` and replace this paragraph.")))
    kit.log_run(out, kit.new_run_id("kit_metrics"), None,
                [path, out / "confusion_payments.csv", out / "confusion_victims.csv"] + secs,
                command=kit.cmd(__file__, argv))
    print(f"all values match outputs/metrics.json; victims warned {kit.fmt_share(rates['victims_warned'])}, "
          f"false pauses {kit.fmt_rate(rates['false_pauses_per_10k'])}")
    return res


if __name__ == "__main__":
    main()
