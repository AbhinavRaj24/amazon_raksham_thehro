"""Warnings people can act on, and an evidence trail reviewers can check.

* Reason codes: each one is a checkable fact about this payment ("first payment to
  this account, 25x your usual"), never a score. They are ranked by how much more
  often they occur in reported scams than in genuine payments (a likelihood ratio
  learned from training data), and the top three are shown.
* Messages in English and Hindi. The advice is picked from the facts present (a long
  call with an unknown number, screen sharing, small credits from strangers first...),
  not from a guess about the scam type, so a new scam still gets advice that fits.
* An append-only, hash-chained evidence record for every decision of tier 2 or above,
  and a pre-filled draft for the National Cybercrime Reporting Portal / 1930.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import TIER_NAMES
from .simulate import CHANNELS, WORKFLOWS


def inr(x: float) -> str:
    """Indian digit grouping: 1234567 -> Rs 12,34,567."""
    n = int(round(float(x)))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + "₹" + s


def _v(row, name, default=0.0):
    x = row.get(name, default)
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return default if np.isnan(x) else x


BAND_EN = {1: "medium", 2: "high", 3: "very high"}
BAND_HI = {1: "मध्यम", 2: "उच्च", 3: "बहुत उच्च"}


@dataclass(frozen=True)
class Reason:
    code: str
    family: str
    en: str
    hi: str


REASONS = [
    Reason("NEW_PAYEE_LARGE", "journey",
           "First payment to this account, {mult}x your usual payment",
           "इस खाते को आपका पहला भुगतान, आपकी आम रकम का {mult} गुना"),
    Reason("ESCALATING", "journey",
           "Payments to accounts you first paid this week keep growing: {n} so far, {total} in total",
           "इस हफ़्ते पहली बार जिन खातों को पैसे भेजे, उनमें रकम बढ़ती जा रही है: अब तक {n} भुगतान, कुल {total}"),
    Reason("FD_BREAK", "journey",
           "A fixed deposit was broken in the last 48 hours",
           "पिछले 48 घंटों में एक FD तोड़ी गई है"),
    Reason("STRANGER_CREDITS", "journey",
           "You received {n} small payment(s) from unknown accounts today, a common hook in job and investment scams",
           "आज आपको अनजान खातों से {n} छोटी रकम मिली, जो नौकरी और निवेश घोटालों का आम जाल है"),
    Reason("DAY_OUTFLOW", "journey",
           "You have sent {mult}x your usual daily amount to people today",
           "आज आप लोगों को अपनी आम दैनिक रकम का {mult} गुना भेज चुके हैं"),
    Reason("NIGHT_NEW", "journey",
           "Late-night payment to a new account",
           "देर रात एक नए खाते को भुगतान"),
    Reason("REGISTRY", "payee",
           "This account has been reported on the national cybercrime portal",
           "इस खाते की शिकायत राष्ट्रीय साइबर अपराध पोर्टल पर दर्ज है"),
    Reason("FRI", "payee",
           "The mobile number linked to this account is rated {band} fraud risk by the telecom department",
           "इस खाते से जुड़े मोबाइल नंबर को दूरसंचार विभाग ने '{band}' धोखाधड़ी जोखिम बताया है"),
    Reason("CAMPAIGN", "payee",
           "This account is part of a network of {size} accounts that collect money from many new payers and move it on within minutes",
           "यह खाता {size} खातों के ऐसे नेटवर्क का हिस्सा है जो कई नए लोगों से पैसे लेकर मिनटों में आगे भेज देता है"),
    Reason("FAN_IN", "payee",
           "{n} new people paid this account in the last 24 hours",
           "पिछले 24 घंटों में {n} नए लोगों ने इस खाते में पैसे भेजे"),
    Reason("PASS_THROUGH", "payee",
           "This account sent on {pct}% of what it received in the last 24 hours",
           "इस खाते ने पिछले 24 घंटों में मिली रकम का {pct}% आगे भेज दिया"),
    Reason("NEW_ACCOUNT", "payee",
           "This account was opened {n} days ago",
           "यह खाता {n} दिन पहले खुला है"),
    Reason("DORMANT", "payee",
           "This account had no activity for {n} months before today",
           "इस खाते में {n} महीनों से कोई लेन-देन नहीं हुआ था"),
    Reason("CALL_UNKNOWN", "context",
           "You are on {kind} with an unknown number{dur}",
           "आप एक अनजान नंबर से {kind} पर हैं{dur}"),
    Reason("CALLER_FRI", "context",
           "The number calling you is rated {band} fraud risk by the telecom department",
           "आपको कॉल कर रहे नंबर को दूरसंचार विभाग ने '{band}' धोखाधड़ी जोखिम बताया है"),
    Reason("SCREEN_SHARE", "context",
           "Your screen is being shared right now",
           "आपकी स्क्रीन अभी शेयर हो रही है"),
    Reason("REMOTE_APP", "context",
           "A remote-access app is running on your phone",
           "आपके फ़ोन पर एक रिमोट-एक्सेस ऐप चल रहा है"),
    Reason("APP_SWITCHING", "context",
           "You switched between apps {n} times during this payment",
           "इस भुगतान के दौरान आपने {n} बार ऐप बदले"),
]
REASON_BY_CODE = {r.code: r for r in REASONS}


def _num(F, name):
    if name not in F:
        return np.zeros(len(F))
    return np.nan_to_num(np.asarray(F[name], dtype=float), nan=0.0)


def reason_masks(F: pd.DataFrame) -> pd.DataFrame:
    """Which reasons are true for each payment (vectorised)."""
    g = lambda c: _num(F, c)  # noqa: E731
    m = {
        "NEW_PAYEE_LARGE": (g("is_new_payee") == 1) & (g("amt_vs_usual") >= 5),
        "ESCALATING": (g("recent_payee_payments_7d") >= 2) & (g("growth_vs_recent_avg") >= 1.5),
        "FD_BREAK": g("fd_break_48h") == 1,
        "STRANGER_CREDITS": g("stranger_credits_24h") >= 1,
        "DAY_OUTFLOW": g("push_out_24h_vs_usual") >= 10,
        "NIGHT_NEW": (g("is_night") == 1) & (g("is_new_payee") == 1),
        "REGISTRY": g("payee_registry") >= 1,
        "FRI": g("payee_fri_band") >= 2,
        "CAMPAIGN": g("payee_in_campaign") >= 1,
        "FAN_IN": g("payee_new_payers_24h") >= 3,
        "PASS_THROUGH": (g("payee_passthrough_24h") >= 0.8) & (g("payee_inflow_24h_log") >= 3),
        # a missing age must not read as "opened today"
        "NEW_ACCOUNT": (np.asarray(F["payee_age_days"], dtype=float) < 30) if "payee_age_days" in F
        else np.zeros(len(F), dtype=bool),
        "DORMANT": g("payee_idle_days") >= 90,
        "CALL_UNKNOWN": (g("on_call") == 1) & (g("caller_unknown") == 1),
        "CALLER_FRI": g("caller_fri_band") >= 2,
        "SCREEN_SHARE": g("screen_share") == 1,
        "REMOTE_APP": g("remote_app") == 1,
        "APP_SWITCHING": g("app_switches") >= 4,
    }
    return pd.DataFrame(m, index=F.index)


def fit_reason_weights(F: pd.DataFrame, y: np.ndarray) -> dict:
    """Likelihood ratio of each reason: P(reason | reported scam) / P(reason | other payments)."""
    masks = reason_masks(F)
    M = masks.values
    y = np.asarray(y, dtype=bool)
    pos, neg = M[y], M[~y]
    lr = (pos.sum(0) + 1) / (len(pos) + 2) / ((neg.sum(0) + 1) / (len(neg) + 2))
    return {code: float(v) for code, v in zip(masks.columns, lr)}


def _fill(code: str, row: dict, lang: str) -> str:
    r = REASON_BY_CODE[code]
    tpl = r.hi if lang == "hi" else r.en
    hi = lang == "hi"
    if code in ("NEW_PAYEE_LARGE",):
        return tpl.format(mult=f"{_v(row, 'amt_vs_usual'):.0f}")
    if code == "DAY_OUTFLOW":
        return tpl.format(mult=f"{_v(row, 'push_out_24h_vs_usual'):.0f}")
    if code == "ESCALATING":
        total = 10 ** _v(row, "recent_payee_sum_7d_log") - 1 + _v(row, "amount")
        return tpl.format(n=int(_v(row, "recent_payee_payments_7d")) + 1, total=inr(total))
    if code == "STRANGER_CREDITS":
        return tpl.format(n=int(_v(row, "stranger_credits_24h")))
    if code in ("FRI", "CALLER_FRI"):
        band = int(_v(row, "payee_fri_band" if code == "FRI" else "caller_fri_band"))
        return tpl.format(band=(BAND_HI if hi else BAND_EN).get(band, ""))
    if code == "CAMPAIGN":
        return tpl.format(size=int(_v(row, "payee_campaign_size")))
    if code == "FAN_IN":
        return tpl.format(n=int(_v(row, "payee_new_payers_24h")))
    if code == "PASS_THROUGH":
        return tpl.format(pct=int(min(_v(row, "payee_passthrough_24h"), 1.0) * 100))
    if code == "NEW_ACCOUNT":
        return tpl.format(n=max(int(_v(row, "payee_age_days")), 1))
    if code == "DORMANT":
        return tpl.format(n=int(_v(row, "payee_idle_days") // 30))
    if code == "CALL_UNKNOWN":
        ott = _v(row, "call_ott") == 1
        mins = _v(row, "call_min")
        if hi:
            kind = "इंटरनेट कॉल" if ott else "फ़ोन कॉल"
            dur = f" (अब तक {mins:.0f} मिनट)" if mins >= 2 else ""
        else:
            kind = "an internet call" if ott else "a phone call"
            dur = f" ({mins:.0f} min so far)" if mins >= 2 else ""
        return tpl.format(kind=kind, dur=dur)
    if code == "APP_SWITCHING":
        return tpl.format(n=int(_v(row, "app_switches")))
    return tpl


def top_reasons(row: dict, weights: dict, k: int = 3) -> list[dict]:
    """The k strongest true reasons for one payment, with English and Hindi text."""
    F = pd.DataFrame([row])
    for col in ["is_new_payee", "amt_vs_usual", "recent_payee_payments_7d", "growth_vs_recent_avg",
                "fd_break_48h", "stranger_credits_24h", "push_out_24h_vs_usual", "is_night",
                "payee_registry", "payee_fri_band", "payee_in_campaign", "payee_new_payers_24h",
                "payee_passthrough_24h", "payee_inflow_24h_log", "payee_age_days", "payee_idle_days",
                "on_call", "caller_unknown", "caller_fri_band", "screen_share", "remote_app", "app_switches"]:
        if col not in F:
            F[col] = np.nan
    true = reason_masks(F).iloc[0]
    codes = sorted([c for c, v in true.items() if v], key=lambda c: -weights.get(c, 1.0))[:k]
    return [{"code": c, "family": REASON_BY_CODE[c].family, "weight": round(weights.get(c, 1.0), 1),
             "en": _fill(c, row, "en"), "hi": _fill(c, row, "hi")} for c in codes]


# ---------------------------------------------------------------------------
# Advice. Chosen from the facts of the payment, never from a model's guess about
# the scam type, so a brand-new scam still gets advice that fits what is happening.
# ---------------------------------------------------------------------------
ADVICE = {
    "phone_control": (
        "Someone may be controlling your phone.",
        "Bank and app support never ask you to share your screen or install an app. Stop sharing and hang up.",
        "हो सकता है कोई और आपका फ़ोन चला रहा हो।",
        "बैंक या ऐप सपोर्ट कभी स्क्रीन शेयर करने या ऐप इंस्टॉल करने को नहीं कहते। स्क्रीन शेयरिंग बंद करें और कॉल काटें।"),
    "authority_call": (
        "Is someone on a call telling you to pay?",
        "Police, CBI, ED, customs or courier companies never ask for money on a call, and nobody can be "
        "'digitally arrested'. Hang up and call 1930.",
        "क्या कॉल पर कोई आपसे पैसे भेजने को कह रहा है?",
        "पुलिस, CBI, ED, कस्टम्स या कूरियर कंपनी कभी कॉल पर पैसे नहीं माँगती, और 'डिजिटल अरेस्ट' जैसा कुछ "
        "नहीं होता। कॉल काटें और 1930 पर कॉल करें।"),
    "pay_to_unlock": (
        "Are you paying to unlock money?",
        "Anyone who asks you to pay first to receive money (a loan, job earnings, investment returns, a refund "
        "or a buyer's payment) is a scammer.",
        "क्या आप पैसे पाने के लिए पैसे दे रहे हैं?",
        "जो भी पैसे देने से पहले आपसे पैसे माँगे (लोन, नौकरी की कमाई, निवेश का मुनाफ़ा, रिफ़ंड या खरीदार का "
        "भुगतान), वह ठग है।"),
    "known_person": (
        "Is it really who they say?",
        "Voices, photos and numbers can be faked with AI. Hang up and call them back on a number you already have.",
        "क्या यह सच में वही हैं जो वे कह रहे हैं?",
        "AI से आवाज़, फ़ोटो और नंबर नकली बनाए जा सकते हैं। कॉल काटें और उन्हें पहले से मौजूद नंबर पर वापस कॉल करें।"),
    "flagged_payee": (
        "This account is linked to fraud.",
        "Don't pay unless you know this person well and have checked with them directly.",
        "यह खाता धोखाधड़ी से जुड़ा है।",
        "जब तक आप इस व्यक्ति को अच्छी तरह न जानते हों और उनसे सीधे पुष्टि न कर ली हो, भुगतान न करें।"),
    "generic": (
        "This payment shows signs of a scam.",
        "Scammers pose as police, bank staff, employers, relatives or buyers. Pay only if you know this person "
        "and nobody is telling you what to do right now.",
        "इस भुगतान में घोटाले के संकेत हैं।",
        "ठग पुलिस, बैंक कर्मचारी, नौकरी देने वाले, रिश्तेदार या खरीदार बनकर आते हैं। भुगतान तभी करें जब आप इस "
        "व्यक्ति को जानते हों और अभी कोई आपको निर्देश नहीं दे रहा हो।"),
}
# which advice fits each simulated scam type (used only to measure message fit)
ADVICE_FIT = {
    "investment": {"pay_to_unlock"}, "digital_arrest": {"authority_call"}, "task": {"pay_to_unlock"},
    "customer_care": {"phone_control"}, "marketplace": {"pay_to_unlock", "known_person"},
    "voice_clone": {"known_person"}, "loan_fee": {"pay_to_unlock"},
}


def advice_keys(F: pd.DataFrame) -> np.ndarray:
    """Advice for each payment, from the facts present."""
    M = reason_masks(F)
    call_min = _num(F, "call_min")
    call = M.CALL_UNKNOWN.values
    series = (M.ESCALATING | M.STRANGER_CREDITS).values
    key = np.full(len(F), "generic", dtype=object)
    rules = [  # each rule overwrites the ones before it, so the list runs from lowest priority up
        ("flagged_payee", (M.REGISTRY | M.FRI | M.CAMPAIGN).values),
        ("pay_to_unlock", series),
        ("known_person", call & (call_min < 40) & ~M.ESCALATING.values),
        ("authority_call", call & (call_min >= 40)),
        ("phone_control", (M.SCREEN_SHARE | M.REMOTE_APP).values),
    ]
    for k, m in rules:
        key[m] = k
    return key


HEADLINE = {
    1: ("Check before you pay", "भुगतान से पहले जाँच लें"),
    2: ("Stop. Take 30 seconds.", "रुकिए। 30 सेकंड लीजिए।"),
    3: ("Let's check this with someone you trust", "इसे किसी भरोसेमंद व्यक्ति से जाँच लें"),
    4: ("We've paused this payment to protect you", "आपकी सुरक्षा के लिए हमने यह भुगतान रोका है"),
}
ACTIONS = {
    2: [("cancel", "Cancel payment", "भुगतान रद्द करें"),
        ("call_bank", "Call my bank", "मेरे बैंक को कॉल करें"),
        ("continue", "Continue (after 30 s)", "जारी रखें (30 सेकंड बाद)")],
    3: [("cancel", "Cancel payment", "भुगतान रद्द करें"),
        ("trusted", "Ask my trusted person to approve", "अपने भरोसेमंद व्यक्ति से मंज़ूरी लें"),
        ("wait", "Wait 1 hour, then pay", "1 घंटा रुककर भुगतान करें"),
        ("call_bank", "Call my bank", "मेरे बैंक को कॉल करें")],
    4: [("cancel", "Cancel payment", "भुगतान रद्द करें"),
        ("call_bank", "Talk to the bank now", "अभी बैंक से बात करें")],
}
HOLD_NOTE = ("Only this payment is held. A bank fraud officer will call your registered number within "
             "30 minutes. Nothing else on your account is affected.",
             "केवल यह भुगतान रोका गया है। बैंक का धोखाधड़ी अधिकारी 30 मिनट के अंदर आपके रजिस्टर्ड नंबर पर "
             "कॉल करेगा। आपके खाते में और कुछ नहीं बदला है।")


def workflow_guess(wf_pred, wf_conf, floor: float) -> str | None:
    """The recogniser's guess at the scam type, for analysts and the complaint draft only."""
    if wf_pred is None or wf_conf is None or wf_conf < floor or int(wf_pred) < 0:
        return None
    return WORKFLOWS[int(wf_pred)]


