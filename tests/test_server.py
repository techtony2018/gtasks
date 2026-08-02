import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import gtasks.gbrain as gbrain

from gtasks.domain import (
    ACTIVE_ROOT,
    AgentProfile,
    COMPLETED_ROOT,
    EventProgress,
    GOALS_ROOT,
    PROJECTS_ROOT,
    QA_FIXTURES_ROOT,
    Goal,
    ProgressMetric,
    Project,
    Task,
    TaskProposal,
    SystemTicket,
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
    SystemTicketRead,
    LifecycleIntegrityError,
)
from gtasks.server import build_server
from gtasks.read_cache import ReadSnapshotStore, ReadSurfaceCache
from gtasks.ical import CalendarPreferences
from gtasks.operational_logs import OperationalLogReader, OperationalLogStore
from gtasks.warnings import WarningDismissalStore


class FakeTodoRead:
    def __init__(self, todos: tuple[dict, ...], *, next_cursor: int | None = None) -> None:
        self.todos = todos
        self.next_cursor = next_cursor

    def to_dict(self) -> dict:
        return {
            "todos": list(self.todos),
            "next_cursor": self.next_cursor,
        }


class FakeTodoReceipt:
    def __init__(self, todo: dict, *, idempotent: bool = False) -> None:
        self.todo = todo
        self.idempotent = idempotent

    def to_dict(self) -> dict:
        return {
            "todo": self.todo,
            "verified": True,
            "idempotent": self.idempotent,
            "parent_relationship_verified": True,
        }


