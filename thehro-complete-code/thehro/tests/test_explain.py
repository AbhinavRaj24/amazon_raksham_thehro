"""Messages, advice and the evidence trail."""
import numpy as np
import pandas as pd

from thehro.explain import (EvidenceLog, advice_keys, build_message, fit_reason_weights, inr, reason_masks,
                            top_reasons)


def test_indian_digit_grouping():
    assert inr(1234567) == "₹12,34,567"
    assert inr(100000) == "₹1,00,000"
    assert inr(999) == "₹999"


def test_reasons_are_facts_ranked_by_how_much_more_often_they_appear_in_scams():
    F = pd.DataFrame({"payee_registry": [1, 0, 0, 0] * 50, "is_new_payee": [1, 1, 0, 0] * 50,
                      "amt_vs_usual": [10, 10, 1, 1] * 50})
    y = np.array([1, 0, 0, 0] * 50)
    w = fit_reason_weights(F, y)
    assert w["REGISTRY"] > w["NEW_PAYEE_LARGE"] > 1
    r = top_reasons({"payee_registry": 1, "is_new_payee": 1, "amt_vs_usual": 12.0, "amount": 30_000}, w)
    assert [x["code"] for x in r] == ["REGISTRY", "NEW_PAYEE_LARGE"]
    assert "12x" in r[1]["en"] and "12 गुना" in r[1]["hi"]


def test_advice_follows_the_strongest_fact():
    F = pd.DataFrame([
        {"on_call": 1, "caller_unknown": 1, "call_min": 90, "screen_share": 1},   # screen share wins
        {"on_call": 1, "caller_unknown": 1, "call_min": 90},                      # long call
        {"on_call": 1, "caller_unknown": 1, "call_min": 5},                       # short call
        {"stranger_credits_24h": 2},                                              # paid-first hook
        {"payee_in_campaign": 1},
        {},
    ])
    assert list(advice_keys(F)) == ["phone_control", "authority_call", "known_person", "pay_to_unlock",
                                    "flagged_payee", "generic"]


def test_messages_offer_a_way_out_and_never_show_a_score():
    reasons = [{"code": "REGISTRY", "en": "Reported account", "hi": "शिकायत दर्ज"}]
    m2 = build_message(2, reasons, "generic", "en")
    assert [a["id"] for a in m2["actions"]] == ["cancel", "call_bank", "continue"]
    m4 = build_message(4, reasons, "generic", "hi")
    assert "hold_note" in m4 and any("ऀ" <= ch <= "ॿ" for ch in m4["headline"])
    assert all("score" not in str(v).lower() for v in m2.values())


def test_evidence_log_detects_tampering():
    log = EvidenceLog()
    for i in range(3):
        log.append("decision", {"i": i})
    assert log.verify()
    log.records[1]["body"]["i"] = 99
    assert not log.verify()


def test_reason_masks_ignore_missing_context():
    F = pd.DataFrame({"on_call": [np.nan], "caller_unknown": [np.nan], "screen_share": [np.nan]})
    M = reason_masks(F)
    assert not M.CALL_UNKNOWN[0] and not M.SCREEN_SHARE[0]
