"""Build the mock responses for every bank-server endpoint from the Round 1 pipeline outputs.

    cd thehro-complete-code/thehro
    python bank/mock/make_mocks.py

Texts, reasons, scores, evidence records and complaint drafts all come from real outputs
(outputs/demo_scenarios.json, outputs/metrics.json), so the pages are built against the shapes and words
the live server will return. Only values that need a live session (decision IDs, timings, SLA clocks) are
placeholders. Every file carries "_mock": true and "_source".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # thehro-complete-code/thehro
sys.path.insert(0, str(ROOT))

from thehro.explain import build_message, fallback_reason, inr, memory_reason  # noqa: E402

DEMO = json.load(open(ROOT / "outputs" / "demo_scenarios.json", encoding="utf-8"))
METRICS = json.load(open(ROOT / "outputs" / "metrics.json", encoding="utf-8"))
THR = METRICS["headline"]["thresholds"]
SC = {s["id"]: s for s in DEMO["scenarios"]}
NOW = {"t_min": 68340, "label": "Day 48 · 11:00"}
CTX = ["on_call", "caller_unknown", "call_ott", "call_min", "screen_share", "remote_app", "app_switches"]
LANGS = ("en", "hi")
STATE = {0: "pin", 1: "awaiting_customer", 2: "awaiting_customer", 3: "awaiting_customer", 4: "held"}
NOT_KEPT = ["call audio", "messages or chats", "contacts", "the caller's number", "location", "screen content"]


def payee_type(f: dict) -> str:
    if f.get("payee_registry"):
        return "registry"
    if f.get("payee_in_campaign"):
        return "mule_network"
    if (f.get("payee_fri_band") or 0) >= 2:
        return "fri_high"
    return "new_individual" if f.get("is_new_payee") else "saved_contact"


def payee_facts(f: dict) -> list[str]:
    facts = []
    if f.get("is_new_payee"):
        facts.append("first payment from this customer")
    if f.get("payee_age_days") is not None:
        facts.append(f"opened {max(int(f['payee_age_days']), 1)} days ago")
    if (f.get("payee_idle_days") or 0) >= 90:
        facts.append(f"no activity for {int(f['payee_idle_days'] // 30)} months before today")
    if f.get("payee_in_campaign"):
        facts.append(f"in a live network of {int(f['payee_campaign_size'])} accounts")
    if f.get("payee_passthrough_24h") is not None and (f.get("payee_inflow_24h_log") or 0) >= 3:
        inflow = 10 ** f["payee_inflow_24h_log"] - 1
        facts.append(f"passed on {int(min(f['payee_passthrough_24h'], 1) * 100)}% of {inr(inflow)} received in 24 h")
    if f.get("payee_registry"):
        facts.append("reported on the national cybercrime portal")
    if (f.get("payee_fri_band") or 0) >= 2:
        facts.append("linked mobile rated high fraud risk by DoT")
    return facts


def stored_view(st: dict) -> dict:
    if st["evidence"]:
        return {"kind": "evidence", "seq": st["evidence"]["seq"], "record": st["evidence"], "chain_ok": True,
                "not_kept": NOT_KEPT}
    return {"kind": "none", "note": "Nothing stored beyond the bank's normal payment ledger; phone flags discarded.",
            "not_kept": NOT_KEPT}


def case_response(sc_id: str, i: int, lang: str, decision_id: str) -> dict:
    sc = SC[sc_id]
    st = sc["steps"][i]
    f = st["features"]
    tier = st["tier"]
    pay = st["payment"]
    reasons, message = st["reasons"], st["message"]
    if tier >= 1 and not reasons:   # the live service always states a true fact (Round 1 sometimes didn't)
        reasons = [fallback_reason(dict(st["features"], amount=pay["amount"]))]
        message = {lg: build_message(tier, reasons, st["advice"], lg) for lg in LANGS}
    if st["escalated"]:      # memory raised the tier: the live service puts the reason first
        reasons = [memory_reason({"kind": "continued", "minutes_since": 480.0})] + reasons[:2]
        message = {lg: build_message(tier, reasons, st["advice"], lg) for lg in LANGS}
    sent = {"payee": pay["payee_ref"], "amount": pay["amount"], "channel": pay["channel"], "consent": st["consent"]}
    if st["consent"]:
        sent["context"] = {k: f.get(k) for k in CTX}
    return {
        "_mock": True,
        "_source": f"outputs/demo_scenarios.json: {sc_id}, payment {i + 1} ({sc['kind']})",
        "decision_id": decision_id,
        "state": STATE[tier],
        "payment": {"customer": pay["customer_ref"], "amount": pay["amount"], "amount_text": st["amount_text"],
                    "channel": pay["channel"], "time": pay["time"]},
        "payee": {"ref": pay["payee_ref"], "type": payee_type(f), "facts": payee_facts(f)},
        "phone": message[lang], "phone_by_lang": message,
        "phone_meta": {"lang": lang, "continue_after_s": 30 if tier == 2 else None,
                       "case_id": "H-0001" if tier == 4 else None},
        "bank_view": {
            "tier": tier, "tier_name": st["tier_name"], "base_tier": st["base_tier"],
            "scores": st["scores"],
            "lines": {"note": THR["t1"], "pause": THR["t2"], "hold": THR["t4"]},
            "family_lines": THR["family"],
            "families": st["families"], "gate": st["gate"], "payee_flag": st["payee_flag"],
            "reasons": reasons, "advice": st["advice"], "features": st["features"],
            "memory_note": "customer continued past a warning in the last 7 days" if st["escalated"] else None,
            "workflow_guess": st["workflow_guess"], "workflow_confidence": st["workflow_confidence"],
            "fallback": None, "fast_path": None,
            "timing_ms": {"features": 150, "decision": 24, "total": 180, "_note": "placeholder timings"},
        },
        "sent_from_phone": sent,
        "stored": stored_view(st),
    }


def hand_built(base: dict, *, source: str, **changes) -> dict:
    out = json.loads(json.dumps(base))
    out["_source"] = source
    for path, value in changes.items():
        node = out
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return out


def write(name: str, obj) -> None:
    (HERE / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main():
    # ---------------- judge: options and session ----------------
    customers = []
    for s in DEMO["scenarios"]:
        st = s["steps"][0]
        p = s["persona"]
        usual = st["payment"]["amount"] / st["features"]["amt_vs_usual"] if st["features"]["amt_vs_usual"] else None
        customers.append({"id": st["payment"]["customer_ref"],
                          "label": f"{p['segment'].title()}, {p['age_band']}, {p['region']}",
                          **p, "usual_payment": round(usual) if usual else None,
                          "usual_payment_text": inr(usual) if usual else None})
    write("judge_options", {
        "_mock": True, "_source": "personas from outputs/demo_scenarios.json; lists as on the live server",
        "now": NOW,
        "customers": customers,
        "payee_types": [
            {"id": "saved_contact", "label": "Saved contact", "scored": True},
            {"id": "shop", "label": "Shop (merchant)", "scored": False, "note": "merchant payments skip scoring"},
            {"id": "regular_vendor", "label": "Regular vendor", "scored": True},
            {"id": "new_individual", "label": "New individual (clean)", "scored": True},
            {"id": "new_landlord", "label": "New landlord", "scored": True},
            {"id": "new_account", "label": "Account opened < 30 days ago", "scored": True},
            {"id": "dormant", "label": "Dormant account woken up", "scored": True},
            {"id": "registry", "label": "Reported on the cybercrime portal", "scored": True},
            {"id": "fri_high", "label": "High telecom fraud-risk number", "scored": True},
            {"id": "flagged_genuine", "label": "Genuine account wrongly flagged by DoT (false positive)",
             "scored": True},
            {"id": "mule_network", "label": "In a live mule network", "scored": True},
            {"id": "mule_unflagged", "label": "Mule not yet flagged (clean-looking)", "scored": True},
        ],
        "channels": ["UPI to person", "IMPS", "NEFT"],
        "history_kinds": [
            {"id": "new_payee_payment", "label": "Paid a new payee earlier", "fields": ["amount", "hours_ago"]},
            {"id": "fd_break", "label": "Broke a fixed deposit", "fields": ["hours_ago"]},
            {"id": "stranger_credit", "label": "Small credit from an unknown account", "fields": ["amount", "hours_ago"]},
            {"id": "sent_today", "label": "Money already sent to people today", "fields": ["amount"]},
        ],
        "context_fields": [
            {"id": "on_call", "type": "bool", "label": "On a call"},
            {"id": "call_ott", "type": "bool", "label": "Internet call (WhatsApp, Meet)"},
            {"id": "call_min", "type": "number", "label": "Call length so far (min)"},
            {"id": "caller_unknown", "type": "bool", "label": "Caller not in contacts",
             "note": "model v1 input; an Android bank app can't read this, so model v2 drops it"},
            {"id": "screen_share", "type": "bool", "label": "Screen being shared"},
            {"id": "remote_app", "type": "bool", "label": "Remote-access app running"},
            {"id": "app_switches", "type": "int", "label": "App switches during this payment"},
        ],
        "languages": ["en", "hi"],
    })
    write("judge_session", {"_mock": True, "_source": "placeholder", "session_id": "S-1", "now": NOW,
                            "history": [], "holds": [], "memory": []})

    # ---------------- judge: case decisions ----------------
    cases = {
        "t0_allow": ("new_landlord", 0),         # genuine; customer has not opted in, so no phone flags sent
        "t0_allow_on_call": ("medical", 0),      # genuine hospital payment during a call: not interrupted
        "t0_missed_scam": ("marketplace", 0),    # a scam we miss: small first step, no outside flags
        "t1_note": ("marketplace", 1),
        "t2_pause": ("loan_fee", 0),
        "t2_false_alarm": ("false_alarm", 0),    # genuine payment paused by mistake
        "t3_trusted": ("digital_arrest", 2),
        "t4_hold": ("digital_arrest", 0),
        "t4_escalated": ("task", 1),             # memory: tier 2 on its own, raised to a hold
    }
    for n, (name, (sc_id, i)) in enumerate(cases.items(), start=1):
        for lang in LANGS:
            write(f"judge_case_{name}.{lang}", case_response(sc_id, i, lang, f"D-{n:04d}"))
    hold = case_response("digital_arrest", 0, "en", "D-0010")
    for lang in LANGS:
        base = case_response("digital_arrest", 0, lang, "D-0010")
        write(f"judge_case_fallback.{lang}", hand_built(
            base, source="hand-built: the digital-arrest payment with the decision service stopped",
            state="pin", phone={}, phone_by_lang={"en": {}, "hi": {}}, phone_meta__case_id=None,
            bank_view={"tier": 0, "tier_name": "Allow", "fallback": "decision service unavailable: the bank's own "
                       "rules applied and nothing was held", "scores": None, "reasons": [],
                       "timing_ms": {"decision_timeout": 150}},
            stored={"kind": "none", "note": "Thehro did not decide; the bank logged the fallback.",
                    "not_kept": NOT_KEPT}))
        write(f"judge_case_t0_merchant.{lang}", hand_built(
            base, source="hand-built: payment to a shop (merchant), which skips scoring",
            state="pin", phone={}, phone_by_lang={"en": {}, "hi": {}}, phone_meta__case_id=None,
            payee={"ref": "Shop ••3301", "type": "shop", "facts": ["registered merchant"]},
            bank_view={"tier": 0, "tier_name": "Allow", "fast_path": "merchant payment: not scored",
                       "scores": None, "reasons": [], "timing_ms": {"total": 2}},
            stored={"kind": "none", "note": "Nothing stored beyond the bank's normal payment ledger.",
                    "not_kept": NOT_KEPT}))

    # ---------------- judge: customer actions ----------------
    actions = {
        "continue": {"state": "pin", "note": "Enter your UPI PIN to pay."},
        "confirm_pin": {"state": "paid", "utr": "MOCK604812345678", "alert_tier": 2, "time_to_get_past_s": 34,
                        "history_added": {"kind": "payment", "amount": hold["payment"]["amount"],
                                          "payee": hold["payee"]["ref"]}},
        "cancel": {"state": "cancelled", "note": "Payment cancelled. No money left your account."},
        "call_bank": {"state": "call_bank", "note": "Call your bank on the number printed on your card or saved in "
                      "this app, never a number someone gave you."},
        "this_was_me": {"state": "review_requested", "case_id": "H-0002",
                        "note": "A bank officer will call your registered number within 30 minutes to check this "
                                "with you."},
        "trusted": {"state": "trusted_pending", "note": "Request sent to your trusted person."},
        "wait": {"state": "waiting", "until": "Day 48 · 12:00", "note": "You can pay after one hour."},
    }
    for a, body in actions.items():
        write(f"judge_action_{a}", {"_mock": True, "_source": "placeholder states",
                                    "decision_id": "D-0008", "action": a, **body})

    # ---------------- judge: a decision's current state (the page polls it while a hold is reviewed) ----------
    base_dec = {"_mock": True, "_source": "placeholder states", "decision_id": "D-0008", "tier": 4,
                "case": {"case_id": "H-0001", "status": "open", "outcome": None}, "state": "held", "notifications": []}
    write("judge_decision_held", base_dec)
    write("judge_decision_released", {**base_dec, "state": "pin", "case": {"case_id": "H-0001", "status": "closed",
          "outcome": "genuine: released by the officer"}, "notifications": [{"at": "11:06:10", "sms":
          "Your payment of ₹15,693 was checked and released. Enter your PIN in the app to finish."}]})
    write("judge_decision_cancelled", {**base_dec, "state": "cancelled", "case": {"case_id": "H-0001",
          "status": "closed", "outcome": "scam confirmed by the officer: cancelled"}, "notifications": [{"at":
          "11:06:10", "sms": "Your payment of ₹15,693 was stopped. It looked like a scam. No money left your account. "
          "To report it, call 1930."}]})

    # ---------------- officer ----------------
    def case_summary(case_id, sc_id, i, minutes_left, note=None):
        st = SC[sc_id]["steps"][i]
        return {"case_id": case_id, "decision_id": f"D-{case_id[-4:]}", "status": "open", "outcome": None,
                "customer_note": note, "opened_at": st["payment"]["time"], "minutes_left": minutes_left,
                "amount": st["payment"]["amount"], "amount_text": st["amount_text"],
                "customer": st["payment"]["customer_ref"], "payee": st["payment"]["payee_ref"],
                "top_reason": st["reasons"][0]["en"] if st["reasons"] else None,
                "families": [k for k, v in st["families"].items() if v]}

    write("officer_cases", {"_mock": True, "_source": "tier 4 payments from outputs/demo_scenarios.json",
                            "now": NOW, "sla_minutes": 30, "cases": [
                                case_summary("H-0001", "digital_arrest", 0, 26),
                                case_summary("H-0002", "customer_care", 0, 14,
                                             "customer says this payment is genuine"),
                                case_summary("H-0003", "investment", 1, 3)]})
    da = SC["digital_arrest"]
    st = da["steps"][0]
    size = int(st["features"]["payee_campaign_size"])
    write("officer_case_detail", {
        "_mock": True, "_source": "outputs/demo_scenarios.json: digital_arrest, payment 1",
        "case_id": "H-0001", "decision_id": "D-0001", "status": "open", "outcome": None,
        "opened_at": st["payment"]["time"], "minutes_left": 26,
        "payment": {"customer": st["payment"]["customer_ref"], "amount": st["payment"]["amount"],
                    "amount_text": st["amount_text"], "channel": st["payment"]["channel"], "time": st["payment"]["time"]},
        "customer": {"ref": st["payment"]["customer_ref"], "age_band": da["persona"]["age_band"],
                     "lang": da["persona"]["lang"], "registered_mobile": "+91 ••••• •4321",
                     "trusted_contact": da["persona"]["trusted_contact"], "note": None},
        "payee": {"ref": st["payment"]["payee_ref"], "facts": payee_facts(st["features"]),
                  "network": {"size": size, "flagged_at": "Day 46 · 18:16",
                              "members": [st["payment"]["payee_ref"]] + [f"A/c ••{(n * 7919 + 137) % 10000:04d}"
                                                                         for n in range(1, size)],
                              "_note": "in the mock, members other than the payee are made up"}},
        "why_held": {"summary": f"{' + '.join(k for k, v in st['families'].items() if v)} agree; outside flag on "
                                "the payee", "reasons": st["reasons"], "families": st["families"],
                     "scores": st["scores"], "lines": {"note": THR["t1"], "pause": THR["t2"], "hold": THR["t4"]},
                     "family_lines": THR["family"]},
        "model_guess": {"scam_type": st["workflow_guess"], "note": "for the officer only; never shown to the customer"},
        "evidence": {"records": [st["evidence"]], "chain_ok": True},
        "call_script": ["Call the registered number, never a number from the payment.",
                        "Ask who asked for this payment and how they first got in touch.",
                        "Explain the warning signs above in plain words.",
                        "Never ask for the PIN, OTP or card details."],
        "actions": [{"id": "release_confirm", "label": "Genuine: release and confirm the payee"},
                    {"id": "cancel_scam", "label": "Scam: cancel, draft the 1930 complaint, report the mule"},
                    {"id": "no_answer", "label": "No answer: cancel at the deadline"}],
    })
    write("officer_action_cancel_scam", {
        "_mock": True, "_source": "complaint draft from outputs/demo_scenarios.json",
        "case_id": "H-0001", "state": "cancelled_scam", "ncrp_draft": da["ncrp_draft"],
        "mule_report": {"payee": st["payment"]["payee_ref"], "network_size": st["features"]["payee_campaign_size"],
                        "sent_to": "NPCI / I4C feeds (simulated)"},
        "customer_sms": "Your payment of ₹15,693 was stopped. It looked like a scam. No money left your account. "
                        "To report it, call 1930.",
        "chain_ok": True})
    write("officer_action_release_confirm", {
        "_mock": True, "_source": "placeholder", "case_id": "H-0001", "state": "released", "payee_confirmed": True,
        "customer_sms": "Your payment of ₹15,693 was checked and released. Enter your PIN in the app to finish.",
        "note": "This payee is now confirmed for this customer: later payments to it go through unless a new "
                "outside flag appears."})
    write("officer_action_no_answer", {
        "_mock": True, "_source": "placeholder", "case_id": "H-0001", "state": "auto_cancelled",
        "note": "No answer within 30 minutes: the payment was cancelled and the money stayed in the account."})
    write("officer_audit", {"_mock": True, "_source": "placeholder", "reads": [
        {"at": "Day 48 · 11:04", "user": "officer.demo", "role": "officer", "action": "read", "what": "case H-0001"},
        {"at": "Day 48 · 11:06", "user": "officer.demo", "role": "officer", "action": "cancel_scam",
         "what": "case H-0001"},
        {"at": "Day 48 · 11:20", "user": "auditor.demo", "role": "auditor", "action": "read", "what": "audit log"}]})

    # ---------------- kit ----------------
    write("kit_cases", {"_mock": True, "_source": "format examples only; P4 writes the real cases in test_kit/cases/",
                        "cases": [
        {"id": "K1-EX1", "title": "Digital arrest: first transfer during a long video call", "written_by": "P4",
         "customer": "C23724", "payee_type": "mule_network", "amount": 15693, "channel": "UPI to person",
         "history": [], "consent": True,
         "context": {"on_call": 1, "call_ott": 1, "call_min": 70, "caller_unknown": 1},
         "expected": "interrupt", "why": "new account in a live mule network, 55x her usual, long call"},
        {"id": "K1-EX2", "title": "Hospital bill while on the phone with the hospital", "written_by": "P4",
         "customer": "C734", "payee_type": "new_individual", "amount": 64759, "channel": "IMPS", "history": [],
         "consent": True, "context": {"on_call": 1, "call_ott": 0, "call_min": 12, "caller_unknown": 1},
         "expected": "no_interrupt", "why": "genuine emergency; clean payee; no series"},
        {"id": "K1-EX3", "title": "Marketplace 'scan to receive', first small try", "written_by": "P4",
         "customer": "C27360", "payee_type": "new_individual", "amount": 2728, "channel": "UPI to person",
         "history": [], "consent": True, "context": {"on_call": 1, "call_ott": 0, "call_min": 4, "caller_unknown": 1},
         "expected": "interrupt", "why": "scam; we expect to miss this one (known failure case)"}]})
    write("kit_run_k1", {"_mock": True, "_source": "placeholder", "case_id": "K1-EX3", "expected": "interrupt",
                         "got": {"tier": 0, "tier_name": "Allow"}, "right": False,
                         "note": "a named failure case: small first step, clean payee"})
    samples = []
    picks = [("digital_arrest", 0), ("investment", 0), ("task", 0), ("customer_care", 0), ("marketplace", 0),
             ("voice_clone", 0), ("loan_fee", 0), ("new_landlord", 0), ("medical", 0), ("false_alarm", 0)]
    for j, (sc_id, i) in enumerate(picks, start=1):
        s = SC[sc_id]
        st = s["steps"][i]
        scam = s["kind"] == "scam"
        right = st["tier"] >= 2 if scam else st["tier"] <= 1
        samples.append({"sample": j, "label": "scam" if scam else "genuine",
                        "scam_type": sc_id if scam else None, "amount_text": st["amount_text"],
                        "expected": "interrupt" if scam else "no_interrupt",
                        "got": {"tier": st["tier"], "tier_name": st["tier_name"]}, "right": right})
    write("kit_run_k2", {"_mock": True, "_source": "first payments of the demo journeys (not a random draw)",
                         "seed": 7, "samples": samples,
                         "summary": {"n": len(samples), "right": sum(x["right"] for x in samples),
                                     "scam_right": sum(x["right"] for x in samples if x["label"] == "scam"),
                                     "genuine_right": sum(x["right"] for x in samples if x["label"] == "genuine")}})

    # ---------------- device flags (automation app on the demo phone) ----------------
    write("device_flags", {"_mock": True, "_source": "placeholder", "device_id": "phone-A",
                           "source": "automation app on the phone (on-device)", "updated_at": NOW["label"],
                           "flags": {"on_call": 1, "call_ott": 0, "call_min": 12}})
    print(f"wrote {len(list(HERE.glob('*.json')))} mock files to {HERE}")


if __name__ == "__main__":
    main()
