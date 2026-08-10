import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "openclaw-agents" / "heartbeats.json"
CHECKLIST_PATH = ROOT / "config" / "openclaw-agents" / "HEARTBEAT.md"


class OpenClawHeartbeatPolicyTests(unittest.TestCase):
    def test_all_openclaw_agents_have_isolated_hourly_fixed_session_policy(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

        self.assertEqual(policy["schema_version"], 1)
        agents = policy["agents"]
        self.assertEqual(
            {item["id"] for item in agents},
            {"tammy-oc", "timmy-oc", "toddy-oc"},
        )
        for item in agents:
            heartbeat = item["heartbeat"]
            self.assertEqual(heartbeat["every"], "1h")
            self.assertEqual(
                heartbeat["session"],
                f"agent:{item['id']}:mission-control",
            )
            self.assertEqual(heartbeat["target"], "none")
            self.assertTrue(heartbeat["skipWhenBusy"])
            self.assertFalse(heartbeat["isolatedSession"])
            self.assertTrue(heartbeat["lightContext"])
            self.assertIn("HEARTBEAT.md", heartbeat["prompt"])

    def test_checklist_is_read_only_and_preserves_dispatcher_authority(self) -> None:
        checklist = CHECKLIST_PATH.read_text(encoding="utf-8")

        self.assertIn("Mission Control hourly reconciliation", checklist)
        self.assertIn("read-only", checklist)
        self.assertIn("owned work first", checklist)
        self.assertIn("delegated work second", checklist)
        self.assertIn("Do not claim, execute, or mutate", checklist)
        self.assertIn("HEARTBEAT_OK", checklist)
        self.assertNotIn("raw GBrain", checklist)


if __name__ == "__main__":
    unittest.main()
