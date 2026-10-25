"""The bank server's own rules (judge mode): safeguards, the customer's choices, holds and the officer flow."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bank.server import Bank, DecisionClient, InProcessDecisions, build_app
from thehro.config import MIN_PER_DAY, TIER_NAMES
from thehro.explain import build_message
from thehro.features import build_features
from thehro.live import LiveWorld
from thehro.simulate import SimConfig, simulate

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "outputs" / "model_bundle.pkl"
LINES = {"tiers": {"note": 0.01, "pause": 0.08, "hold": 0.08}, "family": {"journey": 0.01, "payee": 0.01,
                                                                          "context": 0.03}}


class FakeDecisions:
    """Returns the tier coded in the amount's last digit (…2 → tier 2, …4 → tier 4, else tier 0)."""

    def __init__(self):
        self.records, self.outcomes, self.payloads = [], [], []

    def score(self, payload):
        self.payloads.append(payload)
        amount = payload["payment"]["amount"]
        tier = {1: 1, 2: 2, 3: 3, 4: 4}.get(int(amount) % 10, 0)
        reasons = [{"code": "NEW_PAYEE_LARGE", "family": "journey", "weight": 26.3,
                    "en": "First payment to this account", "hi": "पहला भुगतान"}] if tier else []
        seq = None
        if tier >= 2:
            seq = len(self.records)
            self.records.append({"seq": seq, "kind": "decision", "body": {"workflow_guess": None}, "hash": "h",
                                 "prev_hash": "p"})
        return {"tier": tier, "tier_name": TIER_NAMES[tier], "scores": {"s_fused": 0.5}, "families":
                {"journey": True, "payee": tier >= 3, "context": False}, "gate": tier >= 3, "payee_flag": tier == 4,
                "reasons": reasons, "advice": "generic", "message": build_message(tier, reasons, "generic",
                                                                                   payload["lang"]),
                "memory_note": None, "evidence_seq": seq, "latency_ms": 1.0}

    def outcome(self, seq, action, t_min):
        self.outcomes.append((seq, action))
        return {"recorded": seq, "chain_ok": True, "ncrp_draft": {"category": "Online Financial Fraud"}
                if action == "scam" else None}

    def evidence(self, seq):
        return {"records": [r for r in self.records if r["seq"] == seq], "chain_ok": True}


@pytest.fixture(scope="module")
def live():
    w = simulate(SimConfig(seed=11, n_users=3_000, n_merchants=2_000, n_vendors=400, n_external=20_000,
                           n_suppliers=600), verbose=False)
    _, denom = build_features(w, verbose=False, keep_state=True)
    lw = LiveWorld(w, denom["state"])
    lw.set_now(45 * MIN_PER_DAY + 11 * 60)
    return lw


@pytest.fixture()
def fake():
    return FakeDecisions()


@pytest.fixture()
def client(live, fake):
    customers = busiest_customers(live, 3)
    return TestClient(build_app(Bank(live, fake, customers, LINES))), customers


def busiest_customers(live, n):
    p = live.world.payments
    return [int(c) for c in p.payer[p.payer < live.world.cfg.n_users].value_counts().index[:n]]


