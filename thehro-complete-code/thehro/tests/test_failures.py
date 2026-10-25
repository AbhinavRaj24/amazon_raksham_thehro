"""Failure paths, each run on purpose: what the bank does when a part of the system is missing or slow.

Feeds time out, the decision service is down, no officer acts, a call-back can't reach the customer, the customer
hasn't opted in, and the bank's own scam report reaching every customer. (The model-side safety rules are in
test_policy.py; the normal judge-mode flow is in test_bank.py.)"""
import time

import pytest
from fastapi.testclient import TestClient

import bank.server as srv
from bank.server import Bank, DecisionClient, build_app
from test_bank import LINES, FakeDecisions, busiest_customers, case, live  # noqa: F401  (live is a fixture)


@pytest.fixture()
def setup(live):  # noqa: F811
    fake = FakeDecisions()
    customers = busiest_customers(live, 3)
    return TestClient(build_app(Bank(live, fake, customers, LINES))), customers, fake


def officer(cl, case_id, action):
    return cl.post(f"/officer/cases/{case_id}/action", json={"action": action}).json()


def test_outside_feeds_time_out_the_payment_is_decided_without_them(setup):
    cl, (c, *_), fake = setup
    d = case(cl, c, 15_002, feeds_down=True)
    sent = fake.payloads[-1]["features"]
    assert sent["payee_registry"] is None and sent["payee_fri_band"] is None    # unknown, not "clean"
    assert "didn't answer in time" in d["bank_view"]["feeds_down"]
    normal = case(cl, c, 15_002)
    assert normal["bank_view"]["feeds_down"] is None and fake.payloads[-1]["features"]["payee_registry"] is not None


def test_decision_service_down_drill_applies_the_banks_own_rules_and_holds_nothing(setup):
    cl, (c, *_), fake = setup
    d = case(cl, c, 50_004, service_down=True)                   # would be a hold if the service answered
    assert d["bank_view"]["fallback"] and d["bank_view"]["tier"] == 0 and d["state"] == "pin"
    assert d["phone_meta"]["case_id"] is None and not cl.get("/officer/cases").json()["cases"]


def test_decision_service_really_down_times_out_into_the_same_fallback(live):  # noqa: F811
    dead = DecisionClient("http://127.0.0.1:9", timeout_s=0.2)    # nothing listens on port 9
    cl = TestClient(build_app(Bank(live, dead, busiest_customers(live, 1), LINES)))
    t0 = time.perf_counter()
    d = case(cl, busiest_customers(live, 1)[0], 50_004)
    assert d["bank_view"]["fallback"] and d["state"] == "pin" and time.perf_counter() - t0 < 3


def test_no_officer_within_30_minutes_cancels_the_hold_and_the_money_stays(setup, monkeypatch):
    cl, (c, *_), fake = setup
    d = case(cl, c, 20_004)
    assert d["state"] == "held"
    later = time.time() + 31 * 60
    monkeypatch.setattr(srv.time, "time", lambda: later)
    q = cl.get("/officer/cases").json()["cases"][0]
    assert q["status"] == "closed" and "no officer action" in q["outcome"]
    assert cl.get(f"/judge/decisions/{d['decision_id']}").json()["state"] == "cancelled"


def test_a_call_back_that_cant_reach_the_customer_cancels_nothing(live, monkeypatch):  # noqa: F811
    alone = next(c for c in busiest_customers(live, 400) if not live.customer_profile(c)["trusted_contact"])
    cl = TestClient(build_app(Bank(live, FakeDecisions(), [alone], LINES)))
    d = case(cl, alone, 60_003)
    r = cl.post("/judge/action", json={"decision_id": d["decision_id"], "action": "callback"}).json()
    later = time.time() + 16 * 60
    monkeypatch.setattr(srv.time, "time", lambda: later)
    q = next(x for x in cl.get("/officer/cases").json()["cases"] if x["case_id"] == r["case_id"])
    assert q["status"] == "closed" and "not reached" in q["outcome"]
    v = cl.get(f"/judge/decisions/{d['decision_id']}").json()
    assert v["state"] == "awaiting_customer" and v["notifications"]          # told by SMS; they can wait the hour


def test_without_consent_the_phone_sends_no_flags_at_all(setup):
    cl, (c, *_), fake = setup
    d = case(cl, c, 15_002, consent=False, context={"on_call": 1, "screen_share": 1, "caller_unknown": 1})
    assert "context" not in d["sent_from_phone"] and set(d["dropped_from_phone"]) == {"on_call", "screen_share",
                                                                                       "caller_unknown"}
    assert fake.payloads[-1]["consent"] is False


def test_a_scam_an_officer_confirms_is_flagged_for_every_customer_at_once(setup):
    cl, (a, b, _), fake = setup
    assert cl.post("/judge/case", json={"customer": f"C{b}", "payee_type": "bank_reported",
                                        "amount": 9_000}).status_code == 422      # nothing reported yet
    held = case(cl, a, 40_004)
    officer(cl, held["phone_meta"]["case_id"], "cancel_scam")
    other = cl.post("/judge/session").json()["session_id"]                    # a different customer, another judge
    d = case(cl, b, 9_002, session_id=other, payee_type="bank_reported")
    assert d["payee"]["ref"] == held["payee"]["ref"]
    assert fake.payloads[-1]["features"]["payee_registry"] == 1.0
    assert d["bank_view"]["reasons"][0]["code"] == "BANK_REPORTED"
    assert d["bank_view"]["bank_reported"]["case_id"] == held["phone_meta"]["case_id"]
    assert "minute ago" in d["phone_by_lang"]["en"]["why"][0]                    # the customer sees it too
