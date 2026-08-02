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
        helper = Path("/tmp/Mission Control Calendar.app/Contents/MacOS/MissionControlCalendar")
        reader = ICalendarReader(helper)
        completed = type("Completed", (), {"returncode": 0, "stdout": ""})()
        def complete(command, **_kwargs):
            Path(command[-1]).write_text('{"status":"authorized","events":[]}')
            return completed
        with patch.object(Path, "is_file", return_value=True), patch("gtasks.ical.subprocess.run", side_effect=complete) as run:
            result = reader.read(__import__("datetime").date(2026, 7, 30), __import__("datetime").date(2026, 8, 1), calendar_ids=("home",))
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["/usr/bin/open", "-W", str(helper.parents[2]), "--args"])
        self.assertEqual(command[4], "events")
        self.assertEqual(json.loads(command[7]), ["home"])
        self.assertEqual(command[8], "--output")
        self.assertEqual(result, {"status": "authorized", "events": []})

    def test_helper_parses_launchservices_output_option_separately_from_event_arguments(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gtasks/mission_control_calendar_helper.swift").read_text()
        self.assertIn("var positionalArguments", source)
        self.assertIn("positionalArguments.removeSubrange", source)
        self.assertIn("positionalArguments.count == 4", source)
        self.assertNotIn("guard arguments.count == 5", source)

    def test_helper_source_has_no_eventkit_write_symbols(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gtasks/mission_control_calendar_helper.swift").read_text()
        self.assertIn("requestFullAccessToEvents", source)
        self.assertNotIn("saveEvent", source)
        self.assertNotIn("removeEvent", source)
        self.assertNotIn("EKEvent(", source)
