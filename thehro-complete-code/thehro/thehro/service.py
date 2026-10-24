"""The decision service: one payment in, one decision out, before the PIN screen.

In production the three signal families arrive from three places:
  journey  the bank's own feature store (the customer's history)
  payee    NPCI / MuleHunter.AI / IDPIC risk on the receiving account, DoT FRI,
           the I4C Suspect Registry, and the live mule-network flag
  context  the bank app's SDK on the phone: yes/no flags only, sent only with consent
Here the caller passes all features in the request.
"""
from __future__ import annotations

import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .config import TIER_NAMES
from .explain import (EvidenceLog, advice_keys, build_message, decision_record, ncrp_draft, top_reasons,
                      workflow_guess)
from .features import ALL_FEATURES
from .models import WORKFLOW_CONFIDENCE_FLOOR, score
from .policy import PolicyEngine
from .simulate import CHANNELS


def load_bundle(path: str | Path) -> dict:
    with open(path, "rb") as fh:
        return pickle.load(fh)


class DecisionService:
    def __init__(self, bundle: dict):
        self.models = bundle["models"]
        self.thr = bundle["thresholds"]
        self.weights = bundle["reason_weights"]
        self.versions = bundle["versions"]
        self.policy = PolicyEngine(self.thr)
        self.log = EvidenceLog()
        self._pending: dict[int, dict] = {}

    def decide(self, payment: dict, features: dict, consent: bool, lang: str = "en") -> dict:
        t0 = time.perf_counter()
        row = {c: (np.nan if features.get(c) is None else float(features[c])) for c in ALL_FEATURES}
        if not consent:   # context signals are ignored entirely without consent
            for c in ("on_call", "caller_unknown", "call_ott", "call_min", "caller_fri_band", "screen_share",
                      "remote_app", "app_switches"):
                row[c] = np.nan
        X = pd.DataFrame([row])
        s = score(self.models, X).iloc[0].to_dict()
        feats = dict(row, amount=float(payment["amount"]), consent=bool(consent))
        dec = self.policy.decide(s, feats, str(payment["customer_ref"]), str(payment["payee_ref"]),
                                 float(payment.get("t_min", 0.0)))
        tier = dec["tier"]
        reasons = top_reasons(feats, self.weights, k=3) if tier >= 1 else []
        advice = advice_keys(pd.DataFrame([feats]))[0]
        guess = workflow_guess(s.get("wf_pred"), s.get("wf_conf"), WORKFLOW_CONFIDENCE_FLOOR)
        out = {
            "tier": tier, "tier_name": TIER_NAMES[tier],
            "families": dec["families"], "gate": dec["gate"], "payee_flag": dec["payee_flag"],
            "memory_note": dec["memory_note"],
            "scores": {k: round(float(v), 4) for k, v in s.items() if k.startswith("s_")},
            "reasons": reasons, "advice": advice,
            "message": build_message(tier, reasons, advice, lang),
            "evidence_seq": None,
        }
        if tier >= 2:
            rec = self.log.append("decision", decision_record(payment, s, dec, reasons, guess, self.versions))
            out["evidence_seq"] = rec["seq"]
            self._pending[rec["seq"]] = {"payment": payment, "tier": tier, "reasons": reasons, "guess": guess}
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return out

    def outcome(self, evidence_seq: int, action: str, t_min: float | None = None) -> dict:
        """Record what the customer (or the reviewer) did. 'scam' produces a complaint draft."""
        p = self._pending.get(evidence_seq)
        if p is None:
            raise KeyError(evidence_seq)
        pay = p["payment"]
        self.policy.record_outcome(str(pay["customer_ref"]), str(pay["payee_ref"]),
                                   float(t_min if t_min is not None else pay.get("t_min", 0.0)), p["tier"], action)
        rec = self.log.append("outcome", {"decision_seq": evidence_seq, "action": action})
        out = {"recorded": rec["seq"], "chain_ok": self.log.verify()}
        if action == "scam":
            ch = pay.get("channel")
            pay2 = dict(pay, channel=CHANNELS.index(ch) if ch in CHANNELS else ch)
            out["ncrp_draft"] = ncrp_draft(pay2, p["guess"], p["reasons"])
        return out
