import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DashboardIntegrationTests(unittest.TestCase):
    def test_contract_manages_the_existing_gbrain_backed_app(self) -> None:
        contract = json.loads(
            (PROJECT_ROOT / "dashboard-integration.json").read_text(encoding="utf-8")
        )

        self.assertEqual(contract["service_id"], "gtasks")
        self.assertEqual(contract["service_url"], "http://127.0.0.1:4179/")
        self.assertEqual(contract["health_path"], "/api/health")
        self.assertEqual(contract["logs_path"], "/api/logs")
        self.assertEqual(contract["logs_mode"], "privacy_safe_read_only")
        self.assertEqual(
            contract["queue_reader_observability_url"],
            "http://127.0.0.1:4181/api/observability",
        )
        self.assertTrue(
            contract["queue_reader_observability_artifact"].endswith(
                "gtasks-events/reader-observability.json"
            )
        )
        self.assertEqual(contract["working_directory"], str(PROJECT_ROOT))
        self.assertEqual(
            contract["command"],
            [
                "python3",
                "-m",
                "gtasks.server",
                "--host",
                "127.0.0.1",
                "--port",
                "4179",
                "--artifact-publisher-credentials-file",
                "/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/artifact-publisher-credentials.json",
            ],
        )
        self.assertEqual(contract["canonical_store"], "gbrain")
        self.assertFalse(contract["vendored_copy"])
        self.assertEqual(contract["managed_actions"], ["start", "stop", "restart"])


if __name__ == "__main__":
    unittest.main()
