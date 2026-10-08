#!/usr/bin/env bash
# Build and run the whole ProofPay stack locally on loopback ports.
#   marketplace 3070 | sellers 3081-3085 | agent console + frontend 3069
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${PROOFPAY_LOCAL_STATE:-$HOME/.local/state/proofpay-local}"
CFG="$STATE/etc" RUN="$STATE/run" LOGS="$STATE/logs" BUILD="$STATE/build"
mkdir -p "$RUN" "$LOGS" "$BUILD"

command -v lsl >/dev/null || { echo "lsl is not installed — see docs/SETUP.md (https://lsl.lux-ai.cz/)"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

echo "== local configuration"
LUXAI_KEY_FILE="${LUXAI_KEY_FILE:-$HOME/.config/lux-runner/api-key}" \
  python3 "$ROOT/scripts/local_config.py"

echo "== building LSL binaries"
lsl compile "$ROOT/backend/lsl/seller.lsl" "$BUILD/seller"
lsl compile "$ROOT/backend/lsl/chat_server.lsl" "$BUILD/web-server"

start() { # <name> <command...>
  local name="$1"; shift
  if [ -f "$RUN/$name.pid" ] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then
    echo "   $name already running (pid $(cat "$RUN/$name.pid"))"
    return
  fi
  nohup "$@" >"$LOGS/$name.log" 2>&1 &
  echo $! >"$RUN/$name.pid"
  echo "   started $name (pid $!)"
}

echo "== starting services"
start market python3 "$ROOT/backend/python/marketplace.py" --config "$CFG/market.json"
for s in partial complete scout insight atlas; do
  # shellcheck disable=SC1090
  ( set -a; . "$CFG/seller-$s.env"; set +a; start "seller-$s" "$BUILD/seller" )
done
start console env PROOFPAY_CHAT_CONFIG="$CFG/chat.env" "$BUILD/web-server"

wait_for() { # <url> <label>
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null "$1"; then echo "   $2 ready"; return 0; fi
    sleep 0.25
  done
  echo "   $2 NOT ready — check $LOGS/$2.log" >&2
  return 1
}

echo "== health checks"
wait_for http://127.0.0.1:3070/health market
for p in 3081 3082 3083 3084 3085; do wait_for "http://127.0.0.1:$p/health" "seller-$p"; done
wait_for http://127.0.0.1:3069/api/health console

cat <<EOF

Ready.
  Agent console (frontend + API):  http://127.0.0.1:3069/
  Marketplace dashboard:           http://127.0.0.1:3070/
  Logs: $LOGS
  Stop: scripts/stop-local.sh

Try in the console chat: "Please audit the marketplace demo cart."
EOF
