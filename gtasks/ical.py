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
import tempfile
import threading
import time
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

    _helper_lock = threading.Lock()

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
        bundle = self.helper.parents[2]
        if bundle.suffix != ".app":
            raise ICalendarError("Mission Control Calendar helper is not a branded app bundle.")
        command_args = [action]
        if action == "events":
            if start is None or end is None:
                raise ValueError("Calendar event reads require a date range.")
            command_args.extend([start.isoformat(), end.isoformat(), json.dumps(calendar_ids)])
        attempts = 1 if action == "request_full_access" else 5
        with self._helper_lock:
            for attempt in range(attempts):
                output_path: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        prefix="mission-control-calendar-", delete=False
                    ) as output:
                        output_path = Path(output.name)
                    output_path.chmod(0o600)
                    command = [
                        "/usr/bin/open", "-W", str(bundle), "--args",
                        *command_args, "--output", str(output_path),
                    ]
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=35,
                        check=False,
                    )
                    text = output_path.read_text(encoding="utf-8")
                    if result.returncode == 0:
                        try:
                            value = json.loads(text)
                        except json.JSONDecodeError:
                            value = None
                        if isinstance(value, dict):
                            return value
                except (OSError, subprocess.TimeoutExpired):
                    pass
                finally:
                    if output_path is not None:
                        output_path.unlink(missing_ok=True)
                if attempt + 1 < attempts:
                    time.sleep(0.15 * (attempt + 1))
        raise ICalendarError("Mission Control Calendar helper is unavailable.")

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
