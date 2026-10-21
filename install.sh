#!/usr/bin/env bash
set -euo pipefail

SCOPE="${1:-Global}"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$SCOPE" = "Global" ]; then
    TARGET_DIR="$HOME/.gemini/config"
else
    TARGET_DIR="$(pwd)/.agents"
fi

echo "Installing Antigravity Task Guard to [$SCOPE]: $TARGET_DIR"

mkdir -p "$TARGET_DIR/scripts"

cp "$CURRENT_DIR/hooks.json" "$TARGET_DIR/hooks.json"
cp "$CURRENT_DIR/scripts/pre_run_command.py" "$TARGET_DIR/scripts/pre_run_command.py"
cp "$CURRENT_DIR/scripts/stop_watchdog.py" "$TARGET_DIR/scripts/stop_watchdog.py"

echo "Antigravity Task Guard installed successfully."
