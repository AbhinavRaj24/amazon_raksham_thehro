"""Load test: the decision service and the bank server under concurrent requests, timed from the client side.

    python scripts/loadtest.py                    # about 2 minutes; starts its own servers on free ports
    python scripts/loadtest.py --quick            # fewer requests

1. Decision service (`uvicorn thehro.api:app`, one process, as in the demo): real test-day payments (features from
   the kit cache) sent to POST /v1/score at several concurrency levels. Client-side latency (HTTP included) and
   the service's own in-process time.
2. Bank server in judge mode (`bank.server`, in-process decisions): POST /judge/case end to end, which also builds
   the 39 features live from the customer's and payee's events.
Not measured here: the phone's round trip over Wi-Fi or 4G (that needs the demo phone).
Writes test_kit/results/loadtest.json and the "latency" section of test_kit/report.md.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pickle
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thehro import evaluate as E  # noqa: E402
from thehro import kit  # noqa: E402
from thehro.features import ALL_FEATURES  # noqa: E402
from thehro.simulate import CHANNELS  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(args: list[str], port: int, env: dict, timeout: float = 180) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", *args, "--port", str(port), "--log-level", "warning"],
                            cwd=ROOT, env={**os.environ, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read().decode()[-2000:])
        time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"{args[0]} did not start in {timeout:.0f} s")


def payloads(cache: str, n: int, seed: int) -> list[dict]:
    world, f, denom = pickle.load(open(cache, "rb"))
    ft = f[E.windows(f)["test"]]
    rng = np.random.default_rng(seed)
    rows = ft.iloc[rng.choice(len(ft), size=n, replace=False)]
    out = []
    for i, (_, r) in enumerate(rows.iterrows()):
        out.append({"payment": {"payment_id": f"L{i}", "customer_ref": f"load:C{int(r.payer)}",
                                "payee_ref": f"A{int(r.payee)}", "amount": float(r.amount),
                                "channel": CHANNELS[int(r.channel)], "t_min": float(r.t)},
                    "features": {c: (None if pd.isna(r[c]) else float(r[c])) for c in ALL_FEATURES},
                    "consent": bool(r.consent)})
    return out


async def blast(url: str, bodies: list[dict], concurrency: int) -> dict:
    lat, served, errors, rejected = [], [], 0, 0
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=concurrency)) as client:
        async def one(b):
            nonlocal errors, rejected
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await client.post(url, json=b)
                except Exception:  # noqa: BLE001  (no answer at all)
                    errors += 1
                    return
                if 400 <= r.status_code < 500:           # the input was refused (e.g. no account of that type left)
                    rejected += 1
                elif r.status_code != 200:
                    errors += 1
                else:
                    lat.append((time.perf_counter() - t0) * 1000)
                    served.append(r.json().get("latency_ms"))
        T = time.perf_counter()
        await asyncio.gather(*(one(b) for b in bodies))
        wall = time.perf_counter() - T
    lat = np.array(lat)
    srv = np.array([x for x in served if x is not None], dtype=float)
    q = lambda a, p: float(np.percentile(a, p)) if len(a) else float("nan")  # noqa: E731
    return {"concurrency": concurrency, "requests": len(bodies), "errors": errors, "rejected_inputs": rejected,
            "throughput_per_s": len(lat) / wall, "p50_ms": q(lat, 50), "p95_ms": q(lat, 95), "p99_ms": q(lat, 99),
            "max_ms": float(lat.max()) if len(lat) else float("nan"),
            "service_p50_ms": q(srv, 50), "service_p99_ms": q(srv, 99)}


def judge_bodies(n: int, seed: int, customers: list[str]) -> list[dict]:
    rng = np.random.default_rng(seed)
    types = ["new_individual", "saved_contact", "regular_vendor", "new_account", "mule_network", "registry",
             "new_landlord", "dormant"]
    out = []
    for i in range(n):
        out.append({"customer": str(rng.choice(customers)), "payee_type": str(rng.choice(types)),
                    "payee_skip": int(rng.integers(0, 20)), "amount": float(rng.choice([999, 4_500, 15_693, 60_000])),
                    "consent": bool(rng.random() < 0.7), "context": {"on_call": int(rng.random() < 0.4), "call_min": 12}})
    return out


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=kit.DEFAULT_CACHE)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    out = kit.results_dir(a.out)
    n = 600 if a.quick else 2000
    bodies = payloads(a.cache, n, a.seed)
    res = {"machine": {"cpus": os.cpu_count(), "python": sys.version.split()[0]}, "decision_service": [],
           "bank_server": []}
    port = free_port()
    api = start(["thehro.api:app"], port, {})
    try:
        url = f"http://127.0.0.1:{port}/v1/score"
        asyncio.run(blast(url, bodies[:50], 1))                      # warm-up, not counted
        for c in (1, 8, 32):
            r = asyncio.run(blast(url, bodies, c))
            res["decision_service"].append(r)
            print(f"decision service, {c:>2} at a time: p50 {r['p50_ms']:.1f} ms, p99 {r['p99_ms']:.1f} ms, "
                  f"{r['throughput_per_s']:.0f}/s, {r['errors']} errors", flush=True)
    finally:
        api.terminate()
    port = free_port()
    bank = start(["bank.server:create_app", "--factory"], port, {"THEHRO_INPROCESS": "1"})
    try:
        opts = httpx.get(f"http://127.0.0.1:{port}/judge/options", timeout=10).json()
        jb = judge_bodies(150 if a.quick else 400, a.seed, [c["id"] for c in opts["customers"]])
        url = f"http://127.0.0.1:{port}/judge/case"
        asyncio.run(blast(url, jb[:20], 1))
        for c in (1, 8):
            r = asyncio.run(blast(url, jb, c))
            res["bank_server"].append(r)
            print(f"bank server /judge/case, {c} at a time: p50 {r['p50_ms']:.1f} ms, p99 {r['p99_ms']:.1f} ms, "
                  f"{r['throughput_per_s']:.0f}/s, {r['errors']} errors, {r['rejected_inputs']} inputs refused",
                  flush=True)
    finally:
        bank.terminate()
    path = kit.save_json(res, out / "loadtest.json")
    rows = [[f"Decision service, {r['concurrency']} at a time", f"{r['requests']:,}", f"{r['p50_ms']:.1f}",
             f"{r['p95_ms']:.1f}", f"{r['p99_ms']:.1f}", f"{r['throughput_per_s']:.0f}", str(r["errors"])]
            for r in res["decision_service"]]
    rows += [[f"Bank server, judge case end to end, {r['concurrency']} at a time", f"{r['requests']:,}",
              f"{r['p50_ms']:.1f}", f"{r['p95_ms']:.1f}", f"{r['p99_ms']:.1f}", f"{r['throughput_per_s']:.0f}",
              f"{r['errors']} (+{r['rejected_inputs']} random inputs refused: no account of that type left)"
              if r["rejected_inputs"] else str(r["errors"])] for r in res["bank_server"]]
    one = res["decision_service"][0]
    md = [f"**Measured** on this laptop ({res['machine']['cpus']} CPUs, Python {res['machine']['python']}), client side, "
          "HTTP included, one server process each (as in the demo). Decision-service requests are real test-day "
          "payments (features from the kit cache); judge cases are random judge-mode inputs, and each one builds the "
          "39 features live from events before deciding.", "",
          kit.md_table(["What", "Requests", "p50 ms", "p95 ms", "p99 ms", "Per second", "Errors"], rows), "",
          f"Inside the decision service a decision takes {one['service_p50_ms']:.1f} ms at the median "
          f"({one['service_p99_ms']:.1f} ms p99): models, policy, reasons, message and evidence record. With more "
          "requests at once the service queues them (one Python process); a bank would run several processes behind "
          "a load balancer, so the per-request time, not this laptop's throughput, is the number that carries over. "
          "The bank server handles one judge case at a time (it keeps every session's history in memory).", "",
          "Failure paths are tested in `tests/test_failures.py`: the payee feeds time out (decided without them), the "
          "decision service is down (the bank's own rules apply after a 0.5 s timeout, nothing held), no officer acts "
          "within 30 minutes (the hold is cancelled and the money stays), a call-back can't reach the customer "
          "(nothing is cancelled), and no consent (the phone sends nothing). Not measured here: the demo phone's "
          "round trip over Wi-Fi and 4G."]
    sec = kit.write_section(out, "latency", "\n".join(md))
    kit.log_run(out, kit.new_run_id("loadtest"), a.seed, [path, sec], command=kit.cmd(__file__, argv))
    print(f"saved {path}")
    return res


if __name__ == "__main__":
    main()
