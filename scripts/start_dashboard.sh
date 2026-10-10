#!/bin/zsh
# Start (or restart) the READ-ONLY operations dashboard in the background.
#
#   ./scripts/start_dashboard.sh                 # http://127.0.0.1:9200
#   TA_DASH_PORT=9300 ./scripts/start_dashboard.sh
#
# Read-only by construction: the dashboard opens files for reading and never
# writes state, never talks to a venue and never places an order. It is safe to
# leave running next to the trading loop.
#
# Stop with:  kill "$(cat logs/ta-dash.pid)"

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
PORT="${TA_DASH_PORT:-9200}"
HOST="${TA_DASH_HOST:-127.0.0.1}"
VENUE="${TA_VENUE:-okx_demo}"
METRICS_PORT="${TA_METRICS_PORT:-9101}"
CONFIG_DIR="${TA_CONFIG_DIR:-config}"

cd "$PROJECT_DIR" || { echo "project dir missing: $PROJECT_DIR"; exit 1; }

PID_FILE="logs/ta-dash.pid"
RUN_LOG="logs/ta-dash-$(date +%Y%m%d).log"
mkdir -p logs

# --- stop a previous instance ----------------------------------------------
# Resolve by port first: the pid file goes stale whenever the dashboard is
# started from somewhere else (IDE, a manual shell).
port_pid() { lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1; }

OLD_PID="$(port_pid)"
[[ -z "$OLD_PID" ]] && OLD_PID="$(cat "$PID_FILE" 2>/dev/null)"
if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
  echo "stopping previous dashboard pid=$OLD_PID"
  kill "$OLD_PID" 2>/dev/null
  sleep 2
fi
rm -f "$PID_FILE"

# --- environment ------------------------------------------------------------
export PYTHONPATH="src"
export TZ="Asia/Shanghai"

# --- launch -----------------------------------------------------------------
nohup python3 -c '
import os, sys
os.setsid()
os.execvp(sys.executable, [sys.executable, "-u", "-m", "ta", "dashboard"] + sys.argv[1:])
' --config-dir "$CONFIG_DIR" --host "$HOST" --port "$PORT" \
  --venue "$VENUE" --metrics-port "$METRICS_PORT" >> "$RUN_LOG" 2>&1 &

sleep 3
REAL_PID="$(port_pid)"
[[ -z "$REAL_PID" ]] && REAL_PID="$!"
echo "$REAL_PID" > "$PID_FILE"

echo "started pid=$REAL_PID"
echo "  看板   http://$HOST:$PORT"
echo "  日志   $RUN_LOG"

# liveness is judged by the port answering, not by signal privileges (ps/kill -0
# give false negatives in some sandboxes)
if curl -s -o /dev/null --max-time 5 "http://$HOST:$PORT/api/health"; then
  echo "STATUS=RUNNING"
else
  echo "STATUS=UNKNOWN - check $RUN_LOG"
  tail -n 20 "$RUN_LOG"
fi
