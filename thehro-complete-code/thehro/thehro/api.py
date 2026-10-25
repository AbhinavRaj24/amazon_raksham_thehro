"""HTTP API for the decision service.

    uvicorn thehro.api:app --port 8000

POST /v1/score      payment + features in, decision out (tier, reasons, message, evidence id)
POST /v1/outcome    what the customer or reviewer did: cancel / continue / call_bank / trusted_ok / scam
GET  /v1/evidence/{seq}   the decision and its outcomes, plus a check of the hash chain
GET  /health
"""
from __future__ import annotations

import os
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .policy import CURRENT, RULES
from .service import DecisionService, load_bundle

BUNDLE = os.environ.get("THEHRO_BUNDLE", os.path.join(os.path.dirname(__file__), "..", "outputs", "model_bundle.pkl"))

app = FastAPI(title="Thehro decision API", version=__version__,
              description="Scores a payment before the PIN screen. Synthetic-data prototype.")
_service: Optional[DecisionService] = None


def service() -> DecisionService:
    global _service
    if _service is None:
        # THEHRO_POLICY=v1 runs Round 1's policy (v2: with retry memory), e.g. to show a gap live
        name = os.environ.get("THEHRO_POLICY", CURRENT.name)
        if name not in RULES:
            raise RuntimeError(f"THEHRO_POLICY must be one of {sorted(RULES)}")
        _service = DecisionService(load_bundle(BUNDLE), rules=RULES[name])
    return _service


class Payment(BaseModel):
    payment_id: str
    customer_ref: str = Field(description="pseudonymous customer id")
    payee_ref: str = Field(description="pseudonymous payee account or VPA")
    amount: float = Field(gt=0)
    channel: str = "UPI to person"
    time: Optional[str] = None
    t_min: float = Field(0.0, description="minutes since an epoch, used for 7-day memory")


class ScoreRequest(BaseModel):
    payment: Payment
    features: dict[str, Optional[float]]
    consent: bool = Field(False, description="customer opted in to on-device call/screen signals")
    lang: Literal["en", "hi"] = "en"


class OutcomeRequest(BaseModel):
    evidence_seq: int
    action: Literal["cancel", "continue", "call_bank", "trusted_ok", "wait", "scam", "this_was_me", "appeal",
                    "release", "trusted_declined"]
    t_min: Optional[float] = None


@app.get("/health")
def health():
    s = service()
    return {"ok": True, "versions": s.versions}


@app.post("/v1/score")
def score_payment(req: ScoreRequest):
    return service().decide(req.payment.model_dump(), req.features, req.consent, req.lang)


@app.post("/v1/outcome")
def outcome(req: OutcomeRequest):
    try:
        return service().outcome(req.evidence_seq, req.action, req.t_min)
    except KeyError:
        raise HTTPException(404, "unknown evidence_seq")


@app.get("/v1/evidence/{seq}")
def evidence(seq: int):
    s = service()
    recs = s.log.find(seq)
    if not recs:
        raise HTTPException(404, "unknown evidence_seq")
    return {"records": recs, "chain_ok": s.log.verify()}