class FakeHandoffReceipt:
    def __init__(self, *, task_slug: str, todo: dict, state: str, next_owner: str) -> None:
        self.task_slug = task_slug
        self.todo = todo
        self.state = state
        self.next_owner = next_owner

    def to_dict(self) -> dict:
        return {
            "task": {
                "slug": self.task_slug,
                "status": "blocked" if self.state == "waiting_for_input" else "active",
                "next_action": "Draft the complete seven-day plan.",
                "handoff": {
                    "state": self.state,
                    "question_todo": self.todo["slug"],
                    "resume_owner": "agents/tammy",
                    "resume_action": "Draft the complete seven-day plan.",
                },
            },
            "todo": self.todo,
            "event": None,
            "next_owner": self.next_owner,
            "verified": True,
            "idempotent": False,
        }


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
        system_tickets: tuple[SystemTicket, ...] = (),
    ) -> None:
        self.active = active
        self.completed = completed
        self.goals = goals
        self.projects = projects
        self.agents = agents
        self.agent_work = agent_work
        self.proposals = proposals
        self.system_tickets = system_tickets
        self.created_system_tickets: list[SystemTicket] = []
        self.updated_system_tickets: list[SystemTicket] = []
        self.created: list[Task] = []
        self.created_agent_tasks: list[tuple[Task, str]] = []
        self.duplicated_from: list[str] = []
        self.goal_links: list[tuple[str, str | None]] = []
        self.status_updates: list[tuple[str, str, datetime]] = []
        self.next_action_updates: list[tuple[str, str, datetime]] = []
        self.todo_reads: list[tuple[str, str | None, int, int]] = []
        self.todo_creates: list[dict] = []
        self.todo_edits: list[dict] = []
        self.todo_comments: list[dict] = []
        self.todo_status_updates: list[dict] = []
        self.todo_migrations: list[tuple[str, datetime]] = []
        self.handoff_requests: list[dict] = []
        self.handoff_answers: list[dict] = []
        self.handoff_acknowledgements: list[dict] = []
        self.handoff_questions: set[str] = set()
        self.todos: dict[str, dict] = {}
        self.membership_repairs: list[str] = []
        self.created_projects: list[Project] = []
        self.updated_projects: list[str] = []
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

    def list_system_tickets(self) -> SystemTicketRead:
        return SystemTicketRead(tickets=self.system_tickets)

    def create_system_ticket(self, ticket: SystemTicket) -> MutationReceipt:
        self.created_system_tickets.append(ticket)
        self.system_tickets = (*self.system_tickets, ticket)
        return MutationReceipt(slug=ticket.slug, verified=True)

    def update_system_ticket(self, ticket: SystemTicket) -> MutationReceipt:
        self.updated_system_tickets.append(ticket)
        self.system_tickets = tuple(
            ticket if item.slug == ticket.slug else item
            for item in self.system_tickets
        )
        return MutationReceipt(slug=ticket.slug, verified=True)

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

    def update_project(self, project: Project) -> ProjectMutationReceipt:
        self.updated_projects.append(project.slug)
        self.projects = tuple(
            project if item.slug == project.slug else item
            for item in self.projects
        )
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

    def list_task_todos(
        self,
        task_slug: str,
        *,
        status: str | None = None,
        cursor: int = 0,
        limit: int = 50,
    ) -> FakeTodoRead:
        self.todo_reads.append((task_slug, status, cursor, limit))
        values = [
            todo for todo in self.todos.values()
            if todo["parent_task"] == task_slug
            and (status is None or todo["status"] == status)
        ]
        return FakeTodoRead(tuple(values[cursor : cursor + limit]))

    def create_todo(self, task_slug: str, **payload) -> FakeTodoReceipt:
        self.todo_creates.append({"task_slug": task_slug, **payload})
        slug = f"todos/{len(self.todos) + 1:032d}"
        todo = {
            "slug": slug,
            "parent_task": task_slug,
            "text": payload["text"].strip(),
            "detail": payload["detail"].strip(),
            "status": "not_done",
            "status_label": "Not Done",
            "kind": payload["kind"],
            "creator": payload["actor"],
            "source": payload["source"],
            "created_at": payload["now"].isoformat(),
            "updated_at": payload["now"].isoformat(),
            "comments": [],
            "events": [],
        }
        self.todos[slug] = todo
        return FakeTodoReceipt(todo)

    def edit_todo(self, todo_slug: str, **payload) -> FakeTodoReceipt:
        self.todo_edits.append({"todo_slug": todo_slug, **payload})
        todo = self.todos[todo_slug] = {
            **self.todos[todo_slug],
            "text": payload["text"].strip(),
            "detail": payload["detail"].strip(),
            "updated_at": payload["now"].isoformat(),
        }
        return FakeTodoReceipt(todo)

    def add_todo_comment(self, todo_slug: str, **payload) -> FakeTodoReceipt:
        self.todo_comments.append({"todo_slug": todo_slug, **payload})
        todo = self.todos[todo_slug]
        todo["comments"] = [
            *todo["comments"],
            {
                "slug": f"todo-comments/{len(todo['comments']) + 1:032d}",
                "body": payload["body"].strip(),
                "author": payload["author"],
                "source": payload["source"],
                "created_at": payload["now"].isoformat(),
            },
        ]
        todo["updated_at"] = payload["now"].isoformat()
        return FakeTodoReceipt(todo)

    def set_todo_status(self, todo_slug: str, **payload) -> FakeTodoReceipt:
        self.todo_status_updates.append({"todo_slug": todo_slug, **payload})
        todo = self.todos[todo_slug]
        todo["status"] = payload["status"]
        todo["status_label"] = "Done" if payload["status"] == "done" else "Not Done"
        todo["updated_at"] = payload["now"].isoformat()
        return FakeTodoReceipt(todo)

    def migrate_legacy_next_actions(self, task_slug: str, *, now: datetime) -> FakeTodoRead:
        self.todo_migrations.append((task_slug, now))
        return self.list_task_todos(task_slug, limit=100)

    def request_agent_input(self, task_slug: str, **payload) -> FakeHandoffReceipt:
        self.handoff_requests.append({"task_slug": task_slug, **payload})
        todo = self.create_todo(
            task_slug,
            text=payload["question"],
            detail=payload["question_detail"],
            kind="question",
            actor=payload["agent_slug"],
            source="agent",
            idempotency_key=payload["idempotency_key"],
            now=payload["now"],
        ).todo
        self.handoff_questions.add(todo["slug"])
        return FakeHandoffReceipt(
            task_slug=task_slug,
            todo=todo,
            state="waiting_for_input",
            next_owner="people/tony-guan",
        )

    def answer_agent_question(self, todo_slug: str, **payload) -> FakeHandoffReceipt:
        self.handoff_answers.append({"todo_slug": todo_slug, **payload})
        todo = self.todos[todo_slug]
        todo["status"] = "done"
        todo["status_label"] = "Done"
        todo["updated_at"] = payload["now"].isoformat()
        todo["comments"] = [
            *todo["comments"],
            {"body": payload["answer"], "author": payload["actor"]},
        ]
        self.handoff_questions.discard(todo_slug)
        return FakeHandoffReceipt(
            task_slug=todo["parent_task"],
            todo=todo,
            state="ready_for_agent",
            next_owner="agents/tammy",
        )

    def acknowledge_agent_handoff(self, task_slug: str, **payload) -> FakeHandoffReceipt:
        self.handoff_acknowledgements.append({"task_slug": task_slug, **payload})
        todo = next(
            todo for todo in self.todos.values() if todo["parent_task"] == task_slug
        )
        return FakeHandoffReceipt(
            task_slug=task_slug,
            todo=todo,
            state="agent_working",
            next_owner=payload["actor"],
        )

    def is_active_handoff_question(self, todo_slug: str) -> bool:
        return todo_slug in self.handoff_questions

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
        ical_reader=None,
        read_cache: ReadSurfaceCache | None = None,
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
            ical_reader=ical_reader,
            calendar_preferences=CalendarPreferences(
                runtime_path / "calendar-preferences.json"
            ),
            read_cache=read_cache or ReadSurfaceCache(
                ReadSnapshotStore(runtime_path / "read-snapshots.json"),
                background=False,
            ),
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


