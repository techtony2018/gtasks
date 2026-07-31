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
    AgentProfile,
    COMPLETED_ROOT,
    EventProgress,
    GOALS_ROOT,
    PROJECTS_ROOT,
    Goal,
    ProgressMetric,
    Project,
    Task,
    TaskProposal,
    new_inbox_task,
    new_task,
)
from gtasks.gbrain import (
    AgentRead,
    AgentWorkRead,
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
    TaskProgressMetricReceipt,
    MembershipRepairReceipt,
    ProjectAssignmentReceipt,
    ProjectMutationReceipt,
    ProjectRead,
    ProposalRead,
    ProposalMutationReceipt,
)
from gtasks.server import build_server
from gtasks.operational_logs import OperationalLogReader, OperationalLogStore
from gtasks.warnings import WarningDismissalStore


class FakeAdapter:
    def __init__(
        self,
        active: tuple[Task, ...] = (),
        completed: tuple[Task, ...] = (),
        goals: tuple[Goal, ...] = (),
        projects: tuple[Project, ...] = (),
        agents: tuple[AgentProfile, ...] = (),
        agent_work: tuple[dict, ...] = (),
        proposals: tuple[TaskProposal, ...] = (),
    ) -> None:
        self.active = active
        self.completed = completed
        self.goals = goals
        self.projects = projects
        self.agents = agents
        self.agent_work = agent_work
        self.proposals = proposals
        self.created: list[Task] = []
        self.created_agent_tasks: list[tuple[Task, str]] = []
        self.duplicated_from: list[str] = []
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
        self.proposal_reviews: list[tuple[str, str]] = []
        self.proposal_decisions: list[tuple[str, str, str]] = []
        self.default_goal_updates: list[tuple[str, str, bool]] = []

    def get_tony_profile(self) -> dict:
        return {
            "slug": "people/tony-guan",
            "name": "Tony Guan",
            "avatar": {
                "kind": "attachment",
                "value": "/media/people/tony-guan/Tony%20Profile.jpeg",
            },
        }

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        self.collection_reads.append(root_slug)
        tasks = self.active if root_slug == ACTIVE_ROOT else self.completed
        return CollectionRead(root_slug=root_slug, tasks=tasks)

    def create_inbox(self, task: Task) -> MutationReceipt:
        self.created.append(task)
        return MutationReceipt(slug=task.slug, verified=True)

    def create_task(self, task: Task) -> MutationReceipt:
        return self.create_inbox(task)

    def create_agent_task(
        self,
        task: Task,
        agent_slug: str,
    ) -> MutationReceipt:
        self.created_agent_tasks.append((task, agent_slug))
        return MutationReceipt(slug=task.slug, verified=True)

    def duplicate_task(
        self,
        source_slug: str,
        task: Task,
    ) -> MutationReceipt:
        self.duplicated_from.append(source_slug)
        return self.create_task(task)

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

    def update_goal(self, goal_slug: str, **changes) -> GoalMutationReceipt:
        goal = next(goal for goal in self.goals if goal.slug == goal_slug)
        updated = replace(goal, **changes)
        self.goals = tuple(updated if item.slug == goal_slug else item for item in self.goals)
        return GoalMutationReceipt(goal_slug=goal_slug, goal=updated, verified=True)

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

    def list_agent_profiles(self) -> AgentRead:
        return AgentRead(agents=self.agents)

    def list_agent_work(self) -> AgentWorkRead:
        return AgentWorkRead(tasks=self.agent_work)

    def get_agent_profile(self, agent_slug: str) -> AgentProfile:
        return next(agent for agent in self.agents if agent.slug == agent_slug)

    def set_agent_default_goal(
        self, agent_slug: str, goal_slug: str, *, assigned: bool
    ) -> AgentProfile:
        agent = self.get_agent_profile(agent_slug)
        goals = tuple(
            goal
            for goal in agent.default_goal_slugs
            if goal != goal_slug
        )
        if assigned:
            goals = (*goals, goal_slug)
        updated = replace(agent, default_goal_slugs=goals)
        self.agents = tuple(
            updated if item.slug == agent_slug else item
            for item in self.agents
        )
        self.default_goal_updates.append((agent_slug, goal_slug, assigned))
        return updated

    def list_proposals(self) -> ProposalRead:
        return ProposalRead(proposals=self.proposals)

    def review_proposal(
        self,
        proposal_slug: str,
        *,
        title: str,
        rationale: str,
        proposed_next_step: str,
        due_day: date,
        now: datetime,
    ) -> ProposalMutationReceipt:
        proposal = next(
            proposal
            for proposal in self.proposals
            if proposal.slug == proposal_slug
        )
        stored = replace(
            proposal,
            title=title,
            rationale=rationale,
            proposed_next_step=proposed_next_step,
            due_day=due_day,
            status="review",
            updated_at=now,
        )
        self.proposals = tuple(
            stored if item.slug == proposal_slug else item
            for item in self.proposals
        )
        self.proposal_reviews.append((proposal_slug, title))
        return ProposalMutationReceipt(
            proposal_slug=proposal_slug,
            status="review",
            proposal=stored,
            created_task=None,
            verified=True,
        )

    def decide_proposal(
        self,
        proposal_slug: str,
        *,
        action: str,
        decision_note: str,
        now: datetime,
    ) -> ProposalMutationReceipt:
        proposal = next(
            proposal
            for proposal in self.proposals
            if proposal.slug == proposal_slug
        )
        status = "approved" if action == "approve" else "rejected"
        stored = replace(
            proposal,
            status=status,
            reviewed_at=now,
            updated_at=now,
            decision_note=decision_note,
        )
        self.proposals = tuple(
            stored if item.slug == proposal_slug else item
            for item in self.proposals
        )
        self.proposal_decisions.append(
            (proposal_slug, action, decision_note)
        )
        return ProposalMutationReceipt(
            proposal_slug=proposal_slug,
            status=status,
            proposal=stored,
            created_task=None,
            verified=True,
        )

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

    def set_task_progress_metric(
        self,
        task_slug: str,
        progress_metric: ProgressMetric | None,
        event_progress: EventProgress | None,
        now: datetime,
    ) -> TaskProgressMetricReceipt:
        existing = next(
            task
            for task in (*self.active, *self.completed)
            if task.slug == task_slug
        )
        updated = replace(
            existing,
            progress_metric=progress_metric,
            event_progress=event_progress,
            updated_at=now,
        )
        self.active = tuple(
            updated if task.slug == task_slug else task for task in self.active
        )
        self.completed = tuple(
            updated if task.slug == task_slug else task for task in self.completed
        )
        return TaskProgressMetricReceipt(
            task_slug=task_slug,
            task=updated,
            verified=True,
        )


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


