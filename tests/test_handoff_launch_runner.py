from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from gtasks.handoff_launch_runner import (
    GatedLaunchController,
    LaunchRequest,
)


class GatedLaunchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.controller = GatedLaunchController(self.directory / "launches")

    def _wait_for(self, launch_id: str, state: str, *, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observed = self.controller.observe(launch_id)
            if observed.state == state:
                return observed
            time.sleep(0.02)
        self.fail(
            f"launch {launch_id} did not reach {state}: "
            f"{self.controller.observe(launch_id)}"
        )

    def _request(self, code: str, *, timeout: float = 2.0) -> LaunchRequest:
        return LaunchRequest(
            argv=(sys.executable, "-c", code),
            working_directory=str(self.directory),
            timeout_seconds=timeout,
        )

    def test_target_cannot_run_before_gate_and_success_is_atomic_and_private(self) -> None:
        marker = self.directory / "target-ran"
        launch_id = "launch/gated-success"
        self.controller.start(
            launch_id,
            self._request(
                "from pathlib import Path; Path('target-ran').write_text('once')"
            ),
        )

        ready = self._wait_for(launch_id, "ready")
        self.assertIsInstance(ready.pid, int)
        self.assertFalse(marker.exists())
        self.controller.open_gate(launch_id, "grant/gated-success")
        completed = self._wait_for(launch_id, "completed")

        self.assertEqual(marker.read_text(encoding="utf-8"), "once")
        self.assertEqual(completed.outcome, "completed")
        self.assertEqual(completed.returncode, 0)
        launch_directory = self.controller.launch_directory(launch_id)
        for name in ("request.json", "ready.json", "gate.json", "result.json"):
            path = launch_directory / name
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        result = json.loads((launch_directory / "result.json").read_text())
        self.assertEqual(
            set(result),
            {
                "schema_version",
                "launch_id",
                "outcome",
                "reason",
                "returncode",
                "finished_at",
            },
        )
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)

    def test_duplicate_shim_start_executes_fake_target_at_most_once(self) -> None:
        marker = self.directory / "count"
        code = (
            "from pathlib import Path; p=Path('count'); "
            "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
        )
        launch_id = "launch/duplicate-shim"
        self.controller.start(launch_id, self._request(code))
        ready = self._wait_for(launch_id, "ready")
        duplicate = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "gtasks" / "handoff_launch_runner.py"),
                "--launch-directory",
                str(self.controller.launch_directory(launch_id)),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertTrue(ready.runner_alive)

        self.controller.open_gate(launch_id, "grant/duplicate-shim")
        self._wait_for(launch_id, "completed")
        self.assertEqual(marker.read_text(encoding="utf-8"), "1")

    def test_missing_executable_is_proven_prelaunch_failure(self) -> None:
        launch_id = "launch/missing-executable"
        request = LaunchRequest(
            argv=(str(self.directory / "does-not-exist"),),
            working_directory=str(self.directory),
            timeout_seconds=2,
        )
        self.controller.start(launch_id, request)
        self._wait_for(launch_id, "ready")
        self.controller.open_gate(launch_id, "grant/missing-executable")

        failed = self._wait_for(launch_id, "prelaunch_failure")
        self.assertEqual(failed.outcome, "prelaunch_failure")
        self.assertEqual(failed.reason, "command_not_started")
        self.assertIsNone(failed.returncode)

    def test_nonzero_and_timeout_are_ambiguous_without_command_output(self) -> None:
        cases = (
            ("nonzero", "import sys; print('secret'); sys.exit(7)", 2.0, "nonzero_exit"),
            ("timeout", "import time; print('secret'); time.sleep(2)", 0.05, "timeout"),
        )
        for suffix, code, timeout, reason in cases:
            with self.subTest(suffix=suffix):
                launch_id = f"launch/{suffix}"
                self.controller.start(
                    launch_id, self._request(code, timeout=timeout)
                )
                self._wait_for(launch_id, "ready")
                self.controller.open_gate(launch_id, f"grant/{suffix}")

                ambiguous = self._wait_for(launch_id, "ambiguous")
                self.assertEqual(ambiguous.outcome, "ambiguous")
                self.assertEqual(ambiguous.reason, reason)
                result_path = self.controller.launch_directory(launch_id) / "result.json"
                rendered = result_path.read_text(encoding="utf-8")
                self.assertNotIn("secret", rendered)
                self.assertNotIn("stdout", rendered)
                self.assertNotIn("stderr", rendered)

    def test_cancel_before_gate_never_runs_target(self) -> None:
        marker = self.directory / "cancelled-target"
        launch_id = "launch/cancelled"
        self.controller.start(
            launch_id,
            self._request(
                "from pathlib import Path; Path('cancelled-target').write_text('bad')"
            ),
        )
        self._wait_for(launch_id, "ready")

        self.controller.cancel(launch_id)
        cancelled = self._wait_for(launch_id, "cancelled")

        self.assertEqual(cancelled.outcome, "cancelled")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
