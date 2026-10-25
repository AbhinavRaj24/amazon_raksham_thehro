"""The bank server for judge mode: a judge's case in, a live decision, the phone alert and the officer flow out.

    cd thehro-complete-code/thehro
    uvicorn thehro.api:app --port 8000                                   # the decision service
    uvicorn bank.server:create_app --factory --host 0.0.0.0 --port 8080  # this server

It stands in for the bank's backend:
  1. turns the judge's case into payment events and computes the 39 features with the training code
     (thehro/live.py), using the bank's history of this customer and this payee;
  2. asks the decision service (/v1/score) for a decision, and falls back to the bank's own rules, holding
     nothing, if the service is slow or down;
  3. shows the phone only the message (never scores), drops phone flags when there is no consent, and keeps
     the customer's choices, holds for officer review, and a log of who read which case.
Paths and shapes match the mocks (bank/mock/README.md).
"""
from __future__ import annotations

import io
import itertools
import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from thehro import kit as testkit
from thehro.policy import CURRENT, RULES
from thehro.config import CALLBACK_SLA_MINUTES, MIN_PER_DAY, TIER_NAMES
from thehro.explain import build_message, inr
from thehro.features import ALL_FEATURES
from thehro.scenarios import DISPLAY_FEATURES
from thehro.live import CONTEXT_KEYS, PAYEE_TYPES, LiveWorld, NoPayeeAvailable, payee_facts
from thehro.simulate import CHANNELS, KIND

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
KIT = ROOT.parents[1] / "test_kit" / "cases"
REPORT = ROOT.parents[1] / "test_kit" / "report.md"
SLA_MINUTES = 30
PAUSE_SECONDS = 30
NOT_KEPT = ["call audio", "messages or chats", "contacts", "the caller's number", "location", "screen content"]
PAYEE_LABELS = {
    "saved_contact": "Saved contact", "shop": "Shop (merchant)", "regular_vendor": "Regular vendor",
    "new_individual": "New individual (clean)", "new_landlord": "New landlord",
    "new_account": "Account opened < 30 days ago", "dormant": "Dormant account woken up",
    "registry": "Reported on the cybercrime portal", "fri_high": "High telecom fraud-risk number",
    "flagged_genuine": "Genuine account wrongly flagged by DoT (false positive)",
    "mule_network": "In a live mule network", "mule_unflagged": "Mule not yet flagged (clean-looking)",
    "bank_reported": "Just reported as a scam by a bank officer (cancel a hold as a scam first)",
}
JUDGE_PAYEE_TYPES = PAYEE_TYPES + ("bank_reported",)   # bank_reported: picked from this bank's own reports
FEED_FEATURES = ("payee_registry", "payee_fri_band")    # from outside feeds: the I4C registry and DoT's FRI
PAYEE_KIND = {"saved_contact": KIND["p2p_known"], "regular_vendor": KIND["vendor_regular"],
              "new_landlord": KIND["new_landlord"]}
HISTORY_KINDS = [
    {"id": "new_payee_payment", "label": "Paid a new payee earlier", "fields": ["amount", "hours_ago"]},
    {"id": "fd_break", "label": "Broke a fixed deposit", "fields": ["hours_ago"]},
    {"id": "stranger_credit", "label": "Small credit from an unknown account", "fields": ["amount", "hours_ago"]},
    {"id": "sent_today", "label": "Money already sent to people today", "fields": ["amount", "hours_ago"]},
]
CONTEXT_FIELDS = [
    {"id": "on_call", "type": "bool", "label": "On a call"},
    {"id": "call_ott", "type": "bool", "label": "Internet call (WhatsApp, Meet)"},
    {"id": "call_min", "type": "number", "label": "Call length so far (min)"},
    {"id": "caller_unknown", "type": "bool", "label": "Caller not in contacts",
     "note": "Round 1 input. A bank app can't read the caller's number, so policy v4 drops it before scoring"},
    {"id": "screen_share", "type": "bool", "label": "Screen being shared"},
    {"id": "remote_app", "type": "bool", "label": "Remote-access app running"},
    {"id": "app_switches", "type": "int", "label": "App switches during this payment"},
]
# Policy v4 can't know who is calling: the bank drops these phone fields before scoring
POLICY = RULES[os.environ.get("THEHRO_POLICY", CURRENT.name)]
CALLER_ONLY = ("caller_unknown",) if not POLICY.caller_id else ()
CALLER_FACTS = ("caller_unknown", "caller_fri_band") if not POLICY.caller_id else ()

AWAITING_BY_TIER = {                   # what the customer can do on each alert screen
    1: {"continue", "cancel"},                                  # note: pay anyway, or cancel
    2: {"continue", "this_was_me", "cancel", "call_bank"},      # pause: continue after 30 s
    3: {"trusted", "wait", "cancel", "call_bank"},              # trusted check: no plain continue
}
NO_TRUSTED_TIER3 = {"callback", "wait", "cancel", "call_bank"}  # no trusted person: the bank calls back instead
ACTIONS_BY_STATE = {
    "trusted_pending": {"trusted_ok", "trusted_declined", "cancel"},
    "callback_pending": {"wait", "cancel"},
    "pin": {"confirm_pin", "cancel"},
    "held": {"cancel", "call_bank", "this_was_me"},
    "review_requested": {"cancel"},
    "call_bank": {"cancel"},
}