def sample_agent() -> AgentProfile:
    return AgentProfile(
        slug="agents/toddy",
        name="Toddy",
        title="Agent Toddy",
        summary="Coordinates approved work.",
        work_root="collections/toddys-tasks",
        default_goal_slugs=("goals/ship-product",),
        avatar_kind="initials",
        avatar_value="TO",
    )


def sample_proposal() -> TaskProposal:
    submitted = datetime.fromisoformat("2026-07-30T14:00:00-07:00")
    return TaskProposal(
        slug="proposals/toddy-wellbeing-check-in",
        title="Schedule a wellbeing check-in",
        status="proposed",
        recipient="tony",
        proposing_agent="agents/toddy",
        rationale="A check-in supports the wellbeing goal.",
        proposed_next_step="Choose a 20-minute time tomorrow.",
        due_day=date(2026, 7, 31),
        submitted_at=submitted,
        updated_at=submitted,
        linked_goal="goals/happier-and-healthier",
    )


class ServerHarness:
    def __init__(
        self,
        test_case: unittest.TestCase,
        adapter: FakeAdapter,
        warning_store: WarningDismissalStore | None = None,
        log_reader: OperationalLogReader | None = None,
    ) -> None:
        self.closed = False
        self.runtime_directory = tempfile.TemporaryDirectory()
        test_case.addCleanup(self.runtime_directory.cleanup)
        runtime_path = Path(self.runtime_directory.name)
        if warning_store is None:
            warning_store = WarningDismissalStore(
                runtime_path / "warning-state.json",
                user_id="test-user",
            )
        if log_reader is None:
            log_reader = OperationalLogReader(
                gtasks_store=OperationalLogStore(
                    runtime_path / "operational-events.jsonl"
                ),
                queue_path=runtime_path / "queue-events.jsonl",
                queue_health=lambda: {
                    "status": "unavailable",
                    "broker_connected": False,
                    "message": (
                        "Event Queue Reader status is unavailable. "
                        "GTasks remains available."
                    ),
                },
            )
        self.server = build_server(
            host="127.0.0.1",
            port=0,
            adapter=adapter,
            clock=lambda: datetime(2026, 7, 30, 9, 15).astimezone(),
            identity_factory=lambda: "a1b2c3",
            warning_store=warning_store,
            log_reader=log_reader,
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
        self.assertEqual(payload["operational_logs"], "privacy_safe_read_only")
        self.assertEqual(payload["queue_reader_dependency"], "optional")
        self.assertEqual(
            payload["agent_work_roots"],
            [
                "collections/toddys-tasks",
                "collections/timmys-tasks",
                "collections/tammys-tasks",
            ],
        )
        self.assertEqual(payload["version"], "V0.0.38")

    def test_release_history_is_served_from_the_canonical_catalog(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("GET", "/api/releases")

        self.assertEqual(status, 200)
        self.assertEqual(payload["current_version"], "V0.0.38")
        self.assertEqual(payload["releases"][0]["version"], "V0.0.38")
        self.assertEqual(
            [release["version"] for release in payload["releases"]],
            [
                "V0.0.38",
                "V0.0.37",
                "V0.0.36",
                "V0.0.35",
                "V0.0.34",
                "V0.0.33",
                "V0.0.32",
                "V0.0.31",
                "V0.0.30",
                "V0.0.29",
                "V0.0.28",
                "V0.0.27",
                "V0.0.26",
                "V0.0.25",
                "V0.0.24",
                "V0.0.23",
                "V0.0.22",
                "V0.0.21",
                "V0.0.20",
                "V0.0.19",
                "V0.0.18",
                "V0.0.17",
                "V0.0.16",
                "V0.0.15",
                "V0.0.14",
                "V0.0.13",
                "V0.0.12",
                "V0.0.11",
                "V0.0.10",
                "V0.0.9",
                "V0.0.8",
                "V0.0.7",
                "V0.0.6",
                "V0.0.5",
                "V0.0.4",
                "V0.0.3",
                "V0.0.2",
                "V0.0.1",
            ],
        )


class AgentGoalAssignmentApiTests(unittest.TestCase):
    def test_assign_and_remove_use_one_verified_canonical_agent_goal_path(self) -> None:
        agent = sample_agent()
        adapter = FakeAdapter(
            agents=(agent,),
            goals=(sample_goal(),),
        )
        harness = ServerHarness(self, adapter)
        path = "/api/agents/agents%2Ftoddy/default-goals"

        assigned_status, assigned, _ = harness.request(
            "POST",
            path,
            {"goal_slug": "goals/ship-product", "action": "assign"},
        )
        self.assertEqual(assigned_status, 200)
        self.assertTrue(assigned["verified"])
        self.assertIn("goals/ship-product", assigned["agent"]["default_goal_slugs"])

        removed_status, removed, _ = harness.request(
            "POST",
            path,
            {"goal_slug": "goals/ship-product", "action": "remove"},
        )
        self.assertEqual(removed_status, 200)
        self.assertTrue(removed["verified"])
        self.assertNotIn("goals/ship-product", removed["agent"]["default_goal_slugs"])
        self.assertEqual(
            adapter.default_goal_updates,
            [
                ("agents/toddy", "goals/ship-product", True),
                ("agents/toddy", "goals/ship-product", False),
            ],
        )


class LogsApiTests(unittest.TestCase):
    def test_logs_are_read_only_filtered_paginated_and_queue_optional(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        store = OperationalLogStore(root / "gtasks.jsonl")
        store.append(
            component="gtasks",
            severity="warning",
            message="A GBrain operation was unavailable.",
            now=datetime.fromisoformat("2026-07-30T09:00:00-07:00"),
        )
        store.append(
            component="gtasks",
            severity="info",
            message="GTasks runtime initialized.",
            now=datetime.fromisoformat("2026-07-30T08:59:00-07:00"),
        )
        queue_path = root / "queue.jsonl"
        queue_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "health": {
                        "broker_connected": True,
                        "pending": 2,
                        "ack_pending": 1,
                        "redelivered": 0,
                        "last_error_code": None,
                    },
                    "events": [
                        {
                            "timestamp": "2026-07-30T16:01:00+00:00",
                            "component": "handler",
                            "severity": "error",
                            "message": (
                                "Queue event processing failed; "
                                "queue retry remains active."
                            ),
                        },
                        {
                            "timestamp": "2026-07-30T16:00:00+00:00",
                            "component": "handler",
                            "severity": "error",
                            "message": (
                                "Applied to a private company with "
                                "password=must-never-appear"
                            ),
                        },
                    ],
                    "retention": {
                        "max_events": 100,
                        "order": "newest_first",
                        "storage": "atomic_file",
                    },
                }
            ),
            encoding="utf-8",
        )
        reader = OperationalLogReader(
            gtasks_store=store,
            queue_path=queue_path,
            queue_health=lambda: {
                "status": "connected",
                "broker_connected": True,
                "pending": 2,
                "ack_pending": 1,
                "redelivered": 0,
                "message": "Event Queue Reader is connected.",
            },
        )
        harness = ServerHarness(self, FakeAdapter(), log_reader=reader)

        status, first, _ = harness.request("GET", "/api/logs?limit=2")
        filtered_status, filtered, _ = harness.request(
            "GET",
            "/api/logs?severity=error&component=handler&limit=25",
        )

        self.assertEqual((status, filtered_status), (200, 200))
        self.assertTrue(first["read_only"])
        self.assertEqual(len(first["events"]), 2)
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(
            filtered["events"][0]["message"],
            "Queue event processing failed; queue retry remains active.",
        )
        self.assertNotIn("must-never-appear", json.dumps(filtered))
        self.assertEqual(filtered["queue_reader"]["status"], "connected")

    def test_logs_remain_available_when_queue_reader_is_unavailable(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)

        def unavailable_health() -> dict:
            raise ConnectionError("consumer unavailable")

        reader = OperationalLogReader(
            gtasks_store=OperationalLogStore(root / "gtasks.jsonl"),
            queue_path=root / "missing-queue.jsonl",
            queue_health=unavailable_health,
        )
        harness = ServerHarness(self, FakeAdapter(), log_reader=reader)

        log_status, logs, _ = harness.request("GET", "/api/logs")
        task_status, tasks, _ = harness.request("GET", "/api/tasks")

        self.assertEqual((log_status, task_status), (200, 200))
        self.assertEqual(logs["queue_reader"]["status"], "unavailable")
        self.assertIn("GTasks remains available", logs["queue_reader"]["message"])
        self.assertEqual(tasks["tasks"], [])
        self.assertEqual(tasks["owner"]["slug"], "people/tony-guan")
        self.assertEqual(
            tasks["owner"]["avatar"]["value"],
            "/media/people/tony-guan/Tony%20Profile.jpeg",
        )

    def test_logs_reject_unknown_filters(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, _, _ = harness.request("GET", "/api/logs?severity=debug")
        repeated_status, _, _ = harness.request(
            "GET",
            "/api/logs?component=gtasks&component=event_queue_reader",
        )

        self.assertEqual((status, repeated_status), (400, 400))

    def test_gbrain_runtime_failure_adds_only_a_safe_operational_message(self) -> None:
        class FailingAdapter(FakeAdapter):
            def list_collection_tasks(self, root_slug: str) -> CollectionRead:
                from gtasks.gbrain import GBrainError

                raise GBrainError(
                    "private task title and credential=must-never-appear"
                )

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        reader = OperationalLogReader(
            gtasks_store=OperationalLogStore(root / "gtasks.jsonl"),
            queue_path=root / "missing.json",
            queue_health=lambda: {"status": "unavailable"},
        )
        harness = ServerHarness(self, FailingAdapter(), log_reader=reader)

        failed_status, _, _ = harness.request("GET", "/api/tasks")
        log_status, logs, _ = harness.request(
            "GET",
            "/api/logs?component=gtasks&severity=error",
        )

        self.assertEqual((failed_status, log_status), (503, 200))
        self.assertEqual(logs["events"][0]["message"], "A GBrain operation was unavailable.")
        self.assertNotIn("private task title", json.dumps(logs))
        self.assertNotIn("must-never-appear", json.dumps(logs))


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
        self.assertNotIn("upcoming", payload["views"])
        self.assertIn(future_task.slug, [task["slug"] for task in payload["tasks"]])

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
        self.assertNotIn("upcoming", payload["views"])
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
            status="blocked",
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
    def test_edits_goal_with_verified_receipt(self) -> None:
        goal = sample_goal()
        harness = ServerHarness(self, FakeAdapter(goals=(goal,)))
        payload = {
            "title": "Ship the verified product",
            "outcome": "The verified product is live.",
            "success_criteria": "Ten users complete the workflow.",
            "strategy": "Ship one slice.",
            "review_cadence": "weekly",
            "constraints": "Keep data local.",
            "target_day": "2026-10-31",
        }
        status, response, _ = harness.request("PATCH", "/api/goals/goals%2Fship-product", payload)
        self.assertEqual(status, 200)
        self.assertTrue(response["receipt"]["verified"])
        self.assertEqual(response["goal"]["title"], payload["title"])
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


class FullTaskCreationApiTests(unittest.TestCase):
    def test_default_assignee_preserves_the_tony_task_path(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {
                "title": "Prepare interview notes",
                "detail": "",
                "priority": "normal",
                "next_action": "",
                "due_day": "2026-07-31",
                "project_slug": None,
                "goal_slug": None,
                "progress_metric": None,
                "assignee_slug": "tony",
            },
        )

        self.assertEqual(status, 201)
        self.assertEqual(len(adapter.created), 1)
        self.assertEqual(adapter.created_agent_tasks, [])
        self.assertIsNone(payload["task"]["owner_agent"])
        self.assertEqual(payload["task"]["lifecycle_root"], ACTIVE_ROOT)

    def test_each_agent_assignee_creates_only_one_scoped_agent_task(self) -> None:
        for agent_slug, work_root in (
            ("agents/toddy", "collections/toddys-tasks"),
            ("agents/timmy", "collections/timmys-tasks"),
            ("agents/tammy", "collections/tammys-tasks"),
        ):
            with self.subTest(agent=agent_slug):
                adapter = FakeAdapter(
                    agents=(
                        AgentProfile(
                            slug=agent_slug,
                            name=agent_slug.split("/")[-1].title(),
                            title=agent_slug.split("/")[-1].title(),
                            summary="",
                            work_root=work_root,
                            default_goal_slugs=(),
                        ),
                    )
                )
                harness = ServerHarness(self, adapter)

                status, payload, _ = harness.request(
                    "POST",
                    "/api/tasks",
                    {
                        "title": "Prepare a goal update",
                        "detail": "",
                        "priority": "normal",
                        "next_action": "Draft the update",
                        "due_day": "2026-07-31",
                        "project_slug": None,
                        "goal_slug": None,
                        "progress_metric": None,
                        "assignee_slug": agent_slug,
                    },
                )

                self.assertEqual(status, 201)
                self.assertEqual(adapter.created, [])
                self.assertEqual(len(adapter.created_agent_tasks), 1)
                task, stored_agent = adapter.created_agent_tasks[0]
                self.assertEqual(stored_agent, agent_slug)
                self.assertEqual(task.owner_agent, agent_slug)
                self.assertEqual(task.lifecycle_root, work_root)
                self.assertEqual(task.status, "planned")
                self.assertTrue(task.inbox)
                self.assertEqual(payload["task"]["owner_agent"], agent_slug)
                harness.close()

    def test_rejects_unknown_agent_assignment_without_writing(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {
                "title": "Unsafe assignment",
                "detail": "",
                "priority": "normal",
                "next_action": "",
                "due_day": "2026-07-31",
                "project_slug": None,
                "goal_slug": None,
                "progress_metric": None,
                "assignee_slug": "agents/unknown",
            },
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_task")
        self.assertEqual(adapter.created, [])
        self.assertEqual(adapter.created_agent_tasks, [])

    def test_creates_an_optional_manual_metric_without_auto_completion(self) -> None:
        adapter = FakeAdapter(
            projects=(sample_project(),),
            goals=(sample_goal(),),
        )
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {
                "title": "Apply for five companies",
                "detail": "Use the approved resume.",
                "priority": "high",
                "next_action": "Choose the next company",
                "due_day": "2026-07-31",
                "project_slug": "projects/ship-product",
                "goal_slug": "goals/ship-product",
                "progress_metric": {
                    "kind": "count",
                    "label": "Job applications",
                    "target": 5,
                    "current": 5,
                },
            },
        )

        self.assertEqual(status, 201)
        created = adapter.created[0]
        self.assertEqual(created.status, "planned")
        self.assertIsNone(created.completed_at)
        self.assertEqual(created.project, "projects/ship-product")
        self.assertEqual(created.goal, "goals/ship-product")
        self.assertEqual(created.progress_metric.label, "Job applications")
        self.assertEqual(created.progress_metric.current, 5)
        self.assertIsNone(created.event_progress)
        self.assertEqual(payload["task"]["progress_metric"]["current"], 5)

    def test_duplicate_review_resets_bound_progress_and_historical_receipts(self) -> None:
        source_metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 4,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            }
        )
        source = replace(
            new_task(
                title="Apply for five companies",
                due_day=date(2026, 7, 30),
                progress_metric=source_metric,
                event_progress=EventProgress(
                    evidence_slugs=(
                        "applications/one",
                        "applications/two",
                        "applications/three",
                        "applications/four",
                    ),
                    receipt_ids=("evt-1", "evt-2", "evt-3", "evt-4"),
                ),
                now=datetime(2026, 7, 30, 9, 15).astimezone(),
                identity="source1",
            ),
            status="active",
            inbox=False,
        )
        adapter = FakeAdapter(active=(source,))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            f"/api/tasks/{source.slug.replace('/', '%2F')}/duplicate",
            {
                "title": "Apply for five companies",
                "detail": source.detail,
                "priority": source.priority,
                "next_action": source.next_action,
                "due_day": "2026-07-31",
                "project_slug": None,
                "goal_slug": None,
                "progress_metric": {
                    "kind": "count",
                    "label": "Job applications",
                    "target": 5,
                    "current": 0,
                    "event_binding": "job_applied",
                    "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 201)
        duplicate = adapter.created[0]
        self.assertEqual(adapter.duplicated_from, [source.slug])
        self.assertNotEqual(duplicate.slug, source.slug)
        self.assertEqual(duplicate.status, "planned")
        self.assertIsNone(duplicate.completed_at)
        self.assertEqual(duplicate.due_day, date(2026, 7, 31))
        self.assertEqual(duplicate.progress_metric.current, 0)
        self.assertEqual(duplicate.progress_metric.task_day, date(2026, 7, 31))
        self.assertEqual(duplicate.event_progress.evidence_slugs, ())
        self.assertEqual(duplicate.event_progress.receipt_ids, ())
        self.assertTrue(payload["receipt"]["verified"])


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


class TaskProgressMetricApiTests(unittest.TestCase):
    def test_sets_daily_job_application_metric_with_verified_empty_evidence(
        self,
    ) -> None:
        now = datetime.fromisoformat("2026-07-30T09:00:00-07:00")
        task = new_task(
            title="Apply for five more companies",
            detail="Submit five strong applications.",
            priority="high",
            next_action="Submit the next application",
            due_day=date(2026, 7, 30),
            project=None,
            goal=None,
            progress_metric=None,
            event_progress=None,
            now=now,
            identity="metric01",
        )
        adapter = FakeAdapter(active=(task,))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            f"/api/tasks/{task.slug.replace('/', '%2F')}/progress-metric",
            {
                "task_day": "2026-07-30",
                "progress_metric": {
                    "kind": "count",
                    "label": "Job applications",
                    "target": 5,
                    "current": 0,
                    "event_binding": "job_applied",
                    "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 200)
        receipt = payload["receipt"]
        self.assertTrue(receipt["verified"])
        self.assertEqual(
            receipt["task"]["progress_metric"],
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            },
        )
        self.assertEqual(
            receipt["task"]["event_progress"],
            {"evidence_slugs": [], "receipt_ids": []},
        )

    def test_rejects_event_metric_without_explicit_task_day(self) -> None:
        now = datetime.fromisoformat("2026-07-30T09:00:00-07:00")
        task = new_inbox_task("Apply for five more companies", now, "metric02")
        adapter = FakeAdapter(active=(task,))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            f"/api/tasks/{task.slug.replace('/', '%2F')}/progress-metric",
            {
                "progress_metric": {
                    "kind": "count",
                    "label": "Job applications",
                    "target": 5,
                    "current": 0,
                    "event_binding": "job_applied",
                    "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_progress_metric")
        self.assertIsNone(adapter.active[0].progress_metric)


class AgentApiTests(unittest.TestCase):
    def test_profiles_and_agent_work_are_read_only_separate_endpoints(self) -> None:
        agent = sample_agent()
        task = new_inbox_task(
            "Draft wellbeing check-in",
            datetime.fromisoformat("2026-07-30T09:00:00-07:00"),
            "agent01",
        ).to_dict()
        task.update(
            {
                "owner": {
                    "slug": agent.slug,
                    "name": agent.name,
                    "avatar": {"kind": "initials", "value": "TO"},
                },
                "agent_work": True,
                "read_only": True,
            }
        )
        adapter = FakeAdapter(agents=(agent,), agent_work=(task,))
        harness = ServerHarness(self, adapter)

        status, profiles, _ = harness.request("GET", "/api/agents")
        self.assertEqual(status, 200)
        self.assertEqual(profiles["agents"][0]["name"], "Toddy")
        self.assertEqual(
            profiles["agents"][0]["default_goal_slugs"],
            ["goals/ship-product"],
        )

        status, work, _ = harness.request("GET", "/api/agent-work")
        self.assertEqual(status, 200)
        self.assertEqual(work["tasks"][0]["owner"]["name"], "Toddy")
        self.assertTrue(work["tasks"][0]["read_only"])
        self.assertEqual(adapter.created, [])
        self.assertEqual(adapter.status_updates, [])


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


class ProposalApiTests(unittest.TestCase):
    def test_lists_one_canonical_proposal_without_creating_work(self) -> None:
        adapter = FakeAdapter(proposals=(sample_proposal(),))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request("GET", "/api/proposals")

        self.assertEqual(status, 200)
        self.assertEqual(
            payload["proposals"][0]["proposing_agent"],
            "agents/toddy",
        )
        self.assertEqual(adapter.created, [])

    def test_reviews_then_explicitly_rejects_without_deleting_data(self) -> None:
        adapter = FakeAdapter(proposals=(sample_proposal(),))
        harness = ServerHarness(self, adapter)
        slug = "proposals%2Ftoddy-wellbeing-check-in"

        review_status, reviewed, _ = harness.request(
            "PATCH",
            f"/api/proposals/{slug}/review",
            {
                "title": "Schedule a short wellbeing check-in",
                "rationale": "This still supports the wellbeing goal.",
                "proposed_next_step": "Choose a 15-minute time tomorrow.",
                "due_day": "2026-07-31",
            },
        )
        decision_status, decided, _ = harness.request(
            "POST",
            f"/api/proposals/{slug}/decision",
            {"action": "reject", "decision_note": "Not needed this week."},
        )

        self.assertEqual(review_status, 200)
        self.assertEqual(reviewed["receipt"]["status"], "review")
        self.assertEqual(decision_status, 200)
        self.assertEqual(decided["receipt"]["status"], "rejected")
        self.assertEqual(len(adapter.proposals), 1)
        self.assertEqual(adapter.created, [])

    def test_unknown_or_implicit_decision_fails_safely(self) -> None:
        harness = ServerHarness(
            self,
            FakeAdapter(proposals=(sample_proposal(),)),
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/proposals/proposals%2Ftoddy-wellbeing-check-in/decision",
            {"action": "maybe"},
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_proposal_decision")


if __name__ == "__main__":
    unittest.main()
