"""Local, read-only EventKit boundary for Mission Control Calendar overlays.

The EventKit process is a branded macOS app bundle, rather than Python or a
``swift`` script.  Apple requires *Full Access* to read event data; Mission
Control requests it only after its UI explains that it never writes or deletes
calendar data.  Calendar selection remains a local preference, never GBrain.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Any


class ICalendarError(RuntimeError):
    pass


class CalendarPreferences:
    """Only calendar identifiers are persisted, locally and atomically."""

    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("MISSION_CONTROL_CALENDAR_PREFERENCES")
        self.path = path or (
            Path(configured).expanduser()
            if configured
            else Path.home()
            / "Library/Application Support/Mission Control/calendar-preferences.json"
        )

    def selected_calendar_ids(self) -> tuple[str, ...]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, json.JSONDecodeError):
            return ()
        raw = value.get("selected_calendar_ids") if isinstance(value, dict) else None
        if not isinstance(raw, list):
            return ()
        return tuple(item for item in raw if isinstance(item, str) and item)

    def save_selected_calendar_ids(self, calendar_ids: list[str]) -> tuple[str, ...]:
        cleaned = tuple(dict.fromkeys(item for item in calendar_ids if item))
        if len(cleaned) > 100:
            raise ValueError("Choose no more than 100 calendars.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"selected_calendar_ids": cleaned}, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)
        return cleaned


class ICalendarReader:
    """Runs a purpose-built read-only Mission Control EventKit app helper."""

    def __init__(self, helper: Path | None = None) -> None:
        configured = os.environ.get("MISSION_CONTROL_CALENDAR_HELPER")
        self.helper = helper or (
            Path(configured).expanduser()
            if configured
            else Path.home()
            / "Library/Application Support/Mission Control/Mission Control Calendar.app"
            / "Contents/MacOS/MissionControlCalendar"
        )

    def _run(
        self,
        action: str,
        *,
        start: date | None = None,
        end: date | None = None,
        calendar_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not self.helper.is_file():
            raise ICalendarError(
                "Mission Control Calendar helper is not installed. Calendar events remain unavailable."
            )
        command = [str(self.helper), action]
        if action == "events":
            if start is None or end is None:
                raise ValueError("Calendar event reads require a date range.")
            command.extend([start.isoformat(), end.isoformat(), json.dumps(calendar_ids)])
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=35, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ICalendarError("Mission Control Calendar helper is unavailable.") from exc
        if result.returncode != 0:
            raise ICalendarError("Mission Control Calendar helper is unavailable.")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ICalendarError("Mission Control Calendar helper returned an invalid response.") from exc
        if not isinstance(value, dict):
            raise ICalendarError("Mission Control Calendar helper returned an invalid response.")
        return value

    def status(self) -> dict[str, Any]:
        return self._run("status")

    def request_full_access(self) -> dict[str, Any]:
        return self._run("request_full_access")

    def calendars(self) -> dict[str, Any]:
        return self._run("calendars")

    def read(
        self, start: date, end: date, *, calendar_ids: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        return self._run(
            "events", start=start, end=end, calendar_ids=calendar_ids
        )
