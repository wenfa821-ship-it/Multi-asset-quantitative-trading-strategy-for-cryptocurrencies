#!/bin/zsh
# Install (or remove) the read-only dashboard as a macOS launchd service.
#
#   ./scripts/install_dashboard_service.sh            # install + start + verify
#   ./scripts/install_dashboard_service.sh status
#   ./scripts/install_dashboard_service.sh uninstall
#   TA_DASH_PORT=9300 ./scripts/install_dashboard_service.sh
#
# Why launchd and not `nohup ... &`: a background shell process dies with the
# terminal session and does not come back after a reboot. launchd gives
# RunAtLoad (start at login) + KeepAlive (restart if it exits), which is what
# "常驻" actually requires.
#
# Scope note: this installs the DASHBOARD only. The dashboard is read-only, so
# keeping it alive unattended carries no trading risk. The trading loop is a
# separate decision and is deliberately NOT auto-restarted here.

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
LABEL="com.tradingagent.dashboard"
TEMPLATE="$PROJECT_DIR/deploy/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${TA_DASH_PORT:-9200}"
PYTHON_BIN="${TA_DASH_PYTHON:-$(command -v python3)}"

die() { print -u2 "error: $1"; exit 1; }

port_pid() { lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1; }

verify() {
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/health"; then
      echo "  http://127.0.0.1:$PORT  OK"
      return 0
    fi
    sleep 1
  done
  return 1
}

cmd_status() {
  echo "label    $LABEL"
  echo "plist    $TARGET"
  if [[ -f "$TARGET" ]]; then
    echo "installed  yes"
  else
    echo "installed  no"
  fi
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "loaded     yes"
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "^\s*(state|pid|last exit code)" | sed 's/^/  /'
  else
    echo "loaded     no"
  fi
  local pid; pid="$(port_pid)"
  echo "listening  ${pid:-none} on :$PORT"
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  [[ -f "$TARGET" ]] && rm -f "$TARGET" && echo "removed $TARGET"
  echo "dashboard service removed"
}

cmd_install() {
  [[ -f "$TEMPLATE" ]] || die "template missing: $TEMPLATE"
  [[ -n "$PYTHON_BIN" ]] || die "python3 not found; set TA_DASH_PYTHON"

  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"

  # A stale ad-hoc process on the same port would win the bind and launchd would
  # keep restarting a job that cannot listen.
  local old; old="$(port_pid)"
  if [[ -n "${old:-}" ]]; then
    echo "stopping process already on :$PORT (pid $old)"
    kill "$old" 2>/dev/null
    sleep 2
  fi
  if [[ -f "$PROJECT_DIR/logs/ta-dash.pid" ]]; then
    local p; p="$(cat "$PROJECT_DIR/logs/ta-dash.pid" 2>/dev/null)"
    [[ -n "${p:-}" ]] && kill "$p" 2>/dev/null
    rm -f "$PROJECT_DIR/logs/ta-dash.pid"
  fi

  sed -e "s|__PYTHON__|$PYTHON_BIN|g" \
      -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
      -e "s|__PORT__|$PORT|g" \
      "$TEMPLATE" > "$TARGET" || die "could not write $TARGET"

  plutil -lint "$TARGET" >/dev/null || die "generated plist is not valid"
  echo "wrote $TARGET"

  # Re-bootstrap cleanly: bootout first so an edited plist is actually re-read.
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  # `bootstrap` right after `bootout` intermittently fails with
  # "Bootstrap failed: 5: Input/output error" while the old job is still tearing
  # down (measured 2026-10-01 on this machine). Retry instead of giving up.
  local attempt ok=0
  for attempt in 1 2 3 4 5; do
    sleep $(( attempt * 2 ))
    if launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null; then ok=1; break; fi
  done
  [[ "$ok" == "1" ]] || die "launchctl bootstrap failed after 5 attempts"
  launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true

  echo "service started"
  if verify; then
    echo "STATUS=RUNNING (launchd-managed, 开机自启 + 挂了自动拉起)"
  else
    echo "STATUS=FAILED - 看日志："
    tail -n 20 "$PROJECT_DIR/logs/ta-dash-service.err.log" 2>/dev/null
    tail -n 20 "$PROJECT_DIR/logs/ta-dash-service.log" 2>/dev/null
    return 1
  fi
}

case "${1:-install}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  status)    cmd_status ;;
  *) die "usage: $0 [install|uninstall|status]" ;;
esac
