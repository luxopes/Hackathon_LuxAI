#!/usr/bin/env bash
# Stop everything started by scripts/run-local.sh.
set -euo pipefail
STATE="${PROOFPAY_LOCAL_STATE:-$HOME/.local/state/proofpay-local}"
RUN="$STATE/run"
for pidfile in "$RUN"/*.pid; do
  [ -e "$pidfile" ] || continue
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" && echo "stopped $(basename "$pidfile" .pid) (pid $pid)"
  fi
  rm -f "$pidfile"
done
echo "All local ProofPay processes stopped."
