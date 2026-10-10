#!/bin/zsh
# Install (or remove) the on-chain snapshot watcher as a macOS launchd service.
#
#   ./scripts/install_watcher_service.sh                    # 300s cadence
#   TA_WATCH_INTERVAL=60 ./scripts/install_watcher_service.sh
#   ./scripts/install_watcher_service.sh status
#   ./scripts/install_watcher_service.sh uninstall
#
# Read-only research: public feeds in, snapshots.jsonl out. No keys, no venue, no
# orders - which is why KeepAlive=true here (any exit is resumed), unlike the
# trading loop where a clean exit must be respected.
#
# Liveness is judged by DATA FRESHNESS, not by `ps`/`kill -0`: the sandbox and
# some terminal contexts give false negatives on process checks.

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
LABEL="com.tradingagent.watcher"
TEMPLATE="$PROJECT_DIR/deploy/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
INTERVAL="${TA_WATCH_INTERVAL:-300}"
STORE="$PROJECT_DIR/data/onchain/snapshots.jsonl"

die() { print -u2 "error: $1"; exit 1; }

store_age_min() {
  local secs
  secs=$(( $(date +%s) - $(stat -f %m "$STORE" 2>/dev/null || echo 0) ))
  echo $(( secs / 60 ))
}

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
  if [[ -f "$STORE" ]]; then
    echo "store      $(wc -l < "$STORE" | tr -d ' ') rows, last written $(store_age_min) min ago"
  else
    echo "store      missing"
  fi
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  [[ -f "$TARGET" ]] && rm -f "$TARGET" && echo "removed $TARGET"
  echo "watcher service removed"
}

cmd_install() {
  [[ -f "$TEMPLATE" ]] || die "template missing: $TEMPLATE"
  [[ -f "$PROJECT_DIR/data/onchain/watchlist.json" ]] \
    || die "no watchlist yet - run: python3 -m ta scan discover --limit 25 --write data/onchain/watchlist.json"
  local python_bin; python_bin="$(command -v python3)" || die "python3 not found"

  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"

  # A hand-started scanner writing the same store would double the rows; the
  # event study dedupes them, but a single writer is the honest setup.
  if pgrep -f "ta scan watch" >/dev/null 2>&1; then
    echo "stopping hand-started scanner(s): $(pgrep -f 'ta scan watch' | tr '\n' ' ')"
    pkill -f "ta scan watch"
    sleep 2
  fi
  rm -f "$PROJECT_DIR/logs/ta-watch.pid"

  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
      -e "s|__PYTHON__|$python_bin|g" \
      -e "s|__INTERVAL__|$INTERVAL|g" \
      "$TEMPLATE" > "$TARGET" || die "could not write $TARGET"
  plutil -lint "$TARGET" >/dev/null || die "generated plist is not valid"
  echo "wrote $TARGET"

  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  # See install_dashboard_service.sh: bootstrap right after bootout can fail with
  # "Input/output error" while the previous job is still tearing down.
  local attempt ok=0
  for attempt in 1 2 3 4 5; do
    sleep $(( attempt * 2 ))
    if launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null; then ok=1; break; fi
  done
  [[ "$ok" == "1" ]] || die "launchctl bootstrap failed after 5 attempts"
  launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true

  echo "waiting for the first round to land in the store ..."
  local i pid
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    pid="$(pgrep -f 'ta scan watch' | head -1)"
    if [[ "$(store_age_min)" -lt 2 ]]; then
      echo "STATUS=RUNNING (pid ${pid:-?}, store updated <2min ago)"
      return 0
    fi
    sleep 4
  done
  echo "STATUS=PENDING - first round still in flight (a 20-token round takes ~1 min)"
  tail -n 10 "$PROJECT_DIR/logs/ta-watch-service.log" 2>/dev/null | sed 's/^/  | /'
  return 0
}

case "${1:-install}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  status)    cmd_status ;;
  *) die "usage: $0 [install|uninstall|status]" ;;
esac
