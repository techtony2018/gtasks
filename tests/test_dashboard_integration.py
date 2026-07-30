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
            ],
        )
        self.assertEqual(contract["canonical_store"], "gbrain")
        self.assertFalse(contract["vendored_copy"])
        self.assertEqual(contract["managed_actions"], ["start", "stop", "restart"])


if __name__ == "__main__":
    unittest.main()
