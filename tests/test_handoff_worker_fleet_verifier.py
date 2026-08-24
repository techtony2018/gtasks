from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify_handoff_worker_fleet.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_handoff_worker_fleet", VERIFIER_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("handoff worker fleet verifier module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HandoffWorkerFleetVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workers": [
                        {
                            "name": "timmy",
                            "ssh_target": "toddy@timmy-host.test",
                            "expected_agent_slug": "agents/timmy",
                            "expected_route": "hosts/timmy",
                            "config_path": "/private/timmy/dispatcher.json",
                            "repo_path": "/Users/toddy/gtasks",
                            "launch_label": "com.tony.gtasks-handoff-dispatcher",
                        },
                        {
                            "name": "toddy",
                            "ssh_target": "toddy@toddy-host.test",
                            "expected_agent_slug": "agents/toddy",
                            "expected_route": "hosts/toddy",
                            "config_path": "/private/toddy/dispatcher.json",
                            "repo_path": "/Users/toddy/gtasks",
                            "launch_label": "com.tony.gtasks-handoff-dispatcher",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_fleet_report_keeps_success_and_unreachable_host_distinct(self) -> None:
        verifier = load_verifier()
        calls: list[list[str]] = []

        def run(command, **_kwargs):
            calls.append(list(command))
            if command[0] == "ssh" and "toddy@timmy-host.test" in command:
                return verifier.CompletedProbe(
                    0,
                    json.dumps(
                        {
                            "ok": True,
                            "agent_slug": "agents/timmy",
                            "expected_route": "hosts/timmy",
                            "route": "hosts/timmy",
                            "issues": [],
                        }
                    ),
                    "",
                )
            if command[0] == "ssh" and "toddy@toddy-host.test" in command:
                return verifier.CompletedProbe(255, "", "Operation timed out")
            raise AssertionError(command)

        report = verifier.verify_fleet(
            inventory_path=self.inventory,
            expected_commit="abc123",
            run=run,
            ssh_timeout=7,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"], {"ok": 1, "failed": 1})
        self.assertEqual(report["workers"][0]["name"], "timmy")
        self.assertTrue(report["workers"][0]["ok"])
        self.assertEqual(report["workers"][1]["name"], "toddy")
        self.assertFalse(report["workers"][1]["ok"])
        self.assertIn("ssh_unreachable", report["workers"][1]["issues"])
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("token", encoded.lower())
        self.assertNotIn("private-thread", encoded)
        self.assertTrue(any("-o" in call for call in calls))

    def test_inventory_requires_exact_non_secret_schema(self) -> None:
        verifier = load_verifier()
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        payload["workers"][0]["token"] = "must-not-enter-inventory"
        self.inventory.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "secret|exact"):
            verifier.load_inventory(self.inventory)

    def test_remote_verifier_failure_is_not_reported_as_ssh_unreachable(self) -> None:
        verifier = load_verifier()

        def run(command, **_kwargs):
            self.assertIn("toddy@timmy-host.test", command)
            return verifier.CompletedProbe(
                1,
                json.dumps(
                    {
                        "ok": False,
                        "agent_slug": "agents/timmy",
                        "expected_route": "hosts/timmy",
                        "route": "hosts/timmy",
                        "issues": ["repo_head_mismatch"],
                    }
                ),
                "",
            )

        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        payload["workers"] = [payload["workers"][0]]
        self.inventory.write_text(json.dumps(payload), encoding="utf-8")

        report = verifier.verify_fleet(inventory_path=self.inventory, run=run)

        self.assertFalse(report["ok"])
        self.assertEqual(report["workers"][0]["issues"], ["repo_head_mismatch"])

    def test_default_expected_commit_is_current_local_head(self) -> None:
        verifier = load_verifier()
        expected_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        commands: list[list[str]] = []

        def run(command, **_kwargs):
            commands.append(list(command))
            return verifier.CompletedProbe(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "agent_slug": "agents/timmy",
                        "expected_route": "hosts/timmy",
                        "route": "hosts/timmy",
                        "repo_head": expected_head,
                        "issues": [],
                    }
                ),
                "",
            )

        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        payload["workers"] = [payload["workers"][0]]
        self.inventory.write_text(json.dumps(payload), encoding="utf-8")

        report = verifier.verify_fleet(inventory_path=self.inventory, run=run)

        self.assertTrue(report["ok"])
        remote_script = commands[0][-1]
        self.assertIn(expected_head, remote_script)
        self.assertNotIn('"$expected"', remote_script)


if __name__ == "__main__":
    unittest.main()
