#!/usr/bin/env bash
# Install outlook-icloud-bridge on macOS as a per-user LaunchAgent.
# Re-run any time to update the code; pass --reconfigure to redo the questions.
set -euo pipefail

APP="outlook-icloud-bridge"
LABEL="local.$APP"
KEYCHAIN_SERVICE="$APP"
INTERVAL="${SYNC_INTERVAL:-900}"   # seconds between syncs (rounded to whole minutes, max 60)
STEP=$((INTERVAL / 60)); (( STEP >= 1 && STEP <= 60 )) || STEP=15
SRC="$(cd "$(dirname "$0")" && pwd)"
# Run from Application Support, not the clone: launchd jobs can't read
# ~/Documents, ~/Desktop or ~/Downloads without extra privacy permissions.
DEST="$HOME/Library/Application Support/$APP"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/$APP.log"
CONFIG="$DEST/config.json"

die() { echo "error: $*" >&2; exit 1; }
ask() {  # ask "Question" default -> echoes answer
  local reply
  read -r -p "$1${2:+ [$2]}: " reply
  echo "${reply:-$2}"
}
yesno() {  # yesno "Question" y|n -> true/false
  local reply
  reply="$(ask "$1 (y/n)" "$2")"
  [[ "$reply" =~ ^[Yy] ]] && echo true || echo false
}

[[ "$(uname)" == "Darwin" ]] || die "this installer is for macOS"

RECONFIGURE=false
[[ "${1:-}" == "--reconfigure" ]] && RECONFIGURE=true

# --- Python 3.10+ -----------------------------------------------------------
PYTHON=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PYTHON="$(command -v "$c")"
    break
  fi
done
[[ -n "$PYTHON" ]] || die "Python 3.10 or newer is required. Install it with 'brew install python' or from https://www.python.org/downloads/macos/ and re-run."
echo "Using $PYTHON ($("$PYTHON" --version))"

# --- Code + virtualenv ------------------------------------------------------
mkdir -p "$DEST" "$(dirname "$PLIST")" "$(dirname "$LOG")"
cp "$SRC/bridge.py" "$SRC/requirements.txt" "$DEST/"
if [[ ! -x "$DEST/venv/bin/python" ]]; then
  "$PYTHON" -m venv "$DEST/venv"
fi
"$DEST/venv/bin/pip" install --quiet --upgrade pip
"$DEST/venv/bin/pip" install --quiet -r "$DEST/requirements.txt"

# --- Config -----------------------------------------------------------------
if [[ ! -f "$CONFIG" ]] || $RECONFIGURE; then
  echo
  echo "== Setup =="
  while true; do
    FEED_URL="$(ask "Outlook published calendar ICS link (ends in .ics)" "")"
    # Outlook shows an HTML and an ICS link side by side; they differ only in the extension
    if [[ "$FEED_URL" == http*/calendar.html ]]; then
      FEED_URL="${FEED_URL%.html}.ics"
      echo "That's the HTML link; using the ICS version of it instead."
    fi
    [[ "$FEED_URL" == http*.ics* ]] && break
    echo "That doesn't look like an ICS link. Copy the one ending in .ics and try again."
  done
  APPLE_ID="$(ask "Apple ID email" "")"
  TARGET="$(ask "iCloud calendar to sync into (create it in Calendar first)" "Work")"
  echo
  echo "Synced events are titled 'Label (Meeting title)', or just 'Label' if titles are off."
  LABEL_TEXT="$(ask "Label" "Work")"
  TITLES="$(yesno "Include meeting titles?" n)"
  LOCATION="$(yesno "Include locations? (online meeting links become 'Remote')" n)"
  TENTATIVE="$(yesno "Include tentative events?" y)"
  MULTIDAY="$(yesno "Skip multi-day all-day events (room bookings, trips)?" n)"

  FEED_URL="$FEED_URL" APPLE_ID="$APPLE_ID" TARGET="$TARGET" LABEL_TEXT="$LABEL_TEXT" \
  TITLES="$TITLES" LOCATION="$LOCATION" TENTATIVE="$TENTATIVE" MULTIDAY="$MULTIDAY" \
  "$PYTHON" - "$CONFIG" <<'EOF'
import json, os, sys
b = lambda k: os.environ[k] == "true"
cfg = {
    "feed_url": os.environ["FEED_URL"],
    "apple_id": os.environ["APPLE_ID"],
    "target_calendar": os.environ["TARGET"],
    "label": os.environ["LABEL_TEXT"],
    "include_titles": b("TITLES"),
    "include_location": b("LOCATION"),
    "online_as_remote": True,
    "include_tentative": b("TENTATIVE"),
    "skip_multiday_allday": b("MULTIDAY"),
    "exclude_keywords": [],
    "window_back_days": 7,
    "window_fwd_days": 90,
    "uid_salt": os.environ["TARGET"],
}
with open(sys.argv[1], "w") as f:
    json.dump(cfg, f, indent=2)
EOF
  chmod 600 "$CONFIG"
  echo "Wrote $CONFIG"
fi

APPLE_ID="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["apple_id"])' "$CONFIG")"

# --- iCloud app-specific password in the Keychain ---------------------------
if $RECONFIGURE || ! security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$APPLE_ID" >/dev/null 2>&1; then
  echo
  echo "Create an app-specific password at https://account.apple.com -> Sign-In and Security"
  echo "-> App-Specific Passwords, then paste it below. macOS asks twice ('password data"
  echo "for new item', then 'retype'), and nothing appears on screen as you paste."
  # -w last makes 'security' prompt for the password itself, so it never
  # appears in shell history or the process list.
  security add-generic-password -U -s "$KEYCHAIN_SERVICE" -a "$APPLE_ID" \
    -l "$APP (iCloud CalDAV)" -T /usr/bin/security -w
fi

# --- Preview ----------------------------------------------------------------
echo
echo "== Preview (nothing is written yet) =="
"$DEST/venv/bin/python" "$DEST/bridge.py" --dry-run
echo
[[ "$(yesno "Start syncing now and every $STEP minutes?" y)" == true ]] || {
  echo "Not scheduled. Re-run 'bash install.sh' when ready."; exit 0; }

# --- LaunchAgent ------------------------------------------------------------
# Run at fixed clock minutes (:00, :15, ...) rather than with StartInterval. StartInterval
# only counts time the Mac is awake, so after a wake the next run could be up to 15 minutes
# away; launchd runs a missed calendar slot as soon as the Mac wakes.
MINUTES=""
for ((m = 0; m < 60; m += STEP)); do
  MINUTES+="    <dict><key>Minute</key><integer>$m</integer></dict>"$'\n'
done
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DEST/venv/bin/python</string>
    <string>$DEST/bridge.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
$MINUTES  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# bootstrap can briefly fail while a just-removed job is still shutting down
for attempt in 1 2 3 4 5; do
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
  [[ $attempt == 5 ]] && die "could not load $PLIST (try: launchctl bootstrap gui/$(id -u) \"$PLIST\")"
  sleep 1
done

echo
echo "Installed. It syncs now, every $STEP minutes while the Mac is awake, and right after it wakes."
echo "  Log:        tail -f \"$LOG\""
echo "  Sync now:   launchctl kickstart gui/$(id -u)/$LABEL"
echo "  Uninstall:  bash uninstall.sh"
