#!/bin/zsh
# Install (or remove) the in-session TradFi availability sampler.
#
#   ./scripts/install_tradfi_session_service.sh            # 23:10 + 01:10 CST
#   ./scripts/install_tradfi_session_service.sh status
#   ./scripts/install_tradfi_session_service.sh uninstall
#
# Read-only: one script, ~15 seconds, appends one row to
# data/universe/availability.jsonl. It exists because the daily job's two slots
# (09:10 / 21:10 CST = 01:10 / 13:10 UTC) both fall OUTSIDE the US regular
# session (13:30-20:00 UTC), so the tier decision could never see an in-session
# observation - and 21:10 is only 20 minutes before the open, which the first
# version of the session test would have mislabelled as in-session.
#
# Liveness is judged by STORE FRESHNESS, not by ps/kill -0.

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
LABEL="com.tradingagent.tradfi-session"
TEMPLATE="$PROJECT_DIR/deploy/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
STORE="$PROJECT_DIR/data/universe/availability.jsonl"
# The script builds a venue, which needs ccxt - the python.org build the other
# batch job runs on does not have it (README 45.2), so this pins the interpreter
# that does, the same way daily_research.py hands ccxt steps to VENV_PYTHON.
VENV_PYTHON="${TA_PYTHON:-/opt/anaconda3/bin/python3}"

die() { print -u2 "error: $1"; exit 1; }

rows_now() { [[ -f "$STORE" ]] && wc -l < "$STORE" | tr -d ' ' || echo 0; }

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
  echo "store      $(rows_now) run(s)"
  python3 "$PROJECT_DIR/scripts/tradfi_tier.py" 2>/dev/null | tail -n 3 | sed 's/^/  /' || true
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  [[ -f "$TARGET" ]] && rm -f "$TARGET" && echo "removed $TARGET"
  echo "tradfi-session service removed (store kept: $STORE)"
}

cmd_install() {
  [[ -f "$TEMPLATE" ]] || die "template missing: $TEMPLATE"
  [[ -x "$VENV_PYTHON" ]] || die "interpreter not executable: $VENV_PYTHON (needs ccxt)"

  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs" "$PROJECT_DIR/data/universe"

  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
      -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
      "$TEMPLATE" > "$TARGET" || die "could not write $TARGET"
  plutil -lint "$TARGET" >/dev/null || die "generated plist is not valid"
  echo "wrote $TARGET"

  local before; before="$(rows_now)"
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

  echo "waiting for the RunAtLoad round to land (a 13-symbol round takes ~15s) ..."
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if [[ "$(rows_now)" -gt "$before" ]]; then
      echo "STATUS=RUNNING (store $before -> $(rows_now) rows)"
      tail -n 6 "$PROJECT_DIR/logs/ta-tradfi-session.log" 2>/dev/null | sed 's/^/  | /'
      return 0
    fi
    sleep 5
  done
  echo "STATUS=PENDING - no row yet"
  tail -n 10 "$PROJECT_DIR/logs/ta-tradfi-session.err.log" 2>/dev/null | sed 's/^/  | /'
  return 0
}

case "${1:-install}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  status)    cmd_status ;;
  *) die "usage: $0 [install|uninstall|status]" ;;
esac
