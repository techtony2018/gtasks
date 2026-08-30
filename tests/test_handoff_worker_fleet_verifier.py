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
                    "schema_version": 2,
                    "workers": [
                        {
                            "name": "timmy",
                            "transport": "ssh",
                            "ssh_target": "toddy@timmy-host.test",
                            "expected_agent_slug": "agents/timmy",
                            "expected_route": "hosts/timmy",
                            "config_path": "/private/timmy/dispatcher.json",
                            "repo_path": "/Users/toddy/gtasks",
                            "launch_label": "com.tony.gtasks-handoff-dispatcher",
                        },
                        {
                            "name": "toddy",
                            "transport": "ssh",
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
            if command[:3] == ["tailscale", "status", "--json"]:
                return verifier.CompletedProbe(1, "", "tailscale unavailable")
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

    def test_local_worker_uses_direct_runtime_probe_without_ssh(self) -> None:
        verifier = load_verifier()
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        payload["workers"] = [{
            "name": "tammy",
            "transport": "local",
            "expected_agent_slug": "agents/tammy",
            "expected_route": "hosts/tammy",
            "config_path": "/private/tammy/dispatcher.json",
            "repo_path": str(ROOT),
            "launch_label": "com.tony.gtasks-handoff-dispatcher",
        }]
        self.inventory.write_text(json.dumps(payload), encoding="utf-8")
        commands: list[list[str]] = []

        def run(command, **_kwargs):
            commands.append(list(command))
            return verifier.CompletedProbe(
                0,
                json.dumps({
                    "ok": True,
                    "agent_slug": "agents/tammy",
                    "expected_route": "hosts/tammy",
                    "route": "hosts/tammy",
                    "issues": [],
                }),
                "",
            )

        report = verifier.verify_fleet(
            inventory_path=self.inventory,
            expected_commit="abc123",
            run=run,
        )

        self.assertTrue(report["ok"])
        self.assertNotEqual(commands[0][0], "ssh")
        self.assertIn("verify_handoff_worker_runtime.py", commands[0][1])
        self.assertEqual(report["workers"][0]["transport"], "local")

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

    def test_ssh_failure_reports_tailscale_peer_state_when_available(self) -> None:
        verifier = load_verifier()
        expected_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        payload["workers"] = [payload["workers"][1]]
        payload["workers"][0]["ssh_target"] = "toddy@100.117.212.20"
        self.inventory.write_text(json.dumps(payload), encoding="utf-8")

        def run(command, **_kwargs):
            if command[:3] == ["git", "rev-parse", "HEAD"]:
                return verifier.CompletedProbe(0, expected_head + "\n", "")
            if command[:3] == ["tailscale", "status", "--json"]:
                return verifier.CompletedProbe(
                    0,
                    json.dumps(
                        {
                            "Peer": {
                                "nodekey:test": {
                                    "HostName": "Toddy's Mac Mini-1",
                                    "DNSName": "toddys-mac-mini-1.tail.test.",
                                    "TailscaleIPs": ["100.117.212.20"],
                                    "Online": True,
                                    "Expired": True,
                                    "LastSeen": "2026-08-22T21:46:08Z",
                                }
                            }
                        }
                    ),
                    "",
                )
            if command[0] == "ssh":
                return verifier.CompletedProbe(255, "", "Operation timed out")
            raise AssertionError(command)

        report = verifier.verify_fleet(inventory_path=self.inventory, run=run)

        worker = report["workers"][0]
        self.assertFalse(worker["ok"])
        self.assertIn("ssh_unreachable", worker["issues"])
        self.assertIn("tailscale_key_expired", worker["issues"])
        self.assertEqual(worker["tailscale_peer"]["dns"], "toddys-mac-mini-1.tail.test.")
        self.assertTrue(worker["tailscale_peer"]["online"])


if __name__ == "__main__":
    unittest.main()