def _flag_value(v):
    """A phone flag as a number. Automation apps often send text ("0", "1", "true"), and "0" must mean no."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().lower()
    if t in ("true", "yes", "on"):
        return 1.0
    if t in ("false", "no", "off", ""):
        return 0.0
    try:
        return float(t)
    except ValueError:
        return None                                   # not a flag value: ignored rather than guessed


def allowed_actions(d: dict) -> set:
    if d["state"] == "awaiting_customer":
        if d["tier"] == 3 and not d.get("trusted_contact", True):
            return NO_TRUSTED_TIER3
        return AWAITING_BY_TIER.get(d["tier"], set())
    return ACTIONS_BY_STATE.get(d["state"], set())


def when(t_min: float) -> str:
    day, minute = int(t_min // MIN_PER_DAY), int(t_min % MIN_PER_DAY)
    return f"Day {day + 1} · {minute // 60:02d}:{minute % 60:02d}"


def mask(account: int) -> str:
    return f"A/c ••{(int(account) * 7919) % 10000:04d}"


def parse_customer(c) -> int:
    return int(str(c).lstrip("Cc"))


# ---------------------------------------------------------------------------- talking to the decision service
class DecisionClient:
    """The decision service over HTTP, as the bank backend would call it."""

    def __init__(self, url: str, timeout_s: float):
        self.http = httpx.Client(base_url=url, timeout=timeout_s)

    def score(self, payload: dict) -> dict:
        r = self.http.post("/v1/score", json=payload)
        r.raise_for_status()
        return r.json()

    def outcome(self, seq: int, action: str, t_min: float) -> dict:
        r = self.http.post("/v1/outcome", json={"evidence_seq": seq, "action": action, "t_min": t_min})
        r.raise_for_status()
        return r.json()

    def evidence(self, seq: int) -> dict:
        r = self.http.get(f"/v1/evidence/{seq}")
        r.raise_for_status()
        return r.json()


class InProcessDecisions:
    """The same calls straight into a DecisionService (tests, or a single-process demo)."""

    def __init__(self, service):
        self.svc = service

    def score(self, payload: dict) -> dict:
        return json.loads(json.dumps(self.svc.decide(payload["payment"], payload["features"], payload["consent"],
                                                     payload["lang"]), default=float))

    def outcome(self, seq: int, action: str, t_min: float) -> dict:
        return self.svc.outcome(seq, action, t_min)

    def evidence(self, seq: int) -> dict:
        return {"records": self.svc.log.find(seq), "chain_ok": self.svc.log.verify()}


# ---------------------------------------------------------------------------- state
@dataclass
class Session:
    id: str
    clock: float
    generation: int = 0
    rows: list = field(default_factory=list)      # completed payments and credits, in the world's schema
    fd_t: list = field(default_factory=list)      # fixed deposits broken
    used_payees: set = field(default_factory=set)
    n_cases: int = 0
    last_decision: str | None = None              # what a phone following this session shows

    @property
    def memory_key(self) -> str:                  # decision-service memory is per session (and per reset)
        return f"{self.id}.{self.generation}"


class KitBaseline:
    """The frozen Round 1 baseline behind the Kit tab's spot-checks: thehro.kit.get_baseline and kit.spot_check,
    the same function and the same draws as scripts/spot_check.py. Loading takes about a minute and 3 GB of RAM,
    so it starts in the background the first time the Kit tab asks, not when the server starts."""

    def __init__(self, cache: str):
        self.cache, self.state, self.error, self.started = cache, "idle", None, None
        self.lock, self.run_lock = threading.Lock(), threading.Lock()

    def start(self) -> None:
        with self.lock:
            if self.state in ("loading", "ready"):
                return
            if not Path(self.cache).exists():
                self.state, self.error = "missing", (f"no kit cache at {self.cache}. Build it once: python scripts/"
                                                     f"run_all.py --quick --out /tmp/thehro_quick --cache {self.cache}")
                return
            self.state, self.error, self.started = "loading", None, time.time()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            testkit.get_baseline(self.cache)          # asserts it reproduces Round 1 (679/720, 70 false pauses)
            self.state = "ready"
        except BaseException as exc:  # noqa: BLE001  (get_baseline exits on a missing cache)
            self.state, self.error = "error", f"{exc.__class__.__name__}: {exc}"

    def view(self) -> dict:
        v = {"state": self.state, "error": self.error}
        if self.state == "loading":
            v["seconds"] = round(time.time() - self.started)
        return v

    def spot_check(self, seed: int, journeys: int | None) -> dict:
        if self.state != "ready":
            self.start()
            return {"status": self.state, "baseline": self.view()}
        with self.run_lock:
            res = testkit.spot_check(seed, journeys=journeys, base=testkit.get_baseline(self.cache))
        return {"status": "ran", **testkit.jsonable(res)}


class Bank:
    def __init__(self, live: LiveWorld, decisions, customers: list[int], lines: dict):
        self.live, self.decisions, self.lines = live, decisions, lines
        self.customers = customers
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self.decisions_by_id: dict[str, dict] = {}
        self.cases: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.device = {"device_id": None, "source": None, "updated_at": None, "flags": {}, "call_started": None}
        self.ids = {k: itertools.count(1) for k in ("S", "D", "H", "B")}
        self.kit = KitBaseline(testkit.DEFAULT_CACHE)
        self.reported: dict[int, dict] = {}   # accounts a bank officer confirmed as scams: flagged for every customer

    def device_view(self) -> dict:
        """The demo phone's latest yes/no flags. An automation app can only say a call started or ended, so
        the call length is timed here from those events, never from the number or the audio."""
        d = {k: v for k, v in self.device.items() if k != "call_started"}
        flags = dict(d["flags"])
        if flags.get("on_call") and "call_min" not in flags and self.device["call_started"]:
            flags["call_min"] = round((time.time() - self.device["call_started"]) / 60, 1)
        d["flags"] = flags
        return d

    def set_device(self, body: dict) -> dict:
        raw = body.get("flags") or {}
        flags = {k: _flag_value(v) for k, v in raw.items() if k in CONTEXT_KEYS}   # "0", "true", 1 ... all work
        flags = {k: v for k, v in flags.items() if v is not None}
        on_call = bool(flags.get("on_call"))
        if on_call and not self.device["flags"].get("on_call"):
            self.device["call_started"] = time.time()
        elif not on_call:
            self.device["call_started"] = None
        self.device.update(device_id=body.get("device_id", "phone-A"), source=body.get("source", "phone"),
                           updated_at=time.strftime("%H:%M:%S"), flags=flags)
        return {"ok": True, "kept": sorted(flags), "dropped": sorted(set(raw) - set(flags))}

    def new_id(self, kind: str) -> str:
        return f"{kind}-{next(self.ids[kind]):04d}"

    # ------------------------------------------------------------------ options and sessions
    def options(self) -> dict:
        custs = []
        for c in self.customers:
            p = self.live.customer_profile(c)
            p["label"] = f"{p['segment'].title()}, {p['age_band']}, {p['region']}"
            p["usual_payment_text"] = inr(p["usual_payment"])
            custs.append(p)
        return {"now": {"t_min": self.live.now, "label": when(self.live.now)}, "customers": custs,
                "payee_types": [{"id": k, "label": PAYEE_LABELS[k], "scored": k != "shop"} for k in JUDGE_PAYEE_TYPES],
                "channels": [c for c in CHANNELS if c != "UPI to merchant"], "history_kinds": HISTORY_KINDS,
                "context_fields": CONTEXT_FIELDS, "languages": ["en", "hi"]}

    def session(self, session_id: str | None = None) -> Session:
        with self.lock:
            if session_id and session_id in self.sessions:
                return self.sessions[session_id]
            s = Session(id=session_id or self.new_id("S"), clock=self.live.now)
            self.sessions[s.id] = s
            return s

    def session_view(self, s: Session) -> dict:
        return {"session_id": s.id, "now": {"t_min": s.clock, "label": when(s.clock)},
                "history": [{"t": when(r["t"]), "kind": r["_label"], "amount": r["amount"]} for r in s.rows],
                "holds": [c["case_id"] for c in self.cases.values() if c["session_id"] == s.id]}

    def reset(self, session_id: str) -> dict:
        with self.lock:
            s = self.session(session_id)
            s.clock, s.rows, s.fd_t, s.used_payees, s.n_cases = self.live.now, [], [], set(), 0
            s.generation += 1
            return self.session_view(s)

    # ------------------------------------------------------------------ a case
    def case(self, body: dict) -> dict:
        with self.lock:
            return self._case(body)

    def _case(self, body: dict) -> dict:
        t_start = time.perf_counter()
        live = self.live
        s = self.session(body.get("session_id"))
        cust = parse_customer(body.get("customer", self.customers[0]))
        if not 0 <= cust < live.world.cfg.n_users:
            raise HTTPException(422, f"C{cust} is not a customer of this bank")
        payee_type = body.get("payee_type", "new_individual")
        same = None                                   # "pay that same account again" (e.g. the next step to a mule)
        if body.get("same_payee_as"):
            prev = self.decisions_by_id.get(body["same_payee_as"])
            if prev is None or prev["row"] is None or prev["session_id"] != s.id:
                raise HTTPException(422, "same_payee_as must be an earlier decision in this session, not a shop "
                                         "payment")
            same, payee_type = int(prev["row"]["payee"]), prev["out"]["payee"]["type"]
        if payee_type not in JUDGE_PAYEE_TYPES:
            raise HTTPException(422, f"payee_type must be one of {list(JUDGE_PAYEE_TYPES)}")
        if payee_type == "bank_reported" and same is None:
            fresh = sorted((p for p in self.reported if p not in s.used_payees), key=lambda p: -self.reported[p]["wall"])
            if not fresh:
                raise HTTPException(422, "no account has been reported yet: cancel a hold as a scam in the officer "
                                         "console first" if not self.reported else
                                    "every reported account is already used in this session: reset it or report another")
            same = fresh[0]
        amount = float(body.get("amount", 0))
        if amount <= 0:
            raise HTTPException(422, "amount must be positive")
        channel = body.get("channel") or "UPI to person"
        lang = "hi" if body.get("lang") == "hi" else "en"
        consent = bool(body.get("consent", live.customer_profile(cust)["consent"]))
        if s.n_cases:                                 # each new case happens a few minutes after the last
            s.clock += float(body.get("minutes_later", 5))
        s.n_cases += 1
        t = s.clock
        decision_id = self.new_id("D")

        # what the phone sent: phone flags only with consent, and only the known yes/no fields
        raw = (dict(self.device_view()["flags"]) if body.get("context_source") == "device"
               else dict(body.get("context") or {}))
        sent = {"customer": f"C{cust}", "payee_type": payee_type, "amount": amount, "channel": channel,
                "consent": consent}
        dropped = []
        context = {}
        if consent:
            context = {k: float(raw[k]) for k in CONTEXT_KEYS if raw.get(k) is not None and k not in CALLER_ONLY}
            dropped = sorted(k for k in raw if k not in CONTEXT_KEYS or k in CALLER_ONLY)
            sent["context"] = context
        else:
            dropped = sorted(raw)

        # earlier events the judge added become part of this session's history
        for h in body.get("history") or []:
            self._add_history(s, cust, h, t)

        if payee_type == "shop":
            payee = live.pick_payee(cust, "shop", int(body.get("payee_skip", 0)))
            return self._finish(s, decision_id, cust, payee, payee_type, amount, channel, t, sent, dropped,
                                tier=0, phone={}, bank_view={"tier": 0, "tier_name": TIER_NAMES[0],
                                                             "fast_path": "merchant payment: not scored"},
                                features=None, t_start=t_start, lang=lang)
        payee = same if same is not None else live.pick_payee(cust, payee_type, int(body.get("payee_skip", 0)),
                                                              exclude=s.used_payees)
        row = {"t": t, "payer": cust, "payee": payee, "amount": amount, "channel": live.channel_code(channel),
               "kind": PAYEE_KIND.get(payee_type, KIND["new_individual"])}
        row.update({k: context.get(k, 0) for k in CONTEXT_KEYS})
        new = live.payment_rows([{k: v for k, v in r.items() if not k.startswith("_")} for r in s.rows] + [row])
        f = live.slice_features(cust, payee, live.now, new, s.fd_t, consent=consent)
        feat_row = f.loc[f.pid.values == int(new.pid.iloc[-1])].iloc[0]
        t_feat = time.perf_counter()
        feats = {c: (None if np.isnan(float(feat_row[c])) else float(feat_row[c])) for c in ALL_FEATURES}
        feeds_down = bool(body.get("feeds_down"))          # drill: the outside feeds didn't answer in time
        if feeds_down:
            for k in FEED_FEATURES:
                feats[k] = None                             # unknown, not "clean": the model treats it as missing
        reported = self.reported.get(int(payee))
        if reported is not None:                            # this bank's own confirmed report, never waits on a feed
            feats["payee_registry"] = 1.0
        payload = {"payment": {"payment_id": decision_id, "customer_ref": f"{s.memory_key}:C{cust}",
                               "payee_ref": f"A{payee}", "amount": amount, "channel": channel, "time": when(t),
                               "t_min": t},
                   "features": feats, "consent": consent, "lang": lang}
        try:
            if body.get("service_down"):                 # drill: as if the decision service didn't answer
                raise TimeoutError("decision service did not answer (drill)")
            d = self.decisions.score(payload)
        except Exception as exc:                     # slow or down: the bank's own rules, nothing held
            return self._finish(s, decision_id, cust, payee, payee_type, amount, channel, t, sent, dropped,
                                tier=0, phone={}, bank_view={"tier": 0, "tier_name": TIER_NAMES[0], "fallback":
                                "decision service unavailable: the bank's own rules applied and nothing was held",
                                "error": type(exc).__name__}, features=feats, t_start=t_start, t_feat=t_feat,
                                lang=lang, row=row, payee_info=feat_row)
        guess = None
        stored = {"kind": "none", "note": "Nothing stored beyond the bank's normal payment ledger; phone flags "
                                          "discarded.", "not_kept": NOT_KEPT}
        if d.get("evidence_seq") is not None:
            ev = self.decisions.evidence(d["evidence_seq"])
            rec = next(r for r in ev["records"] if r["seq"] == d["evidence_seq"])
            guess = rec["body"].get("workflow_guess")
            stored = {"kind": "evidence", "seq": rec["seq"], "record": rec, "chain_ok": ev["chain_ok"],
                      "not_kept": NOT_KEPT}
        if reported is not None:                     # say so first: it is the strongest fact there is
            mins = max(1, round((time.time() - reported["wall"]) / 60))
            d["reasons"] = [{"code": "BANK_REPORTED", "family": "payee", "weight": None,
                             "en": f"A bank fraud officer confirmed {mins} minute{'s' if mins > 1 else ''} ago that "
                                   "this account was used in a scam",
                             "hi": f"बैंक के धोखाधड़ी अधिकारी ने {mins} मिनट पहले पुष्टि की कि इस खाते का इस्तेमाल "
                                   "धोखाधड़ी में हुआ था"}] + [r for r in d["reasons"] if r.get("code") != "REGISTRY"][:2]
        bank_view = {"tier": d["tier"], "tier_name": d["tier_name"], "scores": d["scores"],
                     "lines": self.lines["tiers"], "family_lines": self.lines["family"], "families": d["families"],
                     "gate": d["gate"], "payee_flag": d["payee_flag"], "reasons": d["reasons"],
                     "advice": d["advice"], "memory_note": d["memory_note"], "memory": d.get("memory"),
                     "workflow_guess": guess,
                     "fallback": None, "fast_path": None, "decision_ms": d.get("latency_ms"),
                     "feeds_down": ("the outside feeds (I4C registry, DoT FRI) didn't answer in time: decided without "
                                    "them. A hold still needs an outside flag, so only the bank's own mule-network "
                                    "flag could lead to one; the journey and phone signals still counted")
                     if feeds_down else None,
                     "bank_reported": ({"case_id": reported["case_id"], "minutes_ago": mins}
                                       if reported is not None else None)}
        return self._finish(s, decision_id, cust, payee, payee_type, amount, channel, t, sent, dropped,
                            tier=d["tier"], phone=d["message"], bank_view=bank_view, features=feats, t_start=t_start,
                            t_feat=t_feat, lang=lang, row=row, payee_info=feat_row, stored=stored,
                            evidence_seq=d.get("evidence_seq"))

    def _finish(self, s, decision_id, cust, payee, payee_type, amount, channel, t, sent, dropped, *, tier, phone,
                bank_view, features, t_start, lang, t_feat=None, row=None, payee_info=None, stored=None,
                evidence_seq=None) -> dict:
        state = {0: "pin", 1: "awaiting_customer", 2: "awaiting_customer", 3: "awaiting_customer", 4: "held"}[tier]
        now_wall = time.time()
        case_id = None
        if tier == 4:
            case_id = self.new_id("H")
            self._open_case(case_id, "hold", SLA_MINUTES, decision_id, s.id, now_wall, t)
        bank_view["timing_ms"] = {"total": round((time.perf_counter() - t_start) * 1000, 1)}
        if features is not None:                      # the facts behind the family cards (bank side only)
            bank_view["features"] = {k: (None if k in CALLER_FACTS else features.get(k)) for k in DISPLAY_FEATURES}
            bank_view["caller_id"] = POLICY.caller_id
        if t_feat is not None:
            bank_view["timing_ms"]["features"] = round((t_feat - t_start) * 1000, 1)
        if row is not None:
            row = dict(row, _label=PAYEE_LABELS[payee_type])
        reasons, advice = bank_view.get("reasons") or [], bank_view.get("advice") or "generic"
        trusted = self.live.customer_profile(cust)["trusted_contact"]
        phone_by_lang = {lg: (build_message(tier, reasons, advice, lg, trusted_contact=trusted)
                              if not bank_view.get("fallback") else {}) for lg in ("en", "hi")}
        if phone:
            phone = phone_by_lang[lang]
        out = {
            "decision_id": decision_id, "session_id": s.id, "state": state,
            "payment": {"customer": f"C{cust}", "amount": amount, "amount_text": inr(amount),
                        "channel": channel, "time": when(t)},
            "payee": {"ref": mask(payee) if payee_type != "shop" else f"Shop ••{(int(payee) * 7919 + 4321) % 10000:04d}",
                      "type": payee_type,
                      "facts": payee_facts(payee_info) if payee_info is not None else ["registered merchant"],
                      "truth": self.live.account_truth(payee)},
            "phone": phone, "phone_by_lang": phone_by_lang,
            "phone_meta": {"lang": lang, "continue_after_s": PAUSE_SECONDS if tier == 2 else None,
                           "case_id": case_id},
            "bank_view": bank_view,
            "sent_from_phone": sent, "dropped_from_phone": dropped,
            "stored": stored or {"kind": "none", "note": "Nothing stored beyond the bank's normal payment ledger.",
                                 "not_kept": NOT_KEPT},
        }
        self.decisions_by_id[decision_id] = {"out": out, "state": state, "tier": tier, "session_id": s.id,
                                             "row": row, "t": t, "evidence_seq": evidence_seq,
                                             "decided_wall": now_wall, "case_id": case_id, "features": features,
                                             "trusted_contact": trusted}
        if payee_type != "shop":
            s.used_payees.add(int(payee))
        s.last_decision = decision_id
        return out

    def _add_history(self, s: Session, cust: int, h: dict, t: float) -> None:
        live = self.live
        kind = h.get("kind")
        at = t - float(h.get("hours_ago", 2)) * 60
        amount = float(h.get("amount", 0) or 0)
        if kind == "fd_break":
            s.fd_t.append(at)
            return
        if kind == "new_payee_payment":
            payee = live.pick_payee(cust, "new_individual", len(s.used_payees) + 7, exclude=s.used_payees)
            s.used_payees.add(payee)
            s.rows.append({"t": at, "payer": cust, "payee": payee, "amount": amount,
                           "channel": live.channel_code("UPI to person"), "kind": KIND["new_individual"],
                           "_label": "Paid a new payee"})
        elif kind == "stranger_credit":
            payer = live.pick_payee(cust, "new_individual", len(s.used_payees) + 31, exclude=s.used_payees)
            s.rows.append({"t": at, "payer": payer, "payee": cust, "amount": amount or 500.0,
                           "channel": live.channel_code("UPI to person"), "kind": KIND["stranger_credit"],
                           "_label": "Credit from an unknown account"})
        elif kind == "sent_today":
            payee = live.pick_payee(cust, "saved_contact")
            s.rows.append({"t": at, "payer": cust, "payee": payee, "amount": amount,
                           "channel": live.channel_code("UPI to person"), "kind": KIND["p2p_known"],
                           "_label": "Sent to a saved contact"})
        else:
            raise HTTPException(422, f"history kind must be one of {[k['id'] for k in HISTORY_KINDS]}")
        s.rows.sort(key=lambda r: r["t"])

    # ------------------------------------------------------------------ the customer's choices
    def action(self, body: dict) -> dict:
        with self.lock:
            did, action = body.get("decision_id"), body.get("action")
            d = self.decisions_by_id.get(did)
            if d is None:
                raise HTTPException(404, "unknown decision_id")
            allowed = allowed_actions(d)
            if action not in allowed:
                raise HTTPException(409, f"in state '{d['state']}' the actions are {sorted(allowed)}")
            note = None
            if action in ("continue", "this_was_me") and d["tier"] == 2 and not body.get("skip_wait"):
                waited = time.time() - d["decided_wall"]
                if waited < PAUSE_SECONDS:
                    raise HTTPException(409, f"Continue unlocks after {PAUSE_SECONDS} s; "
                                             f"{PAUSE_SECONDS - int(waited)} s left")
            if action in ("continue", "this_was_me") and d["state"] == "awaiting_customer":
                self._outcome(d, action)             # "this_was_me" is logged as the customer's own statement
                d["state"], note = "pin", "Enter your UPI PIN to pay."
                if action == "this_was_me":
                    note = "Noted: you said this payment is yours. Enter your UPI PIN to pay."
            elif action == "this_was_me":                    # on a hold: the officer hears it before calling
                self._outcome(d, "appeal")
                self.cases[d["case_id"]]["customer_note"] = "customer says this payment is genuine"
                d["state"], note = "review_requested", ("A bank officer will call your registered number within "
                                                        f"{SLA_MINUTES} minutes to check this with you.")
            elif action == "cancel":
                self._outcome(d, "cancel")
                d["state"], note = "cancelled", "Payment cancelled. No money left your account."
                if d["case_id"]:
                    self.cases[d["case_id"]].update(status="closed", outcome="cancelled by the customer")
            elif action == "call_bank":
                self._outcome(d, "call_bank")
                d["state"], note = "call_bank", ("Call your bank on the number printed on your card or saved in "
                                                 "this app, never a number someone gave you.")
            elif action == "trusted":
                d["state"], note = "trusted_pending", "Request sent to your trusted person."
            elif action == "callback":                       # no trusted person: a bank officer calls them back
                cid = self.new_id("B")
                self._open_case(cid, "callback", CALLBACK_SLA_MINUTES, did, d["session_id"], time.time(), d["t"])
                d["case_id"], d["state"] = cid, "callback_pending"
                note = (f"Your bank will call your registered number within {CALLBACK_SLA_MINUTES} minutes. They will "
                        "never ask for your PIN or OTP.")
                return {"decision_id": did, "action": action, "state": d["state"], "note": note, "case_id": cid}
            elif action == "trusted_ok":
                self._outcome(d, "trusted_ok")
                d["state"], note = "pin", "Your trusted person approved. Enter your UPI PIN to pay."
            elif action == "trusted_declined":
                self._outcome(d, "cancel")
                d["state"], note = "cancelled", "Your trusted person declined. Payment cancelled."
            elif action == "wait":
                self._outcome(d, "wait")
                c = self.cases.get(d["case_id"]) if d["case_id"] else None
                if c is not None and c["status"] == "open":       # chose the hour over the call-back
                    c.update(status="closed", outcome="customer chose to wait the hour instead")
                d["state"], note = "pin", "One hour has passed (skipped in the demo). Enter your UPI PIN to pay."
            elif action == "confirm_pin":
                d["state"], note = "paid", "Paid."
                s = self.sessions[d["session_id"]]
                if d["row"] is not None:
                    s.rows.append(d["row"])                  # it now counts as history for later payments
                    s.rows.sort(key=lambda r: r["t"])
                past = round(time.time() - d["decided_wall"]) if d["tier"] >= 1 else 0
                return {"decision_id": did, "action": action, "state": "paid", "note": note,
                        "utr": f"SIM{abs(hash(did)) % 10**12:012d}", "history_added": d["row"] is not None,
                        "alert_tier": d["tier"], "time_to_get_past_s": past}
            return {"decision_id": did, "action": action, "state": d["state"], "note": note}

    def latest(self, session_id: str) -> dict:
        """The session's newest payment with its current state: what a judge's own phone shows (?phone=1)."""
        with self.lock:
            self._tick()
            s = self.sessions.get(session_id)
            if s is None:
                raise HTTPException(404, "unknown session")
            if s.last_decision is None:
                return {"session_id": session_id, "decision": None}
            d = self.decisions_by_id[s.last_decision]
            c = self.cases.get(d["case_id"]) if d["case_id"] else None
            out = d["out"]
            phone_safe = {k: out[k] for k in PHONE_FIELDS if k in out}       # never scores, never the bank view
            phone_safe["payee"] = {"ref": out["payee"]["ref"], "type": out["payee"]["type"]}
            return {"session_id": session_id, "decision": {**phone_safe, "state": d["state"]},
                    "age_s": round(time.time() - d["decided_wall"], 1),
                    "notifications": list(d.get("notifications", [])),
                    "case": {k: c[k] for k in ("case_id", "status", "outcome")} if c else None}

    def decision_view(self, did: str) -> dict:
        with self.lock:
            self._tick()
            d = self.decisions_by_id.get(did)
            if d is None:
                raise HTTPException(404, "unknown decision_id")
            c = self.cases.get(d["case_id"]) if d["case_id"] else None
            return {"decision_id": did, "state": d["state"], "tier": d["tier"],
                    "case": {k: c[k] for k in ("case_id", "status", "outcome")} if c else None,
                    "notifications": list(d.get("notifications", []))}

    def _notify(self, d: dict, text: str) -> None:
        d.setdefault("notifications", []).append({"at": time.strftime("%H:%M:%S"), "sms": text})

    def _outcome(self, d: dict, action: str) -> None:
        if d["evidence_seq"] is not None:
            try:
                self.decisions.outcome(d["evidence_seq"], action, d["t"])
            except Exception:
                pass                                      # the customer's choice still stands at the bank

    # ------------------------------------------------------------------ the officer
    def _open_case(self, case_id, kind, sla, decision_id, session_id, now_wall, t) -> None:
        self.cases[case_id] = {"case_id": case_id, "kind": kind, "sla_minutes": sla, "decision_id": decision_id,
                               "session_id": session_id, "status": "open", "opened_wall": now_wall,
                               "opened_at": when(t), "customer_note": None, "outcome": None}

    def _tick(self) -> None:
        now = time.time()
        for c in self.cases.values():
            if c["status"] == "open" and now - c["opened_wall"] >= c["sla_minutes"] * 60:
                if c["kind"] == "callback":
                    self._close(c, "not_reached", f"not reached within {c['sla_minutes']} minutes: back to the "
                                                  "customer (wait the hour, or cancel)")
                    self._notify(self.decisions_by_id[c["decision_id"]], "We couldn't reach you. You can wait an hour "
                                                                         "and then pay, or cancel the payment.")
                else:
                    self._close(c, "auto_cancelled", "no officer action within 30 minutes: cancelled, money stays")

    def _close(self, c: dict, state: str, outcome: str) -> None:
        c.update(status="closed", outcome=outcome)
        d = self.decisions_by_id[c["decision_id"]]
        d["state"] = {"released": "pin", "not_reached": "awaiting_customer"}.get(state, "cancelled")

    def cases_view(self) -> dict:
        with self.lock:
            self._tick()
            out = []
            for c in sorted(self.cases.values(), key=lambda c: (c["status"] != "open", c["opened_wall"])):
                d = self.decisions_by_id[c["decision_id"]]["out"]
                left = c["sla_minutes"] - (time.time() - c["opened_wall"]) / 60
                out.append({"case_id": c["case_id"], "kind": c["kind"], "decision_id": c["decision_id"],
                            "status": c["status"],
                            "outcome": c["outcome"], "opened_at": c["opened_at"],
                            "minutes_left": max(0, round(left, 1)) if c["status"] == "open" else None,
                            "amount": d["payment"]["amount"], "amount_text": d["payment"]["amount_text"],
                            "customer": d["payment"]["customer"], "payee": d["payee"]["ref"],
                            "top_reason": (d["bank_view"]["reasons"] or [{}])[0].get("en"),
                            "families": [k for k, v in d["bank_view"]["families"].items() if v],
                            "customer_note": c["customer_note"]})
            return {"now": {"label": time.strftime("%H:%M")}, "sla_minutes": SLA_MINUTES, "cases": out}

    def case_detail(self, case_id: str, user: str, role: str) -> dict:
        with self.lock:
            self._tick()
            c = self.cases.get(case_id)
            if c is None:
                raise HTTPException(404, "unknown case")
            self._log(user, role, "read", f"case {case_id}")
            dec = self.decisions_by_id[c["decision_id"]]
            d = dec["out"]
            cust = parse_customer(d["payment"]["customer"])
            prof = self.live.customer_profile(cust)
            group = self.live.network.group(int(dec["row"]["payee"]))
            ev = self.decisions.evidence(dec["evidence_seq"]) if dec["evidence_seq"] is not None else None
            bv = d["bank_view"]
            return {
                "case_id": case_id, "kind": c["kind"], "sla_minutes": c["sla_minutes"], "decision_id": c["decision_id"],
                "status": c["status"], "outcome": c["outcome"], "opened_at": c["opened_at"],
                "minutes_left": max(0, round(c["sla_minutes"] - (time.time() - c["opened_wall"]) / 60, 1)),
                "payment": d["payment"],
                "customer": {"ref": d["payment"]["customer"], "age_band": prof["age_band"], "lang": prof["lang"],
                             "registered_mobile": "+91 ••••• •" + f"{cust % 10000:04d}",
                             "trusted_contact": prof["trusted_contact"], "note": c["customer_note"]},
                "payee": {"ref": d["payee"]["ref"], "facts": d["payee"]["facts"],
                          "network": {"size": group["size"], "flagged_at": when(group["flagged_t"])
                                      if group["flagged_t"] is not None else None,
                                      "members": [mask(a) for a in group["members"][:12]]}},
                "why_held": {"summary": (f"{' + '.join(k for k, v in bv['families'].items() if v)} agree; outside "
                                         "flag on the payee") if c["kind"] == "hold" else
                             ("trusted check, and the customer has no trusted person set up, so the bank calls "
                              "instead"), "reasons": bv["reasons"], "families": bv["families"],
                             "scores": bv["scores"], "lines": bv["lines"], "family_lines": bv["family_lines"]},
                "model_guess": {"scam_type": bv.get("workflow_guess"),
                                "note": "for the officer only; never shown to the customer"},
                "evidence": ev,
                "call_script": ["Call the registered number, never a number from the payment.",
                                "Ask who asked for this payment and how they first got in touch.",
                                "Explain the warning signs above in plain words.",
                                "Never ask for the PIN, OTP or card details."],
                "actions": [{"id": "release_confirm", "label": "Genuine: release and confirm the payee"},
                            {"id": "cancel_scam", "label": "Scam: cancel, draft the 1930 complaint, report the mule"},
                            {"id": "no_answer", "label": "No answer: cancel at the deadline" if c["kind"] == "hold"
                             else "Couldn't reach them: back to the customer (they can wait the hour)"}],
            }

    def officer_action(self, case_id: str, body: dict) -> dict:
        with self.lock:
            self._tick()
            c = self.cases.get(case_id)
            if c is None:
                raise HTTPException(404, "unknown case")
            if c["status"] != "open":
                raise HTTPException(409, f"case is already closed: {c['outcome']}")
            action, user = body.get("action"), body.get("user", "officer.demo")
            dec = self.decisions_by_id[c["decision_id"]]
            d = dec["out"]
            self._log(user, body.get("role", "officer"), action, f"case {case_id}")
            if action == "cancel_scam":
                res = self.decisions.outcome(dec["evidence_seq"], "scam", dec["t"])
                self._close(c, "cancelled_scam", "scam confirmed by the officer: cancelled")
                self.reported[int(dec["row"]["payee"])] = {"wall": time.time(), "case_id": case_id}
                group = self.live.network.group(int(dec["row"]["payee"]))
                sms = (f"Your payment of {d['payment']['amount_text']} was stopped. It looked like a scam. No money "
                       "left your account. To report it, call 1930.")
                self._notify(dec, sms)
                return {"case_id": case_id, "state": "cancelled_scam", "ncrp_draft": res.get("ncrp_draft"),
                        "chain_ok": res.get("chain_ok"),
                        "mule_report": {"payee": d["payee"]["ref"], "network_size": group["size"],
                                        "sent_to": "NPCI / I4C feeds (simulated)",
                                        "flagged_for_all_customers": True,
                                        "note": "Flagged for every customer of this bank now: any later payment to "
                                                "this account starts with this report."},
                        "customer_sms": sms}
            if action == "release_confirm":
                self._outcome(dec, "release")        # an officer checked it: this payee is confirmed for them
                self._close(c, "released", "genuine: released by the officer")
                sms = (f"Your payment of {d['payment']['amount_text']} was checked and released. Enter your PIN in "
                       "the app to finish.")
                self._notify(dec, sms)
                return {"case_id": case_id, "state": "released", "payee_confirmed": True, "customer_sms": sms,
                        "note": "This payee is now confirmed for this customer: later payments to it go through "
                                "unless a new outside flag appears."}
            if action == "no_answer" and c["kind"] == "callback":
                self._close(c, "not_reached", "not reached: back to the customer (wait the hour, or cancel)")
                self._notify(dec, "We couldn't reach you. You can wait an hour and then pay, or cancel the payment.")
                return {"case_id": case_id, "state": "not_reached",
                        "note": "Not reached: the payment is back with the customer, who can wait an hour and then "
                                "pay, or cancel. Nothing was cancelled for them."}
            if action == "no_answer":
                self._outcome(dec, "cancel")         # didn't go ahead: retry memory starts
                self._close(c, "auto_cancelled", "no answer: cancelled at the deadline, money stays")
                self._notify(dec, f"We couldn't reach you, so your payment of {d['payment']['amount_text']} was "
                                  "cancelled. No money left your account.")
                return {"case_id": case_id, "state": "auto_cancelled",
                        "note": "No answer within 30 minutes: the payment was cancelled and the money stayed in "
                                "the account."}
            raise HTTPException(422, "action must be one of release_confirm, cancel_scam, no_answer")

    def _log(self, user: str, role: str, action: str, what: str) -> None:
        self.audit.append({"at": time.strftime("%H:%M:%S"), "user": user, "role": role, "action": action,
                           "what": what})

    # ------------------------------------------------------------------ test kit
    def kit_cases(self) -> dict:
        files = sorted(KIT.glob("*.json")) if KIT.exists() else []
        unregistered = set(testkit.not_frozen(files))
        cases = [{**json.loads(p.read_text(encoding="utf-8")), "registered": p.name not in unregistered}
                 for p in files]
        return {"source": str(KIT), "cases": cases, "all_registered": not unregistered,
                "rule": "Hand-written cases are frozen before anything runs on them (test_kit/cases/FROZEN.sha256): a case "
                        "runs only if it is on that list and unchanged."}

    def kit_run(self, body: dict) -> dict:
        case = body.get("case")
        if case is None:
            case = next((c for c in self.kit_cases()["cases"] if c.get("id") == body.get("case_id")), None)
            if case is None:
                raise HTTPException(404, "unknown kit case (or send the case itself in 'case')")
            if not case["registered"]:
                raise HTTPException(409, f"{case['id']} is not on the frozen list (or has changed since), so it "
                                         "is not pre-registered yet and can't run")
        s = self.session(None)
        out = self.case({**case, "session_id": s.id})
        cid = out["phone_meta"].get("case_id")
        if cid:                                       # a measurement, not a live hold: keep the officer queue clear
            with self.lock:
                self.cases[cid].update(status="closed", outcome="test kit case: closed automatically (not a live hold)")
        tier, expected = out["bank_view"]["tier"], case.get("expected")
        right = {"interrupt": tier >= 2, "no_interrupt": tier <= 1, "never_hold": tier < 4}.get(expected)
        return {"case_id": case.get("id"), "expected": expected, "got": {"tier": tier, "tier_name": TIER_NAMES[tier]},
                "right": right, "decision": out}


# ---------------------------------------------------------------------------- the app
def build_app(bank: Bank) -> FastAPI:
    app = FastAPI(title="Thehro bank server (judge mode)",
                  description="Live: a judge's case → features → decision service → phone, officer, evidence.")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    def health():
        return {"ok": True, "now": when(bank.live.now), "sessions": len(bank.sessions), "open_cases":
                sum(c["status"] == "open" for c in bank.cases.values())}

    @app.get("/judge/options")
    def judge_options():
        return bank.options()

    @app.get("/judge/session/{session_id}/latest")
    def judge_latest(session_id: str):
        return bank.latest(session_id)

    @app.get("/judge/lan")
    def judge_lan(request: Request):
        """This laptop's address on the Wi-Fi or hotspot, for the "open on a phone" link (no packet is sent)."""
        ip = "127.0.0.1"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.connect(("10.255.255.255", 1))
                ip = sk.getsockname()[0]
        except OSError:
            pass
        return {"base": f"http://{ip}:{request.url.port or 80}"}

    @app.get("/judge/qr")
    def judge_qr(data: str):
        """A QR code (SVG) for the phone link. Needs the optional segno package; without it the page shows the link."""
        try:
            import segno
        except ImportError:
            raise HTTPException(404, "QR codes need the segno package (pip install segno)") from None
        if len(data) > 300:
            raise HTTPException(422, "too long for a link")
        buf = io.BytesIO()                            # a full SVG file (with its namespace), so <img> accepts it
        segno.make(data, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111", light="#fff")
        return Response(buf.getvalue(), media_type="image/svg+xml")

    @app.post("/judge/session")
    def judge_session():
        return bank.session_view(bank.session(None))

    @app.post("/judge/reset")
    def judge_reset(body: dict = Body(default={})):
        return bank.reset(body.get("session_id") or bank.session(None).id)

    @app.post("/judge/case")
    def judge_case(body: dict = Body(...)):
        try:
            return bank.case(body)
        except NoPayeeAvailable as exc:
            raise HTTPException(409, str(exc))

    @app.get("/judge/decisions/{decision_id}")
    def judge_decision(decision_id: str):
        return bank.decision_view(decision_id)

    @app.post("/judge/action")
    def judge_action(body: dict = Body(...)):
        return bank.action(body)

    @app.get("/officer/cases")
    def officer_cases():
        return bank.cases_view()

    @app.get("/officer/cases/{case_id}")
    def officer_case(case_id: str, user: str = "officer.demo", role: str = "officer"):
        return bank.case_detail(case_id, user, role)

    @app.post("/officer/cases/{case_id}/action")
    def officer_action(case_id: str, body: dict = Body(...)):
        return bank.officer_action(case_id, body)

    @app.get("/officer/audit")
    def officer_audit():
        return {"reads": list(bank.audit)}

    @app.get("/kit/cases")
    def kit_cases():
        return bank.kit_cases()

    @app.post("/kit/run")
    def kit_run(body: dict = Body(...)):
        return bank.kit_run(body)

    @app.get("/kit/status")
    def kit_status():
        files = sorted(KIT.glob("K1-*.json")) if KIT.exists() else []
        return {"spot_check": bank.kit.view(), "k1": {"cases": len(files),
                                                      "unregistered": len(testkit.not_frozen(files))},
                "report": REPORT.exists()}

    @app.post("/kit/spot_check")
    def kit_spot_check(body: dict = Body(default={})):
        """K2: random labelled draws from the test days, the judge picks the seed (kit.spot_check)."""
        try:
            seed = int(body.get("seed", 7))
            journeys = int(body["journeys"]) if body.get("journeys") else None
        except (TypeError, ValueError):
            raise HTTPException(422, "seed and journeys must be whole numbers") from None
        if not 0 <= seed < 2**32 or (journeys is not None and not 1 <= journeys <= 100):
            raise HTTPException(422, "seed must be 0 to 4294967295 and journeys 1 to 100")
        return bank.kit.spot_check(seed, journeys)

    @app.get("/kit/report")
    def kit_report():
        if not REPORT.exists():
            raise HTTPException(404, "no test_kit/report.md yet: run scripts/run_test_kit.py")
        return {"markdown": REPORT.read_text(encoding="utf-8"),
                "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(REPORT.stat().st_mtime))}

    @app.get("/device/flags")
    def device_flags():
        return bank.device_view()

    @app.post("/device/flags")
    def post_device_flags(body: dict = Body(...)):
        """From the automation app on the demo phone: yes/no flags only, worked out on the phone."""
        return bank.set_device(body)

    if STATIC.exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


JUDGE_MODE = Path(__file__).resolve().parent / "judge_mode.json"
# what a phone following a session may receive: the payment, the masked payee, the message and its own flags
PHONE_FIELDS = ("decision_id", "session_id", "payment", "phone", "phone_by_lang", "phone_meta", "sent_from_phone")


def demo_moment(live: LiveWorld) -> tuple[float, list[int]]:
    """The demo starts just before the digital-arrest payment on deck slide 5, with the ten demo customers. Both are
    fixed in bank/judge_mode.json, so they don't move when the model or the policy changes."""
    jm = json.loads(JUDGE_MODE.read_text(encoding="utf-8"))
    pid = int(jm["now"]["just_before_payment"][1:])
    now = float(live.world.payments.t.values[pid]) - float(jm["now"]["minutes_before"])
    return now, [int(c["id"][1:]) for c in jm["customers"]]


def thresholds_from_metrics() -> dict:
    thr = json.loads((ROOT / "outputs" / "metrics.json").read_text())["headline"]["thresholds"]
    return {"tiers": {"note": thr["t1"], "pause": thr["t2"], "hold": thr["t4"]}, "family": thr["family"]}


def create_app() -> FastAPI:
    """Factory for uvicorn --factory: builds (or loads the cached) world, then the app."""
    cache = os.environ.get("THEHRO_WORLD_CACHE", str(ROOT / ".cache" / "live_world.pkl"))
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    live = LiveWorld.build(cache=cache)
    now, customers = demo_moment(live)
    live.set_now(now)
    if os.environ.get("THEHRO_INPROCESS") == "1":
        from thehro.service import DecisionService, load_bundle
        decisions = InProcessDecisions(DecisionService(load_bundle(ROOT / "outputs" / "model_bundle.pkl")))
    else:
        decisions = DecisionClient(os.environ.get("THEHRO_API", "http://127.0.0.1:8000"),
                                   float(os.environ.get("THEHRO_TIMEOUT_S", "0.5")))
    return build_app(Bank(live, decisions, customers, thresholds_from_metrics()))
