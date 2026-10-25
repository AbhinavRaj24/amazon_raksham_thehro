"""Attacks on the held-out weeks, done on the events (the "harder world" of slide 10).

Each attack edits the scammers' behaviour in the test days (days 42-59) of the simulated world, rebuilds the
features with the same code the model was trained with (features.build_features), and scores the result with
the FROZEN model, alert lines and policy in outputs/. Genuine customers are never edited, and nothing is retrained.

  chat_only     coaching by chat: no call, no screen share or remote app during scam payments
  split         every scam payment of Rs 10,000+ split into parts of at most Rs 9,900, minutes apart
  aged_mules    the mule accounts look old and recently active, and forward less, more slowly
  clean_mules   a fresh, clean-looking mule for every payment (the existing feature-level edit, evaluate.perturb)
  all           all of the above together
  rebuild       no edits at all: proves the rebuild path reproduces the baseline exactly

Every random choice uses np.random.default_rng([seed, attack number]), so "all" makes exactly the same choices
as the single attacks, and the seed goes into runs.csv.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from . import evaluate as E
from .config import MIN_PER_DAY, VALID_END
from .features import build_features
from .models import score
from .simulate import CH_UPI_P2P, KIND

TEST_START = VALID_END * MIN_PER_DAY
SPLIT_LINE = 10_000          # payments at or above this are split
SPLIT_MAX = 9_900            # no part is larger than this
MULE_CATEGORIES = ("rented", "dormant", "fresh")

ATTACKS = {
    "rebuild": "No edits (rebuild check)",
    "chat_only": "Chat-only coaching (no calls, no screen share)",
    "split": "Split payments just under Rs 10,000",
    "aged_mules": "Aged mules (old, recently active, forward 50-70% after 6-48 h)",
    "clean_mules": "Fresh, clean mule for every payment (feature-level)",
    "all": "All of the above at once",
}
EVENT_EDITS = {"chat_only": 1, "split": 2, "aged_mules": 3}    # attack number for the random generator


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([int(seed), EVENT_EDITS[name]])


def _scam_test(p: pd.DataFrame) -> np.ndarray:
    return (p.kind.values == KIND["scam"]) & (p.t.values >= TEST_START)


# ---------------------------------------------------------------------------------------------
# Event edits. Each takes and returns (payments, accounts, info); inputs are never modified in place.
# ---------------------------------------------------------------------------------------------
def chat_only(p: pd.DataFrame, acc: pd.DataFrame, rng: np.random.Generator, days: int):
    p = p.copy()
    m = _scam_test(p)
    for c in ("on_call", "caller_unknown", "call_ott", "screen_share", "remote_app"):
        p.loc[m, c] = 0
    p.loc[m, "call_min"] = 0.0
    p.loc[m, "caller_sim"] = -1
    return p, acc, {"scam_payments_edited": int(m.sum())}


def _split_amount(a: float, rng: np.random.Generator) -> np.ndarray:
    """Split a into k = ceil(a / 9,900) whole-rupee parts that sum to a, none above 9,900, near-equal with
    a little jitter (a scammer dictating 'send 9,700, then 9,850...')."""
    k = int(np.ceil(a / SPLIT_MAX))
    whole = int(np.floor(a))
    w = 1.0 + rng.uniform(-0.02, 0.02, k)
    parts = np.minimum(np.floor(whole * w / w.sum()), SPLIT_MAX)
    deficit = whole - int(parts.sum())       # >= 0: flooring and capping only ever take money away
    room = SPLIT_MAX - parts                 # total room >= deficit, because k * 9,900 >= a
    order = np.argsort(-room, kind="stable")
    give = np.clip(deficit - (np.cumsum(room[order]) - room[order]), 0, room[order])
    parts[order] += give
    parts[int(np.argmax(SPLIT_MAX - parts))] += a - whole     # any paise go on the part with the most room
    return parts


def split(p: pd.DataFrame, acc: pd.DataFrame, rng: np.random.Generator, days: int):
    m = _scam_test(p) & (p.amount.values >= SPLIT_LINE)
    idx = np.where(m)[0]
    keep = p[~m]
    new_rows, n_parts, dropped, dropped_amt = [], [], 0, 0.0
    end = days * MIN_PER_DAY
    for i in idx:
        r = p.iloc[[i]]
        a = float(r.amount.iloc[0])
        parts = _split_amount(a, rng)
        gaps = np.concatenate([[0.0], rng.uniform(3, 10, len(parts) - 1)])
        t = float(r.t.iloc[0]) + np.cumsum(gaps)
        ok = t < end                         # payments after day 60 are never observed
        dropped += int((~ok).sum())
        dropped_amt += float(parts[~ok].sum())
        rr = r.loc[r.index.repeat(int(ok.sum()))].copy()
        rr["t"] = t[ok]
        rr["amount"] = parts[ok]
        rr["channel"] = np.int8(CH_UPI_P2P)  # small parts go by UPI to a person
        new_rows.append(rr)
        n_parts.append(int(ok.sum()))
    p2 = pd.concat([keep] + new_rows)
    all_parts = np.concatenate([x.amount.values for x in new_rows]) if new_rows else np.array([])
    info = {"payments_split": int(len(idx)), "parts_created": int(sum(n_parts)),
            "median_parts_per_payment": float(np.median(n_parts)) if n_parts else 0.0,
            "max_parts_per_payment": int(max(n_parts)) if n_parts else 0,
            "parts_9000_to_9900_share": float(((all_parts >= 9000) & (all_parts <= SPLIT_MAX)).mean())
            if len(all_parts) else float("nan"),
            "parts_dropped_after_day_60": dropped, "amount_dropped_after_day_60": dropped_amt}
    return p2, acc, info


def aged_mules(p: pd.DataFrame, acc: pd.DataFrame, rng: np.random.Generator, days: int):
    p = p.copy()
    acc = acc.copy()
    targets = np.unique(p.payee.values[_scam_test(p)])
    targets = targets[np.isin(acc.mule_category.values[targets], MULE_CATEGORIES)]
    acc.loc[targets, "open_day"] = -rng.uniform(365, 1800, len(targets))
    acc.loc[targets, "last_active_before"] = rng.uniform(0, 5, len(targets))
    fw = (p.kind.values == KIND["mule_forward"]) & (p.t.values >= TEST_START) & np.isin(p.payer.values, targets)
    n = int(fw.sum())
    p.loc[fw, "t"] = p.t.values[fw] + rng.uniform(6, 48, n) * 60
    p.loc[fw, "amount"] = np.round(p.amount.values[fw] * rng.uniform(0.5, 0.7, n))
    late = fw & (p.t.values >= days * MIN_PER_DAY)
    p = p[~late]
    return p, acc, {"mule_accounts_aged": int(len(targets)), "forwards_delayed_and_cut": n,
                    "forwards_pushed_past_day_60": int(late.sum())}


EDIT_FUNCS = {"chat_only": chat_only, "split": split, "aged_mules": aged_mules}
COMPONENTS = {"rebuild": [], "chat_only": ["chat_only"], "split": ["split"], "aged_mules": ["aged_mules"],
              "clean_mules": [], "all": ["chat_only", "split", "aged_mules"]}
FEATURE_LEVEL = {"clean_mules": "clean_mules", "all": "clean_mules"}


# ---------------------------------------------------------------------------------------------
# Running an attack
# ---------------------------------------------------------------------------------------------
_CACHE: dict = {}
_PINNED: list = []


def _thr_key(thr) -> tuple:
    return (thr.t1, thr.t2, thr.t4, getattr(thr, "tj", None), tuple(sorted(thr.family.items())))


def build_world(world, edits: list[str], seed: int):
    """The world with the given event edits applied (payments re-sorted by time, pid reset)."""
    p, acc, info = world.payments, world.accounts, {}
    for name in edits:
        p, acc, info[name] = EDIT_FUNCS[name](p, acc, _rng(seed, name), world.cfg.days)
    p = p.sort_values("t", kind="mergesort").reset_index(drop=True)
    p["pid"] = np.arange(len(p), dtype=np.int64)
    return dataclasses.replace(world, payments=p, accounts=acc), info


def run(base: dict, name: str, seed: int = 7, models=None, thr=None, feature_edit=None) -> dict:
    """Build, score and evaluate one attack. Cached per (name, seed, model) within a process.

    models / thr default to the frozen ones in outputs/. feature_edit(ft) -> ft is applied to the test-day
    features after the rebuild and after any feature-level attack (used by model v2 to blank columns).
    """
    models = models or base["bundle"]["models"]
    thr = thr or base["thr"]
    rules = base.get("rules")
    # the key must name everything the result depends on: the models (pinned below, so their id can't be reused),
    # the alert lines by value, and the policy version
    world = base["world"]
    key = (name, seed, id(world), id(models), _thr_key(thr), getattr(rules, "name", None), id(feature_edit))
    if key in _CACHE:
        return _CACHE[key]
    _PINNED.append((world, models, feature_edit))
    fkey = ("features", name, seed, id(world))          # a second world (the fresh-world test) gets its own rebuild
    if fkey not in _CACHE:
        if name == "clean_mules":            # feature-level only: no rebuild needed
            w2, info = world, {}
            ft2, d2 = base["ft"], base["denom"]
        else:
            w2, info = build_world(world, COMPONENTS[name], seed)
            f2, d2 = build_features(w2, verbose=False)
            ft2 = f2[E.windows(f2)["test"]]
            d2 = {k: v for k, v in d2.items() if k != "campaign_components"}
            del f2
        if name in FEATURE_LEVEL:
            ft2 = E.perturb(ft2, FEATURE_LEVEL[name])
            info["clean_mules"] = {"scam_payments_edited": int((ft2.is_scam.values == 1).sum()),
                                   "note": "feature-level: evaluate.perturb(ft, 'clean_mules')"}
        _CACHE[fkey] = (w2, ft2, d2, info)
    w2, ft2, d2, info = _CACHE[fkey]
    if feature_edit is not None:
        ft2 = feature_edit(ft2)
    S2 = score(models, ft2, recogniser=False)            # tiers never need the scam-type guess
    pol2 = E.run_policy(ft2, S2, thr, base["rules"])
    n_all = E.n_customer_payments(d2, VALID_END)
    head2, jt2 = E.summarise(w2, ft2, pol2, n_all, base["n_users"])
    res = dict(name=name, label=ATTACKS[name], seed=seed, world=w2, ft=ft2, St=S2, pol=pol2, head=head2, jt=jt2,
               denom=d2, n_all=n_all, info=info)
    _CACHE[key] = res
    return res
