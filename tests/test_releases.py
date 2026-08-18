from pathlib import Path
import unittest

from gtasks import __version__
from gtasks.releases import CURRENT_RELEASE, RELEASES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README = PROJECT_ROOT / "README.md"
RUNBOOK = PROJECT_ROOT / "docs" / "runbooks" / "agent-handoff-dispatcher.md"
OPENCLAW_DELEGATION_RUNBOOK = PROJECT_ROOT / "docs" / "runbooks" / "openclaw-agent-delegation.md"
DOCUMENTATION_RUNBOOK = PROJECT_ROOT / "docs" / "runbooks" / "mission-control-system-documentation.md"
RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.77.md"
V078_RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.78.md"
V079_RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.79.md"
V080_RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.80.md"
V081_RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.81.md"
V082_RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.82.md"
V083_RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.83.md"
V084_RELEASE_EVIDENCE = PROJECT_ROOT / "docs" / "release-evidence" / "v0.0.84.md"


class ReleaseCatalogTests(unittest.TestCase):
    def test_initial_release_is_exactly_v0_0_1(self) -> None:
        self.assertEqual(RELEASES[0]["version"], "V0.0.1")

    def test_runtime_version_is_the_latest_catalog_entry(self) -> None:
        self.assertEqual(CURRENT_RELEASE["version"], RELEASES[-1]["version"])
        self.assertEqual(__version__, CURRENT_RELEASE["version"])

    def test_v0_0_100_records_bounded_long_open_refresh_scheduling(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.100")

        self.assertIn("Bounded long-open refresh scheduling", release["title"])
        self.assertIn("zero-delay auto-refresh loop", release["summary"])
        self.assertIn("30-minute refresh cadence", release["summary"])

    def test_v0_0_101_records_bounded_task_detail_read_recovery(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.101")

        self.assertIn("Bounded Task detail recovery", release["title"])
        self.assertIn("15-second", release["summary"])
        self.assertIn("without reloading Mission Control", release["summary"])

    def test_v0_0_99_records_completed_task_archive_boundary(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.99")

        self.assertIn("Completed task archive boundary", release["title"])
        self.assertIn("next Monday", release["summary"])
        self.assertIn("exact page/link readback", release["summary"])

    def test_v0_0_97_records_completion_preference_settings_view(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.97")

        self.assertIn("Settings view", release["title"])
        self.assertIn("Completion celebration", release["summary"])
        self.assertIn("dedicated Settings view", release["summary"])

    def test_v0_0_96_records_verified_completion_celebration(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.96")

        self.assertIn("Command Confirmation Sweep", release["title"])
        self.assertIn("explicit Task completion", release["summary"])
        self.assertIn("read back from GBrain", release["summary"])

    def test_v0_0_95_records_system_ticket_refresh_fanout_reduction(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.95")

        self.assertIn("last verified System Tickets snapshot", release["summary"])
        self.assertIn("skips completed-ticket hydration", release["summary"])
        self.assertIn("invalidates the ticket snapshots", release["summary"])

    def test_v0_0_94_records_completed_task_todo_reconciliation(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.94")

        self.assertIn("direct child TODOs", release["summary"])
        self.assertIn("legacy next-action TODOs", release["summary"])
        self.assertIn("same-status repair retries", release["summary"])
        self.assertIn("full Edit saves", release["summary"])

    def test_v0_0_93_records_faster_open_tickets_and_multiline_todos(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.93")

        self.assertIn("completed-ticket hydration fan-out", release["summary"])
        self.assertIn("completed pagination", release["summary"])
        self.assertIn("newline boundaries", release["summary"])

    def test_v0_0_92_records_reliable_long_lived_task_detail_opening(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.92")

        self.assertIn("Task detail busy panel", release["summary"])
        self.assertIn("one-read coalescing", release["summary"])
        self.assertIn("exact GBrain reconciliation", release["summary"])

    def test_v0_0_91_records_canonical_artifact_timestamp_reconciliation(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.91")

        self.assertIn("canonical updated_at", release["summary"])
        self.assertIn("server-managed metadata", release["summary"])
        self.assertIn("idempotent retry", release["summary"])

    def test_v0_0_90_records_bounded_rich_artifact_publication(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.90")

        self.assertIn("evidence-rich Markdown", release["summary"])
        self.assertIn("256 KiB", release["summary"])
        self.assertIn("generic 16 KiB mutation limit", release["summary"])

    def test_v0_0_89_records_reviewed_artifacts_and_newest_first_views(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.89")

        self.assertIn("explicit review-task Artifact links", release["summary"])
        self.assertIn("newest-updated", release["summary"])
        self.assertIn("canonical status", release["summary"])

    def test_v0_0_87_records_authority_mutation_timeout_repair(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.87")

        self.assertEqual(release["version"], "V0.0.87")
        self.assertIn("authority-backed dispatcher mutations", release["summary"])
        self.assertIn("fail-closed wake ordering", release["summary"])

    def test_v0_0_86_records_authenticated_local_dispatcher_preflight(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.86")

        self.assertEqual(release["version"], "V0.0.86")
        self.assertIn("authenticated canonical Agent registration", release["summary"])
        self.assertIn("no-side-effect POST", release["summary"])
        self.assertIn("before legacy replacement", release["summary"])

    def test_v0_0_85_records_openclaw_agents_and_temporary_delegation(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.85")

        self.assertEqual(release["version"], "V0.0.85")
        self.assertIn("three independent fixed-session OpenClaw Agents", release["summary"])
        self.assertIn("Tony-authorized time-bounded delegation", release["summary"])
        self.assertIn("preserves permanent ownership", release["summary"])
        self.assertIn("prioritizes each OpenClaw Agent's own work", release["summary"])

    def test_openclaw_delegation_documentation_contract_is_complete(self) -> None:
        documents = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (README, RUNBOOK, OPENCLAW_DELEGATION_RUNBOOK, DOCUMENTATION_RUNBOOK)
        )

        for identity in (
            "agents/tammy",
            "agents/timmy",
            "agents/toddy",
            "agents/tammy-oc",
            "agents/timmy-oc",
            "agents/toddy-oc",
        ):
            self.assertIn(identity, documents)
        normalized = documents.casefold()
        for required_contract in (
            "fixed session",
            "two-worker supervisor",
            "no default Goal",
            "owned work always outranks delegated work",
            "15 minutes through 7 days",
            "america/los_angeles",
            "--dry-run",
            "Tammy-OC canary",
            "disable only the affected OpenClaw worker",
            "preserve canonical leases and events",
            "leave the Codex worker running",
            "~/Library/Application Support/GTasks/handoff-dispatcher",
            "curl -fsS http://127.0.0.1:4179/api/health",
        ):
            self.assertIn(required_contract.casefold(), normalized)

    def test_v0_0_84_records_single_stargraph_style_word_art(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.84")

        self.assertEqual(release["version"], "V0.0.84")
        self.assertIn("single Mission Control typographic artword", release["summary"])
        self.assertIn("Memory Stargraph family style", release["summary"])
        self.assertIn("blue-white glowing lights", release["summary"])

    def test_v0_0_83_records_board_project_busy_detail_and_word_art_layering(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.83")

        self.assertEqual(release["version"], "V0.0.83")
        self.assertIn("Board drag-and-drop", release["summary"])
        self.assertIn("Project titles", release["summary"])
        self.assertIn("Task details immediately", release["summary"])
        self.assertIn("exterior light rings", release["summary"])

    def test_v0_0_82_records_framed_word_art_and_sidebar_order(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.82")

        self.assertEqual(release["version"], "V0.0.82")
        self.assertIn("word art", release["summary"])
        self.assertIn("dark HUD", release["summary"])
        self.assertIn("sidebar navigation", release["summary"])

    def test_v0_0_83_release_evidence_records_full_ticket_batch_and_qa_gate(self) -> None:
        evidence = V083_RELEASE_EVIDENCE.read_text(encoding="utf-8")

        self.assertIn("Board drag-and-drop", evidence)
        self.assertIn("Project titles", evidence)
        self.assertIn("busy state", evidence)
        self.assertIn("light rings", evidence)
        self.assertIn("Independent pre-commit UI/UX QA PASS", evidence)

    def test_v0_0_84_release_evidence_records_artword_scope_and_qa_gate(self) -> None:
        evidence = V084_RELEASE_EVIDENCE.read_text(encoding="utf-8")
        normalized = " ".join(evidence.split())

        self.assertIn("one typographic `Mission Control` artword", normalized)
        self.assertIn("Memory Stargraph family treatment", normalized)
        self.assertIn("glowing lights", normalized)
        self.assertIn("Independent pre-commit UI/UX QA PASS", normalized)
        self.assertIn("desktop 1440x1000", normalized)
        self.assertIn("genuine 390x844", normalized)

    def test_v0_0_81_records_calendar_and_todo_completion_controls(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.81")

        self.assertEqual(release["version"], "V0.0.81")
        self.assertIn("Calendar navigation", release["summary"])
        self.assertIn("TODO edits", release["summary"])
        self.assertIn("dark Mission Control HUD", release["summary"])

    def test_v0_0_80_records_completed_system_ticket_handoff_links(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.80")

        self.assertEqual(release["version"], "V0.0.80")
        self.assertIn("completed System Tickets", release["summary"])
        self.assertIn("exact canonical slug", release["summary"])

    def test_v0_0_79_records_verified_compact_handoff_task_links(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.79")

        self.assertEqual(release["version"], "V0.0.79")
        self.assertIn("verified server-projected Task references", release["summary"])
        self.assertIn("Unicode-safe truncation", release["summary"])
        self.assertIn("without redundant event-type labels", release["summary"])

    def test_v0_0_78_records_consolidated_handoff_history_surfaces(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.78")

        self.assertEqual(release["version"], "V0.0.78")
        self.assertIn("Task Timeline collapsed at the bottom", release["summary"])
        self.assertIn("removes self-navigation controls", release["summary"])
        self.assertIn("Agents surface", release["summary"])

    def test_v0_0_77_records_semantic_agent_answer_handoffs(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.77")

        self.assertEqual(release["version"], "V0.0.77")
        self.assertIn("Tony answers by semantic effect", release["summary"])
        self.assertIn("material answer revisions", release["summary"])
        self.assertIn("Tony-owned tasks", release["summary"])

    def test_v0_0_76_records_event_driven_agent_handoffs(self) -> None:
        release = next(item for item in RELEASES if item["version"] == "V0.0.76")

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
            [f"V0.0.{index}" for index in range(1, len(RELEASES) + 1)],
        )

    def test_dispatcher_runbook_declares_one_audit_source_and_exact_resume_boundary(self) -> None:
        self.assertTrue(RUNBOOK.is_file(), "Dispatcher runbook must exist")
        runbook = RUNBOOK.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")

        one_source = (
            "Task Timeline and Agents Handoff History are read-only projections "
            "over the same append-only handoff event table."
        )
        self.assertIn(one_source, " ".join(runbook.split()))
        self.assertIn(one_source, " ".join(readme.split()))
        self.assertIn("docs/runbooks/agent-handoff-dispatcher.md", readme)
        self.assertIn(
            "codex exec resume --skip-git-repo-check <fixed-thread-id> <prompt> --json",
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
            "http://127.0.0.1:4179/api/handoffs/",
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
        self.assertIn("--python-path /absolute/path/to/python3", normalized)
        self.assertIn("--module-root /absolute/path/to/gtasks", normalized)
        self.assertIn("--runner-path /absolute/path/to/gtasks/gtasks/local_handoff_dispatcher.py", normalized)
        self.assertIn("/absolute/path/to/python3 -m gtasks.local_handoff_dispatcher", normalized)
        self.assertIn("must not use `/usr/bin/python3`", runbook)
        self.assertIn("pre-existing Agent thread's workspace", normalized)
        self.assertIn("independent paths", normalized)
        self.assertIn("WorkingDirectory", runbook)

    def test_release_evidence_records_verified_semantic_answer_handoff(self) -> None:
        self.assertTrue(RELEASE_EVIDENCE.is_file(), "V0.0.77 release evidence must exist")
        evidence = RELEASE_EVIDENCE.read_text(encoding="utf-8")
        normalized = " ".join(evidence.split())

        self.assertIn("Status: `VERIFIED`", evidence)
        self.assertIn("semantic Tony answer classification", normalized)
        self.assertIn("material answer revisions", normalized)
        self.assertIn("Tony-owned no-Agent tasks", normalized)
        self.assertIn("Focused automated evidence: PASS", normalized)
        self.assertIn("Independent pre-commit UI/UX QA: PASS", normalized)
        self.assertIn("v0.0.77-repair-independent/gate-report-final.md", evidence)
        self.assertIn("0e8bc0bd4591eadbd6126dd5b4f6a792394148849520e0264b95fa97d3d6939f", evidence)

    def test_v0_0_78_release_evidence_records_handoff_surface_gate_scope(self) -> None:
        self.assertTrue(V078_RELEASE_EVIDENCE.is_file(), "V0.0.78 release evidence must exist")
        evidence = V078_RELEASE_EVIDENCE.read_text(encoding="utf-8")
        normalized = " ".join(evidence.split())

        self.assertIn("Task Timeline at the bottom", normalized)
        self.assertIn("collapsed by default", normalized)
        self.assertIn("suppress self-navigation controls", normalized)
        self.assertIn("Agents is the only user-facing handoff surface", normalized)
        self.assertIn("real three-event task shape", normalized)
        self.assertIn("genuine mobile 390x844", normalized)

    def test_v0_0_79_release_evidence_records_verified_task_link_gate_scope(self) -> None:
        self.assertTrue(V079_RELEASE_EVIDENCE.is_file(), "V0.0.79 release evidence must exist")
        self.assertTrue(V080_RELEASE_EVIDENCE.is_file(), "V0.0.80 release evidence must exist")
        self.assertTrue(V081_RELEASE_EVIDENCE.is_file(), "V0.0.81 release evidence must exist")
        self.assertTrue(V082_RELEASE_EVIDENCE.is_file(), "V0.0.82 release evidence must exist")
        evidence = V079_RELEASE_EVIDENCE.read_text(encoding="utf-8")
        normalized = " ".join(evidence.split())

        self.assertIn("verified server-projected Task references", normalized)
        self.assertIn("Task unavailable", normalized)
        self.assertIn("Unicode-safe deterministic truncation", normalized)
        self.assertIn("Independent pre-commit UI/UX QA", normalized)
        self.assertIn("desktop 1440x1000", normalized)
        self.assertIn("genuine mobile 390x844", normalized)

    def test_v0_0_81_release_evidence_records_calendar_todo_and_hud_gate_scope(self) -> None:
        self.assertTrue(V081_RELEASE_EVIDENCE.is_file(), "V0.0.81 release evidence must exist")
        evidence = V081_RELEASE_EVIDENCE.read_text(encoding="utf-8")
        normalized = " ".join(evidence.split())

        self.assertIn("Calendar navigation", normalized)
        self.assertIn("Show iCal Events", normalized)
        self.assertIn("Save & Mark Done", normalized)
        self.assertIn("Save & Complete Task", normalized)
        self.assertIn("dark Mission Control HUD", normalized)
        self.assertIn("Independent pre-commit UI/UX QA", normalized)
        self.assertIn("desktop 1440x1000", normalized)
        self.assertIn("genuine mobile 390x844", normalized)

    def test_v0_0_82_release_evidence_records_word_art_and_sidebar_gate_scope(self) -> None:
        self.assertTrue(V082_RELEASE_EVIDENCE.is_file(), "V0.0.82 release evidence must exist")
        evidence = V082_RELEASE_EVIDENCE.read_text(encoding="utf-8")
        normalized = " ".join(evidence.split())

        self.assertIn("HUD-style frame", normalized)
        self.assertIn("lights/glow/illumination", normalized)
        self.assertIn("Today, Calendar, Board, Inbox, Agents, Artifacts, Blocked, Completed, All Tasks, Projects, Goals", normalized)
        self.assertIn("Independent pre-commit UI/UX QA", normalized)
        self.assertIn("desktop 1440x1000", normalized)
        self.assertIn("genuine mobile 390x844", normalized)

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
