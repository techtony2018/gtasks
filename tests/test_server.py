import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from gtasks.domain import (
    ACTIVE_ROOT,
    COMPLETED_ROOT,
    GOALS_ROOT,
    PROJECTS_ROOT,
    Goal,
    Project,
    Task,
    new_inbox_task,
)
from gtasks.gbrain import (
    CollectionRead,
    GoalLinkReceipt,
    GoalDeletionReceipt,
    GoalMutationReceipt,
    GoalRead,
    GoalRelationshipRead,
    MutationReceipt,
    NextActionMutationReceipt,
    PartialMutationError,
    StatusMutationReceipt,
    MembershipRepairReceipt,
    ProjectAssignmentReceipt,
    ProjectMutationReceipt,
    ProjectRead,
)
from gtasks.server import build_server
from gtasks.warnings import WarningDismissalStore


class FakeAdapter:
    def __init__(
        self,
        active: tuple[Task, ...] = (),
        completed: tuple[Task, ...] = (),
        goals: tuple[Goal, ...] = (),
        projects: tuple[Project, ...] = (),
    ) -> None:
        self.active = active
        self.completed = completed
        self.goals = goals
        self.projects = projects
        self.created: list[Task] = []
        self.goal_links: list[tuple[str, str | None]] = []
        self.status_updates: list[tuple[str, str, datetime]] = []
        self.next_action_updates: list[tuple[str, str, datetime]] = []
        self.membership_repairs: list[str] = []
        self.created_projects: list[Project] = []
        self.project_assignments: list[tuple[str, str | None]] = []
        self.created_goals: list[Goal] = []
        self.paused_goals: list[str] = []
        self.deleted_goals: list[str] = []
        self.collection_reads: list[str] = []

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        self.collection_reads.append(root_slug)
        tasks = self.active if root_slug == ACTIVE_ROOT else self.completed
        return CollectionRead(root_slug=root_slug, tasks=tasks)

    def create_inbox(self, task: Task) -> MutationReceipt:
        self.created.append(task)
        return MutationReceipt(slug=task.slug, verified=True)

    def list_goals(self) -> GoalRead:
        return GoalRead(goals=self.goals)

    def create_goal(self, goal: Goal) -> GoalMutationReceipt:
        self.created_goals.append(goal)
        self.goals = (*self.goals, goal)
        return GoalMutationReceipt(goal_slug=goal.slug, goal=goal, verified=True)

    def set_goal_paused(self, goal_slug: str) -> GoalMutationReceipt:
        goal = next(goal for goal in self.goals if goal.slug == goal_slug)
        paused = replace(goal, status="paused")
        self.goals = tuple(
            paused if candidate.slug == goal_slug else candidate
            for candidate in self.goals
        )
        self.paused_goals.append(goal_slug)
        return GoalMutationReceipt(goal_slug=goal_slug, goal=paused, verified=True)

    def delete_goal(self, goal_slug: str) -> GoalDeletionReceipt:
        self.goals = tuple(goal for goal in self.goals if goal.slug != goal_slug)
        self.deleted_goals.append(goal_slug)
        return GoalDeletionReceipt(
            goal_slug=goal_slug,
            removed_task_links=(),
            recoverable_until_hours=72,
            verified=True,
        )

    def list_projects(self) -> ProjectRead:
        return ProjectRead(projects=self.projects)

    def create_project(self, project: Project) -> ProjectMutationReceipt:
        self.created_projects.append(project)
        self.projects = (*self.projects, project)
        return ProjectMutationReceipt(project_slug=project.slug, verified=True)

    def set_task_project(
        self,
        task_slug: str,
        project_slug: str | None,
    ) -> ProjectAssignmentReceipt:
        self.project_assignments.append((task_slug, project_slug))
        return ProjectAssignmentReceipt(
            task_slug=task_slug,
            project_slug=project_slug,
            verified=True,
        )

    def set_task_goal(self, task_slug: str, goal_slug: str | None) -> GoalLinkReceipt:
        self.goal_links.append((task_slug, goal_slug))
        return GoalLinkReceipt(task_slug=task_slug, goal_slug=goal_slug, verified=True)

    def read_goal_relationships(self, goal_slug: str) -> GoalRelationshipRead:
        goal = next(goal for goal in self.goals if goal.slug == goal_slug)
        return GoalRelationshipRead(
            goal_slug=goal_slug,
            task_slugs=goal.advanced_by,
        )

    def set_task_status(
        self,
        task_slug: str,
        status: str,
        now: datetime,
    ) -> StatusMutationReceipt:
        self.status_updates.append((task_slug, status, now))
        existing = next(
            (task for task in (*self.active, *self.completed) if task.slug == task_slug),
            new_inbox_task("Ship GTasks", now, "status1"),
        )
        stored_task = replace(
            existing,
            slug=task_slug,
            status=status,
            completed_at=now if status == "completed" else None,
        )
        return StatusMutationReceipt(
            task_slug=task_slug,
            status=status,
            lifecycle_root=ACTIVE_ROOT,
            completed_at=now if status == "completed" else None,
            task=stored_task,
            verified=True,
        )

    def set_task_next_action(
        self,
        task_slug: str,
        next_action: str,
        now: datetime,
    ) -> NextActionMutationReceipt:
        self.next_action_updates.append((task_slug, next_action, now))
        return NextActionMutationReceipt(
            task_slug=task_slug,
            next_action=next_action.strip(),
            verified=True,
        )

    def repair_active_membership(self, task_slug: str) -> MembershipRepairReceipt:
        self.membership_repairs.append(task_slug)
        return MembershipRepairReceipt(task_slug=task_slug, verified=True)


