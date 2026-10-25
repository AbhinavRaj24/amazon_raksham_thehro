"""L-1: run the hand-written, pre-registered K1 cases through the live bank server. Misses stay in.

    python scripts/k1_run.py                              # bank server at http://127.0.0.1:8080
    python scripts/k1_run.py --server http://192.168.1.5:8080

Each test_kit/cases/K1-*.json is a judge-mode case (FINALE_PLAN.md section 8: customer, payee_type, amount,
channel, history, consent, context, lang) plus "expected" (interrupt, no_interrupt or never_hold) and "why"
(format: scripts/check_cases.py). P4 commits them before anything runs on them; this script never edits them.

Pre-registration is enforced: test_kit/cases/FROZEN.sha256 lists every case as P4 delivered it, written before
any decision ran. If a case is missing from it or has changed since, nothing runs and the K1 section says why.

For each case it tries POST /kit/run {"case_id": ...}; if the server has no such route it opens a fresh session
(POST /judge/session) and sends the case to POST /judge/case. Right means:
  interrupt: tier 2+    no_interrupt: tier 0-1    never_hold: below tier 4

Writes test_kit/results/k1_results.json (always: "ran", or "not run" with the reason) and the K1 section.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import kit  # noqa: E402

CASE_KEYS = {"customer", "payee_type", "amount", "channel", "history", "consent", "context", "lang"}


def post(url: str, body: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tier_of(resp: dict) -> int | None:
    for path in (("got", "tier"), ("bank_view", "tier"), ("tier",), ("phone", "tier")):
        v = resp
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        if isinstance(v, (int, float)):
            return int(v)
    return None


def run_case(server: str, case_id: str, case: dict) -> dict:
    try:
        resp = post(f"{server}/kit/run", {"case_id": case_id})
        route = "/kit/run"
    except urllib.error.HTTPError as e:
        if e.code not in (404, 405):
            raise
        sess = post(f"{server}/judge/session", {})
        body = {k: v for k, v in case.items() if k in CASE_KEYS}
        body["session_id"] = sess.get("session_id")
        resp = post(f"{server}/judge/case", body)
        route = "/judge/case"
    return {"route": route, "response": resp, "tier": tier_of(resp)}


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8080")
    ap.add_argument("--cases", default=str(kit.KIT / "cases"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    files = sorted(Path(a.cases).glob("K1-*.json"))
    res: dict = {"server": a.server, "cases_folder": a.cases}
    unregistered = kit.not_frozen(files) if files else []
    if not files:
        res.update(status="not run", reason="no test_kit/cases/K1-*.json yet (P4 writes and commits them first)")
    elif unregistered:
        res.update(status="not run", reason=f"{len(unregistered)} of {len(files)} cases are not on the frozen list (or changed "
                                            "since), so they are not pre-registered; nothing ran")
    else:
        try:
            urllib.request.urlopen(a.server, timeout=3)
        except urllib.error.HTTPError:
            pass                                   # the server answered, just not at "/"
        except Exception as e:  # noqa: BLE001
            res.update(status="not run", reason=f"bank server not reachable at {a.server} ({e.__class__.__name__})")
    if "status" in res:
        print(f"K1 not run: {res['reason']}")
        path = kit.save_json(res, out / "k1_results.json")
        kit.write_section(out, "k1", f"_Not run: {res['reason']}._")
        return res
    _, frozen_at = kit.frozen_list(Path(a.cases))
    cases = []
    for f in files:
        case = json.load(open(f))
        cid = case.get("id") or case.get("case_id") or f.stem
        exp = case["expected"]
        try:
            r = run_case(a.server, cid, case)
            tier = r["tier"]
            right = None if tier is None else kit.is_right(exp, tier)
            resp = r["response"]
            view = (resp.get("decision") or resp).get("bank_view") or {}      # /kit/run wraps the judge-case answer
            reasons = view.get("reasons") or []
            cases.append({"case_id": cid, "file": f.name, "title": case.get("title", ""), "group": case.get("group"),
                          "truth": case.get("truth"), "expected": exp,
                          "why_expected": case.get("why") or case.get("reason", ""),
                          "got": {"tier": tier}, "right": right, "route": r["route"],
                          "reasons": [x.get("code") for x in reasons if isinstance(x, dict)],
                          "frozen_at": frozen_at})
        except Exception as e:  # noqa: BLE001
            cases.append({"case_id": cid, "file": f.name, "expected": exp, "got": None, "right": None,
                          "error": f"{e.__class__.__name__}: {e}"})
    done = [c for c in cases if c["right"] is not None]
    k = sum(c["right"] for c in done)
    res.update(status="ran", cases=cases, summary={"n": len(cases), "scored": len(done), "right": int(k),
                                                    "right_share": kit.share(k, len(done)),
                                                    "errors": len(cases) - len(done)})
    path = kit.save_json(res, out / "k1_results.json")
    rows = [[c["case_id"], c["expected"], "–" if not c.get("got") else c["got"]["tier"],
             "✓" if c["right"] else ("✗" if c["right"] is False else "error"), c.get("why_expected", ""),
             ", ".join(c.get("reasons") or []) or c.get("error", ""), c.get("frozen_at") or "–"]
            for c in cases]
    md = (f"**Measured, synthetic** (judge-mode cases on the live bank server). {k} of {len(done)} right "
          f"({kit.fmt_share(kit.share(k, len(done)))}); every miss stays in. Cases were written by P4, who did not build "
          "the model, and frozen with their expected outcome (`test_kit/cases/FROZEN.sha256`) before any run.\n\n"
          + kit.md_table(["Case", "Expected", "Got tier", "Right", "Why expected", "Reasons shown", "Frozen"], rows))
    sec = kit.write_section(out, "k1", md)
    kit.log_run(out, kit.new_run_id("k1"), None, [path, sec], command=kit.cmd(__file__, argv))
    print(f"K1: {k}/{len(done)} right, {len(cases) - len(done)} errors; saved {path}")
    return res


if __name__ == "__main__":
    main()