class CalendarApiTests(unittest.TestCase):
    def test_calendar_selection_is_local_and_events_receive_only_selected_ids(self) -> None:
        class Reader:
            def __init__(self) -> None:
                self.requests = 0
                self.read_ids = None
            def status(self): return {"status": "authorized"}
            def request_full_access(self): self.requests += 1; return {"status": "authorized"}
            def calendars(self): return {"status": "authorized", "calendars": [{"id": "work", "title": "Work"}, {"id": "home", "title": "Home"}]}
            def read(self, start, end, *, calendar_ids=()): self.read_ids = calendar_ids; return {"status": "authorized", "events": []}

        reader = Reader()
        harness = ServerHarness(self, FakeAdapter(), ical_reader=reader)
        status, payload, _ = harness.request("GET", "/api/ical-calendars")
        self.assertEqual(status, 200)
        self.assertEqual(payload["selected_calendar_ids"], [])
        status, payload, _ = harness.request("POST", "/api/ical-preferences", {"selected_calendar_ids": ["work"]})
        self.assertEqual(status, 200)
        self.assertTrue(payload["verified"])
        status, payload, _ = harness.request("GET", "/api/ical-events?start=2026-07-30&end=2026-08-01")
        self.assertEqual(status, 200)
        self.assertEqual(reader.read_ids, ("work",))
        self.assertEqual(payload["selected_calendar_ids"], ["work"])

    def test_calendar_access_is_an_explicit_post_not_a_read_query_side_effect(self) -> None:
        class Reader:
            def __init__(self) -> None: self.requests = 0
            def read(self, start, end, *, calendar_ids=()): return {"status": "not_determined", "events": []}
            def calendars(self): return {"status": "not_determined", "calendars": []}
            def request_full_access(self): self.requests += 1; return {"status": "not_determined"}

        reader = Reader()
        harness = ServerHarness(self, FakeAdapter(), ical_reader=reader)
        status, _, _ = harness.request("GET", "/api/ical-events?start=2026-07-30&end=2026-08-01")
        self.assertEqual(status, 200)
        self.assertEqual(reader.requests, 0)
        status, _, _ = harness.request("POST", "/api/ical-access")
        self.assertEqual(status, 200)
        self.assertEqual(reader.requests, 1)


