"""Mock bank server: canned answers on the real endpoint paths, so the pages can be built before bank/server.py.

    cd thehro-complete-code/thehro
    uvicorn bank.mock_server:app --host 0.0.0.0 --port 8080      # try it at http://127.0.0.1:8080/docs

Paths and shapes follow FINALE_PLAN.md §8. Answers come from bank/mock/*.json (built from the Round 1
outputs by bank/mock/make_mocks.py). Force any screen with "mock" in the request body or ?mock= in the URL,
for example {"mock": "t4_hold"}; GET /mock/list shows the choices. Pages in bank/static/ are served at /.
"""
from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

MOCK = Path(__file__).resolve().parent / "mock"
STATIC = Path(__file__).resolve().parent / "static"
CASE_MOCKS = sorted({p.name.split(".")[0].removeprefix("judge_case_") for p in MOCK.glob("judge_case_*.json")})
ACTIONS = sorted(p.stem.removeprefix("judge_action_") for p in MOCK.glob("judge_action_*.json"))
OFFICER_ACTIONS = sorted(p.stem.removeprefix("officer_action_") for p in MOCK.glob("officer_action_*.json"))
CONTEXT_KEYS = ("on_call", "caller_unknown", "call_ott", "call_min", "screen_share", "remote_app", "app_switches")

app = FastAPI(title="Thehro mock bank server", description="Canned answers for building the pages. Not the live system.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_ids = itertools.count(1)
_device = {"device_id": None, "source": None, "updated_at": None, "flags": {}}


def load(name: str) -> dict:
    path = MOCK / f"{name}.json"
    if not path.exists():
        raise HTTPException(404, f"no mock named {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def pick_case(body: dict) -> str:
    """Which canned decision a case gets. The live server decides for real; this only roughs it out."""
    payee_type = body.get("payee_type")
    amount = float(body.get("amount") or 0)
    if payee_type == "shop":
        return "t0_merchant"
    if payee_type in ("saved_contact", "regular_vendor", "new_landlord"):
        return "t0_allow"
    if payee_type in ("mule_network", "registry", "fri_high"):
        return "t4_hold" if amount >= 10_000 else "t2_pause"
    if payee_type == "mule_unflagged":
        return "t0_missed_scam" if amount < 5_000 else "t1_note"
    return "t3_trusted" if amount >= 50_000 else "t2_false_alarm"


@app.get("/mock/list")
def mock_list():
    return {"case_mocks": CASE_MOCKS, "judge_actions": ACTIONS, "officer_actions": OFFICER_ACTIONS,
            "rules": "payee_type shop → t0_merchant; saved_contact, regular_vendor, new_landlord → t0_allow; "
                     "mule_network, registry, fri_high → t4_hold (₹10,000+) else t2_pause; mule_unflagged → "
                     "t0_missed_scam (under ₹5,000) else t1_note; anything else → t3_trusted (₹50,000+) else "
                     "t2_false_alarm. Add \"mock\": \"<name>\" to force one."}


@app.get("/judge/options")
def judge_options():
    return load("judge_options")


@app.post("/judge/session")
@app.post("/judge/reset")
def judge_session():
    return load("judge_session")


@app.post("/judge/case")
def judge_case(body: dict = Body(...), mock: str | None = None):
    name = mock or body.get("mock") or pick_case(body)
    lang = "hi" if body.get("lang") == "hi" else "en"
    out = load(f"judge_case_{name}.{lang}")
    out["decision_id"] = f"D-{next(_ids):04d}"
    # echo what this phone actually sent, so the "what left the phone" pane reflects the request
    sent = {k: body.get(k) for k in ("customer", "payee_type", "amount", "channel")}
    sent["consent"] = bool(body.get("consent"))
    if sent["consent"] and body.get("context"):
        sent["context"] = {k: v for k, v in body["context"].items() if k in CONTEXT_KEYS}
    out["sent_from_phone"] = sent
    out["_mock_picked"] = name
    return out


@app.get("/judge/decisions/{decision_id}")
def judge_decision(decision_id: str, mock: str = "held"):
    """?mock=held (default), released or cancelled: what the page sees while a hold is reviewed."""
    out = load(f"judge_decision_{mock}")
    out["decision_id"] = decision_id
    return out


@app.post("/judge/action")
def judge_action(body: dict = Body(...)):
    action = body.get("action")
    if action not in ACTIONS:
        raise HTTPException(422, f"action must be one of {ACTIONS}")
    out = load(f"judge_action_{action}")
    out["decision_id"] = body.get("decision_id", out["decision_id"])
    return out


@app.get("/officer/cases")
def officer_cases():
    return load("officer_cases")


@app.get("/officer/cases/{case_id}")
def officer_case(case_id: str):
    out = load("officer_case_detail")
    out["case_id"] = case_id
    return out


@app.post("/officer/cases/{case_id}/action")
def officer_action(case_id: str, body: dict = Body(...)):
    action = body.get("action")
    if action not in OFFICER_ACTIONS:
        raise HTTPException(422, f"action must be one of {OFFICER_ACTIONS}")
    out = load(f"officer_action_{action}")
    out["case_id"] = case_id
    return out


@app.get("/officer/audit")
def officer_audit():
    return load("officer_audit")


@app.get("/kit/cases")
def kit_cases():
    return load("kit_cases")


@app.post("/kit/run")
def kit_run(body: dict = Body(...)):
    if body.get("case_id"):
        out = load("kit_run_k1")
        out["case_id"] = body["case_id"]
        return out
    out = load("kit_run_k2")
    out["seed"] = body.get("seed", out["seed"])
    return out


@app.get("/device/flags")
def device_flags():
    return _device if _device["updated_at"] else load("device_flags")


@app.post("/device/flags")
def post_device_flags(body: dict = Body(...)):
    """What the automation app on the demo phone sends: yes/no flags only, worked out on the phone."""
    flags = {k: v for k, v in (body.get("flags") or {}).items() if k in CONTEXT_KEYS}
    _device.update(device_id=body.get("device_id", "phone-A"), source=body.get("source", "phone"),
                   updated_at=body.get("at", "now"), flags=copy.deepcopy(flags))
    return {"ok": True, "kept": sorted(flags), "dropped": sorted(set(body.get("flags") or {}) - set(flags))}


if STATIC.exists():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
