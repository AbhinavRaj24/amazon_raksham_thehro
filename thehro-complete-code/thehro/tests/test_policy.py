"""The alert policy's safety rules."""
import numpy as np
import pandas as pd

from thehro.config import MIN_PER_DAY
from thehro.policy import V1, V2, V3, V4, PolicyEngine, Thresholds, apply_memory, base_tiers, calibrate, evidence

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
                  row(s_fused=0.02, s_journey=0.9, s_payee=0.9, t=2 * day, payee=7),  # gate passes: v2 -> 3, v3 -> 2
                  row(s_fused=0.02, t=20 * day, payee=8)])                # long after: stays 1
    ev = evidence(S, F, THR)
    for rules, third in [(V2, 3), (V3, 2)]:       # v3: a Rs 1,000 unflagged payment isn't worth an hour's wait
        tier, esc, _ = apply_memory(F, base_tiers(S, F, THR, ev, rules), ev, rules=rules)
        assert list(tier) == [2, 2, third, 1]
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


# ---------------------------------------------------------------- policy v2: retry memory
def test_cancel_then_retry_elsewhere_is_paused_in_v2_but_not_in_v1():
    """The coached evasion: warned, cancel, then a smaller payment to a different new account."""
    warn = dict(s_fused=0.2, s_journey=0.0, s_payee=0.0, s_context=0.0)
    quiet = dict(s_fused=0.0, s_journey=0.0, s_payee=0.0, s_context=0.0)
    for rules, expected in [(V1, 0), (V2, 2), (V3, 2)]:
        eng = PolicyEngine(THR, rules=rules)
        d1 = eng.decide(warn, row(**warn), "c1", "mule-1", 0.0)
        assert d1["tier"] == 2
        eng.record_outcome("c1", "mule-1", 0.0, d1["tier"], "cancel")
        d2 = eng.decide(quiet, row(**quiet, is_new_payee=1, amount=6_000), "c1", "mule-2", 45.0)
        assert d2["tier"] == expected
    assert d2["memory"] == {"kind": "retry", "minutes_since": 45.0}


def test_retry_memory_covers_the_cancelled_payee_but_not_old_known_payees():
    quiet = dict(s_fused=0.0, s_journey=0.0, s_payee=0.0, s_context=0.0)
    eng = PolicyEngine(THR)
    eng.decide(dict(quiet, s_fused=0.2), row(s_fused=0.2), "c1", "mule-1", 0.0)
    eng.record_outcome("c1", "mule-1", 0.0, 2, "cancel")
    assert eng.decide(quiet, row(**quiet), "c1", "mule-1", 30.0)["tier"] == 2       # same account again
    assert eng.decide(quiet, row(**quiet), "c1", "grocer", 40.0)["tier"] == 0       # a known, saved payee
    assert eng.decide(quiet, row(**quiet, is_new_payee=1), "c1", "x", 25 * 60.0)["tier"] == 0   # after 24 h


def test_retry_memory_escalates_to_a_hold_when_the_hold_evidence_is_there():
    eng = PolicyEngine(THR)
    eng.decide(dict(s_fused=0.2, s_journey=0, s_payee=0, s_context=0), row(s_fused=0.2), "c1", "m1", 0.0)
    strong = dict(s_fused=0.09, s_journey=0.9, s_payee=0.9, s_context=0.0)
    d = eng.decide(strong, row(**strong, payee_in_campaign=1, is_new_payee=1, amount=4_000), "c1", "m2", 20.0)
    assert d["tier"] == 4          # below the ₹10,000 hold line, but the line is waived right after a warning


def test_engine_matches_batch_memory_with_retry_rule():
    day = MIN_PER_DAY
    rows = [row(s_fused=0.2, t=0.0, payee=5),                                      # warned, cancelled
            row(s_fused=0.0, t=60.0, payee=6, is_new_payee=1),                     # retry rule: 0 -> 2, continued
            row(s_fused=0.0, t=2 * day, payee=7, is_new_payee=1),                  # > 24 h: stays 0
            row(s_fused=0.02, t=3 * day, payee=8, is_new_payee=1)]                 # continue memory: 1 -> 2
    continued = [False, True, True, True]
    S, F = frame(rows)
    ev = evidence(S, F, THR)
    batch, esc, _ = apply_memory(F, base_tiers(S, F, THR, ev), ev, continued=continued)
    eng = PolicyEngine(THR)
    live = []
    for r, went_ahead in zip(rows, continued):
        s = {k: r[k] for k in ("s_fused", "s_journey", "s_payee", "s_context")}
        d = eng.decide(s, r, "c1", r["payee"], r["t"])
        live.append(d["tier"])
        eng.record_outcome("c1", r["payee"], r["t"], d["tier"], "continue" if went_ahead else "cancel")
    assert list(batch) == live == [2, 2, 0, 2]
    assert list(esc) == [False, True, False, True]


