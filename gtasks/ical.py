"""Read-only local EventKit boundary for Mission Control Calendar overlays."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any


class ICalendarError(RuntimeError):
    pass


class ICalendarReader:
    """Uses the OS permission boundary; never writes GBrain or event data."""

    def __init__(self, helper: Path | None = None) -> None:
        self.helper = helper or Path(__file__).with_name("ical_events.swift")

    def read(self, start: date, end: date, *, request_access: bool = False) -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["swift", str(self.helper), start.isoformat(), end.isoformat(), "request" if request_access else "status"],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise ICalendarError("Local Calendar integration is unavailable.") from exc
        if result.returncode != 0:
            raise ICalendarError("Local Calendar integration is unavailable.")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ICalendarError("Local Calendar integration returned an invalid response.") from exc
        if not isinstance(value, dict):
            raise ICalendarError("Local Calendar integration returned an invalid response.")
        return value
