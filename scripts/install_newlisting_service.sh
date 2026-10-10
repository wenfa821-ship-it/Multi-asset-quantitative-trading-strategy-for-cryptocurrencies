#!/bin/zsh
# Install (or remove) the new-listing accumulator as a macOS launchd service.
#
#   ./scripts/install_newlisting_service.sh            # daily 09:10 + 21:10
#   ./scripts/install_newlisting_service.sh status
#   ./scripts/install_newlisting_service.sh uninstall
#   TA_NEWLISTING_MAX_AGE_DAYS=400 ./scripts/install_newlisting_service.sh
#
# Read-only market data in, one append-only JSONL out. No keys, no orders.
# This is a BATCH job (KeepAlive=false) and is deliberately independent of the
# runner/watcher/dashboard trio - it must not be able to disturb them.
#
# Liveness is judged by STORE FRESHNESS, not by ps/kill -0: the sandbox and some
# terminal contexts give false negatives on process checks.

set -u

PROJECT_DIR="/Users/wenfa/Documents/Codex/2026-09-28/rh/outputs/trading-agent"
LABEL="com.tradingagent.newlisting"
TEMPLATE="$PROJECT_DIR/deploy/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
STORE="$PROJECT_DIR/data/new_listings/records.jsonl"
MAX_AGE_DAYS="${TA_NEWLISTING_MAX_AGE_DAYS:-400}"

die() { print -u2 "error: $1"; exit 1; }

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
    echo "store      $(wc -l < "$STORE" | tr -d ' ') records"
  else
    echo "store      missing (first run will create it)"
  fi
  python3 "$PROJECT_DIR/scripts/new_listing_track.py" --status 2>/dev/null | sed 's/^/  /' || true
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  [[ -f "$TARGET" ]] && rm -f "$TARGET" && echo "removed $TARGET"
  echo "newlisting service removed (store kept: $STORE)"
}

cmd_install() {
  [[ -f "$TEMPLATE" ]] || die "template missing: $TEMPLATE"
  local python_bin; python_bin="$(command -v python3)" || die "python3 not found"

  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs" "$PROJECT_DIR/data/new_listings"

  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
      -e "s|__PYTHON__|$python_bin|g" \
      -e "s|__MAX_AGE_DAYS__|$MAX_AGE_DAYS|g" \
      "$TEMPLATE" > "$TARGET" || die "could not write $TARGET"
  plutil -lint "$TARGET" >/dev/null || die "generated plist is not valid"
  echo "wrote $TARGET"

  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  local attempt ok=0
  for attempt in 1 2 3 4 5; do
    sleep $(( attempt * 2 ))
    if launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null; then ok=1; break; fi
  done
  [[ "$ok" == "1" ]] || die "launchctl bootstrap failed after 5 attempts"
  launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
  echo "STATUS=LOADED (first run happens now via RunAtLoad; check logs/ta-newlisting-service.log)"
}

case "${1:-install}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  status)    cmd_status ;;
  *) die "usage: $0 [install|uninstall|status]" ;;
esac
