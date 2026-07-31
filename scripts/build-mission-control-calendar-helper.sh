#!/bin/zsh
set -euo pipefail

bundle_root="${MISSION_CONTROL_CALENDAR_BUNDLE_ROOT:-$HOME/Library/Application Support/Mission Control/Mission Control Calendar.app}"
executable="$bundle_root/Contents/MacOS/MissionControlCalendar"
plist="$bundle_root/Contents/Info.plist"
mkdir -p "$bundle_root/Contents/MacOS"
swiftc "$(cd "$(dirname "$0")/.." && pwd)/gtasks/mission_control_calendar_helper.swift" -o "$executable"
cat > "$plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>local.mission-control.calendar</string>
  <key>CFBundleName</key><string>Mission Control Calendar</string>
  <key>CFBundleDisplayName</key><string>Mission Control</string>
  <key>CFBundleExecutable</key><string>MissionControlCalendar</string>
  <key>NSCalendarsFullAccessUsageDescription</key><string>Mission Control needs Full Access only to read events from the calendars you choose. It never writes or deletes calendar events.</string>
</dict></plist>
PLIST
codesign --force --sign - "$bundle_root" >/dev/null
printf 'Built %s\n' "$bundle_root"