def test_going_ahead_after_a_warning_does_not_start_retry_memory():
    """A genuine customer who continues past a false alarm isn't paused again for every new payee."""
    eng = PolicyEngine(THR)
    d = eng.decide(dict(s_fused=0.2, s_journey=0, s_payee=0, s_context=0), row(s_fused=0.2), "c1", "landlord", 0.0)
    eng.record_outcome("c1", "landlord", 0.0, d["tier"], "continue")
    quiet = dict(s_fused=0.0, s_journey=0.0, s_payee=0.0, s_context=0.0)
    assert eng.decide(quiet, row(**quiet, is_new_payee=1), "c1", "mover", 60.0)["tier"] == 0


def test_a_warning_with_no_answer_counts_as_not_going_ahead():
    eng = PolicyEngine(THR)
    eng.decide(dict(s_fused=0.2, s_journey=0, s_payee=0, s_context=0), row(s_fused=0.2), "c1", "m1", 0.0)
    quiet = dict(s_fused=0.0, s_journey=0.0, s_payee=0.0, s_context=0.0)       # app closed, no outcome sent
    assert eng.decide(quiet, row(**quiet, is_new_payee=1), "c1", "m2", 30.0)["tier"] == 2


def test_v1_policy_is_unchanged():
    """rules=V1 must give exactly Round 1's tiers (the Round 1 comparison relies on it)."""
    day = MIN_PER_DAY
    S, F = frame([row(s_fused=0.2, t=0.0, payee=5),
                  row(s_fused=0.0, t=60.0, payee=6, is_new_payee=1),
                  row(s_fused=0.02, t=1 * day, payee=6)])
    ev = evidence(S, F, THR)
    tier, _, _ = apply_memory(F, base_tiers(S, F, THR, ev, V1), ev, rules=V1)
    assert list(tier) == [2, 0, 2]


# ---------------------------------------------------------------- policy v3
def test_a_flagged_new_payee_is_at_least_a_pause_in_v3():
    quiet = dict(s_fused=0.0, s_journey=0.0, s_payee=0.0, s_context=0.0)
    S, F = frame([row(**quiet, is_new_payee=1, payee_registry=1),        # first payment to a reported account
                  row(**quiet, is_new_payee=1, payee_fri_band=2),         # ... to a DoT high-risk number
                  row(**quiet, is_new_payee=0, payee_in_campaign=1),      # a payee they have paid before
                  row(**quiet, is_new_payee=1)])                          # a clean new payee
    ev = evidence(S, F, THR)
    assert list(base_tiers(S, F, THR, ev, V2)) == [0, 0, 0, 0]
    assert list(base_tiers(S, F, THR, ev, V3)) == [2, 2, 0, 0]


def test_memory_reaches_a_trusted_check_only_for_big_or_flagged_payments_in_v3():
    """After going ahead past a warning, a risky Rs 20,000 payment is a pause in v3 (a trusted check in v2)."""
    warn = dict(s_fused=0.2, s_journey=0.0, s_payee=0.0, s_context=0.0)
    risky = dict(s_fused=0.02, s_journey=0.9, s_payee=0.0, s_context=0.9)          # note-level, two families agree
    for rules, small, big in [(V2, 3, 3), (V3, 2, 3)]:
        got = []
        for amount in (20_000, 60_000):
            eng = PolicyEngine(THR, rules=rules)
            d = eng.decide(warn, row(**warn), "c1", "p1", 0.0)
            eng.record_outcome("c1", "p1", 0.0, d["tier"], "continue")
            got.append(eng.decide(risky, row(**risky, amount=amount, is_new_payee=1), "c1", "p2", 60.0)["tier"])
        assert got == [small, big], (rules.name, got)


