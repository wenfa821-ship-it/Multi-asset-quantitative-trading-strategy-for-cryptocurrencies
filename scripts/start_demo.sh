#!/bin/zsh
# Start (or restart) the trading-agent loop against OKX Demo Trading.
#
# Runs detached so it survives the shell/session that launched it.
# Everything is sandbox/demo: ccxt sends `x-simulated-trading: 1`,
# no real money moves. Local proxy is required to reach okx.com.

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
PROXY="http://127.0.0.1:7897"
VENUE="${TA_VENUE:-okx_demo}"
# unset or "all" -> run every strategy enabled in config/strategies.toml
# (trend_breakout alone is the lowest-frequency one; the 15m edge lives in
#  trend_pullback / mean_reversion / new_listing_breakout)
STRATEGY="${TA_STRATEGY:-all}"
METRICS_PORT="${TA_METRICS_PORT:-9101}"
# Set TRADING=0 to observe only (no orders are sent to the venue).
TRADING="${TA_TRADING:-1}"

cd "$PROJECT_DIR" || { echo "project dir missing: $PROJECT_DIR"; exit 1; }

PID_FILE="logs/ta-run.pid"
RUN_LOG="logs/ta-run-$(date +%Y%m%d).log"

# --- refuse to fight launchd -------------------------------------------------
# If the loop is service-managed, this script would kill the launchd-owned
# process, start its own, and launchd would then restart the job: two loops
# trading the same account and fighting over the metrics port. Stop here and
# point at the right controls instead.
RUNNER_LABEL="gui/$(id -u)/com.tradingagent.runner"
if launchctl print "$RUNNER_LABEL" >/dev/null 2>&1; then
  echo "the trading loop is managed by launchd (com.tradingagent.runner)."
  echo "  restart it:  launchctl kickstart -k $RUNNER_LABEL"
  echo "  status:      ./scripts/install_runner_service.sh status"
  echo "  stop/remove: ./scripts/install_runner_service.sh uninstall"
  exit 3
fi

# --- stop a previous instance ----------------------------------------------
# Resolve the live instance by its metrics port, not just the pid file: the pid
# file goes stale whenever the loop is started from somewhere else (IDE, a
# manual shell) and then the watchdog/review job reports a false "dead".
port_pid() { lsof -tiTCP:"$METRICS_PORT" -sTCP:LISTEN 2>/dev/null | head -1; }

OLD_PID="$(cat "$PID_FILE" 2>/dev/null)"
[[ -z "$OLD_PID" ]] && OLD_PID="$(pgrep -f "ta run --venue" 2>/dev/null | head -1)"

if [[ -z "$OLD_PID" ]]; then
  OLD_PID="$(port_pid)"
fi

if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
  echo "stopping previous instance pid=$OLD_PID"
  kill "$OLD_PID" 2>/dev/null
  sleep 3
  kill -9 "$OLD_PID" 2>/dev/null
fi
rm -f "$PID_FILE"

# --- environment ------------------------------------------------------------
set -a
. ./.env
set +a

export PYTHONPATH="src"
export HTTP_PROXY="$PROXY"  HTTPS_PROXY="$PROXY"
export http_proxy="$PROXY"  https_proxy="$PROXY"
export NO_PROXY="localhost,127.0.0.1,::1"
export TZ="Asia/Shanghai"

# --- pre-flight -------------------------------------------------------------
python3 -m ta doctor --config-dir config || exit 2

FLAGS=(--venue "$VENUE" --iterations 0 --interval 60 --config-dir config --metrics-port "$METRICS_PORT")
[[ "$STRATEGY" != "all" ]] && FLAGS+=(--strategy "$STRATEGY")
[[ "$TRADING" == "1" ]] && FLAGS+=(--enable-trading)

# --- launch -----------------------------------------------------------------
# Detach from the launching shell's process group / session so the loop is not
# reaped when the calling session (cron, automation, terminal) goes away.
# plain `nohup ... &` is not enough - it survives SIGHUP but not a process-group kill.
nohup python3 -c '
import os, sys
os.setsid()
os.execvp("python3", ["python3", "-m", "ta", "run"] + sys.argv[1:])
' "${FLAGS[@]}" >> "$RUN_LOG" 2>&1 &
LAUNCH_PID=$!

# give it time to bind the metrics port and print the first cycle
sleep 25

# The authoritative pid is the one holding the metrics port - with setsid +
# execvp the launcher pid is usually right, but a stale instance or a failed
# bind would leave us recording a dead pid.
REAL_PID="$(port_pid)"
[[ -z "$REAL_PID" ]] && REAL_PID="$LAUNCH_PID"
echo "$REAL_PID" > "$PID_FILE"

echo "started pid=$REAL_PID venue=$VENUE trading=$TRADING log=$RUN_LOG"

if kill -0 "$REAL_PID" 2>/dev/null; then
  echo "STATUS=RUNNING"
else
  echo "STATUS=DEAD"
fi

echo "----- last 30 log lines -----"
tail -n 30 "$RUN_LOG"
