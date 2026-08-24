from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify_handoff_worker_runtime.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_handoff_worker_runtime", VERIFIER_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("handoff worker verifier module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HandoffWorkerRuntimeVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.token = self.root / "token"
        self.token.write_text("private-token\n", encoding="utf-8")
        self.token.chmod(0o600)
        self.config = self.root / "dispatcher.json"
        self.registration_id = "private-registration"
        self.registration_ref = hashlib.sha256(
            self.registration_id.encode("utf-8")
        ).hexdigest()
        self.config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "agent_slug": "agents/timmy",
                    "registration_id": self.registration_id,
                    "fixed_thread_id": "private-thread",
                    "mission_control_url": "https://mission-control.test",
                    "token_file": str(self.token),
                }
            ),
            encoding="utf-8",
        )
        self.config.chmod(0o600)

    def test_preflight_report_is_safe_and_identifies_stale_checkout(self) -> None:
        verifier = load_verifier()

        def opener(request, *, timeout):
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(
                request.full_url,
                "https://mission-control.test/api/handoffs/preflight",
            )
            self.assertEqual(request.headers["Authorization"], "Bearer private-token")

            class Response:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self):
                    return json.dumps(
                        {
                            "verified": True,
                            "agent_slug": "agents/timmy",
                            "registration_ref": self_registration_ref,
                            "route": "hosts/timmy",
                        }
                    ).encode("utf-8")

            self_registration_ref = self.registration_ref
            return Response()

        def run(command, **_kwargs):
            if command[:2] == ["git", "-C"] and command[-2:] == ["rev-parse", "HEAD"]:
                return verifier.CompletedProbe(0, "old-head\n", "")
            if command[:2] == ["launchctl", "list"]:
                return verifier.CompletedProbe(
                    0, "123\t0\tcom.tony.gtasks-handoff-dispatcher\n", ""
                )
            raise AssertionError(f"unexpected command: {command!r}")

        report = verifier.verify_worker_runtime(
            config_path=self.config,
            expected_agent_slug="agents/timmy",
            expected_commit="new-head",
            repo_path=self.root / "gtasks",
            launch_label="com.tony.gtasks-handoff-dispatcher",
            opener=opener,
            run=run,
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["agent_slug"], "agents/timmy")
        self.assertEqual(report["route"], "hosts/timmy")
        self.assertEqual(report["repo_head"], "old-head")
        self.assertIn("repo_head_mismatch", report["issues"])
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("private-token", encoded)
        self.assertNotIn(self.registration_id, encoded)
        self.assertNotIn("private-thread", encoded)
        self.assertIn(self.registration_ref[:12], encoded)

    def test_unreachable_preflight_is_precise_without_leaking_credentials(self) -> None:
        verifier = load_verifier()

        def opener(_request, *, timeout):
            raise TimeoutError("timed out")

        report = verifier.verify_worker_runtime(
            config_path=self.config,
            expected_agent_slug="agents/timmy",
            opener=opener,
            run=lambda *_args, **_kwargs: verifier.CompletedProbe(0, "", ""),
        )

        self.assertFalse(report["ok"])
        self.assertIn("preflight_unreachable", report["issues"])
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("private-token", encoded)
        self.assertNotIn(self.registration_id, encoded)

    def test_identity_mismatch_fails_closed_generically(self) -> None:
        verifier = load_verifier()

        def opener(request, *, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

        report = verifier.verify_worker_runtime(
            config_path=self.config,
            expected_agent_slug="agents/toddy",
            opener=opener,
            run=lambda *_args, **_kwargs: verifier.CompletedProbe(0, "", ""),
        )

        self.assertFalse(report["ok"])
        self.assertIn("agent_identity_mismatch", report["issues"])
        self.assertIn("preflight_forbidden", report["issues"])


if __name__ == "__main__":
    unittest.main()
