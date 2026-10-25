"""Alert policy: five tiers, a fixed interruption budget, and friction that grows with evidence.

Tier 0  Allow             nothing shown.
Tier 1  Note              one line under the Pay button, no delay.
Tier 2  Pause and explain full-screen warning with the reasons and three buttons:
                          Cancel / Call my bank (saved number) / Continue (enabled after 30 s).
Tier 3  Trusted check     as tier 2, then the payment waits for the customer's trusted person
                          to approve it, or for one hour. A customer with no trusted person set up
                          (most customers) gets a call-back from the bank within 15 minutes instead.
Tier 4  Hold for review   this payment only is held; a bank fraud analyst calls the customer on
                          the registered number within 30 minutes. Nothing else is frozen.

Rules
* Tier 1: fused risk above t1 (genuine payments reach it at most 1 in 1,000).
* Tier 2: fused risk above t2 (genuine payments reach it at most 1 in 10,000). Policy v3: also any
  first payment to a payee with an outside flag (fraud registry, DoT FRI high / very high, or a live
  mule network), whatever the risk score. A pause delays money by 30 seconds only.
* Tier 3: tier 2, AND two independent signal families agree (the "gate"), AND either the
  amount is Rs 50,000+ or the customer continued past a warning in the last 7 days.
* Tier 4: the gate, AND the payee is flagged by an outside source (fraud registry, DoT FRI
  high / very high, or a live mule network), AND fused risk above t4 (genuine payments at
  most 1 in 100,000), AND the amount is Rs 10,000+.
* Memory: after the customer continues past a tier 2+ warning, for 7 days the amount lines
  are waived and any tier 1 payment counts as tier 2: it becomes tier 3 if the gate passes,
  tier 4 if the tier 4 evidence is there too. Policy v3 caps this: memory raises a payment to a
  trusted check only if it is Rs 50,000+ or the payee has an outside flag; otherwise to a pause. A payee they chose to pay after a warning,
  still unflagged 7 days later, counts as confirmed: payments to it stay at tier 1 or below
  unless an outside flag appears.
* Retry memory (policy v2, the finale): after a tier 2+ warning the customer did NOT go ahead
  with (cancelled, called the bank, the trusted person declined, or no answer at all), for
  24 hours a payment to a new payee, or to the payee they cancelled, is at least a pause (and a
  trusted check or hold if that evidence is there). This closes the coached "cancel, then try
  a smaller amount somewhere else" evasion. Going ahead after a warning keeps the 7-day memory
  above instead, so genuine customers who continue past a false alarm aren't paused again.
* Policy v4: (a) a journey-only pause line. 30% of the tier-2 budget goes to the journey family's own
  score, so a scam journey still gets a pause when the payee looks clean and the phone signals are
  silent; the fused line keeps the other 70%, so the total budget is unchanged. (b) No caller
  identity: a bank app can't read the caller's number, so the caller's DoT rating no longer counts as
  context evidence (and the models don't use it either).
* Versions: V1 is Round 1, V2 adds retry memory, V3 adds the flagged-new-payee pause and the memory
  cap, V4 (the default) adds the journey line and drops caller identity. Every evaluation takes
  `rules`, and each version's lines are calibrated for it, so Round 1 stays reproducible for comparison.
* Officer confirmation (live engine only): when a bank officer checks a held payment with the
  customer and releases it, that payee is confirmed for this customer at once. Later payments
  to it are allowed unless an outside flag appears that wasn't there when the officer checked.
  The customer's own "This was me" doesn't do this, because a scammer can coach a victim to say
  it; it counts as going ahead, and the payee is confirmed after 7 clean days as above.

So a warning can come from risk alone, but anything that delays money needs two kinds of
evidence, and a hold needs an outside flag on the payee plus a human decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .config import (ALERT_BUDGET_T2, FAMILY_ELEVATED_PCTL, HOLD_MIN_AMOUNT, JOURNEY_LINE_SHARE, MIN_PER_DAY,
                     NUDGE_BUDGET_T1, OVERRIDE_MEMORY_DAYS, RETRY_MEMORY_HOURS, REVIEW_BUDGET_T4,
                     TRUSTED_CHECK_MIN_AMOUNT)

FAMILY_NAMES = ("journey", "payee", "context")


@dataclass(frozen=True)
class Rules:
    """Which policy rules are on. See the module docstring for what each version adds."""
    name: str = "v4"
    retry_hours: float = RETRY_MEMORY_HOURS   # v2: retry memory after a warning the customer didn't go ahead with
    flagged_new_payee_pause: bool = True      # v3: an outside flag on a first payment is at least a pause
    memory_cap: bool = True                   # v3: memory raises to a trusted check only for Rs 50,000+ or a flag
    journey_line: bool = True                 # v4: a journey-only pause line, from 30% of the tier-2 budget
    caller_id: bool = False                   # v4: False = the caller's number is not readable, so it is not used


V4 = Rules()
V3 = replace(V4, name="v3", journey_line=False, caller_id=True)
V2 = replace(V3, name="v2", flagged_new_payee_pause=False, memory_cap=False)
V1 = replace(V2, name="v1", retry_hours=0.0)
CURRENT = V4
RULES = {r.name: r for r in (V1, V2, V3, V4)}


def _col(F, name):
    return np.nan_to_num(np.asarray(F[name], dtype=float), nan=0.0)


def payee_flag(F) -> np.ndarray:
    """Outside evidence about the receiving account."""
    return (_col(F, "payee_registry") >= 1) | (_col(F, "payee_fri_band") >= 2) | (_col(F, "payee_in_campaign") >= 1)


def context_flag(F, caller_id: bool = True) -> np.ndarray:
    """Strong live signals: screen sharing, a remote-access app, or (only where the caller's number is readable)
    a caller DoT rates high risk."""
    flag = (_col(F, "screen_share") >= 1) | (_col(F, "remote_app") >= 1)
    if caller_id:
        flag = flag | (_col(F, "caller_fri_band") >= 2)
    return flag


@dataclass
class Thresholds:
    t1: float
    t2: float
    t4: float
    family: dict
    info: dict = field(default_factory=dict)
    tj: float | None = None      # policy v4: journey-only pause line (None: no such line)


def evidence(S: pd.DataFrame, F: pd.DataFrame, thr: Thresholds, rules: Rules = CURRENT) -> dict:
    """Which signal families are elevated, and whether two of them agree."""
    consent = np.asarray(F["consent"], dtype=bool)
    ev = {
        "journey": S["s_journey"].values > thr.family["journey"],
        "payee": (S["s_payee"].values > thr.family["payee"]) | payee_flag(F),
        # no consent, no context evidence: the family simply does not vote
        "context": ((S["s_context"].values > thr.family["context"]) | context_flag(F, rules.caller_id)) & consent,
    }
    ev["n_families"] = ev["journey"].astype(np.int8) + ev["payee"] + ev["context"]
    ev["gate"] = ev["n_families"] >= 2
    ev["payee_flag"] = payee_flag(F)
    ev["hold_ok"] = ev["gate"] & ev["payee_flag"] & (S["s_fused"].values >= thr.t4)
    return ev


def base_tiers(S: pd.DataFrame, F: pd.DataFrame, thr: Thresholds, ev: dict | None = None,
               rules: Rules = CURRENT) -> np.ndarray:
    """Tier for each payment before customer memory is applied."""
    ev = ev if ev is not None else evidence(S, F, thr, rules)
    s = S["s_fused"].values
    amount = np.asarray(F["amount"], dtype=float)
    tier = np.zeros(len(s), dtype=np.int8)
    tier[s >= thr.t1] = 1
    tier[s >= thr.t2] = 2
    tier[(s >= thr.t2) & ev["gate"] & (amount >= TRUSTED_CHECK_MIN_AMOUNT)] = 3
    tier[ev["hold_ok"] & (amount >= HOLD_MIN_AMOUNT)] = 4
    if rules.flagged_new_payee_pause:
        tier[(tier < 2) & new_payee(F) & ev["payee_flag"]] = 2
    if rules.journey_line and getattr(thr, "tj", None) is not None:
        tier[(tier < 2) & (S["s_journey"].values >= thr.tj)] = 2
    return tier


def calibrate(S: pd.DataFrame, F: pd.DataFrame, genuine: np.ndarray, n_all: int,
              budgets: dict | None = None, rules: Rules = CURRENT, journey_share: float | None = None) -> Thresholds:
    """Choose thresholds on a validation week so genuine payments stay inside each budget.

    genuine: mask of genuine scored payments in the validation week.
    n_all:   ALL customer payments that week, shop payments included (the budget denominator).
    rules:   with rules.journey_line, the tier-2 budget is split: JOURNEY_LINE_SHARE of it for the journey-only
             line, the rest for the fused line, so the total stays the same. journey_share overrides the share
             (scripts/design_choices.py sweeps it).
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
    share = (JOURNEY_LINE_SHARE if journey_share is None else journey_share) if rules.journey_line else 0.0
    t1 = top(s, genuine, b[1])
    t2 = max(top(s, genuine, b[2] * (1 - share)), t1)
    tj = top(S["s_journey"].values, genuine, b[2] * share) if share else None
    thr = Thresholds(t1=t1, t2=t2, t4=1.0, family=fam, tj=tj)
    ev = evidence(S, F, thr, rules)
    t4 = max(top(s, genuine & ev["gate"] & ev["payee_flag"], b[4]), t2)
    thr.t4 = t4
    thr.info = {"budgets": b, "journey_share": share, "rules": rules.name, "n_all": int(n_all),
                "n_genuine_scored": int(genuine.sum())}
    return thr


def escalated_tier(gate: bool, hold_ok: bool, strong: bool = True) -> int:
    """Tier for a risky payment soon after an earlier warning. strong: the payment is big enough for a trusted
    check on its own (Rs 50,000+) or its payee has an outside flag; v3 needs it for memory to reach tier 3."""
    return 4 if hold_ok else (3 if gate and strong else 2)


def _strong(rules: Rules, amount: float, flagged: bool) -> bool:
    return (not rules.memory_cap) or amount >= TRUSTED_CHECK_MIN_AMOUNT or bool(flagged)


def new_payee(F) -> np.ndarray:
    """True where the payment goes to an account this customer has never paid before."""
    if "is_new_payee" not in F:
        return np.zeros(len(F), dtype=bool)
    return np.nan_to_num(np.asarray(F["is_new_payee"], dtype=float), nan=0.0) == 1


def _memory_state() -> dict:
    return {"last": -np.inf, "warned": {}, "last_cancel": -np.inf, "cancelled_payee": None, "pending": None,
            "confirmed": {}, "flags": {}}


def outside_flags(F) -> set:
    """Which outside flags the payee carries (the evidence an officer saw when confirming it)."""
    flags = set()
    if _col(F, "payee_registry")[0] >= 1:
        flags.add("registry")
    if _col(F, "payee_fri_band")[0] >= 2:
        flags.add("fri")
    if _col(F, "payee_in_campaign")[0] >= 1:
        flags.add("network")
    return flags


def apply_memory(F: pd.DataFrame, tier: np.ndarray, ev: dict, assume_continue: bool = True,
                 rules: Rules = CURRENT, continued=None):
    """Per-customer memory, applied in time order.

    assume_continue=True evaluates the path on which the customer pressed Continue on every
    warning (the only path on which their later payments exist). `continued` (one bool per
    row) overrides it row by row, to evaluate customers who cancel some warnings. Returns the
    final tiers and flags marking escalated and suppressed payments. `rules` picks the policy version.
    """
    tier = tier.copy()
    t = np.asarray(F["t"], dtype=float)
    payer = np.asarray(F["payer"])
    payee = np.asarray(F["payee"])
    fresh = new_payee(F)
    amount = np.asarray(F["amount"], dtype=float)
    cont = np.full(len(tier), bool(assume_continue)) if continued is None else np.asarray(continued, dtype=bool)
    gate, pflag, hold_ok = ev["gate"], ev["payee_flag"], ev["hold_ok"]
    escalated = np.zeros(len(tier), dtype=bool)
    suppressed = np.zeros(len(tier), dtype=bool)
    users = np.unique(payer[tier >= 2])
    rows = np.where(np.isin(payer, users))[0]
    rows = rows[np.lexsort((t[rows], payer[rows]))]
    window = OVERRIDE_MEMORY_DAYS * MIN_PER_DAY
    retry_window = rules.retry_hours * 60
    state: dict = {}
    for i in rows.tolist():
        st = state.setdefault(payer[i], _memory_state())
        tw = st["warned"].get(payee[i])
        if tw is not None and t[i] - tw > window and not pflag[i] and tier[i] >= 2:
            tier[i] = 1
            suppressed[i] = True
        else:
            if t[i] - st["last"] <= window and 1 <= tier[i] < 4:
                new = escalated_tier(gate[i], hold_ok[i], _strong(rules, amount[i], pflag[i]))
                if new > tier[i]:
                    tier[i] = new
                    escalated[i] = True
            if (retry_window > 0 and t[i] - st["last_cancel"] <= retry_window and tier[i] < 4
                    and (fresh[i] or payee[i] == st["cancelled_payee"])):
                new = max(2, escalated_tier(gate[i], hold_ok[i], _strong(rules, amount[i], pflag[i])))
                if new > tier[i]:
                    tier[i] = new
                    escalated[i] = True
        if tier[i] >= 2:
            if cont[i]:
                st["last"] = t[i]
                st["warned"].setdefault(payee[i], t[i])
            else:
                st["last_cancel"], st["cancelled_payee"] = t[i], payee[i]
    return tier, escalated, suppressed


class PolicyEngine:
    """Stateful version used by the API: one decision at a time, memory per customer.

    Gives the same tiers as apply_memory when it is told the customer continued past every
    warning (tests/test_policy.py checks this)."""

    def __init__(self, thr: Thresholds, rules: Rules = CURRENT):
        self.thr = thr
        self.rules = rules
        self.memory: dict = {}

    def decide(self, s: dict, feats: dict, customer: str, payee: str, t_min: float) -> dict:
        S = pd.DataFrame([s])
        F = pd.DataFrame([feats])
        ev = evidence(S, F, self.thr, self.rules)
        tier = int(base_tiers(S, F, self.thr, ev, self.rules)[0])
        gate, hold_ok = bool(ev["gate"][0]), bool(ev["hold_ok"][0])
        strong = _strong(self.rules, float(feats["amount"]), bool(ev["payee_flag"][0]))
        st = self.memory.setdefault(customer, _memory_state())
        if st["pending"] is not None:      # an earlier warning got no answer: the customer didn't go ahead
            st["last_cancel"], st["cancelled_payee"] = st["pending"]
            st["pending"] = None
        window = OVERRIDE_MEMORY_DAYS * MIN_PER_DAY
        note, memory = None, None
        flags = outside_flags(F)
        st["flags"][payee] = flags          # what an officer would see if this payment is held
        tw = st["warned"].get(payee)
        conf = st["confirmed"].get(payee)
        if conf is not None and flags <= conf["flags"]:
            if tier >= 1:                   # an officer confirmed it and nothing new has turned up since
                tier, note = 0, "payee confirmed by a bank officer for this customer; no new outside flag since"
                memory = {"kind": "confirmed", "minutes_since": float(t_min - conf["t"])}
        elif tw is not None and t_min - tw > window and not ev["payee_flag"][0] and tier >= 2:
            tier, note = 1, "payee confirmed by customer after an earlier warning; no outside flag since"
        else:
            if t_min - st["last"] <= window and 1 <= tier < 4:
                new = escalated_tier(gate, hold_ok, strong)
                if new > tier:
                    tier, note = new, "customer continued past a warning in the last 7 days"
                    memory = {"kind": "continued", "minutes_since": float(t_min - st["last"])}
            if (self.rules.retry_hours > 0 and t_min - st["last_cancel"] <= self.rules.retry_hours * 60 and tier < 4
                    and (bool(new_payee(F)[0]) or payee == st["cancelled_payee"])):
                new = max(2, escalated_tier(gate, hold_ok, strong))
                if new > tier:
                    tier = new
                    note = ("the customer didn't go ahead with a warning in the last 24 hours; a new payee soon "
                            "after gets at least a pause")
                    memory = {"kind": "retry", "minutes_since": float(t_min - st["last_cancel"])}
            if conf is not None and note is None:
                note = "payee was confirmed by an officer, but a new outside flag has appeared since"
        if tier >= 2:
            st["pending"] = (t_min, payee)  # settled by record_outcome; no answer counts as not going ahead
        return {
            "tier": tier,
            "families": {k: bool(ev[k][0]) for k in FAMILY_NAMES},
            "gate": gate,
            "payee_flag": bool(ev["payee_flag"][0]),
            "memory_note": note,
            "memory": memory,
        }

    def record_outcome(self, customer: str, payee: str, t_min: float, tier: int, action: str):
        """Remember what the customer did after a warning."""
        if tier < 2 or action == "appeal":  # an appeal waits for the officer's decision
            return
        st = self.memory.setdefault(customer, _memory_state())
        st["pending"] = None
        if action == "release":             # an officer checked it with the customer: confirm this payee now
            st["confirmed"][payee] = {"t": t_min, "flags": set(st["flags"].get(payee, set()))}
            return
        if action == "this_was_me":         # the customer's own word: same as going ahead
            action = "continue"
        if action == "continue":
            st["last"] = t_min
            st["warned"].setdefault(payee, t_min)
        elif action in ("cancel", "call_bank", "trusted_declined", "scam"):
            st["last_cancel"], st["cancelled_payee"] = t_min, payee   # didn't go ahead: retry memory starts
        # "wait" and "trusted_ok" end in a payment the customer chose after a check: neither memory starts
