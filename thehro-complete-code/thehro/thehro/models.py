"""Detection models.

* Three family models (journey, payee, context), each seeing only its own
  family's signals. They feed the policy's "two kinds of evidence must agree"
  gate, so no single family can interrupt a payment on its own.
* One fused model over all signals, which ranks risk.
* A workflow recogniser that names the likely scam type, so the warning can say
  something specific. Below a confidence floor it says "unfamiliar pattern" and
  the case goes to the analysts' emerging-pattern queue.

Every supervised model learns only from scams that victims actually reported,
and only from reports filed before the training cut-off, the way a bank learns
in practice. Scams that were never reported are (wrongly, but realistically)
treated as genuine during training.

Monotonic constraints mean the models can never lower risk because, say, the
payee is on the fraud registry or the screen is being shared.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from .config import LABEL_CUTOFF_TRAIN, MIN_PER_DAY, TRAIN_END, WARMUP_END
from .features import ALL_FEATURES, CATEGORICAL, FAMILIES
from .simulate import WORKFLOWS

MONOTONE = {
    "payee_registry": 1, "payee_fri_band": 1, "payee_in_campaign": 1,
    "caller_fri_band": 1, "screen_share": 1, "remote_app": 1,
    "fd_break_48h": 1, "stranger_credits_24h": 1, "is_new_payee": 1,
    "payee_is_valid": -1,
}
WORKFLOW_CONFIDENCE_FLOOR = 0.6   # below this the warning uses the generic message
# Caller identity: a bank app can't read the caller's number on Android (call-log access is restricted to the
# phone app), so models for policy v4 are trained without these two columns.
CALLER_FEATURES = ("caller_unknown", "caller_fri_band")


def _hgb(features, seed, multiclass=False):
    cats = [c for c in CATEGORICAL if c in features]
    mono = None if multiclass else ({k: v for k, v in MONOTONE.items() if k in features} or None)
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.08, max_leaf_nodes=31, min_samples_leaf=50,
        l2_regularization=1.0, categorical_features=cats or None, monotonic_cst=mono,
        early_stopping=True, validation_fraction=0.15, n_iter_no_change=25, random_state=seed)


def training_labels(f: pd.DataFrame, start_day=WARMUP_END, end_day=TRAIN_END,
                    label_cutoff_day=LABEL_CUTOFF_TRAIN):
    """Training window mask and the labels a bank would actually have."""
    t = f.t.values
    window = (t >= start_day * MIN_PER_DAY) & (t < end_day * MIN_PER_DAY)
    y = ((f.is_scam.values == 1) & (f.report_t.values < label_cutoff_day * MIN_PER_DAY)).astype(np.int8)
    return window, y


@dataclass
class Models:
    fused: HistGradientBoostingClassifier
    family: dict
    workflow: HistGradientBoostingClassifier | None
    workflow_classes: np.ndarray | None
    meta: dict = field(default_factory=dict)
    features: list | None = None          # columns the fused model and recogniser use (None: all, as in Round 1)
    family_features: dict | None = None   # columns each family model uses (None: FAMILIES)


def model_features(models: Models) -> tuple[list, dict]:
    """The columns each model was trained on. Bundles saved before this field existed used all of them."""
    return (getattr(models, "features", None) or ALL_FEATURES,
            getattr(models, "family_features", None) or FAMILIES)


def fit(f: pd.DataFrame, window: np.ndarray, y: np.ndarray, exclude_workflows=(), seed: int = 0,
        with_recogniser: bool = True, verbose: bool = True, drop=()) -> Models:
    """Fit all models on payments in `window`.

    exclude_workflows: scam types to hide completely from training (leave-one-
    workflow-out tests): the models never see a single example of them.
    drop: feature columns no model may use (CALLER_FEATURES for policy v4).
    """
    t0 = time.time()
    keep = window.copy()
    if exclude_workflows:
        keep &= ~np.isin(f.workflow.values, [WORKFLOWS.index(w) for w in exclude_workflows])
    feats = [c for c in ALL_FEATURES if c not in drop]
    fam_feats = {name: [c for c in cols if c not in drop] for name, cols in FAMILIES.items()}
    X = f.loc[keep, feats]
    yy = y[keep]
    fused = _hgb(feats, seed).fit(X, yy)
    family = {name: _hgb(cols, seed).fit(X[cols], yy) for name, cols in fam_feats.items()}
    wf_model, wf_classes = None, None
    if with_recogniser:
        pos = yy == 1
        wf = f.workflow.values[keep][pos]
        wf_model = _hgb(feats, seed, multiclass=True).fit(X[pos], wf)
        wf_classes = wf_model.classes_
    meta = dict(n_train=int(keep.sum()), n_pos=int(yy.sum()), excluded=list(exclude_workflows),
                dropped=list(drop), seconds=round(time.time() - t0, 1), n_iter_fused=int(fused.n_iter_))
    if verbose:
        print(f"fit: {meta['n_train']:,} payments, {meta['n_pos']:,} reported-scam labels, "
              f"{meta['seconds']}s" + (f", hidden: {list(exclude_workflows)}" if exclude_workflows else ""))
    return Models(fused, family, wf_model, wf_classes, meta, features=None if not drop else feats,
                  family_features=None if not drop else fam_feats)


def score(models: Models, f: pd.DataFrame, recogniser: bool = True) -> pd.DataFrame:
    """Scores for every row of `f`: fused risk, one score per family, likely workflow. recogniser=False skips the
    scam-type guess (the slowest model): the live service asks for it only when a payment reaches tier 2."""
    feats, fam_feats = model_features(models)
    X = f[feats]
    out = pd.DataFrame(index=f.index)
    out["s_fused"] = models.fused.predict_proba(X)[:, 1]
    for name, cols in fam_feats.items():
        out[f"s_{name}"] = models.family[name].predict_proba(X[cols])[:, 1]
    if recogniser:
        out = out.join(workflow_scores(models, f))
    return out


def workflow_scores(models: Models, f: pd.DataFrame) -> pd.DataFrame:
    """The scam-type recogniser's guess and confidence for every row of `f` (empty if there is no recogniser)."""
    out = pd.DataFrame(index=f.index)
    if models.workflow is not None:
        P = models.workflow.predict_proba(f[model_features(models)[0]])
        out["wf_pred"] = models.workflow_classes[P.argmax(1)].astype(np.int16)
        out["wf_conf"] = P.max(1)
    return out