class HealthApiTests(unittest.TestCase):
    def test_health_declares_read_cache_and_isolated_qa_scope(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload["qa_fixtures_root"], QA_FIXTURES_ROOT)
        self.assertEqual(payload["read_surfaces"], "last_verified_local_cache")

    def test_static_mission_control_identity_assets_are_allowlisted(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        for path, content_type in (
            ("/favicon.svg", "image/svg+xml"),
            ("/favicon.ico", "image/x-icon"),
            ("/assets/mission-control-command-mark.svg", "image/svg+xml"),
            ("/assets/inbox-check.svg", "image/svg+xml"),
            ("/assets/apple-touch-icon-180.png", "image/png"),
            ("/assets/mission-control-word-art.png", "image/png"),
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", harness.server.server_address[1], timeout=3
            )
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200, path)
            self.assertTrue(body, path)
            self.assertTrue(
                response.getheader("Content-Type").startswith(content_type), path
            )
            connection.close()

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
        self.assertEqual(payload["version"], "V0.0.68")

    def test_release_history_is_served_from_the_canonical_catalog(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("GET", "/api/releases")

        self.assertEqual(status, 200)
        self.assertEqual(payload["current_version"], "V0.0.68")
        self.assertEqual(payload["releases"][0]["version"], "V0.0.68")
        self.assertEqual(
            [release["version"] for release in payload["releases"]],
            [
                "V0.0.68",
                "V0.0.67",
                "V0.0.66",
                "V0.0.65",
                "V0.0.64",
                "V0.0.63",
                "V0.0.62",
                "V0.0.61",
                "V0.0.60",
                "V0.0.59",
                "V0.0.58",
                "V0.0.57",
                "V0.0.56",
                "V0.0.55",
                "V0.0.54",
                "V0.0.53",
                "V0.0.52",
                "V0.0.51",
                "V0.0.50",
                "V0.0.49",
                "V0.0.48",
                "V0.0.47",
                "V0.0.46",
                "V0.0.45",
                "V0.0.44",
                "V0.0.43",
                "V0.0.42",
                "V0.0.41",
                "V0.0.40",
                "V0.0.39",
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
                "initial_todo": "",
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
                        "initial_todo": "Draft the update",
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
                self.assertEqual(adapter.todo_creates[0]["task_slug"], task.slug)
                self.assertEqual(adapter.todo_creates[0]["text"], "Draft the update")
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
                "initial_todo": "",
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
                "initial_todo": "Choose the next company",
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
        self.assertEqual(adapter.todo_creates[0]["task_slug"], created.slug)
        self.assertEqual(adapter.todo_creates[0]["text"], "Choose the next company")
        self.assertEqual(payload["task"]["todos"][0]["status"], "not_done")
        self.assertEqual(payload["task"]["next_action"], "Choose the next company")
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
                "initial_todo": "Choose a fresh company",
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
        self.assertEqual(adapter.todo_creates[0]["task_slug"], duplicate.slug)
        self.assertEqual(adapter.todo_creates[0]["text"], "Choose a fresh company")
        self.assertTrue(payload["receipt"]["verified"])

    def test_task_editor_rejects_legacy_next_action_field(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        create_status, create_payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {
                "title": "Legacy write",
                "detail": "",
                "priority": "normal",
                "next_action": "Must not bypass canonical To Dos",
                "due_day": "2026-07-31",
                "project_slug": None,
                "goal_slug": None,
                "progress_metric": None,
            },
        )
        edit_status, edit_payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks",
            {
                "title": "Ship GTasks",
                "detail": "",
                "priority": "normal",
                "next_action": "Must not bypass canonical To Dos",
                "due_day": "2026-07-31",
                "project_slug": None,
                "goal_slug": None,
                "status": "planned",
                "assignee_slug": "tony",
                "progress_metric": None,
                "handoff_reason": "",
            },
        )

        self.assertEqual((create_status, edit_status), (422, 422))
        self.assertEqual(create_payload["code"], "invalid_task")
        self.assertEqual(edit_payload["code"], "invalid_task_edit")
        self.assertEqual(adapter.created, [])


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


class TaskTodoApiTests(unittest.TestCase):
    def test_handoff_question_answer_and_acknowledgement_use_strict_endpoints(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, blocked, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fagent-work/questions",
            {
                "question": "Which Bible translation should I use?",
                "question_detail": "Name the exact translation.",
                "resume_action": "Draft the complete seven-day plan.",
                "agent_slug": "agents/tammy",
                "idempotency_key": "question-round-1",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(blocked["task"]["handoff"]["state"], "waiting_for_input")
        question = blocked["todo"]

        status, answered, _ = harness.request(
            "POST",
            f"/api/todos/{question['slug'].replace('/', '%2F')}/answer",
            {
                "answer": "Chinese Union Version; 30 minutes; independent readings.",
                "expected_updated_at": question["updated_at"],
                "actor": "people/tony-guan",
                "source": "mission_control",
                "idempotency_key": "answer-round-1",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(answered["task"]["handoff"]["state"], "ready_for_agent")
        self.assertEqual(answered["todo"]["status"], "done")
        self.assertEqual(answered["next_owner"], "agents/tammy")

        status, working, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fagent-work/handoff/acknowledge",
            {"actor": "agents/tammy"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(working["task"]["handoff"]["state"], "agent_working")

    def test_handoff_endpoints_reject_extra_fields_and_stale_answers(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)
        status, payload, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fagent-work/questions",
            {
                "question": "Question?",
                "question_detail": "Detail",
                "resume_action": "Resume.",
                "agent_slug": "agents/tammy",
                "idempotency_key": "question-round-1",
                "unexpected": True,
            },
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_handoff")
        self.assertEqual(adapter.handoff_requests, [])

        class StaleAdapter(FakeAdapter):
            def answer_agent_question(self, todo_slug: str, **payload) -> FakeHandoffReceipt:
                raise gbrain.ConcurrentTodoUpdateError(todo_slug)

        harness = ServerHarness(self, StaleAdapter())
        status, payload, _ = harness.request(
            "POST",
            "/api/todos/todos%2Fquestion/answer",
            {
                "answer": "Answer",
                "expected_updated_at": "2026-08-02T10:00:00-07:00",
                "actor": "people/tony-guan",
                "source": "mission_control",
                "idempotency_key": "answer-round-1",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "todo_changed")

    def test_generic_done_rejects_the_current_handoff_question(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)
        _, blocked, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fagent-work/questions",
            {
                "question": "Which translation?",
                "question_detail": "Name it.",
                "resume_action": "Draft the plan.",
                "agent_slug": "agents/tammy",
                "idempotency_key": "question-round-1",
            },
        )
        question = blocked["todo"]

        status, payload, _ = harness.request(
            "PATCH",
            f"/api/todos/{question['slug'].replace('/', '%2F')}/status",
            {
                "status": "done",
                "expected_updated_at": question["updated_at"],
                "actor": "people/tony-guan",
                "source": "mission_control",
                "idempotency_key": "unsafe-done",
            },
        )

        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "handoff_answer_required")
        self.assertEqual(adapter.todo_status_updates, [])

    def test_lists_bounded_filtered_todos_for_one_parent(self) -> None:
        adapter = FakeAdapter()
        adapter.todos = {
            "todos/one": {
                "slug": "todos/one", "parent_task": "tasks/ship-gtasks",
                "text": "First", "detail": "", "status": "not_done",
                "status_label": "Not Done", "kind": "action", "comments": [], "events": [],
            },
            "todos/two": {
                "slug": "todos/two", "parent_task": "tasks/ship-gtasks",
                "text": "Second", "detail": "", "status": "done",
                "status_label": "Done", "kind": "action", "comments": [], "events": [],
            },
        }
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "GET",
            "/api/tasks/tasks%2Fship-gtasks/todos?status=not_done&cursor=0&limit=25",
        )

        self.assertEqual(status, 200)
        self.assertEqual([todo["slug"] for todo in payload["todos"]], ["todos/one"])
        self.assertEqual(adapter.todo_reads[-1], ("tasks/ship-gtasks", "not_done", 0, 25))

    def test_creates_action_and_agent_question_todos_with_idempotency(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)
        payload = {
            "text": "Tony: choose the deployment window",
            "detail": "Reply with 17:00 or 18:00.",
            "kind": "question",
            "actor": "agents/toddy",
            "source": "agent",
            "idempotency_key": "toddy-window-question",
        }

        status, body, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fship-gtasks/todos",
            payload,
        )

        self.assertEqual(status, 201)
        self.assertEqual(body["receipt"]["todo"]["status"], "not_done")
        self.assertEqual(body["receipt"]["todo"]["kind"], "question")
        self.assertEqual(adapter.todo_creates[0]["actor"], "agents/toddy")
        self.assertIsNotNone(adapter.todo_creates[0]["now"].tzinfo)

    def test_edits_comments_and_changes_status_with_expected_version(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)
        create_status, created, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fship-gtasks/todos",
            {
                "text": "Confirm window", "detail": "", "kind": "question",
                "actor": "people/tony-guan", "source": "mission_control",
                "idempotency_key": "create-one",
            },
        )
        self.assertEqual(create_status, 201)
        todo = created["receipt"]["todo"]
        encoded = todo["slug"].replace("/", "%2F")

        status, edited, _ = harness.request(
            "PATCH",
            f"/api/todos/{encoded}",
            {
                "text": "Confirm 17:00 window", "detail": "Answer before deploy.",
                "expected_updated_at": todo["updated_at"], "actor": "people/tony-guan",
                "source": "mission_control", "idempotency_key": "edit-one",
            },
        )
        self.assertEqual(status, 200)
        current = edited["receipt"]["todo"]

        status, commented, _ = harness.request(
            "POST",
            f"/api/todos/{encoded}/comments",
            {
                "body": "17:00 works.", "expected_updated_at": current["updated_at"],
                "author": "people/tony-guan", "source": "mission_control",
                "idempotency_key": "reply-one",
            },
        )
        self.assertEqual(status, 201)
        current = commented["receipt"]["todo"]
        self.assertEqual(current["comments"][0]["body"], "17:00 works.")

        status, completed, _ = harness.request(
            "PATCH",
            f"/api/todos/{encoded}/status",
            {
                "status": "done", "expected_updated_at": current["updated_at"],
                "actor": "people/tony-guan", "source": "mission_control",
                "idempotency_key": "done-one",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(completed["receipt"]["todo"]["status_label"], "Done")

    def test_rejects_invalid_todo_payloads_before_mutation(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fship-gtasks/todos",
            {"text": "x", "detail": "", "kind": "action"},
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_todo")
        self.assertEqual(adapter.todo_creates, [])

        adapter.todos["todos/one"] = {
            "slug": "todos/one", "parent_task": "tasks/ship-gtasks",
            "text": "First", "detail": "", "status": "not_done",
            "status_label": "Not Done", "kind": "action", "comments": [], "events": [],
            "updated_at": "2026-08-01T10:00:00-07:00",
        }
        status, payload, _ = harness.request(
            "PATCH",
            "/api/todos/todos%2Fone/status",
            {
                "status": "completed", "expected_updated_at": "2026-08-01T10:00:00-07:00",
                "actor": "people/tony-guan", "source": "mission_control",
                "idempotency_key": "bad-status",
            },
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_todo")
        self.assertEqual(adapter.todo_status_updates, [])

    def test_reports_verified_rollback_and_concurrent_conflict_distinctly(self) -> None:
        class FailureAdapter(FakeAdapter):
            def create_todo(self, task_slug: str, **payload) -> FakeTodoReceipt:
                raise PartialMutationError(task_slug, "To Do write failed. Rollback verified.")

        harness = ServerHarness(self, FailureAdapter())
        status, payload, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fship-gtasks/todos",
            {
                "text": "Confirm window", "detail": "", "kind": "action",
                "actor": "people/tony-guan", "source": "mission_control",
                "idempotency_key": "create-one",
            },
        )
        self.assertEqual(status, 502)
        self.assertEqual(payload["code"], "partial_write")
        self.assertIn("Rollback verified", payload["error"])

        self.assertTrue(hasattr(gbrain, "ConcurrentTodoUpdateError"))

        class ConcurrentAdapter(FakeAdapter):
            def edit_todo(self, todo_slug: str, **payload) -> FakeTodoReceipt:
                raise gbrain.ConcurrentTodoUpdateError(todo_slug)

        concurrent = ConcurrentAdapter()
        concurrent.todos["todos/one"] = {
            "slug": "todos/one", "parent_task": "tasks/ship-gtasks",
            "text": "First", "detail": "", "status": "not_done",
            "status_label": "Not Done", "kind": "action", "comments": [], "events": [],
            "updated_at": "2026-08-01T10:00:00-07:00",
        }
        harness = ServerHarness(self, concurrent)
        status, payload, _ = harness.request(
            "PATCH",
            "/api/todos/todos%2Fone",
            {
                "text": "Changed", "detail": "", "expected_updated_at": "2026-08-01T10:00:00-07:00",
                "actor": "people/tony-guan", "source": "mission_control",
                "idempotency_key": "stale-edit",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "todo_changed")

    def test_migration_endpoint_is_idempotent_and_legacy_next_action_write_is_retired(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, _, _ = harness.request(
            "POST",
            "/api/tasks/tasks%2Fship-gtasks/todos/migrate",
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(adapter.todo_migrations[0][0], "tasks/ship-gtasks")

        status, payload, _ = harness.request(
            "PATCH",
            "/api/tasks/tasks%2Fship-gtasks/next-action",
            {"next_action": "Legacy divergent write"},
        )
        self.assertEqual(status, 410)
        self.assertEqual(payload["code"], "next_action_retired")


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

    def test_edits_the_same_project_and_returns_verified_canonical_readback(self) -> None:
        adapter = FakeAdapter(projects=(sample_project(),))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/projects/projects%2Fship-product",
            {
                "title": "Ship Mission Control",
                "summary": "## Updated\n\nVerified project detail.",
                "status": "paused",
                "supporting_goal_slugs": [],
            },
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["receipt"]["verified"])
        self.assertEqual(payload["project"]["slug"], "projects/ship-product")
        self.assertEqual(payload["project"]["title"], "Ship Mission Control")
        self.assertEqual(payload["project"]["status"], "paused")
        self.assertEqual(adapter.updated_projects, [payload["project"]["slug"]])

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


class SystemTicketApiTests(unittest.TestCase):
    def test_lists_separate_canonical_system_tickets(self) -> None:
        ticket = SystemTicket(
            slug="tasks/system-tickets/calendar-highlight-a1b2c3",
            title="Highlight selected Calendar task",
            status="planned",
            verbatim_request="Highlight the selected task in Calendar.",
            target_subsystem="mission_control",
            priority="high",
            acceptance_criteria="Selected task has a clear accessible treatment.",
        )
        adapter = FakeAdapter(system_tickets=(ticket,))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request("GET", "/api/system-tickets")

        self.assertEqual(status, 200)
        self.assertEqual(payload["root_slug"], "collections/mission-control-system-tickets")
        self.assertEqual(payload["tickets"][0]["status"], "planned")
        self.assertEqual(adapter.created, [])
        self.assertEqual(adapter.created_agent_tasks, [])

    def test_creates_a_normal_planned_task_with_verified_receipt(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/system-tickets",
            {
                "title": "Improve Calendar selection",
                "verbatim_request": "Make the selected Calendar task clearer.",
                "target_subsystem": "mission_control",
                "priority": "high",
                "acceptance_criteria": "Selection is visible on desktop and mobile.",
            },
        )

        self.assertEqual(status, 201)
        self.assertTrue(payload["receipt"]["verified"])
        self.assertEqual(payload["ticket"]["status"], "planned")
        self.assertEqual(payload["ticket"]["target_subsystem"], "mission_control")
        self.assertEqual(len(adapter.created_system_tickets), 1)
        self.assertEqual(adapter.created, [])
        self.assertEqual(adapter.created_agent_tasks, [])

    def test_rejects_proposed_as_a_system_ticket_status_or_unknown_fields(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request(
            "POST",
            "/api/system-tickets",
            {
                "title": "Do not create a proposal",
                "verbatim_request": "System Tickets use standard statuses.",
                "status": "proposed",
            },
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_system_ticket")

    def test_edits_same_system_ticket_without_touching_receipts_or_membership(self) -> None:
        ticket = SystemTicket(
            slug="tasks/system-tickets/edit-me-a1b2c3",
            title="Original title",
            status="planned",
            verbatim_request="Original exact request.",
            target_subsystem="mission_control",
            priority="normal",
            acceptance_criteria="Original criteria.",
            linked_evidence=("evidence",),
            implementation_receipts=("implementation",),
            qa_receipts=("qa",),
        )
        adapter = FakeAdapter(system_tickets=(ticket,))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            "/api/system-tickets/tasks%2Fsystem-tickets%2Fedit-me-a1b2c3",
            {
                "title": "Edited title",
                "status": "active",
                "priority": "high",
                "target_subsystem": "mission_control",
                "verbatim_request": "Edited exact request.",
                "acceptance_criteria": "Edited criteria.",
            },
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["receipt"]["verified"])
        self.assertEqual(payload["ticket"]["slug"], ticket.slug)
        self.assertEqual(payload["ticket"]["linked_evidence"], ["evidence"])
        self.assertEqual(payload["ticket"]["implementation_receipts"], ["implementation"])
        self.assertEqual(payload["ticket"]["qa_receipts"], ["qa"])
        self.assertEqual(len(adapter.updated_system_tickets), 1)
        self.assertEqual(adapter.created, [])
        self.assertEqual(adapter.created_agent_tasks, [])

    def test_system_ticket_edit_rejects_receipt_mutation(self) -> None:
        ticket = SystemTicket(
            "tasks/system-tickets/edit-me-a1b2c3", "Original", "planned",
            "Request", "mission_control", "normal",
        )
        harness = ServerHarness(self, FakeAdapter(system_tickets=(ticket,)))

        status, payload, _ = harness.request(
            "PATCH",
            "/api/system-tickets/tasks%2Fsystem-tickets%2Fedit-me-a1b2c3",
            {"qa_receipts": ["invented"]},
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_system_ticket")


class ProposalApiTests(unittest.TestCase):
    def test_cold_proposal_failure_is_a_bounded_safe_error(self) -> None:
        class FailingProposalAdapter(FakeAdapter):
            def list_proposals(self) -> ProposalRead:
                raise RuntimeError("private oauth response")

        harness = ServerHarness(self, FailingProposalAdapter())

        status, payload, _ = harness.request("GET", "/api/proposals")

        self.assertEqual(status, 503)
        self.assertEqual(payload["code"], "gbrain_refresh_delayed")
        self.assertNotIn("oauth", json.dumps(payload).lower())

    def test_cold_proposal_refresh_does_not_block_task_surface(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class SlowProposalAdapter(FakeAdapter):
            def list_proposals(self) -> ProposalRead:
                entered.set()
                release.wait(timeout=2)
                return ProposalRead(proposals=())

        with tempfile.TemporaryDirectory() as temporary:
            cache = ReadSurfaceCache(
                ReadSnapshotStore(Path(temporary) / "reads.json"),
                background=True,
            )
            harness = ServerHarness(
                self,
                SlowProposalAdapter(),
                read_cache=cache,
            )

            proposal_status, proposal_payload, _ = harness.request(
                "GET", "/api/proposals"
            )
            self.assertEqual(proposal_status, 202)
            self.assertEqual(proposal_payload["read_state"]["status"], "loading")
            self.assertTrue(entered.wait(timeout=1))

            task_status, task_payload, _ = harness.request("GET", "/api/tasks")
            self.assertEqual(task_status, 202)
            self.assertEqual(task_payload["read_state"]["surface"], "tasks")
            release.set()
            self.assertTrue(cache.wait_for_idle("proposals"))
            self.assertTrue(cache.wait_for_idle("tasks"))

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

    def test_lifecycle_duplicate_returns_safe_repair_route_without_decision(self) -> None:
        class DuplicateLifecycleAdapter(FakeAdapter):
            def decide_proposal(self, *args, **kwargs):
                raise LifecycleIntegrityError(
                    "collections/toddys-tasks/example",
                    [
                        {"to_slug": "collections/toddys-tasks"},
                        {"to_slug": "collections/toddys-tasks"},
                    ],
                )

        adapter = DuplicateLifecycleAdapter(proposals=(sample_proposal(),))
        harness = ServerHarness(self, adapter)
        status, payload, _ = harness.request(
            "POST",
            "/api/proposals/proposals%2Ftoddy-wellbeing-check-in/decision",
            {"action": "approve", "decision_note": ""},
        )

        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "lifecycle_membership_needs_attention")
        self.assertEqual(payload["lifecycle_edge_count"], 2)
        self.assertEqual(payload["slug"], "collections/toddys-tasks/example")
        self.assertIn("127.0.0.1:8788/?slug=collections%2Ftoddys-tasks%2Fexample", payload["repair_url"])
        self.assertEqual(adapter.proposal_decisions, [])


if __name__ == "__main__":
    unittest.main()
