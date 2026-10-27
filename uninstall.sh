#!/usr/bin/env bash
# Remove outlook-icloud-bridge: stops the LaunchAgent and optionally removes
# the synced events and the Keychain password.
set -euo pipefail

APP="outlook-icloud-bridge"
LABEL="local.$APP"
DEST="$HOME/Library/Application Support/$APP"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CONFIG="$DEST/config.json"

confirm() { local r; read -r -p "$1 (y/n) [n]: " r; [[ "$r" =~ ^[Yy] ]]; }

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Stopped background sync."

if [[ -f "$CONFIG" ]]; then
  if confirm "Delete the events this tool added to your iCloud calendar? (your own events are never touched)"; then
    "$DEST/venv/bin/python" "$DEST/bridge.py" --purge
  fi
  APPLE_ID="$("$DEST/venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["apple_id"])' "$CONFIG")"
  if confirm "Remove the iCloud app-specific password from the Keychain?"; then
    security delete-generic-password -s "$APP" -a "$APPLE_ID" >/dev/null 2>&1 && echo "Removed."
    echo "You can also revoke it at https://account.apple.com."
  fi
fi

rm -rf "$DEST"
echo "Removed $DEST. The log is at ~/Library/Logs/$APP.log if you want to delete it."