def case(cl, cust, amount, **kw):
    body = {"customer": f"C{cust}", "payee_type": "new_individual", "amount": amount, "consent": True,
            "context": {"on_call": 1, "call_min": 12}, **kw}
    r = cl.post("/judge/case", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_options_list_customers_and_payee_types(client):
    cl, customers = client
    o = cl.get("/judge/options").json()
    assert [c["id"] for c in o["customers"]] == [f"C{c}" for c in customers]
    assert {p["id"] for p in o["payee_types"]} >= {"mule_network", "shop", "new_landlord"}


def test_phone_gets_the_message_never_the_scores(client):
    cl, (c, *_) = client
    out = case(cl, c, 20_004)
    assert out["bank_view"]["tier"] == 4 and out["state"] == "held"
    assert "s_fused" not in json.dumps(out["phone"]) and "score" not in json.dumps(out["phone"]).lower()
    assert out["phone_by_lang"][out["phone_meta"]["lang"]] == out["phone"] and out["phone_by_lang"]["hi"]["headline"]
    assert out["stored"]["kind"] == "evidence"


def test_no_consent_means_no_phone_flags_and_unknown_fields_are_dropped(client):
    cl, (c, *_) = client
    off = case(cl, c, 20_000, consent=False, context={"on_call": 1, "screen_share": 1})
    assert "context" not in off["sent_from_phone"] and off["dropped_from_phone"] == ["on_call", "screen_share"]
    on = case(cl, c, 20_000, context={"on_call": 1, "phone_number": "+91 98…"})
    assert on["sent_from_phone"]["context"] == {"on_call": 1.0} and on["dropped_from_phone"] == ["phone_number"]


def test_pause_unlocks_after_30_seconds_then_pin_then_history(client, fake):
    cl, (c, *_) = client
    s = cl.post("/judge/session").json()["session_id"]
    out = case(cl, c, 9_002, session_id=s)
    assert out["state"] == "awaiting_customer" and out["phone_meta"]["continue_after_s"] == 30
    early = cl.post("/judge/action", json={"decision_id": out["decision_id"], "action": "continue"})
    assert early.status_code == 409
    ok = cl.post("/judge/action", json={"decision_id": out["decision_id"], "action": "continue", "skip_wait": True})
    assert ok.json()["state"] == "pin" and fake.outcomes[-1][1] == "continue"
    paid = cl.post("/judge/action", json={"decision_id": out["decision_id"], "action": "confirm_pin"}).json()
    assert paid["state"] == "paid" and paid["history_added"]
    assert len(cl.post("/judge/reset", json={"session_id": s}).json()["history"]) == 0


def test_paid_payment_becomes_history_for_the_next_one(client, live):
    cl, (c, *_) = client
    s = cl.post("/judge/session").json()["session_id"]
    first = case(cl, c, 9_000, session_id=s)
    cl.post("/judge/action", json={"decision_id": first["decision_id"], "action": "confirm_pin"})
    second = case(cl, c, 15_000, session_id=s)
    assert second["payment"]["time"] != first["payment"]["time"]        # the clock moved on


def test_each_tier_offers_only_its_own_actions(live, fake):
    c = next(c for c in busiest_customers(live, 400) if live.customer_profile(c)["trusted_contact"])
    cl = TestClient(build_app(Bank(live, fake, [c], LINES)))
    trusted = case(cl, c, 60_003)
    assert cl.post("/judge/action", json={"decision_id": trusted["decision_id"], "action": "continue"}).status_code == 409
    r = cl.post("/judge/action", json={"decision_id": trusted["decision_id"], "action": "trusted"}).json()
    assert r["state"] == "trusted_pending"
    assert cl.post("/judge/action", json={"decision_id": trusted["decision_id"],
                                          "action": "trusted_declined"}).json()["state"] == "cancelled"


def test_hold_goes_to_the_officer_reads_are_logged_and_scam_drafts_1930(client, fake):
    cl, (c, *_) = client
    held = case(cl, c, 30_004)
    case_id = held["phone_meta"]["case_id"]
    queue = cl.get("/officer/cases").json()["cases"]
    assert any(x["case_id"] == case_id and x["status"] == "open" for x in queue)
    detail = cl.get(f"/officer/cases/{case_id}", params={"user": "priya", "role": "officer"}).json()
    assert detail["why_held"]["reasons"] and detail["call_script"]
    assert any(a["user"] == "priya" and a["what"] == f"case {case_id}" for a in cl.get("/officer/audit").json()["reads"])
    assert cl.get(f"/judge/decisions/{held['decision_id']}").json()["state"] == "held"
    done = cl.post(f"/officer/cases/{case_id}/action", json={"action": "cancel_scam", "user": "priya"}).json()
    assert done["state"] == "cancelled_scam" and done["ncrp_draft"]["category"] == "Online Financial Fraud"
    now = cl.get(f"/judge/decisions/{held['decision_id']}").json()
    assert now["state"] == "cancelled" and now["case"]["status"] == "closed" and "1930" in now["notifications"][0]["sms"]
    again = cl.post(f"/officer/cases/{case_id}/action", json={"action": "release_confirm"})
    assert again.status_code == 409                                      # a closed case can't be reopened


def test_officer_release_lets_the_customer_pay(client, fake):
    cl, (c, *_) = client
    held = case(cl, c, 40_004)
    cl.post(f"/officer/cases/{held['phone_meta']['case_id']}/action", json={"action": "release_confirm"})
    assert fake.outcomes[-1][1] == "release"              # the officer's check confirms the payee
    assert cl.post("/judge/action", json={"decision_id": held["decision_id"],
                                          "action": "confirm_pin"}).json()["state"] == "paid"


def test_shop_payments_skip_scoring(client):
    cl, (c, *_) = client
    out = case(cl, c, 450, payee_type="shop")
    assert out["bank_view"]["fast_path"] and out["state"] == "pin" and out["phone"] == {}


def test_decision_service_down_means_bank_rules_and_nothing_held(live):
    down = DecisionClient("http://127.0.0.1:9", timeout_s=0.2)               # nothing listens on port 9
    cl = TestClient(build_app(Bank(live, down, [0], LINES)))
    out = case(cl, 0, 50_004)
    assert out["bank_view"]["fallback"] and out["state"] == "pin" and out["phone"] == {}
    assert cl.get("/officer/cases").json()["cases"] == []


@pytest.mark.skipif(not BUNDLE.exists(), reason="run scripts/run_all.py first")
def test_real_decision_service_end_to_end(live):
    from thehro.service import DecisionService, load_bundle
    svc = InProcessDecisions(DecisionService(load_bundle(BUNDLE)))
    cust = busiest_customers(live, 1)[0]
    cl = TestClient(build_app(Bank(live, svc, [cust], LINES)))
    out = case(cl, cust, 25_000, payee_type="mule_network",
               context={"on_call": 1, "call_ott": 1, "call_min": 70, "caller_unknown": 1})
    assert out["bank_view"]["tier"] in range(5) and set(out["bank_view"]["families"]) == {"journey", "payee", "context"}
    assert "scores" in out["bank_view"] and "scores" not in out["phone"]
    if out["bank_view"]["tier"] >= 2:
        assert out["stored"]["kind"] == "evidence" and out["stored"]["chain_ok"]
        assert len(out["phone"]["why"]) >= 1


def test_phone_call_is_timed_from_yes_no_events(client):
    cl, (c, *_) = client
    r = cl.post("/device/flags", json={"device_id": "phone-A", "flags": {"on_call": 1, "caller_number": "+91…"}}).json()
    assert r["kept"] == ["on_call"] and r["dropped"] == ["caller_number"]
    flags = cl.get("/device/flags").json()["flags"]
    assert flags["on_call"] == 1 and flags["call_min"] >= 0
    out = case(cl, c, 20_000, context_source="device")
    assert out["sent_from_phone"]["context"]["on_call"] == 1.0 and "call_min" in out["sent_from_phone"]["context"]
    cl.post("/device/flags", json={"flags": {"on_call": 0}})
    assert "call_min" not in cl.get("/device/flags").json()["flags"]


def test_flags_sent_as_text_by_an_automation_app_are_read_correctly(client):
    cl, _ = client
    cl.post("/device/flags", json={"source": "MacroDroid", "flags": {"on_call": "1", "call_ott": "true"}})
    f = cl.get("/device/flags").json()["flags"]
    assert f["on_call"] == 1 and f["call_ott"] == 1 and f["call_min"] >= 0
    cl.post("/device/flags", json={"source": "MacroDroid", "flags": {"on_call": "0", "screen_share": "maybe"}})
    f = cl.get("/device/flags").json()["flags"]
    assert f["on_call"] == 0 and "call_min" not in f and "screen_share" not in f    # "0" is no; nonsense is ignored


def test_this_was_me_is_logged_as_the_customers_word_and_paying_reports_the_time(client, fake):
    cl, (c, *_) = client
    paused = case(cl, c, 9_002)
    r = cl.post("/judge/action", json={"decision_id": paused["decision_id"], "action": "this_was_me",
                                       "skip_wait": True}).json()
    assert r["state"] == "pin" and fake.outcomes[-1][1] == "this_was_me"
    paid = cl.post("/judge/action", json={"decision_id": paused["decision_id"], "action": "confirm_pin"}).json()
    assert paid["alert_tier"] == 2 and paid["time_to_get_past_s"] >= 0
    held = case(cl, c, 20_004)
    cl.post("/judge/action", json={"decision_id": held["decision_id"], "action": "this_was_me"})
    assert fake.outcomes[-1][1] == "appeal"                 # a hold's appeal waits for the officer


def test_same_payee_as_pays_that_account_again(client):
    cl, (c, *_) = client
    s = cl.post("/judge/session").json()["session_id"]
    first = case(cl, c, 9_000, session_id=s)
    cl.post("/judge/action", json={"decision_id": first["decision_id"], "action": "confirm_pin"})
    again = case(cl, c, 12_000, session_id=s, same_payee_as=first["decision_id"])
    assert again["payee"]["ref"] == first["payee"]["ref"]
    other = cl.post("/judge/case", json={"session_id": "elsewhere", "customer": f"C{c}", "amount": 100,
                                         "same_payee_as": first["decision_id"]})
    assert other.status_code == 422                          # only within the same session


def test_hand_written_cases_run_only_once_frozen(live, fake, tmp_path, monkeypatch):
    import bank.server as srv
    customers = busiest_customers(live, 3)
    k1 = {"id": "K1-99", "customer": f"C{customers[0]}", "payee_type": "new_individual", "amount": 15_002,
          "expected": "interrupt", "why": "a test case that was never frozen"}
    (tmp_path / "K1-99.json").write_text(json.dumps(k1))
    monkeypatch.setattr(srv, "KIT", tmp_path)
    cl = TestClient(build_app(Bank(live, fake, customers, LINES)))
    listed = cl.get("/kit/cases").json()
    assert listed["cases"][0]["registered"] is False and listed["all_registered"] is False
    assert cl.post("/kit/run", json={"case_id": "K1-99"}).status_code == 409     # not pre-registered: never runs
    own = cl.post("/kit/run", json={"case": k1}).json()                         # a judge's own case runs at once
    assert own["got"]["tier"] == 2 and own["right"] is True
    held = cl.post("/kit/run", json={"case": dict(k1, amount=40_004)}).json()   # a kit hold never waits for an officer
    assert held["got"]["tier"] == 4 and all(c["status"] == "closed" for c in cl.get("/officer/cases").json()["cases"])
    import hashlib
    digest = hashlib.sha256((tmp_path / "K1-99.json").read_bytes()).hexdigest()
    (tmp_path / "FROZEN.sha256").write_text(f"# frozen 2026-10-25T00:00\n{digest}  K1-99.json\n")
    assert cl.post("/kit/run", json={"case_id": "K1-99"}).json()["right"] is True   # frozen: it runs
    (tmp_path / "K1-99.json").write_text(json.dumps(dict(k1, expected="no_interrupt")))
    assert cl.post("/kit/run", json={"case_id": "K1-99"}).status_code == 409     # edited after freezing: refused


def test_spot_check_validates_the_seed_and_reports_a_missing_cache(live, fake):
    from bank.server import KitBaseline
    bank = Bank(live, fake, busiest_customers(live, 3), LINES)
    bank.kit = KitBaseline("/nonexistent/thehro_world.pkl")
    cl = TestClient(build_app(bank))
    assert cl.post("/kit/spot_check", json={"seed": -1}).status_code == 422
    assert cl.post("/kit/spot_check", json={"seed": 7, "journeys": 500}).status_code == 422
    r = cl.post("/kit/spot_check", json={"seed": 7}).json()
    assert r["status"] == "missing" and "run_all.py" in r["baseline"]["error"]
    assert cl.get("/kit/status").json()["spot_check"]["state"] == "missing"


def test_no_trusted_person_means_a_bank_call_back_not_an_hours_wait(live, fake):
    pool = busiest_customers(live, 400)
    alone = next(c for c in pool if not live.customer_profile(c)["trusted_contact"])
    paired = next(c for c in pool if live.customer_profile(c)["trusted_contact"])
    cl = TestClient(build_app(Bank(live, fake, [alone, paired], LINES)))
    act = lambda did, a: cl.post("/judge/action", json={"decision_id": did, "action": a})  # noqa: E731
    with_person = case(cl, paired, 60_003)                      # FakeDecisions: ...3 -> tier 3
    assert "trusted" in [a["id"] for a in with_person["phone"]["actions"]]
    d = case(cl, alone, 60_003)
    assert [a["id"] for a in d["phone"]["actions"]] == ["cancel", "callback", "wait", "call_bank"]
    assert d["phone_by_lang"]["hi"]["headline"] != d["phone_by_lang"]["en"]["headline"]
    assert act(d["decision_id"], "trusted").status_code == 409       # nobody to ask
    r = act(d["decision_id"], "callback").json()
    assert r["state"] == "callback_pending" and r["case_id"].startswith("B-")
    queued = next(c for c in cl.get("/officer/cases").json()["cases"] if c["case_id"] == r["case_id"])
    assert queued["kind"] == "callback" and 14 <= queued["minutes_left"] <= 15
    # couldn't reach them: nothing is cancelled, the choice goes back to the customer
    nr = cl.post(f"/officer/cases/{r['case_id']}/action", json={"action": "no_answer"}).json()
    assert nr["state"] == "not_reached"
    assert cl.get(f"/judge/decisions/{d['decision_id']}").json()["state"] == "awaiting_customer"
    assert not any(a == "cancel" for _, a in fake.outcomes)
    # they ask again; this time the officer reaches them and releases it: the payee is confirmed
    r2 = act(d["decision_id"], "callback").json()
    rel = cl.post(f"/officer/cases/{r2['case_id']}/action", json={"action": "release_confirm"}).json()
    assert rel["payee_confirmed"] and cl.get(f"/judge/decisions/{d['decision_id']}").json()["state"] == "pin"
    assert fake.outcomes[-1][1] == "release"


def test_a_judges_phone_follows_the_sessions_newest_payment(client):
    cl, (c, *_) = client
    s = cl.post("/judge/session").json()["session_id"]
    assert cl.get(f"/judge/session/{s}/latest").json()["decision"] is None
    first = case(cl, c, 9_002, session_id=s)
    second = case(cl, c, 12_002, session_id=s)
    got = cl.get(f"/judge/session/{s}/latest").json()
    assert got["decision"]["decision_id"] == second["decision_id"] != first["decision_id"]
    cl.post("/judge/action", json={"decision_id": second["decision_id"], "action": "cancel"})
    assert cl.get(f"/judge/session/{s}/latest").json()["decision"]["state"] == "cancelled"   # acted on elsewhere
    assert got["age_s"] >= 0 and got["decision"]["phone"]
    assert not {"bank_view", "stored"} & set(got["decision"]) and set(got["decision"]["payee"]) == {"ref", "type"}
    assert cl.get("/judge/session/nope/latest").status_code == 404
    qr = cl.get("/judge/qr", params={"data": "http://192.168.0.2:8080/?phone=1&s=S-0001"})
    assert qr.status_code in (200, 404) and (qr.status_code == 404 or 'xmlns="http://www.w3.org/2000/svg"' in qr.text)