def build_message(tier: int, reasons: list[dict], advice: str = "generic", lang: str = "en") -> dict:
    """What the customer sees. Tier 1 is a single line; tier 2+ is a full screen."""
    hi = lang == "hi"
    if tier <= 0:
        return {}
    if tier == 1:
        line = reasons[0]["hi" if hi else "en"] if reasons else (
            "पहली बार इस खाते को भुगतान" if hi else "First payment to this account")
        return {"tier": 1, "headline": HEADLINE[1][hi], "note": line}
    a_en, b_en, a_hi, b_hi = ADVICE.get(advice, ADVICE["generic"])
    msg = {
        "tier": tier,
        "headline": HEADLINE[tier][hi],
        "pattern": a_hi if hi else a_en,
        "advice": b_hi if hi else b_en,
        "why": [r["hi" if hi else "en"] for r in reasons],
        "actions": [{"id": a, "label": (h if hi else e)} for a, e, h in ACTIONS[tier]],
    }
    if tier == 4:
        msg["hold_note"] = HOLD_NOTE[hi]
    return msg


# ---------------------------------------------------------------------------
# Evidence trail
# ---------------------------------------------------------------------------
def _hash(obj: dict) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class EvidenceLog:
    """Append-only, hash-chained log. Changing any past record breaks every later hash."""

    def __init__(self):
        self.records: list[dict] = []

    @property
    def head(self) -> str:
        return self.records[-1]["hash"] if self.records else "0" * 64

    def append(self, kind: str, body: dict, at: str | None = None) -> dict:
        rec = {"seq": len(self.records), "kind": kind,
               "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "body": body, "prev_hash": self.head}
        rec["hash"] = _hash(rec)
        self.records.append(rec)
        return rec

    def verify(self) -> bool:
        prev = "0" * 64
        for rec in self.records:
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec["prev_hash"] != prev or _hash(body) != rec["hash"]:
                return False
            prev = rec["hash"]
        return True

    def find(self, decision_seq: int) -> list[dict]:
        return [r for r in self.records
                if r["seq"] == decision_seq or r["body"].get("decision_seq") == decision_seq]


def decision_record(payment: dict, scores: dict, decision: dict, reasons: list[dict],
                    workflow: str | None, versions: dict) -> dict:
    """What gets written for every tier 2+ decision. Pseudonymous ids, no message or call content."""
    return {
        "payment": {k: payment.get(k) for k in ("payment_id", "customer_ref", "payee_ref", "amount", "channel", "time")},
        "decision": {"tier": decision["tier"], "tier_name": TIER_NAMES[decision["tier"]],
                     "families_elevated": [k for k, v in decision["families"].items() if v],
                     "gate": decision["gate"], "payee_flag": decision["payee_flag"],
                     "memory_note": decision.get("memory_note")},
        "scores": {k: round(float(v), 4) for k, v in scores.items() if k.startswith("s_")},
        "reasons": [r["code"] for r in reasons],
        "workflow_guess": workflow,
        "versions": versions,
    }


def ncrp_draft(payment: dict, workflow: str | None, reasons: list[dict]) -> dict:
    """Pre-filled complaint for cybercrime.gov.in / 1930. The customer checks it and submits it."""
    channel = payment.get("channel")
    ch_name = CHANNELS[int(channel)] if isinstance(channel, (int, np.integer)) else str(channel or "")
    return {
        "portal": "National Cybercrime Reporting Portal (cybercrime.gov.in) / helpline 1930",
        "category": "Online Financial Fraud",
        "sub_category": "UPI related fraud" if "UPI" in ch_name else "Internet banking related fraud",
        "incident_time": payment.get("time"),
        "amount": payment.get("amount"),
        "transaction_ref": payment.get("utr", "<UTR from the payment receipt>"),
        "payee": payment.get("payee_ref"),
        "channel": ch_name,
        "suspected_scam_type": workflow or "not sure",
        "what_we_saw": [r["en"] for r in reasons],
        "note": "Report within the first hour if you can: banks can hold money that has not yet moved on.",
    }
