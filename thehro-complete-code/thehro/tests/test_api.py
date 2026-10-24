"""The HTTP API, using the model bundle from scripts/run_all.py."""
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "outputs" / "model_bundle.pkl"
SAMPLES = ROOT / "outputs" / "sample_requests.json"
pytestmark = pytest.mark.skipif(not BUNDLE.exists(), reason="run scripts/run_all.py first")


@pytest.fixture(scope="module")
def client():
    os.environ["THEHRO_BUNDLE"] = str(BUNDLE)
    from fastapi.testclient import TestClient

    from thehro import api
    api._service = None
    return TestClient(api.app)


@pytest.fixture(scope="module")
def samples():
    return json.load(open(SAMPLES))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_score_returns_a_decision_with_reasons_and_evidence(client, samples):
    body = dict(samples[0], lang="en")
    r = client.post("/v1/score", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["tier"] in range(5)
    assert set(d["families"]) == {"journey", "payee", "context"}
    if d["tier"] >= 2:
        assert d["evidence_seq"] is not None and len(d["message"]["why"]) >= 1
        ev = client.get(f"/v1/evidence/{d['evidence_seq']}").json()
        assert ev["chain_ok"]
        out = client.post("/v1/outcome", json={"evidence_seq": d["evidence_seq"], "action": "scam"}).json()
        assert out["chain_ok"] and out["ncrp_draft"]["category"] == "Online Financial Fraud"
    assert d["latency_ms"] < 250


def test_context_is_ignored_without_consent(client, samples):
    body = dict(samples[0])
    body["features"] = dict(body["features"], screen_share=1.0, remote_app=1.0, on_call=1.0, caller_unknown=1.0)
    body["consent"] = False
    d = client.post("/v1/score", json=body).json()
    assert d["families"]["context"] is False


def test_bad_input_is_rejected(client, samples):
    body = dict(samples[0])
    body["payment"] = dict(body["payment"], amount=-5)
    assert client.post("/v1/score", json=body).status_code == 422
