#!/bin/zsh
# Start (or restart) the on-chain snapshot watcher in the background.
#
# Why this exists: the event study needs a CONTINUOUS observation series. A
# 2-hour automation can only ever study the +24h horizon; the +5m / +1h horizons
# need a 5-minute cadence, which means a long-running process on your machine.
#
#   ./scripts/start_watch.sh                      # 300s cadence, refreshes list if stale
#   TA_WATCH_INTERVAL=60 ./scripts/start_watch.sh # faster cadence
#   TA_DISCOVER=1 ./scripts/start_watch.sh        # always refresh the watchlist first
#
# Stop with:  kill "$(cat logs/ta-watch.pid)"

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
PROXY="${TA_PROXY:-http://127.0.0.1:7897}"
INTERVAL="${TA_WATCH_INTERVAL:-300}"
WATCHLIST="${TA_WATCHLIST:-data/onchain/watchlist.json}"
STORE="${TA_WATCH_STORE:-data/onchain/snapshots.jsonl}"
DISCOVER="${TA_DISCOVER:-0}"
# refresh the candidate list when it is older than this many minutes
MAX_AGE_MIN="${TA_WATCHLIST_MAX_AGE_MIN:-720}"
CONFIG_DIR="${TA_CONFIG_DIR:-config}"

cd "$PROJECT_DIR" || { echo "project dir missing: $PROJECT_DIR"; exit 1; }

PID_FILE="logs/ta-watch.pid"
RUN_LOG="logs/ta-watch-$(date +%Y%m%d).log"
mkdir -p logs data/onchain

# --- stop a previous instance ----------------------------------------------
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null)"
  if [[ -n "${OLD_PID:-}" ]] && ps -p "$OLD_PID" >/dev/null 2>&1; then
    echo "stopping previous watcher pid=$OLD_PID"
    kill "$OLD_PID" 2>/dev/null
    sleep 2
    kill -9 "$OLD_PID" 2>/dev/null
  fi
  rm -f "$PID_FILE"
fi

# --- environment ------------------------------------------------------------
set -a
[[ -f ./.env ]] && . ./.env
set +a

export PYTHONPATH="src"
export TA_PROXY="$PROXY"
export HTTP_PROXY="$PROXY"  HTTPS_PROXY="$PROXY"
export http_proxy="$PROXY"  https_proxy="$PROXY"
export NO_PROXY="localhost,127.0.0.1,::1"
export TZ="Asia/Shanghai"

PY="$(command -v python3)"

# --- candidate list ---------------------------------------------------------
NEED_DISCOVER=0
if [[ "$DISCOVER" == "1" || ! -f "$WATCHLIST" ]]; then
  NEED_DISCOVER=1
elif [[ -n "$(find "$WATCHLIST" -mmin +"$MAX_AGE_MIN" -print 2>/dev/null)" ]]; then
  NEED_DISCOVER=1
  echo "watchlist older than ${MAX_AGE_MIN}m -> refreshing"
fi

if [[ "$NEED_DISCOVER" == "1" ]]; then
  echo "refreshing candidate list (ta scan discover) ..."
  python3 -m ta scan discover --limit 25 --write "$WATCHLIST" --config-dir "$CONFIG_DIR" \
    | tail -n 12
  if [[ ! -f "$WATCHLIST" ]]; then
    echo "discover produced no watchlist; aborting"
    exit 2
  fi
fi

COUNT="$(python3 - <<PY
import json
try:
    print(len(json.load(open("$WATCHLIST"))["candidates"]))
except Exception:
    print(0)
PY
)"
if [[ "$COUNT" == "0" ]]; then
  echo "watchlist is empty ($WATCHLIST); run with TA_DISCOVER=1 to rebuild"
  exit 2
fi

# --- launch -----------------------------------------------------------------
# Detach from this shell's process group: plain `&` survives SIGHUP but not a
# process-group kill. macOS has no setsid(1), hence the python shim.
nohup python3 -c '
import os, sys
os.setsid()
os.execvp(sys.executable, [sys.executable, "-u", "-m", "ta", "scan", "watch"] + sys.argv[1:])
' --mints-from "$WATCHLIST" --interval "$INTERVAL" --store "$STORE" \
  --config-dir "$CONFIG_DIR" >> "$RUN_LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

echo "started pid=$NEW_PID interval=${INTERVAL}s watchlist=$COUNT tokens"
echo "log=$RUN_LOG  store=$STORE"

sleep 25
# Liveness is judged by DATA FRESHNESS, not by signal privileges: if the store
# was written in the last two minutes the watcher is doing its job. `ps -p` is
# only a hint (it is blocked in some sandboxes and gives false negatives).
if [[ -n "$(find "$STORE" -mmin -2 -print 2>/dev/null)" ]]; then
  echo "STATUS=RUNNING (store updated <2min ago)"
elif ps -p "$NEW_PID" >/dev/null 2>&1; then
  echo "STATUS=RUNNING (pid alive, first round still in flight)"
else
  echo "STATUS=DEAD - check $RUN_LOG"
fi
echo "store rows: $(wc -l < "$STORE" 2>/dev/null | tr -d ' ')"

echo "----- last 20 log lines -----"
tail -n 20 "$RUN_LOG"

echo "----- 事件研究（现在跑一次看覆盖率） -----"
python3 -m ta scan evaluate --store "$STORE" --horizons "${TA_HORIZONS:-5,60,1440}" \
  --config-dir "$CONFIG_DIR" 2>&1 | tail -n 12
