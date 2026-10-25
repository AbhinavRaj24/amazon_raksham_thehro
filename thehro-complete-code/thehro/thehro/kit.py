"""The test kit's shared pieces: the frozen baseline in outputs/, right/wrong rules, confidence intervals,
the spot-check runner, the run log and the report builder.

Every script in scripts/ that writes a test-kit result imports this module, and so does the judge
page's Kit tab (kit.spot_check). Nothing here trains or tunes anything: the model, the alert lines and the
policy version are the ones in outputs/ (written by run_all.py), and nothing here writes into outputs/.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2, norm

from . import evaluate as E
from .config import TIER_NAMES, VALID_END
from .explain import inr
from .features import ALL_FEATURES
from .models import score
from .policy import RULES
from .service import DecisionService, load_bundle
from .simulate import CHANNELS, KIND_NAME, WORKFLOWS

ROOT = Path(__file__).resolve().parents[1]            # thehro-complete-code/thehro
REPO = ROOT.parents[1]                                 # repository root
KIT = REPO / "test_kit"
RESULTS = KIT / "results"
DATA = REPO / "data"                                   # git-ignored: full exports, PaySim, model v2
DEFAULT_CACHE = os.environ.get("THEHRO_CACHE", str(ROOT / ".cache" / "thehro_world.pkl"))   # git-ignored
RISKY_GENUINE_KINDS = (7, 8, 9, 10)          # new_individual, new_landlord, medical, gray_invest

INTERRUPT = 2        # tier 2+ (pause, trusted check, hold) counts as an interruption / a warning
HOLD = 4


# ---------------------------------------------------------------------------------------------
# The frozen baseline: whatever outputs/ holds (model, lines, policy version)
# ---------------------------------------------------------------------------------------------
def load_baseline(cache: str = DEFAULT_CACHE) -> dict:
    """World, features and the frozen model in outputs/, scored and run through the same policy version as
    outputs/metrics.json on the test days. Asserts that it reproduces the committed headline numbers before
    anything else uses it."""
    world, f, denom = pickle.load(open(cache, "rb"))
    bundle = load_bundle(ROOT / "outputs" / "model_bundle.pkl")
    rules = RULES[json.load(open(ROOT / "outputs" / "metrics.json")).get("policy", "v1")]   # no field: Round 1
    S = score(bundle["models"], f)
    w = E.windows(f)
    ft, St = f[w["test"]], S[w["test"]]
    pol = E.run_policy(ft, St, bundle["thresholds"], rules)
    n_all = E.n_customer_payments(denom, VALID_END)
    head, jt = E.summarise(world, ft, pol, n_all, denom["n_users"])
    ref = json.load(open(ROOT / "outputs" / "metrics.json"))["headline"]
    assert head["genuine_t2plus"] == ref["genuine_t2plus"], "baseline drifted: rebuild the cache"
    assert abs(head["victims_warned"] - ref["victims_warned"]) < 1e-6, "baseline drifted: rebuild the cache"
    return dict(world=world, f=f, denom=denom, bundle=bundle, S=S, ft=ft, St=St, pol=pol, head=head, jt=jt,
                n_all=n_all, n_users=denom["n_users"], thr=bundle["thresholds"], rules=rules, cache=str(cache))


def system(base: dict | None = None) -> str:
    """The system under test as generated text names it, read from outputs/ so the report never goes stale."""
    if base is not None:
        model, policy = base["bundle"]["versions"]["model"], base["rules"].name
    else:
        model = load_bundle(ROOT / "outputs" / "model_bundle.pkl")["versions"]["model"]
        policy = json.load(open(ROOT / "outputs" / "metrics.json")).get("policy", "v1")
    return f"model {model}, policy {policy}"


class DropColumns:
    """A fitted model that only ever sees `keep` of the columns it is given. Lets a model trained without some
    features (model v2: no caller identity) plug into models.score(), which passes all 39 columns."""

    def __init__(self, est, keep: list[str]):
        self.est, self.keep = est, list(keep)

    def predict_proba(self, X):
        return self.est.predict_proba(X[[c for c in self.keep if c in X.columns]])

    @property
    def n_iter_(self):
        return self.est.n_iter_

    @property
    def classes_(self):
        return self.est.classes_


_BASE: dict = {}


def get_baseline(cache: str | None = None) -> dict:
    """load_baseline() once per process. run_test_kit.py and the Kit tab share one copy."""
    cache = str(cache or DEFAULT_CACHE)
    if cache not in _BASE:
        if not Path(cache).exists():
            sys.exit(f"no cache at {cache}. Build it first:\n  python scripts/run_all.py --quick --out /tmp/thehro_quick "
                     f"--cache {cache}")
        _BASE[cache] = load_baseline(cache)
    return _BASE[cache]


# ---------------------------------------------------------------------------------------------
# Confidence intervals
# ---------------------------------------------------------------------------------------------
def wilson(k: int, n: int) -> tuple[float, float]:
    """95% interval for a share k/n (victims warned, kit samples right)."""
    if n == 0:
        return float("nan"), float("nan")
    z = norm.ppf(0.975)
    p = k / n
    den = 1 + z**2 / n
    mid = (p + z**2 / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return float(mid - half), float(mid + half)


def poisson(k: int) -> tuple[float, float]:
    """Exact 95% interval for a count k (false pauses, holds). Divide by payments and multiply by 10,000."""
    return float(0.0 if k == 0 else chi2.ppf(0.025, 2 * k) / 2), float(chi2.ppf(0.975, 2 * k + 2) / 2)


def share(k: int, n: int) -> dict:
    """A share with its Wilson interval and sample size."""
    lo, hi = wilson(int(k), int(n))
    return {"k": int(k), "n": int(n), "value": (k / n) if n else float("nan"), "lo": lo, "hi": hi}


def per10k(k: int, n: int) -> dict:
    """A rare count per 10,000 payments with its exact Poisson interval."""
    lo, hi = poisson(int(k))
    s = 1e4 / n if n else float("nan")
    return {"k": int(k), "n": int(n), "value": k * s, "lo": lo * s, "hi": hi * s}


def fmt_share(d: dict, digits: int = 1) -> str:
    return f"{d['value']:.{digits}%} ({d['lo']:.{digits}%}–{d['hi']:.{digits}%}), {d['k']:,}/{d['n']:,}"


def fmt_rate(d: dict, digits: int = 2) -> str:
    return f"{d['value']:.{digits}f} per 10,000 ({d['lo']:.{digits}f}–{d['hi']:.{digits}f}; {d['k']:,} of {d['n']:,})"


def headline_rates(head: dict, jt: pd.DataFrame, ft: pd.DataFrame, tiers: np.ndarray, n_all: int) -> dict:
    """The headline measures of one policy run, each with its interval and sample size."""
    scam = ft.is_scam.values == 1
    return {
        "victims_warned": share(int(jt.warned.sum()), len(jt)),
        "victims_warned_first_payment": share(int(jt.warned_first_payment.sum()), len(jt)),
        "scam_payments_paused": share(int(((tiers >= INTERRUPT) & scam).sum()), int(scam.sum())),
        "false_pauses_per_10k": per10k(head["genuine_t2plus"], n_all),
        "genuine_trusted_checks_per_10k": per10k(head["genuine_tier3"], n_all),
        "genuine_holds_per_10k": per10k(head["genuine_t4plus"], n_all),
        "money_protected_share_modelled": float(head["money_protected_share"]),
    }


def group_rates(world, ft: pd.DataFrame, tiers: np.ndarray, jt: pd.DataFrame, denom: dict) -> dict:
    """Per group (age, language, region, consent): false pauses per 10k with exact Poisson intervals (counted
    here, against all customer payments of that group on test days) and victims warned with Wilson intervals.
    Age, language and region are never model inputs; they are used only for this check."""
    users = world.users
    cust = denom["customer_payments_by_user_day"]
    cust = cust[cust.day >= VALID_END]
    gen = (tiers >= INTERRUPT) & (ft.is_scam.values == 0)
    out = {}
    for col in ["age_band", "lang", "region", "consent"]:
        labels = E.GROUP_LABELS[col]
        n_pay = pd.Series(users[col].values[cust.payer.values]).value_counts()
        n_fp = pd.Series(users[col].values[ft.payer.values[gen]]).value_counts()
        vic = pd.Series(users[col].values[jt.victim.values])
        overall = gen.sum() / len(cust)
        rows = {}
        for g in sorted(n_pay.index, key=lambda v: (str(type(v)), v)):
            k, n = int(n_fp.get(g, 0)), int(n_pay[g])
            m = (vic == g).values
            fp = per10k(k, n)
            rows[labels.get(g, g)] = {"false_pauses": fp, "ratio_to_overall": (k / n) / overall if overall else float("nan"),
                                      "victims_warned": share(int(jt.warned.values[m].sum()), int(m.sum()))}
        out[col] = rows
    return out


# ---------------------------------------------------------------------------------------------
# What counts as right
# ---------------------------------------------------------------------------------------------
def expected_for(is_scam: bool) -> str:
    return "interrupt" if is_scam else "no_interrupt"


def is_right(expected: str, tier: int) -> bool:
    """interrupt: tier 2+. no_interrupt: tier 0-1. never_hold: anything below tier 4."""
    if expected == "interrupt":
        return tier >= INTERRUPT
    if expected == "no_interrupt":
        return tier < INTERRUPT
    if expected == "never_hold":
        return tier < HOLD
    raise ValueError(f"unknown expected outcome {expected!r}")


# ---------------------------------------------------------------------------------------------
# Spot-checks: fresh labelled samples from the test days, through the live decision service
# ---------------------------------------------------------------------------------------------
def _payment(r: pd.Series, ref: str) -> dict:
    return {"payment_id": f"P{int(r['pid'])}", "customer_ref": ref, "payee_ref": f"A{int(r['payee'])}",
            "amount": float(r["amount"]), "channel": CHANNELS[int(r["channel"])], "t_min": float(r["t"])}


def _features(r: pd.Series) -> dict:
    return {c: (None if pd.isna(r[c]) else float(r[c])) for c in ALL_FEATURES}


def _replay(svc: DecisionService, ft: pd.DataFrame, rows, ref: str) -> dict:
    """Decide rows (index labels of ft, in time order) for one customer, pressing Continue after every
    tier 2+ decision, as the evaluation assumes. Returns {index label: decision}."""
    out = {}
    for i in rows:
        r = ft.loc[i]
        d = svc.decide(_payment(r, ref), _features(r), bool(r["consent"]))
        if d["tier"] >= INTERRUPT:
            svc.outcome(d["evidence_seq"], "continue", float(r["t"]))
        out[i] = d
    return out


def _history(ft: pd.DataFrame, pol: pd.DataFrame, payer: int, before_t: float, before_label) -> list:
    """The customer's earlier test-day payments that can change memory, in time order.

    Only rows whose memory-free tier (base_tier) is 1 or more are replayed: a base tier 0 payment always
    ends at tier 0 and never changes memory (policy.apply_memory touches only tiers 1+ and records only
    tiers 2+), so skipping it gives exactly the same state, just faster.
    """
    m = (ft.payer.values == payer) & (pol.base_tier.values >= 1)
    t = ft.t.values
    lab = ft.index.values
    m &= (t < before_t) | ((t == before_t) & (lab < before_label))
    idx = ft.index[m]
    return list(ft.loc[idx].sort_values("t", kind="mergesort").index)


def _kind_name(k: int) -> str:
    return KIND_NAME.get(int(k), str(k))


def spot_check(seed: int, n_scam: int = 25, n_risky: int = 25, n_normal: int = 10, journeys: int | None = None,
               base: dict | None = None) -> dict:
    """Random labelled samples from the test days (days 42-59, never used for training or for setting lines),
    each run through the live DecisionService after replaying that customer's earlier test-day payments.

    journeys=None: payment-level draws (n_scam scam payments, n_risky risky-looking genuine, n_normal ordinary
    genuine). journeys=N: N scam journeys starting on test days; a journey is right if any of its payments
    was warned (tier 2+).
    """
    base = base or get_baseline()
    ft, pol, jt, bundle = base["ft"], base["pol"], base["jt"], base["bundle"]
    rng = np.random.default_rng(seed)
    svc = DecisionService(bundle, rules=base["rules"])
    samples = []

    if journeys:
        pick = rng.choice(len(jt), size=min(journeys, len(jt)), replace=False)
        for n, j in enumerate(pick, start=1):
            jr = jt.iloc[int(j)]
            jid, payer = int(jr.journey), int(jr.victim)
            steps = ft[(ft.journey.values == jid) & (ft.is_scam.values == 1)].sort_values("t", kind="mergesort")
            last_t, last_lab = float(steps.t.iloc[-1]), steps.index[-1]
            hist = _history(ft, pol, payer, last_t, last_lab)
            rows = sorted(set(hist) | set(steps.index), key=lambda i: (ft.at[i, "t"], i))
            dec = _replay(svc, ft, rows, f"K2-{n}:C{payer}")
            tiers = [dec[i]["tier"] for i in steps.index]
            max_tier = int(max(tiers))
            ev_tiers = pol.loc[steps.index, "tier"].astype(int).tolist()
            warned = max_tier >= INTERRUPT
            first_warn = next((k for k, t_ in enumerate(tiers) if t_ >= INTERRUPT), None)
            top = next((dec[i]["reasons"][0]["en"] for i in steps.index if dec[i]["reasons"]), "")
            samples.append({
                "sample": n, "label": "scam journey", "scam_type": str(jr.workflow), "journey": jid,
                "amount_text": inr(float(jr.total)), "n_payments": int(len(steps)), "expected": "interrupt",
                "got": {"tier": max_tier, "tier_name": TIER_NAMES[max_tier]}, "right": bool(warned),
                "payment_tiers": tiers, "first_warned_payment": None if first_warn is None else first_warn + 1,
                "evaluation_tiers": ev_tiers, "evaluation_warned": bool(jr.warned),
                "live_equals_evaluation": tiers == ev_tiers, "top_reason": top,
            })
    else:
        scam = ft.is_scam.values == 1
        risky = ~scam & np.isin(ft.kind.values, RISKY_GENUINE_KINDS)
        normal = ~scam & ~risky
        # risky-looking genuine payments are split evenly across their four kinds (new individual, new landlord,
        # medical, informal investment); a plain random draw would be almost all "new individual"
        per_kind = np.full(len(RISKY_GENUINE_KINDS), n_risky // len(RISKY_GENUINE_KINDS))
        per_kind[: n_risky % len(RISKY_GENUINE_KINDS)] += 1
        pools = [("scam", scam, n_scam)]
        pools += [("risky-looking genuine", risky & (ft.kind.values == kd), int(k))
                  for kd, k in zip(RISKY_GENUINE_KINDS, per_kind)]
        pools += [("ordinary genuine", normal, n_normal)]
        draws = []
        for label, mask, k in pools:
            pool = ft.index[mask]
            got = rng.choice(len(pool), size=min(k, len(pool)), replace=False)
            draws += [(label, pool[int(g)]) for g in sorted(got)]
        for n, (label, i) in enumerate(draws, start=1):
            r = ft.loc[i]
            payer, ref = int(r["payer"]), f"K2-{n}:C{int(r['payer'])}"
            _replay(svc, ft, _history(ft, pol, payer, float(r["t"]), i), ref)
            d = svc.decide(_payment(r, ref), _features(r), bool(r["consent"]))
            is_scam = label == "scam"
            exp = expected_for(is_scam)
            ev = int(pol.at[i, "tier"])
            samples.append({
                "sample": n, "label": "scam" if is_scam else "genuine", "group": label,
                "scam_type": WORKFLOWS[int(r["workflow"])] if is_scam else None,
                "kind": "scam" if is_scam else _kind_name(r["kind"]),
                "pid": int(r["pid"]), "amount_text": inr(float(r["amount"])),
                "expected": exp, "got": {"tier": int(d["tier"]), "tier_name": d["tier_name"]},
                "right": is_right(exp, int(d["tier"])),
                "evaluation_tier": ev, "live_equals_evaluation": int(d["tier"]) == ev,
                "consent": bool(r["consent"]), "memory_note": d["memory_note"],
                "reasons": [x["code"] for x in d["reasons"]],
                "top_reason": d["reasons"][0]["en"] if d["reasons"] else "",
            })

    is_s = np.array([s["label"] != "genuine" for s in samples])
    right = np.array([s["right"] for s in samples])
    agree = int(sum(s["live_equals_evaluation"] for s in samples))
    summary = {
        "n": len(samples), "right": int(right.sum()),
        "scam_right": int(right[is_s].sum()), "genuine_right": int(right[~is_s].sum()),
        "n_scam": int(is_s.sum()), "n_genuine": int((~is_s).sum()),
        "right_share": share(right.sum(), len(samples)),
        "scam_right_share": share(right[is_s].sum(), is_s.sum()),
        "genuine_right_share": share(right[~is_s].sum(), (~is_s).sum()),
        "live_equals_evaluation": agree,
    }
    note = ("Journey level: a victim counts as right if any of their payments was warned (tier 2+). "
            "Over all 720 test-day victims this is 94.3%." if journeys else
            "Payment level: about 86% of all scam payments are paused, because a scam's first step is often "
            "small and harmless-looking. At the victim level (--journeys) 94.3% are warned, so a missed "
            "scam payment here does not mean the victim was missed.")
    return {"seed": int(seed), "level": "journey" if journeys else "payment",
            "data": "synthetic test days 42-59 (Thehro simulator, seed 7); never used for training or setting lines",
            "model": bundle["versions"], "samples": samples, "summary": summary, "note": note}


# ---------------------------------------------------------------------------------------------
# Output helpers: JSON, markdown tables, run log, report sections
# ---------------------------------------------------------------------------------------------
def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, pd.DataFrame):
        return {str(i): jsonable(r) for i, r in x.to_dict(orient="index").items()}
    if isinstance(x, pd.Series):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(f"{float(x):.6g}")
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return [jsonable(v) for v in x.tolist()]
    return x


def save_json(obj, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(jsonable(obj), open(path, "w"), indent=1, ensure_ascii=False)
    return path


def md_table(headers: list[str], rows: list[list]) -> str:
    """A plain markdown table (no extra libraries)."""
    def cell(v):
        return str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(cell(h) for h in headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return (sha + ("-dirty" if dirty else "")) if sha else "no-git"
    except Exception:  # noqa: BLE001
        return "no-git"


def frozen_list(folder: Path) -> tuple[dict, str | None]:
    """The case folder's FROZEN.sha256: {file name: sha256} and the time it was frozen (from its header)."""
    path = Path(folder) / "FROZEN.sha256"
    if not path.exists():
        return {}, None
    sums, when = {}, None
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            if when is None and " frozen " in line:
                when = line.split(" frozen ", 1)[1].split(",", 1)[0].strip()
        elif line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.strip().lstrip("*")] = digest
    return sums, when


