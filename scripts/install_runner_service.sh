#!/bin/zsh
# Install (or remove) the TRADING LOOP as a macOS launchd service.
#
#   ./scripts/install_runner_service.sh            # install + start + verify
#   ./scripts/install_runner_service.sh status
#   ./scripts/install_runner_service.sh uninstall
#   TA_TRADING=0 ./scripts/install_runner_service.sh   # observe-only service
#
# WHAT THIS CHANGES (read it):
#   * the loop now starts BY ITSELF at login and after a reboot;
#   * if it crashes (or is killed) launchd restarts it after 30s;
#   * if it shuts itself down cleanly - kill switch, or too many broken ticks -
#     launchd LEAVES IT DOWN on purpose, and you restart it deliberately.
#
# It raises by one notch the amount of unattended trading. The risk framework is
# unchanged (5 positions, 45% gross, 3.5% heat, daily -1.5% stop-open / -3%
# flatten, -12% peak-drawdown kill switch) and it is all OKX Demo, but the point
# stands: after a reboot nobody is watching.
#
# Emergency brake, works at any time:
#     touch <project>/KILL_SWITCH      # runner stops itself and stays stopped
#     rm <project>/KILL_SWITCH         # clear it

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
LABEL="com.tradingagent.runner"
TEMPLATE="$PROJECT_DIR/deploy/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
METRICS_PORT="${TA_METRICS_PORT:-9101}"

die() { print -u2 "error: $1"; exit 1; }
port_pid() { lsof -tiTCP:"$METRICS_PORT" -sTCP:LISTEN 2>/dev/null | head -1; }

verify() {
  local i pid
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    pid="$(port_pid)"
    if [[ -n "${pid:-}" ]]; then
      echo "  metrics http://127.0.0.1:$METRICS_PORT  pid=$pid  OK"
      return 0
    fi
    sleep 2
  done
  return 1
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
  local pid; pid="$(port_pid)"
  echo "listening  ${pid:-none} on :$METRICS_PORT"
  echo "pid file   $(cat "$PROJECT_DIR/logs/ta-run.pid" 2>/dev/null || echo '-')"
  [[ -f "$PROJECT_DIR/KILL_SWITCH" ]] && echo "KILL_SWITCH  present (trading is halted)"
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  [[ -f "$TARGET" ]] && rm -f "$TARGET" && echo "removed $TARGET"
  echo "runner service removed (the loop is stopped; restart it by hand with"
  echo "  TA_STRATEGY=all TRADING=1 ./scripts/start_demo.sh )"
}

cmd_install() {
  [[ -f "$TEMPLATE" ]] || die "template missing: $TEMPLATE"
  [[ -f "$PROJECT_DIR/.env" ]] || die "missing $PROJECT_DIR/.env (API keys)"
  local python_bin; python_bin="$(command -v python3)" || die "python3 not found"

  cat <<'NOTICE'
---------------------------------------------------------------
即将把交易循环做成常驻服务：
  · 登录/重启后自动开始交易（OKX 模拟盘）
  · 崩溃或被 kill 会在 30 秒后自动拉起
  · 自行干净退出（熔断 / 连续报错放弃）不会被拉起，需要你手动重启
紧急刹车：touch <project>/KILL_SWITCH
---------------------------------------------------------------
NOTICE

  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"

  # A hand-started loop on the same metrics port would fight the service for the
  # bind, and launchd would keep restarting a job that cannot listen.
  local old; old="$(port_pid)"
  if [[ -n "${old:-}" ]]; then
    echo "stopping the hand-started loop on :$METRICS_PORT (pid $old)"
    kill "$old" 2>/dev/null
    sleep 3
  fi

  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
      -e "s|__PYTHON__|$python_bin|g" \
      -e "s|__METRICS_PORT__|$METRICS_PORT|g" \
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

  if verify; then
    echo "STATUS=RUNNING (launchd-managed)"
    tail -n 6 "$PROJECT_DIR/logs/ta-run-service.log" 2>/dev/null | sed 's/^/  | /'
  else
    echo "STATUS=FAILED - 看日志："
    tail -n 20 "$PROJECT_DIR/logs/ta-run-service.err.log" 2>/dev/null
    tail -n 20 "$PROJECT_DIR/logs/ta-run-service.log" 2>/dev/null
    return 1
  fi
}

case "${1:-install}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  status)    cmd_status ;;
  *) die "usage: $0 [install|uninstall|status]" ;;
esac