# ---------------------------------------------------------------- the way out for genuine customers
HOLD = dict(s_fused=0.2, s_journey=0.9, s_payee=0.9, s_context=0.0)


def held(eng, payee="acct", t=0.0, **flags):
    d = eng.decide(HOLD, row(**HOLD, amount=20_000, payee_fri_band=2, **flags), "c1", payee, t)
    assert d["tier"] == 4
    return d


def test_officer_release_confirms_the_payee_at_once():
    eng = PolicyEngine(THR)
    held(eng)
    eng.record_outcome("c1", "acct", 0.0, 4, "appeal")          # customer: "This was me"; waits for the officer
    eng.record_outcome("c1", "acct", 5.0, 4, "release")         # officer checked it on the phone
    d = eng.decide(HOLD, row(**HOLD, amount=20_000, payee_fri_band=2), "c1", "acct", 60.0)
    assert d["tier"] == 0 and d["memory"]["kind"] == "confirmed"
    quiet = dict(s_fused=0.0, s_journey=0.0, s_payee=0.0, s_context=0.0)
    other = eng.decide(quiet, row(**quiet, is_new_payee=1), "c1", "someone-new", 90.0)
    assert other["tier"] == 0                                  # no retry memory: the customer did go ahead


def test_a_new_outside_flag_after_the_release_brings_warnings_back():
    eng = PolicyEngine(THR)
    held(eng)
    eng.record_outcome("c1", "acct", 5.0, 4, "release")
    d = eng.decide(HOLD, row(**HOLD, amount=20_000, payee_fri_band=2, payee_registry=1), "c1", "acct", 3 * 1440.0)
    assert d["tier"] == 4 and "new outside flag" in d["memory_note"]


def test_the_customers_own_word_does_not_confirm_a_payee():
    """A scammer can coach a victim to press "This was me", so it only counts as going ahead."""
    eng = PolicyEngine(THR)
    warn = dict(s_fused=0.2, s_journey=0.0, s_payee=0.0, s_context=0.0)
    eng.decide(warn, row(**warn), "c1", "mule", 0.0)
    eng.record_outcome("c1", "mule", 0.0, 2, "this_was_me")
    again = eng.decide(warn, row(**warn), "c1", "mule", 30.0)
    assert again["tier"] >= 2                                  # still warned
    assert (again["memory"] or {}).get("kind") != "confirmed"


# ---------------------------------------------------------------- policy v4
def test_v4_journey_line_pauses_a_scam_like_journey_when_payee_and_phone_look_clean():
    thr = Thresholds(t1=0.01, t2=0.08, t4=0.08, family=THR.family, tj=0.3)
    S, F = frame([row(s_fused=0.02, s_journey=0.5),          # note-level overall, but the journey alone is extreme
                  row(s_fused=0.02, s_journey=0.2)])         # journey below its own line
    assert list(base_tiers(S, F, thr, evidence(S, F, thr, V4), V4)) == [2, 1]
    assert list(base_tiers(S, F, thr, evidence(S, F, thr, V3), V3)) == [1, 1]


def test_v4_does_not_use_the_callers_number():
    S, F = frame([row(s_fused=0.02, caller_fri_band=3)])     # only a bank that can read the caller would know this
    assert evidence(S, F, THR, V3)["context"][0] and not evidence(S, F, THR, V4)["context"][0]


def test_calibration_splits_the_pause_budget_and_stays_inside_it():
    rng = np.random.default_rng(0)
    n = 200_000
    S = pd.DataFrame({k: rng.random(n) for k in ("s_fused", "s_journey", "s_payee", "s_context")})
    F = pd.DataFrame({**{k: np.zeros(n) for k in QUIET if k != "consent"}, "consent": True, "amount": 1_000.0})
    genuine = np.ones(n, dtype=bool)
    v3, v4 = calibrate(S, F, genuine, n, rules=V3), calibrate(S, F, genuine, n, rules=V4)
    assert v3.tj is None and v4.tj is not None
    assert (S.s_fused.values >= v3.t2).sum() <= 20                       # 1 in 10,000 of 200,000
    assert (S.s_fused.values >= v4.t2).sum() <= 14 and (S.s_journey.values >= v4.tj).sum() <= 6
    assert ((S.s_fused.values >= v4.t2) | (S.s_journey.values >= v4.tj)).sum() <= 20
