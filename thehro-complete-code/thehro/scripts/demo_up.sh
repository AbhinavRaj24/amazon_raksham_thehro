#!/usr/bin/env bash
# Start judge mode: the decision service on :8000 and the bank server on :8080 (reachable from phones).
#
#   bash scripts/demo_up.sh             # Ctrl+C stops both; restarting resets every session and hold
#   MOCK=1 bash scripts/demo_up.sh      # canned answers only (bank/mock_server.py), for building the pages
#
# The first start simulates the world and caches it in .cache/live_world.pkl (about 30 s, 250 MB);
# later starts load the cache in a few seconds.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-../../.venv/bin/python}"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

wait_for() {  # url, label
  if ! curl -s --fail --retry-all-errors --retry 120 --retry-delay 1 -o /dev/null "$1"; then
    echo "!! $2 did not start; see the log above" >&2
    exit 1
  fi
}

if [[ "${MOCK:-0}" == "1" ]]; then
  "$PY" -m uvicorn bank.mock_server:app --host 0.0.0.0 --port 8080 --log-level warning &
  BANK=$!
  trap 'kill $BANK 2>/dev/null' EXIT INT TERM
  wait_for http://127.0.0.1:8080/mock/list "mock server"
  echo "MOCK bank server (canned answers): http://127.0.0.1:8080/docs   phones: http://${IP}:8080/"
  wait
  exit 0
fi

"$PY" -m uvicorn thehro.api:app --port 8000 --log-level warning &
API=$!
THEHRO_API=http://127.0.0.1:8000 "$PY" -m uvicorn bank.server:create_app --factory --host 0.0.0.0 --port 8080 \
  --log-level warning &
BANK=$!
trap 'kill $API $BANK 2>/dev/null' EXIT INT TERM
wait_for http://127.0.0.1:8000/health "decision service"
echo "decision service ready: http://127.0.0.1:8000/docs"
echo "bank server starting (loads or builds the world)..."
wait_for http://127.0.0.1:8080/health "bank server"
cat <<EOF

  Judge mode is live
    on this laptop     http://127.0.0.1:8080/
    phones (same Wi-Fi or hotspot)   http://${IP}:8080/
    officer console    http://${IP}:8080/officer.html   (also the judge page's Officer tab)
    bank API docs      http://127.0.0.1:8080/docs
    decision service   http://127.0.0.1:8000/docs

  Kit tab: the first random draw loads the frozen baseline from .cache/thehro_world.pkl (about a minute,
  3 GB of RAM, once per start). Hand-written K1 cases run only if they match test_kit/cases/FROZEN.sha256.

  Stop the decision service (kill $API) to show the fallback: the bank applies its own rules, holds nothing.
  Ctrl+C stops everything.
EOF
wait
