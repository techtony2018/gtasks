import unittest

from gtasks import __version__
from gtasks.releases import CURRENT_RELEASE, RELEASES


class ReleaseCatalogTests(unittest.TestCase):
    def test_initial_release_is_exactly_v0_0_1(self) -> None:
        self.assertEqual(RELEASES[0]["version"], "V0.0.1")

    def test_runtime_version_is_the_latest_catalog_entry(self) -> None:
        self.assertEqual(CURRENT_RELEASE["version"], RELEASES[-1]["version"])
        self.assertEqual(__version__, CURRENT_RELEASE["version"])
        self.assertEqual(__version__, "V0.0.56")

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
                "V0.0.8",
                "V0.0.9",
                "V0.0.10",
                "V0.0.11",
                "V0.0.12",
                "V0.0.13",
                "V0.0.14",
                "V0.0.15",
                "V0.0.16",
                "V0.0.17",
                "V0.0.18",
                "V0.0.19",
                "V0.0.20",
                "V0.0.21",
                "V0.0.22",
                "V0.0.23",
                "V0.0.24",
                "V0.0.25",
                "V0.0.26",
                "V0.0.27",
                "V0.0.28",
                "V0.0.29",
                "V0.0.30",
                "V0.0.31",
                "V0.0.32",
                "V0.0.33",
                "V0.0.34",
                "V0.0.35",
                "V0.0.36",
                "V0.0.37",
                "V0.0.38",
                "V0.0.39",
                "V0.0.40",
                "V0.0.41",
                "V0.0.42",
                "V0.0.43",
                "V0.0.44",
                "V0.0.45",
                "V0.0.46",
                "V0.0.47",
                "V0.0.48",
                "V0.0.49",
                "V0.0.50",
                "V0.0.51",
                "V0.0.52",
                "V0.0.53",
                "V0.0.54",
                "V0.0.55",
                "V0.0.56",
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

    def test_v0_0_8_records_privacy_safe_operational_logs(self) -> None:
        release = RELEASES[7]

        self.assertEqual(release["version"], "V0.0.8")
        self.assertIn("read-only Logs dialog", release["summary"])
        self.assertIn("Event Queue Reader history and health", release["summary"])
        self.assertIn("never blocks core task workflows", release["summary"])

    def test_v0_0_9_records_optional_metrics_and_safe_duplication(self) -> None:
        release = RELEASES[8]

        self.assertEqual(release["version"], "V0.0.9")
        self.assertIn("optional count metric", release["summary"])
        self.assertIn("progress", release["summary"])
        self.assertIn("five distinct accepted events", release["summary"])

    def test_v0_0_10_records_agent_profiles_and_board_filter(self) -> None:
        release = RELEASES[9]

        self.assertEqual(release["version"], "V0.0.10")
        self.assertIn("Toddy, Timmy, and Tammy", release["summary"])
        self.assertIn("Show agent tasks", release["summary"])
        self.assertIn("read-only", release["summary"])

    def test_v0_0_11_records_confirmation_bound_agent_proposals(self) -> None:
        release = RELEASES[10]

        self.assertEqual(release["version"], "V0.0.11")
        self.assertIn("grouped by Toddy, Timmy, and Tammy", release["summary"])
        self.assertIn("explicit review, approval, and rejection", release["summary"])
        self.assertIn("creates no suggestions", release["summary"])

    def test_v0_0_12_records_explicit_agent_assignment(self) -> None:
        release = RELEASES[11]

        self.assertEqual(release["version"], "V0.0.12")
        self.assertIn("defaults to Tony", release["summary"])
        self.assertIn("assigned_to ownership", release["summary"])
        self.assertIn("no duplicate Tony task or proposal", release["summary"])
        self.assertIn("authoritative status updates", release["summary"])

    def test_v0_0_13_records_the_single_full_creation_flow(self) -> None:
        release = RELEASES[12]

        self.assertEqual(release["version"], "V0.0.13")
        self.assertIn("reduced Quick Add", release["summary"])
        self.assertIn("sidebar", release["summary"])
        self.assertIn("Today and Inbox", release["summary"])

    def test_v0_0_14_records_unified_task_editing(self) -> None:
        release = RELEASES[13]

        self.assertEqual(release["version"], "V0.0.14")
        self.assertIn("read-only until Edit", release["summary"])
        self.assertIn("associated goal", release["summary"])
        self.assertIn("assignment history", release["summary"])

    def test_v0_0_15_records_avatar_and_simplified_navigation(self) -> None:
        release = RELEASES[14]

        self.assertEqual(release["version"], "V0.0.15")
        self.assertIn("avatar", release["summary"].lower())
        self.assertIn("Upcoming", release["summary"])
        self.assertIn("text-first", release["summary"])

    def test_v0_0_16_records_safe_avatar_slug_validation_and_preview(self) -> None:
        release = RELEASES[15]

        self.assertEqual(release["version"], "V0.0.16")
        self.assertIn("exact selected Agent Directory slug", release["summary"])
        self.assertIn("local previews", release["summary"])
        self.assertIn("assign or unassign default goals", release["summary"])

    def test_v0_0_17_records_avatar_identity_preservation(self) -> None:
        release = RELEASES[16]

        self.assertEqual(release["version"], "V0.0.17")
        self.assertIn("canonical Agent identity", release["summary"])
        self.assertIn("cannot make Toddy, Timmy, or Tammy disappear", release["summary"])

    def test_v0_0_18_records_tony_board_avatar(self) -> None:
        release = RELEASES[17]

        self.assertEqual(release["version"], "V0.0.18")
        self.assertIn("Tony’s personal tasks", release["summary"])
        self.assertIn("canonical GBrain profile avatar", release["summary"])

    def test_v0_0_19_records_agent_surface_rendering_repair(self) -> None:
        release = RELEASES[18]

        self.assertEqual(release["version"], "V0.0.19")
        self.assertIn("Agent Work and Proposed Tasks", release["summary"])
        self.assertIn("previous view visible", release["summary"])

    def test_v0_0_20_records_clear_agent_profiles(self) -> None:
        release = RELEASES[19]

        self.assertEqual(release["version"], "V0.0.20")
        self.assertIn("current verified avatar", release["summary"])
        self.assertIn("structured content", release["summary"])
        self.assertIn("Agents navigation label", release["summary"])

    def test_v0_0_21_records_weekly_due_date_view(self) -> None:
        release = RELEASES[20]

        self.assertEqual(release["version"], "V0.0.21")
        self.assertIn("Monday-to-Sunday", release["summary"])
        self.assertIn("canonical GBrain due date", release["summary"])

    def test_v0_0_22_records_goal_editing(self) -> None:
        release = RELEASES[21]
        self.assertEqual(release["version"], "V0.0.22")
        self.assertIn("Goal details now include Edit", release["summary"])

    def test_v0_0_23_records_calendar_and_project_goal_links(self) -> None:
        release = RELEASES[22]
        self.assertEqual(release["version"], "V0.0.23")
        self.assertIn("Week and Month", release["summary"])

    def test_v0_0_24_records_same_task_agent_proposals(self) -> None:
        release = RELEASES[23]
        self.assertEqual(release["version"], "V0.0.24")
        self.assertIn("exact same task", release["summary"])

    def test_v0_0_25_records_read_only_coordination(self) -> None:
        release = RELEASES[24]
        self.assertEqual(release["version"], "V0.0.25")
        self.assertIn("read-only Coordinator", release["summary"])

    def test_v0_0_26_records_focused_proposal_review(self) -> None:
        release = RELEASES[25]
        self.assertEqual(release["version"], "V0.0.26")
        self.assertIn("title-only rows", release["summary"])

    def test_v0_0_27_records_wider_workspace(self) -> None:
        release = RELEASES[26]
        self.assertEqual(release["version"], "V0.0.27")
        self.assertIn("seven equal desktop day columns", release["summary"])


if __name__ == "__main__":
    unittest.main()