def not_frozen(files) -> list[str]:
    """Hand-written case files missing from their folder's FROZEN.sha256, or changed since it was written. Nothing
    may run on those (scripts/k1_run.py and the bank server's /kit/run both check): the expected outcomes are fixed
    before any decision runs, and never edited after."""
    files = [Path(f) for f in files]
    if not files:
        return []
    sums, _ = frozen_list(files[0].parent)
    return [f.name for f in files if sums.get(f.name) != hashlib.sha256(f.read_bytes()).hexdigest()]


def portable(arg: str) -> str:
    """An absolute path inside the repository, written relative to thehro-complete-code/thehro (where the scripts
    run), so runs.csv commands work on any machine."""
    try:
        p = Path(arg)
        if p.is_absolute() and p.resolve().is_relative_to(REPO):
            return os.path.relpath(p.resolve(), ROOT)
    except (OSError, ValueError):
        pass
    return arg


def cmd(script_file: str, argv=None) -> str:
    """The command that reproduces this run, e.g. 'python scripts/attacks.py --seed 7'."""
    args = list(argv if argv is not None else sys.argv[1:])
    return " ".join(["python", f"scripts/{Path(script_file).name}"] + [portable(x) for x in args])


def new_run_id(name: str) -> str:
    return f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{name}"


