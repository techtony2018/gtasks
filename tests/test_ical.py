import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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
    def test_helper_build_uses_a_stable_signing_identity(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "scripts" / "build-mission-control-calendar-helper.sh").read_text()

        self.assertIn("MISSION_CONTROL_CALENDAR_CODESIGN_IDENTITY", script)
        self.assertIn("Apple Development", script)
        self.assertNotIn("codesign --force --sign - ", script)

    @unittest.skipUnless(shutil.which("swiftc"), "Swift compiler is unavailable")
    def test_helper_source_typechecks(self) -> None:
        source = Path(__file__).resolve().parents[1] / "gtasks" / "mission_control_calendar_helper.swift"
        result = subprocess.run(
            ["swiftc", "-typecheck", str(source)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_helper_invocations_are_serialized_to_avoid_launchservices_output_races(self) -> None:
        helper = Path("/tmp/Mission Control Calendar.app/Contents/MacOS/MissionControlCalendar")
        reader = ICalendarReader(helper)
        completed = type("Completed", (), {"returncode": 0, "stdout": ""})()
        guard = threading.Lock()
        active = 0
        maximum = 0

        def complete(command, **_kwargs):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            Path(command[-1]).write_text('{"status":"authorized","calendars":[]}')
            with guard:
                active -= 1
            return completed

        with patch.object(Path, "is_file", return_value=True), patch(
            "gtasks.ical.subprocess.run", side_effect=complete
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: reader.calendars(), range(2)))

        self.assertEqual(maximum, 1)
        self.assertEqual(len(results), 2)

    def test_read_only_helper_recovers_from_repeated_transient_launchservices_failures(self) -> None:
        helper = Path("/tmp/Mission Control Calendar.app/Contents/MacOS/MissionControlCalendar")
        reader = ICalendarReader(helper)
        calls = 0

        def complete(command, **_kwargs):
            nonlocal calls
            calls += 1
            Path(command[-1]).write_text(
                '{}' if calls < 4 else '{"status":"authorized","calendars":[]}'
            )
            return type("Completed", (), {"returncode": 1 if calls < 4 else 0})()

        with patch.object(Path, "is_file", return_value=True), patch(
            "gtasks.ical.subprocess.run", side_effect=complete
        ), patch("gtasks.ical.time.sleep"):
            result = reader.calendars()

        self.assertEqual(calls, 4)
        self.assertEqual(result["status"], "authorized")

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

    def test_helper_serializes_read_only_event_time_and_detail_fields(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gtasks/mission_control_calendar_helper.swift").read_text()

        for field in (
            '"start"',
            '"end"',
            '"calendar_title"',
            '"location"',
            '"notes"',
            '"url"',
            '"recurrence"',
            '"availability"',
            '"timezone"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, source)
        self.assertIn("ISO8601DateFormatter", source)
