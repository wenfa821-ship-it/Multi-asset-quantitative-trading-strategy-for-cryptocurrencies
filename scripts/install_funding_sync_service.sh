#!/bin/zsh
# Install (or remove) the hourly funding sync.
#
#   ./scripts/install_funding_sync_service.sh            # every 3600s
#   TA_FUNDING_INTERVAL=1800 ./scripts/install_funding_sync_service.sh
#   ./scripts/install_funding_sync_service.sh status
#   ./scripts/install_funding_sync_service.sh uninstall
#
# Funding settles 3x/day per open swap; the daily job synced it twice a day, so the
# cash residual drifted between runs and the watchdog escalated it (2026-10-10).
# sync_funding.py is idempotent by billId, so running it hourly is free and bounds
# the drift to one hour.
#
# Liveness is judged by the LEDGER growing (or by a clean "already holds N"), not
# by ps/kill -0.

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
LABEL="com.tradingagent.funding-sync"
TEMPLATE="$PROJECT_DIR/deploy/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$PROJECT_DIR/logs/ta-funding-sync.log"
LEDGER="$PROJECT_DIR/data/ledger.okx_demo.jsonl"
INTERVAL="${TA_FUNDING_INTERVAL:-3600}"
VENV_PYTHON="${TA_PYTHON:-/opt/anaconda3/bin/python3}"

die() { print -u2 "error: $1"; exit 1; }
rows_now() { [[ -f "$LEDGER" ]] && grep -c '"kind": "funding"' "$LEDGER" || echo 0; }

cmd_status() {
  echo "label      $LABEL"
  echo "installed  $([[ -f "$TARGET" ]] && echo yes || echo no)"
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "loaded     yes"
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null \
      | grep -E "^\s*(state|pid|last exit code|runs)" | sed 's/^/  /'
  else
    echo "loaded     no"
  fi
  echo "ledger     $(rows_now) funding row(s)"
  tail -n 3 "$LOG" 2>/dev/null | sed 's/^/  | /'
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  [[ -f "$TARGET" ]] && rm -f "$TARGET" && echo "removed $TARGET"
  echo "funding-sync service removed (ledger kept: $LEDGER)"
}

cmd_install() {
  [[ -f "$TEMPLATE" ]] || die "template missing: $TEMPLATE"
  [[ -x "$VENV_PYTHON" ]] || die "interpreter not executable: $VENV_PYTHON (needs ccxt)"
  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"

  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
      -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
      -e "s|<integer>3600</integer>|<integer>$INTERVAL</integer>|g" \
      "$TEMPLATE" > "$TARGET" || die "could not write $TARGET"
  plutil -lint "$TARGET" >/dev/null || die "generated plist is not valid"
  echo "wrote $TARGET"

  local before; before="$(rows_now)"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  local attempt ok=0
  for attempt in 1 2 3 4 5; do
    sleep $(( attempt * 2 ))
    if launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null; then ok=1; break; fi
  done
  [[ "$ok" == "1" ]] || die "launchctl bootstrap failed after 5 attempts"
  launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true

  echo "waiting for the RunAtLoad sync ..."
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if [[ "$(rows_now)" -gt "$before" ]] || grep -q "already holds" "$LOG" 2>/dev/null; then
      echo "STATUS=RUNNING (ledger $before -> $(rows_now) funding rows)"
      tail -n 4 "$LOG" 2>/dev/null | sed 's/^/  | /'
      return 0
    fi
    sleep 4
  done
  echo "STATUS=PENDING - no run recorded yet"
  tail -n 6 "$LOG" 2>/dev/null | sed 's/^/  | /'
  return 0
}

case "${1:-install}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  status)    cmd_status ;;
  *) die "usage: $0 [install|uninstall|status]" ;;
esac