def log_run(results: Path, run_id: str, seed, files, command: str | None = None, note: str = "") -> None:
    """Append one row to runs.csv: run ID, time, git commit, command, seed, files written, and a note (e.g. why
    a run was superseded; rows are never deleted)."""
    results.mkdir(parents=True, exist_ok=True)
    path = results / "runs.csv"
    new = not path.exists()
    rel = []
    for f in files:
        f = Path(f)
        try:
            rel.append(str(f.resolve().relative_to(REPO)))
        except ValueError:
            rel.append(str(f))
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["run_id", "time", "git_commit", "command", "seed", "files", "note"])
        w.writerow([run_id, dt.datetime.now().isoformat(timespec="seconds"), git_commit(),
                    command or " ".join(["python"] + sys.argv), "" if seed is None else seed, ";".join(rel), note])


# Order of sections in report.md. Each script writes its own section into results/sections/<name>.md.
SECTIONS = [
    ("summary", "Summary"),
    ("progress", "Round 1 → now"),
    ("fresh", "Fresh world: every system on a world no choice ever saw"),
    ("spot_checks", "Spot-checks"),
    ("k1", "Hand-written cases (K1)"),
    ("error_rates", "Error rates and confusion matrices"),
    ("unseen", "Data the model never trained on"),
    ("failure_cases", "Named failure cases"),
    ("false_positives", "When a genuine customer is flagged by mistake"),
    ("attacks", "Attacks on the held-out weeks"),
    ("policy", "Policy versions and the cancel-and-retry attack"),
    ("switchoff", "Signals switched off in turn"),
    ("fairness", "Fairness re-check"),
    ("caller_id", "Caller identity: what it would add"),
    ("design", "Design choices: what was tried"),
    ("paysim", "Public benchmark: PaySim"),
    ("latency", "Latency and failure paths"),
    ("completeness", "Completeness of tests"),
    ("limits", "Limits"),
]


