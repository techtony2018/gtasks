from pathlib import Path
import unittest

from gtasks import __version__
from gtasks.releases import CURRENT_RELEASE, RELEASES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README = PROJECT_ROOT / "README.md"
RUNBOOK = PROJECT_ROOT / "docs" / "runbooks" / "agent-handoff-dispatcher.md"
RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.76.md"


class ReleaseCatalogTests(unittest.TestCase):
    def test_initial_release_is_exactly_v0_0_1(self) -> None:
        self.assertEqual(RELEASES[0]["version"], "V0.0.1")

    def test_runtime_version_is_the_latest_catalog_entry(self) -> None:
        self.assertEqual(CURRENT_RELEASE["version"], RELEASES[-1]["version"])
        self.assertEqual(__version__, CURRENT_RELEASE["version"])
        self.assertEqual(__version__, "V0.0.76")

    def test_v0_0_76_records_event_driven_agent_handoffs(self) -> None:
        release = RELEASES[-1]

        self.assertEqual(release["version"], "V0.0.76")
        self.assertIn("event-driven Agent handoffs", release["summary"])
        self.assertIn("Task Timeline and Handoff Log", release["summary"])
        self.assertIn("same redacted append-only audit events", release["summary"])

    def test_v0_0_65_records_canonical_per_task_todos(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.65")

        self.assertEqual(release["version"], "V0.0.65")
        self.assertIn("stable per-task TODO records", release["summary"])
        self.assertIn("append-only comments", release["summary"])
        self.assertIn("idempotent audit history", release["summary"])

    def test_v0_0_66_records_explicit_todo_entry_and_project_details(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.66")

        self.assertEqual(release["version"], "V0.0.66")
        self.assertIn("accessible Plus action", release["summary"])
        self.assertIn("Project card", release["summary"])
        self.assertIn("identity-preserving Edit", release["summary"])

    def test_v0_0_67_records_verified_agent_answer_handoffs(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.67")

        self.assertEqual(release["version"], "V0.0.67")
        self.assertIn("canonical Blocked task", release["summary"])
        self.assertIn("Answer and Hand Back", release["summary"])
        self.assertIn("same task", release["summary"])

    def test_v0_0_68_records_blocked_agent_work_visibility(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.68")

        self.assertEqual(release["version"], "V0.0.68")
        self.assertIn("Today and Blocked", release["summary"])
        self.assertIn("blocked Agent tasks", release["summary"])
        self.assertIn("deduplicated counts", release["summary"])

    def test_v0_0_69_records_progressive_controls_and_completed_history(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.69")

        self.assertEqual(release["version"], "V0.0.69")
        self.assertIn("Agent profile metadata readable", release["summary"])
        self.assertIn("Show completed ones", release["summary"])
        self.assertIn("five-ticket pages", release["summary"])

    def test_v0_0_70_records_canonical_read_only_agent_artifacts(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.70")

        self.assertEqual(release["version"], "V0.0.70")
        self.assertIn("GBrain is the only Artifact source of truth", release["summary"])
        self.assertIn("one producing-Agent collection membership", release["summary"])
        self.assertIn("typed Task and Agent provenance", release["summary"])
        self.assertIn("read-only", release["summary"])

    def test_v0_0_71_records_responsive_verified_workflow_surfaces(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.71")

        self.assertEqual(release["version"], "V0.0.71")
        self.assertIn("last-verified System Ticket view", release["summary"])
        self.assertIn("only pending proposed work", release["summary"])
        self.assertIn("rolling-month date scope", release["summary"])
        self.assertIn("Calendar restores", release["summary"])
        self.assertIn("TODO wording", release["summary"])

    def test_v0_0_72_records_navigable_artifacts_and_adaptive_details(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.72")

        self.assertEqual(release["version"], "V0.0.72")
        self.assertIn("Agent-to-Goal-to-Project-to-producing-Task", release["summary"])
        self.assertIn("Calendar event time/details", release["summary"])
        self.assertIn("resized and remembered", release["summary"])

    def test_v0_0_73_records_recoverable_agent_handoff_completion(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.73")

        self.assertEqual(release["version"], "V0.0.73")
        self.assertIn("clears the resolved handoff", release["summary"])
        self.assertIn("preserving its original completion time", release["summary"])

    def test_v0_0_74_records_verified_job_application_progress(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.74")

        self.assertEqual(release["version"], "V0.0.74")
        self.assertIn("explicit task binding", release["summary"])
        self.assertIn("preserved manual baseline", release["summary"])
        self.assertIn("privacy-safe activity receipts", release["summary"])
        self.assertIn("first activation", release["summary"])
        self.assertIn("legacy verified evidence", release["summary"])

    def test_v0_0_75_records_focused_artifact_and_calendar_navigation(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.75")

        self.assertEqual(release["version"], "V0.0.75")
        self.assertIn("producing Task", release["summary"])
        self.assertIn("compact title-only", release["summary"])
        self.assertIn("Default Goal", release["summary"])
        self.assertIn("healthy Calendar", release["summary"])

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
                "V0.0.57",
                "V0.0.58",
                "V0.0.59",
                "V0.0.60",
                "V0.0.61",
                "V0.0.62",
                "V0.0.63",
                "V0.0.64",
                "V0.0.65",
                "V0.0.66",
                "V0.0.67",
                "V0.0.68",
                "V0.0.69",
                "V0.0.70",
                "V0.0.71",
                "V0.0.72",
                "V0.0.73",
                "V0.0.74",
                "V0.0.75",
                "V0.0.76",
            ],
        )

    def test_dispatcher_runbook_declares_one_audit_source_and_exact_resume_boundary(self) -> None:
        self.assertTrue(RUNBOOK.is_file(), "Dispatcher runbook must exist")
        runbook = RUNBOOK.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")

        one_source = (
            "Task Timeline and Handoff Log are read-only projections over the same "
            "append-only handoff event table."
        )
        self.assertIn(one_source, " ".join(runbook.split()))
        self.assertIn(one_source, " ".join(readme.split()))
        self.assertIn("docs/runbooks/agent-handoff-dispatcher.md", readme)
        self.assertIn(
            "codex exec resume <fixed-thread-id> <prompt> --json",
            runbook,
        )
        self.assertIn(
            "never create, fork, replace, or guess a Codex thread",
            runbook,
        )

    def test_dispatcher_runbook_documents_retention_export_and_redaction(self) -> None:
        self.assertTrue(RUNBOOK.is_file(), "Dispatcher runbook must exist")
        runbook = RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("90-day default retention", runbook)
        self.assertIn("GET /api/handoff-events?export=1", runbook)
        self.assertIn("handoff-audit-v1", runbook)
        self.assertIn("registration_ref", runbook)
        for forbidden in ("bearer tokens", "raw registration ids", "fixed thread ids", "full prompts"):
            self.assertIn(forbidden, runbook)

    def test_dispatcher_runbook_documents_failure_recovery_and_rollback(self) -> None:
        self.assertTrue(RUNBOOK.is_file(), "Dispatcher runbook must exist")
        runbook = RUNBOOK.read_text(encoding="utf-8")
        normalized = " ".join(runbook.split())

        for required in (
            "retrying",
            "dead_letter",
            "Guardian",
            "does not roll back an already verified canonical GBrain mutation",
            "Rollback",
            "previous verified release",
        ):
            self.assertIn(required, normalized)

    def test_dispatcher_runbook_limits_tailnet_exposure_to_authenticated_handoffs(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        normalized = " ".join(runbook.split())

        self.assertIn("http://127.0.0.1:4179/", runbook)
        self.assertIn("https://tonys-macbook-pro.taildb46a7.ts.net", runbook)
        self.assertIn(
            "tailscale serve --bg --https=443 --set-path=/api/handoffs/ "
            "http://127.0.0.1:4179",
            normalized,
        )
        self.assertIn("only `/api/handoffs`", runbook)
        self.assertIn("HTTP 404", runbook)
        self.assertIn("HTTP 401", runbook)
        self.assertIn("HTTP 422", runbook)
        self.assertIn("invalid_handoff_claim", runbook)
        self.assertIn("valid bearer", normalized)

    def test_dispatcher_runbook_orders_canonical_registration_before_runtime_enablement(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        normalized = " ".join(runbook.split())

        for agent, route in (("tammy", "hosts/tammy"), ("timmy", "hosts/timmy"), ("toddy", "hosts/toddy")):
            self.assertIn(f"agents/{agent}", runbook)
            self.assertIn(f"route: {route}", runbook)
        self.assertGreaterEqual(runbook.count("registration_sha256:"), 3)
        self.assertGreaterEqual(runbook.count("verified: true"), 3)
        self.assertIn("gbrain put agents/tammy", runbook)
        self.assertIn("gbrain get agents/tammy", runbook)
        self.assertIn("exactly three unique routes", normalized)
        self.assertLess(
            runbook.index("gbrain put agents/tammy"),
            runbook.index("provision_handoff_dispatcher_credentials.py"),
        )
        self.assertLess(
            runbook.index("provision_handoff_dispatcher_credentials.py"),
            runbook.index("POST http://127.0.0.1:4188/api/services/gtasks/restart"),
        )

    def test_dispatcher_runbook_uses_verified_host_python_and_checkout_module(self) -> None:
        runbook = RUNBOOK.read_text(encoding="utf-8")
        normalized = " ".join(runbook.split())

        self.assertIn("Tammy, Timmy, and Toddy", runbook)
        self.assertIn("--python-path /usr/local/bin/python3", normalized)
        self.assertIn("--runner-path /absolute/path/to/gtasks/gtasks/local_handoff_dispatcher.py", normalized)
        self.assertIn("/usr/local/bin/python3 -m gtasks.local_handoff_dispatcher", normalized)
        self.assertIn("must not use `/usr/bin/python3`", runbook)
        self.assertIn("WorkingDirectory", runbook)

    def test_release_evidence_keeps_install_and_canary_work_explicitly_pending(self) -> None:
        self.assertTrue(RELEASE_EVIDENCE.is_file(), "V0.0.76 release evidence must exist")
        evidence = RELEASE_EVIDENCE.read_text(encoding="utf-8")
        normalized = " ".join(evidence.split())

        self.assertIn("PRECOMMIT_QA_PASSED", normalized)
        self.assertIn("Tammy, Timmy, and Toddy installations: NOT RUN", normalized)
        self.assertIn("Tammy-only canary: NOT RUN", normalized)
        self.assertIn("Do not canary Timmy or Toddy in V0.0.76", normalized)
        self.assertIn("No live Agent wake", normalized)
        self.assertIn("709 passed", normalized)
        self.assertIn("5 skipped", normalized)
        self.assertIn("Ran 52 tests", evidence)
        self.assertIn("git diff --check", evidence)
        self.assertIn("Source review: `APPROVED`", evidence)
        self.assertIn("no Critical or Important findings", normalized)
        self.assertIn("Independent pre-commit UI/UX QA", evidence)
        self.assertIn("explicit `PASS`", evidence)
        self.assertIn("desktop `1440x1000`", evidence)
        self.assertIn("genuine mobile `390x844`", evidence)
        self.assertIn(
            "`be4eee74ff413cd88194dcdff025a58c18e81e916b66a78c489c2bab9c4bb00e`",
            evidence,
        )
        self.assertIn("`b479...`", evidence)
        self.assertIn(
            "`b5db8a71ef8e389df743db1536fdc91b9ccdac750cf65817da6d7c5cb06331d1`",
            evidence,
        )
        self.assertIn("superseded", normalized)
        self.assertNotIn("V0.0.75", evidence)
        self.assertNotIn("task-6-report.md", evidence)

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
