import unittest

from gtasks import __version__
from gtasks.releases import CURRENT_RELEASE, RELEASES


class ReleaseCatalogTests(unittest.TestCase):
    def test_initial_release_is_exactly_v0_0_1(self) -> None:
        self.assertEqual(RELEASES[0]["version"], "V0.0.1")

    def test_runtime_version_is_the_latest_catalog_entry(self) -> None:
        self.assertEqual(CURRENT_RELEASE["version"], RELEASES[-1]["version"])
        self.assertEqual(__version__, CURRENT_RELEASE["version"])
        self.assertEqual(__version__, "V0.0.7")

    def test_v0_0_2_records_the_verified_task_visibility_release(self) -> None:
        release = RELEASES[1]

        self.assertEqual(release["version"], "V0.0.2")
        self.assertIn("every valid task", release["summary"])
        self.assertIn("exactly once", release["summary"])
        self.assertIn("lifecycle relationships", release["summary"])

    def test_v0_0_3_records_only_the_two_confirmed_iteration_features(self) -> None:
        release = RELEASES[2]

        self.assertEqual(release["version"], "V0.0.3")
        self.assertIn("Next Action editing", release["summary"])
        self.assertIn("every 30 minutes", release["summary"])
        self.assertIn("defers while hidden", release["summary"])

    def test_every_release_has_user_facing_history_fields(self) -> None:
        versions: set[str] = set()
        for release in RELEASES:
            self.assertRegex(release["version"], r"^V\d+\.\d+\.\d+$")
            self.assertRegex(release["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(release["title"].strip())
            self.assertTrue(release["summary"].strip())
            self.assertNotIn("commit", release["summary"].lower())
            self.assertNotIn(release["version"], versions)
            versions.add(release["version"])

    def test_release_history_is_strictly_sequential_patch_only(self) -> None:
        self.assertEqual(
            [release["version"] for release in RELEASES],
            [
                "V0.0.1",
                "V0.0.2",
                "V0.0.3",
                "V0.0.4",
                "V0.0.5",
                "V0.0.6",
                "V0.0.7",
            ],
        )

    def test_v0_0_4_records_durable_projects_and_read_latency_work(self) -> None:
        release = RELEASES[3]

        self.assertEqual(release["version"], "V0.0.4")
        self.assertIn("verified GBrain project creation", release["summary"])
        self.assertIn("bounded parallel reads", release["summary"])

    def test_v0_0_5_records_immediate_status_and_scoped_projects(self) -> None:
        release = RELEASES[4]

        self.assertEqual(release["version"], "V0.0.5")
        self.assertIn("canonical GBrain readback immediately", release["summary"])
        self.assertIn("typed members of Tony's Projects", release["summary"])

    def test_v0_0_6_records_goal_lifecycle_and_read_coalescing(self) -> None:
        release = RELEASES[5]

        self.assertEqual(release["version"], "V0.0.6")
        self.assertIn("goal creation", release["summary"])
        self.assertIn("recoverable Delete", release["summary"])
        self.assertIn("coalesced duplicate initial loads", release["summary"])

    def test_v0_0_7_records_inbox_only_durable_warning_controls(self) -> None:
        release = RELEASES[6]

        self.assertEqual(release["version"], "V0.0.7")
        self.assertIn("warnings in Inbox", release["summary"])
        self.assertIn("restart-safe preferences", release["summary"])
        self.assertIn("without hiding canonical data", release["summary"])


if __name__ == "__main__":
    unittest.main()