def write_section(results: Path, name: str, md: str) -> Path:
    path = results / "sections" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md.strip() + "\n")
    build_report(results)
    return path


LIMITS = """\
- **All Thehro numbers are synthetic.** They come from our own simulator (seed 7), which is cleaner than reality.
  Only a shadow-mode pilot on real payments can confirm them.
- **Scams are 158–315× more common in the synthetic world than in reality,** so there are enough to measure.
  Precision at India's real fraud rate is therefore *estimated*, not measured.
- **Money protected is *modelled*:** it depends on assumed stop rates (`config.STOP_PROB`) that a pilot must measure.
- **PaySim is account takeover, not coached scams.** It tests the method (point-in-time features, gradient-boosted
  trees, budgeted alert lines), not the scam signals: it has no calls, no coaching and no cross-bank mule networks.
- **Attacks change the scammer's behaviour, not the genuine customers'.** A real adversary would also adapt over
  time; the attacks are one round, with the model frozen.
"""


def build_report(results: Path) -> Path:
    """Assemble test_kit/report.md from the section files. Missing sections say 'not run yet'."""
    report = results.parent / "report.md"
    parts = ["# Thehro test kit: report",
             "",
             "Generated by `python scripts/run_test_kit.py`; each section is rewritten by the script named in it.",
             "Every number is labelled **measured** (on the synthetic test days, days 42–59), **estimated** "
             "(converted to India's real fraud rate) or **modelled** (depends on assumed stop rates), and says "
             "which data it comes from (Thehro synthetic or PaySim). Intervals are 95%: Wilson for shares, exact "
             "Poisson for rare counts. Run IDs are in `results/runs.csv`.",
             ""]
    for name, title in SECTIONS:
        p = results / "sections" / f"{name}.md"
        parts.append(f"## {title}")
        parts.append("")
        if name == "limits":
            parts.append(LIMITS)
        elif p.exists():
            parts.append(p.read_text().strip())
        else:
            parts.append("_Not run yet._")
        parts.append("")
    report.write_text("\n".join(parts))
    return report


def results_dir(arg: str | None) -> Path:
    p = Path(arg) if arg else RESULTS
    p.mkdir(parents=True, exist_ok=True)
    return p
