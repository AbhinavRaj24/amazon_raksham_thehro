"""The alert policy's safety rules."""
import numpy as np
import pandas as pd

from thehro.config import MIN_PER_DAY
from thehro.policy import PolicyEngine, Thresholds, apply_memory, base_tiers, calibrate, evidence

THR = Thresholds(t1=0.01, t2=0.08, t4=0.08, family={"journey": 0.02, "payee": 0.02, "context": 0.03})
QUIET = dict(payee_registry=0, payee_fri_band=0, payee_in_campaign=0, caller_fri_band=0, screen_share=0,
             remote_app=0, consent=True)


def frame(rows):
    S = pd.DataFrame([{k: r[k] for k in ("s_fused", "s_journey", "s_payee", "s_context")} for r in rows])
    F = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("s_")} for r in rows])
    return S, F


def row(**kw):
    r = dict(s_fused=0.0, s_journey=0.0, s_payee=0.0, s_context=0.0, amount=1_000.0, t=0.0, payer=1, payee=9, **QUIET)
    r.update(kw)
    return r


def test_one_family_alone_never_delays_money():
    # very high risk and a big amount, but only the journey family is elevated
    S, F = frame([row(s_fused=0.9, s_journey=0.9, amount=200_000)])
    tier = base_tiers(S, F, THR)
    assert tier[0] == 2          # a warning, never a trusted check or a hold


def test_hold_needs_an_outside_flag_on_the_payee_and_rs_10k():
    base = dict(s_fused=0.9, s_journey=0.9, s_payee=0.9)
    S, F = frame([row(**base, amount=60_000),
                  row(**base, amount=60_000, payee_in_campaign=1),
                  row(**base, amount=5_000, payee_in_campaign=1)])
    tier = base_tiers(S, F, THR)
    assert list(tier) == [3, 4, 2]


def test_context_does_not_vote_without_consent():
    S, F = frame([row(s_fused=0.9, s_journey=0.9, screen_share=1, consent=False, amount=60_000),
                  row(s_fused=0.9, s_journey=0.9, screen_share=1, consent=True, amount=60_000)])
    ev = evidence(S, F, THR)
    assert not ev["context"][0] and ev["context"][1]
    assert list(base_tiers(S, F, THR, ev)) == [2, 3]


def test_memory_escalates_after_the_customer_continues_past_a_warning():
    day = MIN_PER_DAY
    S, F = frame([row(s_fused=0.2, t=0.0, payee=5),                       # tier 2, customer continues
                  row(s_fused=0.02, t=1 * day, payee=6),                  # tier 1 -> raised to 2
                  row(s_fused=0.02, s_journey=0.9, s_payee=0.9, t=2 * day, payee=7),  # gate passes -> 3
                  row(s_fused=0.02, t=20 * day, payee=8)])                # long after: stays 1
    ev = evidence(S, F, THR)
    tier, esc, _ = apply_memory(F, base_tiers(S, F, THR, ev), ev)
    assert list(tier) == [2, 2, 3, 1]
    assert list(esc) == [False, True, True, False]


def test_confirmed_payee_stops_triggering_warnings_unless_flagged():
    day = MIN_PER_DAY
    S, F = frame([row(s_fused=0.2, t=0.0, payee=5),
                  row(s_fused=0.2, t=10 * day, payee=5),                       # confirmed: capped at 1
                  row(s_fused=0.2, t=12 * day, payee=5, payee_registry=1)])    # outside flag: warns again
    ev = evidence(S, F, THR)
    tier, _, sup = apply_memory(F, base_tiers(S, F, THR, ev), ev)
    assert list(tier) == [2, 1, 2]
    assert list(sup) == [False, True, False]


def test_engine_matches_batch_memory():
    eng = PolicyEngine(THR)
    s = dict(s_fused=0.2, s_journey=0.0, s_payee=0.0, s_context=0.0)
    d1 = eng.decide(s, row(**s), "c1", "p1", 0.0)
    assert d1["tier"] == 2
    eng.record_outcome("c1", "p1", 0.0, d1["tier"], "continue")
    s2 = dict(s_fused=0.02, s_journey=0.0, s_payee=0.0, s_context=0.0)
    d2 = eng.decide(s2, row(**s2), "c1", "p2", 60.0)
    assert d2["tier"] == 2 and d2["memory_note"]


def test_calibration_keeps_genuine_payments_inside_the_budget():
    rng = np.random.default_rng(0)
    n = 200_000
    S = pd.DataFrame({"s_fused": rng.beta(0.3, 30, n), "s_journey": rng.random(n) * 0.01,
                      "s_payee": rng.random(n) * 0.01, "s_context": rng.random(n) * 0.01})
    F = pd.DataFrame({"consent": np.ones(n, bool), "amount": np.full(n, 1000.0), **{k: np.zeros(n) for k in QUIET if k != "consent"}})
    genuine = np.ones(n, bool)
    n_all = 600_000                       # shop payments count in the denominator too
    thr = calibrate(S, F, genuine, n_all)
    assert (S.s_fused.values >= thr.t2).sum() <= 1e-4 * n_all
    assert (S.s_fused.values >= thr.t1).sum() <= 1e-3 * n_all
    assert thr.t1 <= thr.t2 <= thr.t4