def sample_goal(slug: str = "goals/ship-product") -> Goal:
    return Goal(
        slug=slug,
        title="Ship the product",
        status="planned",
        outcome="Ship the product.",
        success_criteria="V1 is in daily use.",
        target_day=date(2026, 9, 30),
        strategy="Deliver the smallest useful loop.",
        review_cadence="weekly",
        constraints="GBrain remains canonical.",
    )


def sample_project(slug: str = "projects/ship-product") -> Project:
    return Project(
        slug=slug,
        title="Ship the product",
        status="active",
        summary="Ship the product",
    )


class ServerHarness:
    def __init__(
        self,
        test_case: unittest.TestCase,
        adapter: FakeAdapter,
        warning_store: WarningDismissalStore | None = None,
    ) -> None:
        self.closed = False
        if warning_store is None:
            self.warning_directory = tempfile.TemporaryDirectory()
            test_case.addCleanup(self.warning_directory.cleanup)
            warning_store = WarningDismissalStore(
                Path(self.warning_directory.name) / "warning-state.json",
                user_id="test-user",
            )
        self.server = build_server(
            host="127.0.0.1",
            port=0,
            adapter=adapter,
            clock=lambda: datetime(2026, 7, 30, 9, 15).astimezone(),
            identity_factory=lambda: "a1b2c3",
            warning_store=warning_store,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        test_case.addCleanup(self.close)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        body: dict | str | None = None,
    ) -> tuple[int, dict, dict[str, str]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=3
        )
        headers: dict[str, str] = {}
        payload = None
        if isinstance(body, dict):
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        elif isinstance(body, str):
            payload = body
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        parsed = json.loads(raw) if raw else {}
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        return response.status, parsed, response_headers


