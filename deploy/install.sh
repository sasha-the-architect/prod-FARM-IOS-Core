#!/bin/bash
# deploy/install.sh — install the four Phone Farm launchd agents and start them.
#
# Usage:  bash deploy/install.sh          # install + start all four
#         bash deploy/install.sh stop     # stop + unload all four
#
# Prerequisites (one-time, before this script):
#   1. cd /Users/sashapaskal/mac-farm-controller && npm install
#   2. npm run appium:install-driver      # XCUITest driver into .appium2
#   3. npm run db:migrate                 # Postgres must be up (brew services start postgresql@14)
#   4. npm run build:web                  # dashboard browser assets
#   5. npm run wda:prepare                # per connected iPhone (needs Xcode + unlocked keychain)
set -euo pipefail

AGENTS=(
    com.sashapaskal.phonefarm.appium
    com.sashapaskal.phonefarm.wda-service
    com.sashapaskal.phonefarm.worker
    com.sashapaskal.phonefarm.web
)

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
HERE="$(cd "$(dirname "$0")" && pwd)"

case "${1:-start}" in
    stop)
        for agent in "${AGENTS[@]}"; do
            launchctl bootout "gui/$(id -u)/$agent" 2>/dev/null || true
            echo "Stopped $agent"
        done
        ;;
    start)
        mkdir -p "$LAUNCH_AGENTS"
        for agent in "${AGENTS[@]}"; do
            src="$HERE/$agent.plist"
            dst="$LAUNCH_AGENTS/$agent.plist"
            cp "$src" "$dst"
            launchctl bootout "gui/$(id -u)/$agent" 2>/dev/null || true
            launchctl bootstrap "gui/$(id -u)" "$dst"
            echo "Started $agent"
        done
        echo ""
        echo "All four processes are running:"
        echo "  Appium        : http://127.0.0.1:4725/status"
        echo "  Dashboard/API : http://127.0.0.1:3000  (GET /health)"
        echo "  Logs          : /tmp/phonefarm-*.log"
        ;;
    *)
        echo "Usage: $0 [start|stop]" >&2
        exit 1
        ;;
esac
