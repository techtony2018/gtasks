import json
import tempfile
import unittest
from pathlib import Path

from gtasks.warnings import WarningDismissalStore, warning_fingerprint


class WarningFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_changes_with_meaningful_issue_content(self) -> None:
        issue = {
            "slug": "tasks/example",
            "message": "Goal reciprocal link is missing.",
            "severity": "warning",
            "category": "relationship",
            "impact": "The task remains visible.",
            "repair_action": "choose_goal",
        }

        self.assertEqual(warning_fingerprint(issue), warning_fingerprint(dict(issue)))
        self.assertNotEqual(
            warning_fingerprint(issue),
            warning_fingerprint(
                {
                    **issue,
                    "message": "Active collection membership is missing.",
                }
            ),
        )


class WarningDismissalStoreTests(unittest.TestCase):
    def test_dismissal_and_restore_are_durable_and_user_scoped(self) -> None:
        fingerprint = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            first = WarningDismissalStore(path, user_id="tony")
            self.assertTrue(first.dismiss(fingerprint))

            restarted = WarningDismissalStore(path, user_id="tony")
            self.assertEqual(restarted.dismissed(), {fingerprint})
            self.assertTrue(restarted.restore(fingerprint))
            self.assertEqual(
                WarningDismissalStore(path, user_id="tony").dismissed(),
                set(),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["user"], "tony")

    def test_state_owned_by_another_user_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            WarningDismissalStore(path, user_id="tony").dismiss("b" * 64)

            with self.assertRaisesRegex(RuntimeError, "another user"):
                WarningDismissalStore(path, user_id="someone-else").dismissed()


if __name__ == "__main__":
    unittest.main()
