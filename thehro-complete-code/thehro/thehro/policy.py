"""Alert policy: five tiers, a fixed interruption budget, and friction that grows with evidence.

Tier 0  Allow             nothing shown.
Tier 1  Note              one line under the Pay button, no delay.
Tier 2  Pause and explain full-screen warning with the reasons and three buttons:
                          Cancel / Call my bank (saved number) / Continue (enabled after 30 s).
Tier 3  Trusted check     as tier 2, then the payment waits for the customer's trusted person
                          to approve it, or for one hour; the bank offers a call-back.
Tier 4  Hold for review   this payment only is held; a bank fraud analyst calls the customer on
                          the registered number within 30 minutes. Nothing else is frozen.

Rules
* Tier 1: fused risk above t1 (genuine payments reach it at most 1 in 1,000).
* Tier 2: fused risk above t2 (genuine payments reach it at most 1 in 10,000).
* Tier 3: tier 2, AND two independent signal families agree (the "gate"), AND either the
  amount is Rs 50,000+ or the customer continued past a warning in the last 7 days.
* Tier 4: the gate, AND the payee is flagged by an outside source (fraud registry, DoT FRI
  high / very high, or a live mule network), AND fused risk above t4 (genuine payments at
  most 1 in 100,000), AND the amount is Rs 10,000+.
* Memory: after the customer continues past a tier 2+ warning, for 7 days the amount lines
  are waived and any tier 1 payment counts as tier 2: it becomes tier 3 if the gate passes,
  tier 4 if the tier 4 evidence is there too. A payee they chose to pay after a warning,
  still unflagged 7 days later, counts as confirmed: payments to it stay at tier 1 or below
  unless an outside flag appears.

So a warning can come from risk alone, but anything that delays money needs two kinds of
evidence, and a hold needs an outside flag on the payee plus a human decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import (ALERT_BUDGET_T2, FAMILY_ELEVATED_PCTL, HOLD_MIN_AMOUNT, MIN_PER_DAY, NUDGE_BUDGET_T1,
                     OVERRIDE_MEMORY_DAYS, REVIEW_BUDGET_T4, TRUSTED_CHECK_MIN_AMOUNT)

FAMILY_NAMES = ("journey", "payee", "context")


def _col(F, name):
    return np.nan_to_num(np.asarray(F[name], dtype=float), nan=0.0)


def payee_flag(F) -> np.ndarray:
    """Outside evidence about the receiving account."""
    return (_col(F, "payee_registry") >= 1) | (_col(F, "payee_fri_band") >= 2) | (_col(F, "payee_in_campaign") >= 1)


def context_flag(F) -> np.ndarray:
    """Strong live signals: a flagged caller, screen sharing or a remote-access app."""
    return (_col(F, "caller_fri_band") >= 2) | (_col(F, "screen_share") >= 1) | (_col(F, "remote_app") >= 1)


@dataclass
class Thresholds:
    t1: float
    t2: float
    t4: float
    family: dict
    info: dict = field(default_factory=dict)


def evidence(S: pd.DataFrame, F: pd.DataFrame, thr: Thresholds) -> dict:
    """Which signal families are elevated, and whether two of them agree."""
    consent = np.asarray(F["consent"], dtype=bool)
    ev = {
        "journey": S["s_journey"].values > thr.family["journey"],
        "payee": (S["s_payee"].values > thr.family["payee"]) | payee_flag(F),
        # no consent, no context evidence: the family simply does not vote
        "context": ((S["s_context"].values > thr.family["context"]) | context_flag(F)) & consent,
    }
    ev["n_families"] = ev["journey"].astype(np.int8) + ev["payee"] + ev["context"]
    ev["gate"] = ev["n_families"] >= 2
    ev["payee_flag"] = payee_flag(F)
    ev["hold_ok"] = ev["gate"] & ev["payee_flag"] & (S["s_fused"].values >= thr.t4)
    return ev


def base_tiers(S: pd.DataFrame, F: pd.DataFrame, thr: Thresholds, ev: dict | None = None) -> np.ndarray:
    """Tier for each payment before customer memory is applied."""
    ev = ev if ev is not None else evidence(S, F, thr)
    s = S["s_fused"].values
    amount = np.asarray(F["amount"], dtype=float)
    tier = np.zeros(len(s), dtype=np.int8)
    tier[s >= thr.t1] = 1
    tier[s >= thr.t2] = 2
    tier[(s >= thr.t2) & ev["gate"] & (amount >= TRUSTED_CHECK_MIN_AMOUNT)] = 3
    tier[ev["hold_ok"] & (amount >= HOLD_MIN_AMOUNT)] = 4
    return tier


def calibrate(S: pd.DataFrame, F: pd.DataFrame, genuine: np.ndarray, n_all: int,
              budgets: dict | None = None) -> Thresholds:
    """Choose thresholds on a validation week so genuine payments stay inside each budget.

    genuine: mask of genuine scored payments in the validation week.
    n_all:   ALL customer payments that week, shop payments included (the budget denominator).
    """
    b = {1: NUDGE_BUDGET_T1, 2: ALERT_BUDGET_T2, 4: REVIEW_BUDGET_T4}
    b.update(budgets or {})
    s = S["s_fused"].values
    consent = np.asarray(F["consent"], dtype=bool)

    def top(score, mask, budget):
        allowed = int(np.floor(budget * n_all))
        v = np.sort(score[mask])[::-1]
        # the threshold sits just above the (allowed+1)-th highest genuine score
        return float(np.nextafter(v[allowed], np.inf)) if allowed < len(v) else 0.0

    fam = {}
    for name in FAMILY_NAMES:
        m = genuine & consent if name == "context" else genuine
        fam[name] = float(np.percentile(S[f"s_{name}"].values[m], FAMILY_ELEVATED_PCTL))
    t1 = top(s, genuine, b[1])
    t2 = max(top(s, genuine, b[2]), t1)
    thr = Thresholds(t1=t1, t2=t2, t4=1.0, family=fam)
    ev = evidence(S, F, thr)
    t4 = max(top(s, genuine & ev["gate"] & ev["payee_flag"], b[4]), t2)
    thr.t4 = t4
    thr.info = {"budgets": b, "n_all": int(n_all), "n_genuine_scored": int(genuine.sum())}
    return thr


def escalated_tier(gate: bool, hold_ok: bool) -> int:
    """Tier for a risky payment soon after the customer continued past a warning."""
    return 4 if hold_ok else (3 if gate else 2)


def apply_memory(F: pd.DataFrame, tier: np.ndarray, ev: dict, assume_continue: bool = True):
    """Per-customer memory, applied in time order.

    assume_continue=True evaluates the path on which the customer pressed Continue on every
    warning (the only path on which their later payments exist). Returns the final tiers
    and flags marking escalated and suppressed payments.
    """
    tier = tier.copy()
    t = np.asarray(F["t"], dtype=float)
    payer = np.asarray(F["payer"])
    payee = np.asarray(F["payee"])
    gate, pflag, hold_ok = ev["gate"], ev["payee_flag"], ev["hold_ok"]
    escalated = np.zeros(len(tier), dtype=bool)
    suppressed = np.zeros(len(tier), dtype=bool)
    users = np.unique(payer[tier >= 2])
    rows = np.where(np.isin(payer, users))[0]
    rows = rows[np.lexsort((t[rows], payer[rows]))]
    window = OVERRIDE_MEMORY_DAYS * MIN_PER_DAY
    state: dict = {}
    for i in rows.tolist():
        st = state.setdefault(payer[i], {"last": -np.inf, "warned": {}})
        tw = st["warned"].get(payee[i])
        if tw is not None and t[i] - tw > window and not pflag[i] and tier[i] >= 2:
            tier[i] = 1
            suppressed[i] = True
        elif t[i] - st["last"] <= window and 1 <= tier[i] < 4:
            new = escalated_tier(gate[i], hold_ok[i])
            if new > tier[i]:
                tier[i] = new
                escalated[i] = True
        if tier[i] >= 2 and assume_continue:
            st["last"] = t[i]
            st["warned"].setdefault(payee[i], t[i])
    return tier, escalated, suppressed


class PolicyEngine:
    """Stateful version used by the API: one decision at a time, memory per customer."""

    def __init__(self, thr: Thresholds):
        self.thr = thr
        self.memory: dict = {}

    def decide(self, s: dict, feats: dict, customer: str, payee: str, t_min: float) -> dict:
        S = pd.DataFrame([s])
        F = pd.DataFrame([feats])
        ev = evidence(S, F, self.thr)
        tier = int(base_tiers(S, F, self.thr, ev)[0])
        st = self.memory.setdefault(customer, {"last": -np.inf, "warned": {}})
        window = OVERRIDE_MEMORY_DAYS * MIN_PER_DAY
        note = None
        tw = st["warned"].get(payee)
        if tw is not None and t_min - tw > window and not ev["payee_flag"][0] and tier >= 2:
            tier, note = 1, "payee confirmed by customer after an earlier warning; no outside flag since"
        elif t_min - st["last"] <= window and 1 <= tier < 4:
            new = escalated_tier(bool(ev["gate"][0]), bool(ev["hold_ok"][0]))
            if new > tier:
                tier, note = new, "customer continued past a warning in the last 7 days"
        return {
            "tier": tier,
            "families": {k: bool(ev[k][0]) for k in FAMILY_NAMES},
            "gate": bool(ev["gate"][0]),
            "payee_flag": bool(ev["payee_flag"][0]),
            "memory_note": note,
        }

    def record_outcome(self, customer: str, payee: str, t_min: float, tier: int, action: str):
        """Remember that the customer continued past a warning (or not)."""
        if tier >= 2 and action == "continue":
            st = self.memory.setdefault(customer, {"last": -np.inf, "warned": {}})
            st["last"] = t_min
            st["warned"].setdefault(payee, t_min)
