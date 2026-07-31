import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gtasks.ical import CalendarPreferences, ICalendarReader


class CalendarPreferencesTests(unittest.TestCase):
    def test_persists_only_selected_calendar_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calendar.json"
            preferences = CalendarPreferences(path)
            self.assertEqual(
                preferences.save_selected_calendar_ids(["home", "work", "home"]),
                ("home", "work"),
            )
            self.assertEqual(preferences.selected_calendar_ids(), ("home", "work"))
            self.assertEqual(json.loads(path.read_text()), {"selected_calendar_ids": ["home", "work"]})


class CalendarHelperContractTests(unittest.TestCase):
    def test_events_helper_receives_only_selected_identifiers(self) -> None:
        helper = Path("/tmp/MissionControlCalendar")
        reader = ICalendarReader(helper)
        completed = type("Completed", (), {"returncode": 0, "stdout": '{"status":"authorized","events":[]}'})()
        with patch.object(Path, "is_file", return_value=True), patch("gtasks.ical.subprocess.run", return_value=completed) as run:
            reader.read(__import__("datetime").date(2026, 7, 30), __import__("datetime").date(2026, 8, 1), calendar_ids=("home",))
        command = run.call_args.args[0]
        self.assertEqual(command[:2], [str(helper), "events"])
        self.assertEqual(json.loads(command[-1]), ["home"])

    def test_helper_source_has_no_eventkit_write_symbols(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gtasks/mission_control_calendar_helper.swift").read_text()
        self.assertIn("requestFullAccessToEvents", source)
        self.assertNotIn("saveEvent", source)
        self.assertNotIn("removeEvent", source)
        self.assertNotIn("EKEvent(", source)