class HealthApiTests(unittest.TestCase):
    def test_health_declares_gbrain_as_canonical_store_and_due_default(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload["canonical_store"], "gbrain")
        self.assertEqual(payload["default_due_day"], "task_creation_day")
        self.assertEqual(payload["default_goal_target_day"], "end_of_creation_quarter")
        self.assertEqual(payload["mutations"], "explicit_user_actions_only")
        self.assertEqual(payload["version"], "V0.0.7")

    def test_release_history_is_served_from_the_canonical_catalog(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("GET", "/api/releases")

        self.assertEqual(status, 200)
        self.assertEqual(payload["current_version"], "V0.0.7")
        self.assertEqual(payload["releases"][0]["version"], "V0.0.7")
        self.assertEqual(payload["releases"][1]["version"], "V0.0.6")
        self.assertEqual(payload["releases"][2]["version"], "V0.0.5")
        self.assertEqual(payload["releases"][3]["version"], "V0.0.4")
        self.assertEqual(payload["releases"][4]["version"], "V0.0.3")
        self.assertEqual(payload["releases"][5]["version"], "V0.0.2")
        self.assertEqual(payload["releases"][6]["version"], "V0.0.1")


class TasksApiTests(unittest.TestCase):
    def test_initial_reads_coalesce_through_short_cache_and_refresh_bypasses(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        first, _, _ = harness.request("GET", "/api/tasks")
        second, _, _ = harness.request("GET", "/api/tasks")
        refreshed, _, _ = harness.request("GET", "/api/tasks?refresh=1")

        self.assertEqual((first, second, refreshed), (200, 200, 200))
        self.assertEqual(adapter.collection_reads.count(ACTIVE_ROOT), 2)
        self.assertEqual(adapter.collection_reads.count(COMPLETED_ROOT), 2)

    def test_returns_empty_root_scoped_views_without_sample_tasks(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, headers = harness.request("GET", "/api/tasks")

        self.assertEqual(status, 200)
        self.assertEqual(payload["tasks"], [])
        self.assertEqual(payload["today"]["in_progress"], [])
        self.assertEqual(payload["today"]["todays_actions"], [])
        self.assertEqual(payload["views"]["inbox"], [])
        self.assertEqual(payload["goals"], [])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_returns_real_tasks_in_today_and_navigation_views(self) -> None:
        today_task = new_inbox_task(
            "Ship GTasks",
            datetime(2026, 7, 30, 9, 15).astimezone(),
            "a1b2c3",
        )
        future_task = replace(
            today_task,
            slug="tasks/future",
            summary="Prepare follow-up",
            title="Prepare follow-up",
            due_day=date(2026, 8, 2),
            inbox=False,
        )
        harness = ServerHarness(self, FakeAdapter(active=(today_task, future_task)))

        status, payload, _ = harness.request("GET", "/api/tasks")

        self.assertEqual(status, 200)
        self.assertEqual(
            [task["slug"] for task in payload["today"]["todays_actions"]],
            [today_task.slug],
        )
        self.assertEqual(
            [task["slug"] for task in payload["views"]["upcoming"]],
            [future_task.slug],
        )

    def test_snapshot_preserves_visible_relationship_warnings(self) -> None:
        task = new_inbox_task(
            "Visible despite warning",
            datetime(2026, 7, 30, 9, 15).astimezone(),
            "a1b2c3",
        )

        class WarningAdapter(FakeAdapter):
            def list_collection_tasks(self, root_slug: str) -> CollectionRead:
                if root_slug == ACTIVE_ROOT:
                    from gtasks.gbrain import CollectionIssue

                    return CollectionRead(
                        root_slug=root_slug,
                        tasks=(task,),
                        issues=(
                            CollectionIssue(
                                slug=task.slug,
                                message="Optional relationships were not loaded.",
                                severity="warning",
                            ),
                        ),
                    )
                return CollectionRead(root_slug=root_slug, tasks=())

        harness = ServerHarness(self, WarningAdapter())

        status, payload, _ = harness.request("GET", "/api/tasks")

        self.assertEqual(status, 200)
        self.assertEqual([item["slug"] for item in payload["tasks"]], [task.slug])
        self.assertEqual(payload["issues"][0]["severity"], "warning")
        self.assertEqual(len(payload["issues"][0]["fingerprint"]), 64)
        self.assertFalse(payload["issues"][0]["dismissed"])

    def test_warning_dismissal_survives_refresh_restart_and_changed_issue(self) -> None:
        task = new_inbox_task(
            "Visible despite warning",
            datetime(2026, 7, 30, 9, 15).astimezone(),
            "a1b2c3",
        )

        class WarningAdapter(FakeAdapter):
            def __init__(self, message: str) -> None:
                super().__init__(active=(task,))
                self.message = message

            def list_collection_tasks(self, root_slug: str) -> CollectionRead:
                from gtasks.gbrain import CollectionIssue

                if root_slug == ACTIVE_ROOT:
                    return CollectionRead(
                        root_slug=root_slug,
                        tasks=(task,),
                        issues=(
                            CollectionIssue(
                                slug=task.slug,
                                message=self.message,
                                severity="warning",
                                impact="Task remains visible.",
                            ),
                        ),
                    )
                return CollectionRead(root_slug=root_slug, tasks=())

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "warning-state.json"
        first = ServerHarness(
            self,
            WarningAdapter("Optional relationship is stale."),
            WarningDismissalStore(path, user_id="tony"),
        )

        status, initial, _ = first.request("GET", "/api/tasks")
        fingerprint = initial["issues"][0]["fingerprint"]
        dismissed_status, receipt, _ = first.request(
            "POST",
            "/api/warnings/dismiss",
            {"fingerprint": fingerprint},
        )
        refresh_status, refreshed, _ = first.request("GET", "/api/tasks?refresh=1")

        self.assertEqual((status, dismissed_status, refresh_status), (200, 200, 200))
        self.assertTrue(receipt["verified"])
        self.assertTrue(refreshed["issues"][0]["dismissed"])
        self.assertEqual([item["slug"] for item in refreshed["tasks"]], [task.slug])

        first.close()
        restarted = ServerHarness(
            self,
            WarningAdapter("Optional relationship is stale."),
            WarningDismissalStore(path, user_id="tony"),
        )
        _, after_restart, _ = restarted.request("GET", "/api/tasks")
        self.assertTrue(after_restart["issues"][0]["dismissed"])

        restarted.close()
        changed = ServerHarness(
            self,
            WarningAdapter("Optional relationship target is now missing."),
            WarningDismissalStore(path, user_id="tony"),
        )
        _, changed_payload, _ = changed.request("GET", "/api/tasks")
        changed_issue = changed_payload["issues"][0]
        self.assertNotEqual(changed_issue["fingerprint"], fingerprint)
        self.assertFalse(changed_issue["dismissed"])
        self.assertEqual([item["slug"] for item in changed_payload["tasks"]], [task.slug])

        restore_status, restored, _ = changed.request(
            "POST",
            "/api/warnings/restore",
            {"fingerprint": fingerprint},
        )
        self.assertEqual(restore_status, 200)
        self.assertTrue(restored["verified"])
        self.assertFalse(restored["dismissed"])

    def test_dedupes_collection_tasks_before_today_and_navigation_views(self) -> None:
        task = new_inbox_task(
            "Appear once",
            datetime(2026, 7, 30, 9, 15).astimezone(),
            "a1b2c3",
        )
        upcoming = replace(
            task,
            slug="tasks/upcoming-once",
            due_day=date(2026, 8, 2),
        )
        harness = ServerHarness(
            self,
            FakeAdapter(
                active=(task, task, upcoming, upcoming),
            ),
        )

        status, payload, _ = harness.request("GET", "/api/tasks")

        self.assertEqual(status, 200)
        self.assertEqual(
            [item["slug"] for item in payload["today"]["todays_actions"]],
            [task.slug],
        )
        self.assertEqual(
            [item["slug"] for item in payload["views"]["upcoming"]],
            [upcoming.slug],
        )
        self.assertEqual(
            [item["slug"] for item in payload["tasks"]],
            [task.slug, upcoming.slug],
        )

    def test_legacy_waiting_tasks_are_visible_in_the_blocked_view(self) -> None:
        waiting_task = replace(
            new_inbox_task(
                "Wait for an external reply",
                datetime(2026, 7, 30, 9, 15).astimezone(),
                "a1b2c3",
            ),
            status="waiting",
        )
        harness = ServerHarness(self, FakeAdapter(active=(waiting_task,)))

        status, payload, _ = harness.request("GET", "/api/tasks")

        self.assertEqual(status, 200)
        self.assertEqual(
            [task["slug"] for task in payload["views"]["blocked"]],
            [waiting_task.slug],
        )

    def test_goal_progress_links_active_and_completed_tasks(self) -> None:
        goal = sample_goal()
        task = replace(
            new_inbox_task(
                "Ship GTasks",
                datetime(2026, 7, 30, 9, 15).astimezone(),
                "a1b2c3",
            ),
            goal=goal.slug,
        )
        finished = replace(
            task,
            slug="tasks/finished",
            status="completed",
            lifecycle_root=COMPLETED_ROOT,
        )
        harness = ServerHarness(
            self,
            FakeAdapter(active=(task,), completed=(finished,), goals=(goal,)),
        )

        status, payload, _ = harness.request("GET", "/api/tasks")

        self.assertEqual(status, 200)
        progress = payload["goals"][0]
        self.assertEqual([item["slug"] for item in progress["active_tasks"]], [task.slug])
        self.assertEqual(
            [item["slug"] for item in progress["completed_tasks"]],
            [finished.slug],
        )
        self.assertEqual(progress["progress"]["completed"], 1)
        self.assertEqual(progress["progress"]["linked"], 2)

    def test_goal_progress_keeps_legacy_forward_links_until_detail_hydration(self) -> None:
        goal = sample_goal()
        explicit = new_inbox_task(
            "Explicit reciprocal task",
            datetime(2026, 7, 30, 9, 15).astimezone(),
            "a1b2c3",
        )
        legacy = replace(
            explicit,
            slug="tasks/legacy-one-way",
            title="Legacy one-way task",
            summary="Legacy one-way task",
            goal=goal.slug,
        )
        harness = ServerHarness(
            self,
            FakeAdapter(active=(explicit, legacy), goals=(goal,)),
        )

        status, payload, _ = harness.request("GET", "/api/tasks")

        self.assertEqual(status, 200)
        progress = payload["goals"][0]
        self.assertEqual(
            [item["slug"] for item in progress["active_tasks"]],
            [legacy.slug],
        )


class GoalRelationshipApiTests(unittest.TestCase):
    def test_reads_explicit_reciprocal_tasks_for_one_goal(self) -> None:
        goal = replace(sample_goal(), advanced_by=("tasks/explicit",))
        harness = ServerHarness(self, FakeAdapter(goals=(goal,)))

        status, payload, _ = harness.request(
            "GET",
            "/api/goals/goals%2Fship-product/relationships",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["goal_slug"], goal.slug)
        self.assertEqual(payload["task_slugs"], ["tasks/explicit"])


class GoalMutationApiTests(unittest.TestCase):
    def test_creates_goal_with_quarter_end_default_and_exact_user_fields(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/goals",
            {
                "title": "Launch the pilot",
                "outcome": "The pilot is live.",
                "success_criteria": "Ten users complete the workflow.",
                "strategy": "Ship one validated slice each week.",
                "review_cadence": "weekly",
                "constraints": "No customer data leaves the local system.",
            },
        )

        self.assertEqual(status, 201)
        self.assertTrue(payload["receipt"]["verified"])
        self.assertEqual(payload["goal"]["target_day"], "2026-09-30")
        self.assertEqual(adapter.created_goals[0].strategy, "Ship one validated slice each week.")

    def test_pause_and_delete_are_explicit_verified_actions(self) -> None:
        goal = sample_goal()
        adapter = FakeAdapter(goals=(goal,))
        harness = ServerHarness(self, adapter)

        pause_status, pause_payload, _ = harness.request(
            "PATCH",
            f"/api/goals/{goal.slug.replace('/', '%2F')}/status",
            {"status": "paused"},
        )
        delete_status, delete_payload, _ = harness.request(
            "DELETE",
            f"/api/goals/{goal.slug.replace('/', '%2F')}",
        )

        self.assertEqual(pause_status, 200)
        self.assertEqual(pause_payload["receipt"]["goal"]["status"], "paused")
        self.assertEqual(delete_status, 200)
        self.assertEqual(delete_payload["receipt"]["recoverable_until_hours"], 72)


class QuickAddApiTests(unittest.TestCase):
    def test_omitted_due_date_defaults_to_tonys_local_creation_day(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST", "/api/tasks", {"title": "Book the venue"}
        )

        self.assertEqual(status, 201)
        self.assertEqual(adapter.created[0].due_day, date(2026, 7, 30))
        self.assertEqual(payload["due_day_source"], "task_creation_day")
        self.assertEqual(payload["task"]["due_day"], "2026-07-30")

    def test_explicit_due_date_is_preserved(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {"title": "Book the venue", "due_day": "2026-08-04"},
        )

        self.assertEqual(status, 201)
        self.assertEqual(adapter.created[0].due_day, date(2026, 8, 4))
        self.assertEqual(payload["due_day_source"], "explicit")

    def test_rejects_invalid_due_date(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {"title": "Book the venue", "due_day": "next Tuesday"},
        )

        self.assertEqual(status, 422)
        self.assertIn("due_day", payload["error"])

    def test_rejects_invalid_json(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("POST", "/api/tasks", "{bad json")

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Request body must be valid JSON.")


class GoalLinkApiTests(unittest.TestCase):
    def test_links_a_task_to_an_approved_goal(self) -> None:
        adapter = FakeAdapter(goals=(sample_goal(),))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/goal",
            {"goal_slug": "goals/ship-product"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            adapter.goal_links,
            [("tasks/ship-gtasks", "goals/ship-product")],
        )
        self.assertTrue(payload["receipt"]["verified"])

    def test_rejects_non_string_goal_selection(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/goal",
            {"goal_slug": 42},
        )

        self.assertEqual(status, 422)
        self.assertIn("goal_slug", payload["error"])


class TaskStatusApiTests(unittest.TestCase):
    def test_updates_a_task_status_with_the_server_local_clock(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/status",
            {"status": "active"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["receipt"]["status"], "active")
        self.assertEqual(payload["receipt"]["task"]["status"], "active")
        self.assertEqual(adapter.status_updates[0][0:2], ("tasks/ship-gtasks", "active"))
        self.assertIsNotNone(adapter.status_updates[0][2].tzinfo)

    def test_rejects_an_unsupported_status_before_mutation(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/status",
            {"status": "someday"},
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_status")
        self.assertEqual(adapter.status_updates, [])

    def test_rejects_legacy_waiting_as_a_current_ui_status(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Flegacy-waiting/status",
            {"status": "waiting"},
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_status")
        self.assertEqual(adapter.status_updates, [])

    def test_reports_a_partial_status_write_with_the_task_slug(self) -> None:
        class PartialWriteAdapter(FakeAdapter):
            def set_task_status(
                self,
                task_slug: str,
                status: str,
                now: datetime,
            ) -> StatusMutationReceipt:
                raise PartialMutationError(
                    task_slug,
                    "GBrain status readback did not match the requested value.",
                )

        harness = ServerHarness(self, PartialWriteAdapter())

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/status",
            {"status": "active"},
        )

        self.assertEqual(status, 502)
        self.assertEqual(payload["code"], "partial_write")
        self.assertEqual(payload["slug"], "tasks/ship-gtasks")


class TaskNextActionApiTests(unittest.TestCase):
    def test_updates_and_clears_next_action_with_the_server_local_clock(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/next-action",
            {"next_action": "  Draft three STAR examples  "},
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["receipt"]["next_action"], "Draft three STAR examples")
        self.assertEqual(
            adapter.next_action_updates[0][0:2],
            ("tasks/ship-gtasks", "  Draft three STAR examples  "),
        )
        self.assertIsNotNone(adapter.next_action_updates[0][2].tzinfo)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/next-action",
            {"next_action": ""},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["receipt"]["next_action"], "")

    def test_rejects_non_text_or_overlong_next_action_before_mutation(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        for value in (42, "x" * 241):
            status, payload, _ = harness.request(
                "PATCH",
                "/api/tasks/tasks%2Fship-gtasks/next-action",
                {"next_action": value},
            )
            self.assertEqual(status, 422)
            self.assertEqual(payload["code"], "invalid_next_action")

        self.assertEqual(adapter.next_action_updates, [])

    def test_reports_a_rolled_back_next_action_write(self) -> None:
        class PartialWriteAdapter(FakeAdapter):
            def set_task_next_action(
                self,
                task_slug: str,
                next_action: str,
                now: datetime,
            ) -> NextActionMutationReceipt:
                raise PartialMutationError(
                    task_slug,
                    "GBrain next action readback failed. Rollback verified.",
                )

        harness = ServerHarness(self, PartialWriteAdapter())

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/next-action",
            {"next_action": "Draft three STAR examples"},
        )

        self.assertEqual(status, 502)
        self.assertEqual(payload["code"], "partial_write")
        self.assertEqual(payload["slug"], "tasks/ship-gtasks")


class ProjectApiTests(unittest.TestCase):
    def test_lists_durable_projects_even_without_assigned_tasks(self) -> None:
        harness = ServerHarness(
            self,
            FakeAdapter(projects=(sample_project(),)),
        )

        status, payload, _ = harness.request("GET", "/api/projects")

        self.assertEqual(status, 200)
        self.assertEqual(payload["root_slug"], PROJECTS_ROOT)
        self.assertEqual(
            [project["slug"] for project in payload["projects"]],
            ["projects/ship-product"],
        )

    def test_creates_a_project_only_after_verified_adapter_receipt(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/projects",
            {"title": "Interview preparation"},
        )

        self.assertEqual(status, 201)
        self.assertTrue(payload["receipt"]["verified"])
        self.assertEqual(adapter.created_projects[0].title, "Interview preparation")
        self.assertTrue(payload["project"]["slug"].startswith("projects/"))

    def test_assigns_a_task_to_a_durable_project_separately(self) -> None:
        adapter = FakeAdapter(projects=(sample_project(),))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/project",
            {"project_slug": "projects/ship-product"},
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["receipt"]["verified"])
        self.assertEqual(
            adapter.project_assignments,
            [("tasks/ship-gtasks", "projects/ship-product")],
        )


class TaskRelationshipRepairApiTests(unittest.TestCase):
    def test_repairs_an_unambiguous_active_membership(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Flegacy/relationships/active-membership",
            {},
        )

        self.assertEqual(status, 200)
        self.assertEqual(adapter.membership_repairs, ["tasks/legacy"])
        self.assertTrue(payload["receipt"]["verified"])


if __name__ == "__main__":
    unittest.main()
