import http.client
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import gtasks.gbrain as gbrain
import gtasks.server as server_module

from gtasks.domain import (
    ACTIVE_ROOT,
    AgentArtifact,
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
    DomainValidationError,
    new_inbox_task,
    new_agent_artifact,
    new_task,
)
from gtasks.gbrain import (
    ArtifactMutationReceipt,
    ArtifactRead,
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
    TaskEditReceipt,
    MembershipRepairReceipt,
    ProjectAssignmentReceipt,
    ProjectMutationReceipt,
    ProjectRead,
    ProposalRead,
    ProposalMutationReceipt,
    SystemTicketRead,
    LifecycleIntegrityError,
)
from gtasks.server import (
    ArtifactPublisherAuth,
    HandoffDispatcherAuth,
    build_task_snapshot,
    build_server,
    load_artifact_publisher_auth,
)
from gtasks.handoff_dispatcher import (
    ActionableChange,
    AgentRegistration,
    DurableHandoffStore,
    HandoffDispatcher,
)
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
        self.verified = True

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
        self.verified = True
        self.idempotent = False

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
        artifacts: tuple[AgentArtifact, ...] = (),
    ) -> None:
        self.active = active
        self.completed = completed
        self.goals = goals
        self.projects = projects
        self.agents = agents
        self.agent_work = agent_work
        self.proposals = proposals
        self.system_tickets = system_tickets
        self.artifacts = artifacts
        self.created_artifacts: list[tuple[AgentArtifact, str]] = []
        self.artifact_reads: list[dict[str, object]] = []
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

    def get_task(self, task_slug: str) -> Task:
        return next(
            task
            for task in (*self.active, *self.completed)
            if task.slug == task_slug
        )

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

    def list_agent_artifacts(self, **filters) -> ArtifactRead:
        self.artifact_reads.append(filters)
        artifacts = self.artifacts
        for field, attribute in (
            ("agent", "created_by"),
            ("task", "produced_for"),
            ("project", "project"),
            ("goal", "goal"),
            ("kind", "artifact_kind"),
        ):
            value = filters.get(field)
            if value:
                artifacts = tuple(
                    artifact
                    for artifact in artifacts
                    if getattr(artifact, attribute) == value
                )
        cursor = filters.get("cursor", 0)
        limit = filters.get("limit", 25)
        page = artifacts[cursor : cursor + limit]
        next_cursor = cursor + len(page) if cursor + len(page) < len(artifacts) else None
        return ArtifactRead(artifacts=page, next_cursor=next_cursor)

    def get_agent_artifact(self, slug: str) -> AgentArtifact:
        return next(artifact for artifact in self.artifacts if artifact.slug == slug)

    def create_agent_artifact(
        self,
        artifact: AgentArtifact,
        *,
        executing_agent: str,
        idempotency_key: str,
    ) -> ArtifactMutationReceipt:
        if executing_agent != artifact.created_by:
            raise DomainValidationError(
                "Artifact publisher identity does not match its installed execution contract"
            )
        for existing, existing_key in self.created_artifacts:
            if existing_key == idempotency_key:
                existing_fields = existing.to_dict()
                incoming_fields = artifact.to_dict()
                existing_fields.pop("slug")
                incoming_fields.pop("slug")
                if existing_fields != incoming_fields:
                    raise gbrain.ArtifactIdempotencyConflict(
                        "artifact idempotency key already has different content"
                    )
                return ArtifactMutationReceipt(
                    artifact=existing,
                    verified=True,
                    idempotent=True,
                )
        self.created_artifacts.append((artifact, idempotency_key))
        self.artifacts = (*self.artifacts, artifact)
        return ArtifactMutationReceipt(artifact=artifact, verified=True)

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

    def get_todo(self, todo_slug: str) -> dict:
        return dict(self.todos[todo_slug])

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

    def edit_task(self, task_slug: str, **payload) -> TaskEditReceipt:
        existing = next(
            task for task in (*self.active, *self.completed) if task.slug == task_slug
        )
        updated = replace(
            existing,
            title=payload["title"],
            summary=payload["title"],
            detail=payload["detail"],
            priority=payload["priority"],
            due_day=payload["due_day"],
            status=payload["status"],
            progress_metric=payload["progress_metric"],
            event_progress=payload["event_progress"],
            updated_at=payload["now"],
        )
        self.active = tuple(updated if task.slug == task_slug else task for task in self.active)
        return TaskEditReceipt(task_slug=task_slug, task=updated, verified=True)


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


def sample_artifact() -> AgentArtifact:
    return AgentArtifact(
        slug="artifacts/72a4d170-978f-4a37-bd92-b9d3bdde9339",
        title="Family care weekly review brief",
        artifact_kind="markdown",
        created_by="agents/toddy",
        agent_collection="collections/toddys-artifacts",
        produced_for="tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
        markdown="# Weekly review\n\nCanonical content.",
        attachments=(),
        project="projects/65c2f720-fb49-5403-9a9e-76228e285277",
        goal="goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10",
        git_url=None,
        supersedes=None,
        created_at=datetime.fromisoformat("2026-08-02T14:00:00-07:00"),
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
        handoff_store: DurableHandoffStore | None = None,
        handoff_dispatcher_auth: HandoffDispatcherAuth | None = None,
        handoff_registration_validator=None,
        handoff_waiter=None,
        handoff_event_bridge=None,
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
            artifact_publisher_auth=ArtifactPublisherAuth.from_plaintext_tokens_for_tests(
                {
                    "agents/tammy": "tammy-test-publisher-token",
                    "agents/timmy": "timmy-test-publisher-token",
                    "agents/toddy": "toddy-test-publisher-token",
                }
            ),
            handoff_store=handoff_store,
            handoff_dispatcher_auth=handoff_dispatcher_auth,
            handoff_registration_validator=handoff_registration_validator,
            handoff_waiter=handoff_waiter,
            handoff_event_bridge=handoff_event_bridge,
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
        extra_headers: dict[str, str] | None = None,
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
        headers.update(extra_headers or {})
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        parsed = json.loads(raw) if raw else {}
        response_headers = {key: value for key, value in response.getheaders()}
        connection.close()
        return response.status, parsed, response_headers


class HandoffRuntimeConstructionTests(unittest.TestCase):
    NOW = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    TASK = "tasks/11111111-1111-4111-8111-111111111111"
    RAW_REGISTRATIONS = {
        "agents/tammy": "private-registration-tammy",
        "agents/timmy": "private-registration-timmy",
        "agents/toddy": "private-registration-toddy",
    }

    class RuntimeAdapter:
        def __init__(self, routes: dict[str, str | None]) -> None:
            self.routes = routes
            self.reads: list[tuple[str, str]] = []

        def read_handoff_dispatcher_registration_by_reference(
            self,
            agent_slug: str,
            registration_reference: str,
        ):
            self.reads.append((agent_slug, registration_reference))
            route = self.routes.get(agent_slug)
            if route is None:
                return None
            return AgentRegistration.from_reference(
                registration_reference,
                agent_slug=agent_slug,
                route=route,
            )

    def _auth(self) -> HandoffDispatcherAuth:
        return HandoffDispatcherAuth.from_plaintext_tokens_for_tests(
            {
                agent: (registration_id, f"token-{agent.rsplit('/', 1)[-1]}")
                for agent, registration_id in self.RAW_REGISTRATIONS.items()
            }
        )

    def _run_main(self, adapter, auth, *, exercise_agent: str | None = None):
        captured: dict[str, object] = {}

        class FakeServer:
            server_address = ("127.0.0.1", 4179)

            def serve_forever(_self) -> None:
                bridge = captured.get("handoff_event_bridge")
                if bridge is None or exercise_agent is None:
                    return
                route = adapter.routes[exercise_agent]
                before = {
                    "task_slug": self.TASK,
                    "task": {
                        "slug": self.TASK,
                        "status": "blocked",
                        "assigned_to": [exercise_agent],
                        "blockers": ["systems/runtime"],
                    },
                    "route": route,
                }
                after = {
                    **before,
                    "task": {
                        **before["task"],
                        "status": "active",
                        "blockers": [],
                    },
                }
                captured["record"] = bridge.after_verified_mutation(
                    before,
                    after,
                    {
                        "verified": True,
                        "canonical_event_id": "events/runtime-wiring",
                        "canonical_version": "versions/1",
                        "mutation_kind": "system_dependency_recovered",
                    },
                    self.NOW,
                )
                reference = hashlib.sha256(
                    self.RAW_REGISTRATIONS[exercise_agent].encode("utf-8")
                ).hexdigest()
                captured["claim"] = captured["handoff_store"].claim(
                    reference,
                    now=self.NOW,
                    lease_seconds=30,
                )

            def server_close(_self) -> None:
                return None

        def capture_build_server(**kwargs):
            captured.update(kwargs)
            return FakeServer()

        with tempfile.TemporaryDirectory() as temporary:
            store_path = Path(temporary) / "handoffs.sqlite3"
            argv = [
                "gtasks.server",
                "--handoff-store",
                str(store_path),
                "--handoff-dispatcher-credentials-file",
                str(Path(temporary) / "credentials.json"),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(server_module, "GBrainAdapter", return_value=adapter),
                patch.object(
                    server_module,
                    "load_handoff_dispatcher_auth",
                    return_value=auth,
                ),
                patch.object(
                    server_module,
                    "load_artifact_publisher_auth",
                    return_value=ArtifactPublisherAuth(),
                ),
                patch.object(
                    server_module,
                    "build_server",
                    side_effect=capture_build_server,
                ),
            ):
                server_module.main()
        return captured

    def test_main_wires_hash_only_runtime_bridge_into_verified_mutations(self) -> None:
        routes = {
            "agents/tammy": "hosts/tammy",
            "agents/timmy": "hosts/timmy",
            "agents/toddy": "hosts/toddy",
        }
        adapter = self.RuntimeAdapter(routes)
        auth = self._auth()

        captured = self._run_main(
            adapter,
            auth,
            exercise_agent="agents/tammy",
        )

        self.assertIs(captured.get("adapter"), adapter)
        bridge = captured.get("handoff_event_bridge")
        self.assertIsNotNone(bridge)
        expected_reads = {
            (
                agent,
                hashlib.sha256(registration_id.encode("utf-8")).hexdigest(),
            )
            for agent, registration_id in self.RAW_REGISTRATIONS.items()
        }
        self.assertEqual(set(adapter.reads), expected_reads)
        registrations = bridge.dispatcher.registrations
        self.assertEqual(len(registrations), 3)
        for registration in registrations:
            self.assertEqual(registration.registration_id, registration.reference)
            self.assertEqual(registration.lease_identity, registration.reference)
        self.assertEqual(captured["record"].status, "queued")
        self.assertIsNotNone(captured["claim"])
        self.assertNotIn(
            "private-registration-",
            repr((adapter.reads, registrations, captured["record"].to_dict())),
        )

    def test_main_quarantines_missing_and_duplicate_canonical_routes(self) -> None:
        adapter = self.RuntimeAdapter(
            {
                "agents/tammy": "hosts/shared",
                "agents/timmy": "hosts/shared",
                "agents/toddy": None,
            }
        )

        captured = self._run_main(
            adapter,
            self._auth(),
            exercise_agent="agents/tammy",
        )

        bridge = captured.get("handoff_event_bridge")
        self.assertIsNotNone(bridge)
        self.assertEqual(bridge.dispatcher.registrations, ())
        self.assertEqual(captured["record"].status, "suppressed")
        self.assertEqual(captured["record"].reason, "missing_registration")
        self.assertIsNone(captured["claim"])

class HandoffMutationBridgeTests(unittest.TestCase):
    TASK = "tasks/agent-work"
    TODO = "todos/question"

    class RecordingBridge:
        def __init__(self, *, fail: bool = False) -> None:
            self.calls = []
            self.fail = fail

        def after_verified_mutation(self, before, after, receipt, now):
            self.calls.append((before, after, receipt, now))
            if self.fail:
                raise RuntimeError("synthetic dispatcher storage failure")
            return {"recorded": True}

    def adapter_with_question(self) -> FakeAdapter:
        now = datetime(2026, 7, 30, 9, 0).astimezone()
        task = replace(
            new_task(
                title="Agent work",
                detail="",
                priority="normal",
                next_action="Use Tony's answer.",
                due_day=now.date(),
                project=None,
                goal=None,
                now=now,
                identity="11111111-1111-4111-8111-111111111111",
            ),
            slug=self.TASK,
            status="blocked",
            owner_agent="agents/tammy",
            blockers=("people/tony-guan",),
        )
        adapter = FakeAdapter(active=(task,))
        adapter.todos[self.TODO] = {
            "slug": self.TODO,
            "parent_task": self.TASK,
            "text": "Which verified option?",
            "detail": "Choose one.",
            "status": "not_done",
            "status_label": "Not Done",
            "kind": "question",
            "creator": "agents/tammy",
            "source": "agent",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "comments": [],
            "events": [],
        }
        adapter.handoff_questions.add(self.TODO)
        return adapter

    def test_answer_dispatches_only_after_verified_mutation_readback(self) -> None:
        adapter = self.adapter_with_question()
        bridge = self.RecordingBridge()
        harness = ServerHarness(self, adapter, handoff_event_bridge=bridge)

        status, body, _ = harness.request(
            "POST",
            "/api/todos/todos%2Fquestion/answer",
            {
                "answer": "Use the verified option.",
                "expected_updated_at": adapter.todos[self.TODO]["updated_at"],
                "actor": "people/tony-guan",
                "source": "mission_control",
                "idempotency_key": "answer-verified-option",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(bridge.calls), 1)
        before, after, receipt, _now = bridge.calls[0]
        self.assertTrue(receipt["verified"])
        self.assertEqual(before["task_slug"], self.TASK)
        self.assertEqual(after["task_slug"], self.TASK)
        self.assertEqual(before["todo"]["slug"], self.TODO)
        self.assertEqual(after["todo"]["slug"], self.TODO)
        self.assertEqual(body["todo"]["slug"], self.TODO)

    def test_partial_write_records_attention_without_dispatching_user_work(self) -> None:
        class PartialAdapter(FakeAdapter):
            def create_todo(self, task_slug: str, **payload) -> FakeTodoReceipt:
                raise PartialMutationError(task_slug, "To Do write was not verified.")

        task = new_inbox_task(
            "Ship GTasks",
            datetime(2026, 7, 30, 9, 0).astimezone(),
            "partial1",
        )
        adapter = PartialAdapter(active=(task,))
        bridge = self.RecordingBridge()
        harness = ServerHarness(self, adapter, handoff_event_bridge=bridge)

        status, body, _ = harness.request(
            "POST",
            f"/api/tasks/{task.slug.replace('/', '%2F')}/todos",
            {
                "text": "Verify the write",
                "detail": "",
                "kind": "action",
                "actor": "people/tony-guan",
                "source": "mission_control",
                "idempotency_key": "partial-create",
            },
        )

        self.assertEqual(status, 502)
        self.assertEqual(body["code"], "partial_write")
        self.assertEqual(len(bridge.calls), 1)
        self.assertFalse(bridge.calls[0][2]["verified"])

    def test_dispatcher_failure_never_rolls_back_verified_canonical_answer(self) -> None:
        adapter = self.adapter_with_question()
        bridge = self.RecordingBridge(fail=True)
        harness = ServerHarness(self, adapter, handoff_event_bridge=bridge)

        status, body, _ = harness.request(
            "POST",
            "/api/todos/todos%2Fquestion/answer",
            {
                "answer": "Use the verified option.",
                "expected_updated_at": adapter.todos[self.TODO]["updated_at"],
                "actor": "people/tony-guan",
                "source": "mission_control",
                "idempotency_key": "answer-verified-option",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["todo"]["status"], "done")
        self.assertEqual(adapter.todos[self.TODO]["status"], "done")
        self.assertEqual(len(bridge.calls), 1)

    def test_verified_todo_write_survives_post_write_snapshot_failure_with_attention(self) -> None:
        now = datetime(2026, 7, 30, 9, 0).astimezone()
        task = new_inbox_task("Ship GTasks", now, "postread1")

        class CreateReadFailureAdapter(FakeAdapter):
            def __init__(self):
                super().__init__(active=(task,))
                self.task_reads = 0

            def get_task(self, task_slug: str):
                self.task_reads += 1
                if self.task_reads > 1:
                    raise gbrain.GBrainProtocolError("synthetic post-write task read failure")
                return super().get_task(task_slug)

        class TodoReadFailureAdapter(FakeAdapter):
            def __init__(self):
                super().__init__(active=(task,))
                self.todo_reads_after_write = 0
                self.todos["todos/one"] = {
                    "slug": "todos/one",
                    "parent_task": task.slug,
                    "text": "Confirm window",
                    "detail": "",
                    "status": "not_done",
                    "status_label": "Not Done",
                    "kind": "action",
                    "creator": "people/tony-guan",
                    "source": "mission_control",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "comments": [],
                    "events": [],
                }

            def get_todo(self, todo_slug: str):
                self.todo_reads_after_write += 1
                if self.todo_reads_after_write > 1:
                    raise gbrain.GBrainProtocolError("synthetic post-write To Do read failure")
                return super().get_todo(todo_slug)

        cases = (
            (
                CreateReadFailureAdapter,
                "POST",
                f"/api/tasks/{task.slug.replace('/', '%2F')}/todos",
                {
                    "text": "Verify the write", "detail": "", "kind": "action",
                    "actor": "people/tony-guan", "source": "mission_control",
                    "idempotency_key": "post-read-create",
                },
                201,
            ),
            (
                TodoReadFailureAdapter,
                "PATCH",
                "/api/todos/todos%2Fone",
                {
                    "text": "Confirm the verified window", "detail": "",
                    "expected_updated_at": now.isoformat(), "actor": "people/tony-guan",
                    "source": "mission_control", "idempotency_key": "post-read-edit",
                },
                200,
            ),
            (
                TodoReadFailureAdapter,
                "PATCH",
                "/api/todos/todos%2Fone/status",
                {
                    "status": "done", "expected_updated_at": now.isoformat(),
                    "actor": "people/tony-guan", "source": "mission_control",
                    "idempotency_key": "post-read-status",
                },
                200,
            ),
        )

        for adapter_factory, method, path, payload, expected_status in cases:
            with self.subTest(path=path):
                adapter = adapter_factory()
                bridge = self.RecordingBridge()
                harness = ServerHarness(self, adapter, handoff_event_bridge=bridge)
                status, body, _ = harness.request(method, path, payload)

                self.assertEqual(status, expected_status)
                self.assertTrue(body["receipt"]["verified"])
                self.assertEqual(len(bridge.calls), 1)
                self.assertFalse(bridge.calls[0][2]["verified"])

    @staticmethod
    def proposal_task_fixture() -> tuple[Task, TaskProposal]:
        now = datetime(2026, 7, 30, 9, 0).astimezone()
        task = replace(
            new_task(
                title="Approved Agent work",
                detail="Proceed only after Tony approves.",
                priority="normal",
                next_action="Execute the approved work.",
                due_day=now.date(),
                project=None,
                goal=None,
                now=now,
                identity="33333333-3333-4333-8333-333333333333",
            ),
            status="proposed",
            lifecycle_root="collections/toddys-tasks",
            owner_agent="agents/toddy",
            proposal_recipient="tony",
        )
        proposal = TaskProposal(
            slug=task.slug,
            title=task.title,
            status="proposed",
            recipient="tony",
            proposing_agent="agents/toddy",
            rationale=task.detail,
            proposed_next_step=task.next_action,
            due_day=task.due_day,
            submitted_at=now,
            updated_at=now,
            source_kind="task",
        )
        return task, proposal

    def test_real_proposal_approval_emits_authorization_granted(self) -> None:
        task, proposal = self.proposal_task_fixture()

        class ApprovalAdapter(FakeAdapter):
            def decide_proposal(self, proposal_slug: str, *, action: str, decision_note: str, now: datetime):
                approved = replace(
                    task,
                    status="planned",
                    proposal_decision="approve",
                    proposal_decided_at=now,
                    proposal_decision_note=decision_note,
                    updated_at=now,
                )
                self.active = (approved,)
                stored_proposal = replace(
                    proposal,
                    status="approved",
                    decision="approve",
                    decision_at=now,
                    resulting_status="planned",
                    reviewed_at=now,
                    updated_at=now,
                )
                return ProposalMutationReceipt(
                    proposal_slug=proposal_slug,
                    status="approved",
                    proposal=stored_proposal,
                    created_task=approved,
                    verified=True,
                )

        adapter = ApprovalAdapter(active=(task,), proposals=(proposal,))
        bridge = self.RecordingBridge()
        harness = ServerHarness(self, adapter, handoff_event_bridge=bridge)
        status, body, _ = harness.request(
            "POST",
            f"/api/proposals/{task.slug.replace('/', '%2F')}/decision",
            {"action": "approve", "decision_note": "Approved."},
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["receipt"]["proposal"]["decision"], "approve")
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(bridge.calls[0][1]["task"]["proposal_decision"], "approve")
        self.assertTrue(bridge.calls[0][2]["verified"])

    def test_proposal_approval_partial_write_appends_attention_without_dispatch(self) -> None:
        task, proposal = self.proposal_task_fixture()

        class PartialApprovalAdapter(FakeAdapter):
            def decide_proposal(self, *args, **kwargs):
                raise PartialMutationError(task.slug, "Proposal approval was not verified.")

        bridge = self.RecordingBridge()
        harness = ServerHarness(
            self,
            PartialApprovalAdapter(active=(task,), proposals=(proposal,)),
            handoff_event_bridge=bridge,
        )
        status, body, _ = harness.request(
            "POST",
            f"/api/proposals/{task.slug.replace('/', '%2F')}/decision",
            {"action": "approve", "decision_note": "Approved."},
        )

        self.assertEqual(status, 502)
        self.assertEqual(body["code"], "partial_write")
        self.assertEqual(len(bridge.calls), 1)
        self.assertFalse(bridge.calls[0][2]["verified"])


class HandoffDispatcherApiTests(unittest.TestCase):
    NOW = datetime(2026, 8, 4, 17, 0, tzinfo=timezone.utc)
    TASK = "tasks/11111111-1111-4111-8111-111111111111"
    REGISTRATION = "private-registration-tammy"

    def setUp(self) -> None:
        handle, self.store_path = tempfile.mkstemp(
            prefix="handoff-api-", suffix=".sqlite3"
        )
        os.close(handle)
        Path(self.store_path).unlink()
        self.store = DurableHandoffStore(self.store_path, retention_days=30)
        self.addCleanup(self._cleanup_store)
        self.registration = AgentRegistration(
            registration_id=self.REGISTRATION,
            agent_slug="agents/tammy",
            route="hosts/tammy",
            verified=True,
        )
        self.dispatcher = HandoffDispatcher(
            self.store, registrations=(self.registration,)
        )
        self.auth = HandoffDispatcherAuth.from_plaintext_tokens_for_tests(
            {
                "agents/tammy": (
                    self.REGISTRATION,
                    "tammy-handoff-api-token",
                ),
                "agents/timmy": (
                    "private-registration-timmy",
                    "timmy-handoff-api-token",
                ),
            }
        )
        self.harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=self.store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=lambda agent, registration: (
                self.registration
                if agent == "agents/tammy" and registration == self.REGISTRATION
                else None
            ),
            handoff_waiter=lambda _seconds: None,
        )

    def _cleanup_store(self) -> None:
        self.store.close()
        Path(self.store_path).unlink(missing_ok=True)

    def _record(self, *, event: str = "events/100", task: str | None = None):
        return self.dispatcher.record(
            ActionableChange(
                task_slug=task or self.TASK,
                canonical_event_id=event,
                canonical_version="42",
                trigger="answer_received",
                assigned_to=("agents/tammy",),
                route="hosts/tammy",
                summary="A verified answer is ready.",
                occurred_at=self.NOW,
                correlation_id=f"correlation-{event.rsplit('/', 1)[-1]}",
            ),
            now=self.NOW,
        )

    def _event_count(self) -> int:
        return self.store.query_events(limit=200, after_sequence=0).total

    @staticmethod
    def _auth(token: str = "tammy-handoff-api-token") -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _claim(self, **overrides):
        body = {
            "registration_id": self.REGISTRATION,
            "wait_seconds": 0,
            "lease_seconds": 30,
        }
        body.update(overrides)
        return self.harness.request(
            "POST", "/api/handoffs/claim", body, self._auth()
        )

    def _lease_headers(self, claim: dict, *, token: str = "tammy-handoff-api-token"):
        return {
            **self._auth(token),
            "X-Handoff-Registration-ID": self.REGISTRATION,
            "X-Handoff-Lease-Capability": claim["lease_capability"],
            "X-Handoff-Lease-Generation": str(claim["lease_generation"]),
            "Idempotency-Key": "mutation-api-1",
        }

    class MutableRuntimeAdapter(FakeAdapter):
        def __init__(self, routes: dict[str, str | None], references: dict[str, str]):
            super().__init__()
            self.routes = routes
            self.references = references
            self.registration_reads: list[tuple[str, str]] = []

        def read_handoff_dispatcher_registration_by_reference(
            self,
            agent_slug: str,
            registration_reference: str,
        ):
            self.registration_reads.append((agent_slug, registration_reference))
            if self.references.get(agent_slug) != registration_reference:
                return None
            route = self.routes.get(agent_slug)
            if route is None:
                return None
            return AgentRegistration.from_reference(
                registration_reference,
                agent_slug=agent_slug,
                route=route,
            )

        def read_handoff_dispatcher_registration(
            self,
            agent_slug: str,
            registration_id: str,
        ):
            registration_reference = hashlib.sha256(
                registration_id.encode("utf-8")
            ).hexdigest()
            self.registration_reads.append((agent_slug, registration_reference))
            route = self.routes.get(agent_slug)
            if self.references.get(agent_slug) != registration_reference or route is None:
                return None
            return AgentRegistration(
                registration_id=registration_id,
                agent_slug=agent_slug,
                route=route,
                verified=True,
                _registration_reference=registration_reference,
            )

    def _runtime_route_harness(
        self,
        *,
        store: DurableHandoffStore,
        dispatcher: HandoffDispatcher,
        adapter: FakeAdapter,
        waiter=lambda _seconds: None,
    ) -> ServerHarness:
        return ServerHarness(
            self,
            adapter,
            handoff_store=store,
            handoff_dispatcher_auth=self.auth,
            handoff_waiter=waiter,
            handoff_event_bridge=gbrain.CanonicalHandoffEventBridge(dispatcher),
        )

    def _runtime_registrations(self) -> tuple[AgentRegistration, AgentRegistration]:
        return (
            AgentRegistration.from_reference(
                hashlib.sha256(self.REGISTRATION.encode("utf-8")).hexdigest(),
                agent_slug="agents/tammy",
                route="hosts/tammy",
            ),
            AgentRegistration.from_reference(
                hashlib.sha256(b"private-registration-timmy").hexdigest(),
                agent_slug="agents/timmy",
                route="hosts/timmy",
            ),
        )

    def test_runtime_claim_rejects_post_startup_duplicate_route_without_store_mutation(self) -> None:
        registrations = self._runtime_registrations()
        private_path = Path(self.harness.runtime_directory.name) / "route-drift-claim.sqlite3"
        store = DurableHandoffStore(str(private_path))
        self.addCleanup(store.close)
        dispatcher = HandoffDispatcher(store, registrations=registrations)
        record = dispatcher.record(
            ActionableChange(
                task_slug=self.TASK,
                canonical_event_id="events/route-drift-claim",
                canonical_version="42",
                trigger="answer_received",
                assigned_to=("agents/tammy",),
                route="hosts/tammy",
                summary="A verified answer is ready.",
                occurred_at=self.NOW,
                correlation_id="correlation-route-drift-claim",
            ),
            now=self.NOW,
        )
        adapter = self.MutableRuntimeAdapter(
            {"agents/tammy": "hosts/tammy", "agents/timmy": "hosts/tammy"},
            {registration.agent_slug: registration.reference for registration in registrations},
        )
        harness = self._runtime_route_harness(
            store=store,
            dispatcher=dispatcher,
            adapter=adapter,
        )
        baseline = store.query_events(limit=50, after_sequence=0).total

        status, payload, _ = harness.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30},
            self._auth(),
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        self.assertEqual(store.get(record.handoff_id).status, "queued")
        self.assertEqual(store.query_events(limit=50, after_sequence=0).total, baseline)
        self.assertEqual(len(adapter.registration_reads), 2)
        self.assertNotIn(self.REGISTRATION, repr(adapter.registration_reads))

    def test_runtime_claim_revalidates_all_routes_after_long_poll_wait(self) -> None:
        registrations = self._runtime_registrations()
        private_path = Path(self.harness.runtime_directory.name) / "route-drift-wait.sqlite3"
        store = DurableHandoffStore(str(private_path))
        self.addCleanup(store.close)
        dispatcher = HandoffDispatcher(store, registrations=registrations)
        adapter = self.MutableRuntimeAdapter(
            {"agents/tammy": "hosts/tammy", "agents/timmy": "hosts/timmy"},
            {registration.agent_slug: registration.reference for registration in registrations},
        )

        def drift_during_wait(_seconds: float) -> None:
            dispatcher.record(
                ActionableChange(
                    task_slug=self.TASK,
                    canonical_event_id="events/route-drift-wait",
                    canonical_version="42",
                    trigger="answer_received",
                    assigned_to=("agents/tammy",),
                    route="hosts/tammy",
                    summary="A verified answer is ready.",
                    occurred_at=self.NOW,
                    correlation_id="correlation-route-drift-wait",
                ),
                now=self.NOW,
            )
            adapter.routes["agents/timmy"] = "hosts/tammy"

        harness = self._runtime_route_harness(
            store=store,
            dispatcher=dispatcher,
            adapter=adapter,
            waiter=drift_during_wait,
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 25, "lease_seconds": 30},
            self._auth(),
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        events = store.query_events(limit=50, after_sequence=0)
        self.assertEqual(events.total, 1)
        self.assertEqual(events.events[0].status, "queued")
        self.assertEqual(len(adapter.registration_reads), 4)

    def test_runtime_recovery_revalidates_route_uniqueness_immediately_before_mutation(self) -> None:
        registrations = self._runtime_registrations()
        references = {
            registration.agent_slug: registration.reference
            for registration in registrations
        }
        routes = {"agents/tammy": "hosts/tammy", "agents/timmy": "hosts/timmy"}

        class DriftAfterReadStore(DurableHandoffStore):
            def read_recovery_state(_self, handoff_id, *, registration):
                state = super().read_recovery_state(
                    handoff_id,
                    registration=registration,
                )
                routes["agents/timmy"] = "hosts/tammy"
                return state

        private_path = Path(self.harness.runtime_directory.name) / "route-drift-recover.sqlite3"
        store = DriftAfterReadStore(str(private_path))
        self.addCleanup(store.close)
        dispatcher = HandoffDispatcher(store, registrations=registrations)
        record = dispatcher.record(
            ActionableChange(
                task_slug=self.TASK,
                canonical_event_id="events/route-drift-recover",
                canonical_version="42",
                trigger="answer_received",
                assigned_to=("agents/tammy",),
                route="hosts/tammy",
                summary="A verified answer is ready.",
                occurred_at=self.NOW,
                correlation_id="correlation-route-drift-recover",
            ),
            now=self.NOW,
        )
        claim = store.claim(
            registrations[0].lease_identity,
            now=self.NOW,
            lease_seconds=30,
        )
        store.acknowledge(
            record.handoff_id,
            "received",
            registration_id=registrations[0].lease_identity,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id="mutation-route-drift-received",
            now=self.NOW,
        )
        adapter = self.MutableRuntimeAdapter(routes, references)
        harness = self._runtime_route_harness(
            store=store,
            dispatcher=dispatcher,
            adapter=adapter,
        )
        baseline = store.query_events(limit=50, after_sequence=0).total

        status, payload, _ = harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": claim.lease_generation,
            },
            self._auth(),
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        self.assertEqual(store.get(record.handoff_id).status, "received")
        self.assertEqual(store.query_events(limit=50, after_sequence=0).total, baseline)
        self.assertEqual(len(adapter.registration_reads), 4)

    def test_claim_auth_is_identity_scoped_and_rejected_requests_do_not_write(self) -> None:
        self._record()
        baseline = self._event_count()
        cases = (
            ({}, {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30}),
            (self._auth("invalid-token"), {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30}),
            (self._auth("timmy-handoff-api-token"), {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30}),
            (self._auth(), {"registration_id": "private-registration-other", "wait_seconds": 0, "lease_seconds": 30}),
        )

        for headers, body in cases:
            with self.subTest(headers=headers, registration=body["registration_id"]):
                status, payload, _ = self.harness.request(
                    "POST", "/api/handoffs/claim", body, headers
                )
                self.assertIn(status, {401, 403})
                self.assertIn("code", payload)
                self.assertEqual(self._event_count(), baseline)
                self.assertEqual(self.store.get(self._record().handoff_id).status, "queued")

    def test_shared_tokens_are_rejected_by_the_auth_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            HandoffDispatcherAuth.from_plaintext_tokens_for_tests(
                {
                    "agents/tammy": (self.REGISTRATION, "shared-token"),
                    "agents/timmy": ("private-registration-timmy", "shared-token"),
                }
            )

    def test_claim_validates_wait_and_lease_bounds_without_mutation(self) -> None:
        self._record()
        baseline = self._event_count()
        for field, value in (
            ("wait_seconds", -1),
            ("wait_seconds", 26),
            ("wait_seconds", True),
            ("lease_seconds", 4),
            ("lease_seconds", 121),
            ("lease_seconds", 30.5),
        ):
            with self.subTest(field=field, value=value):
                status, payload, _ = self._claim(**{field: value})
                self.assertEqual(status, 422)
                self.assertEqual(payload["code"], "invalid_handoff_claim")
                self.assertEqual(self._event_count(), baseline)

        empty_store_path = Path(self.harness.runtime_directory.name) / "empty.sqlite3"
        empty_store = DurableHandoffStore(str(empty_store_path))
        self.addCleanup(empty_store.close)
        waits: list[float] = []
        empty_harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=empty_store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=lambda *_args: self.registration,
            handoff_waiter=waits.append,
        )
        for wait_seconds, lease_seconds in ((0, 5), (25, 120)):
            status, payload, _ = empty_harness.request(
                "POST",
                "/api/handoffs/claim",
                {
                    "registration_id": self.REGISTRATION,
                    "wait_seconds": wait_seconds,
                    "lease_seconds": lease_seconds,
                },
                self._auth(),
            )
            self.assertEqual((status, payload), (204, {}))
        self.assertEqual(waits, [25])

    def test_route_readback_failure_fails_closed_without_claiming(self) -> None:
        record = self._record()

        def unavailable(_agent: str, _registration: str):
            raise RuntimeError("private route source unavailable")

        harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=self.store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=unavailable,
            handoff_waiter=lambda _seconds: None,
        )
        baseline = self._event_count()
        status, payload, _ = harness.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30},
            self._auth(),
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["code"], "handoff_route_unavailable")
        self.assertEqual(self._event_count(), baseline)
        self.assertEqual(self.store.get(record.handoff_id).status, "queued")

    def test_production_route_reader_is_wired_and_required_when_runtime_is_enabled(self) -> None:
        record = self._record()
        harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=self.store,
            handoff_dispatcher_auth=self.auth,
            handoff_waiter=lambda _seconds: None,
        )
        baseline = self._event_count()

        status, payload, _ = harness.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30},
            self._auth(),
        )

        self.assertEqual(status, 503)
        self.assertEqual(payload["code"], "handoff_route_unavailable")
        self.assertEqual(self.store.get(record.handoff_id).status, "queued")
        self.assertEqual(self._event_count(), baseline)

        class CanonicalAdapter(FakeAdapter):
            def read_handoff_dispatcher_registration(
                _self, agent_slug: str, registration_id: str
            ):
                if (
                    agent_slug == self.registration.agent_slug
                    and registration_id == self.registration.registration_id
                ):
                    return self.registration
                return None

        wired = ServerHarness(
            self,
            CanonicalAdapter(),
            handoff_store=self.store,
            handoff_dispatcher_auth=self.auth,
            handoff_waiter=lambda _seconds: None,
        )
        status, payload, _ = wired.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30},
            self._auth(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["handoff_id"], record.handoff_id)

    def test_route_is_revalidated_after_wait_before_delayed_claim(self) -> None:
        route_available = True
        callback_count = 0

        def read_registration(_agent: str, _registration: str):
            nonlocal callback_count
            callback_count += 1
            return self.registration if route_available else None

        def during_wait(_seconds: float) -> None:
            nonlocal route_available
            self._record(event="events/during-wait")
            route_available = False

        empty_path = Path(self.harness.runtime_directory.name) / "wait.sqlite3"
        waiting_store = DurableHandoffStore(str(empty_path))
        self.addCleanup(waiting_store.close)
        waiting_dispatcher = HandoffDispatcher(
            waiting_store, registrations=(self.registration,)
        )
        original_dispatcher = self.dispatcher
        original_store = self.store
        self.dispatcher = waiting_dispatcher
        self.store = waiting_store
        self.addCleanup(setattr, self, "dispatcher", original_dispatcher)
        self.addCleanup(setattr, self, "store", original_store)
        harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=waiting_store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=read_registration,
            handoff_waiter=during_wait,
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 25, "lease_seconds": 30},
            self._auth(),
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        events = waiting_store.query_events(limit=50, after_sequence=0)
        self.assertEqual(events.total, 1)
        record = events.events[0]
        self.assertEqual(record.status, "queued")
        self.assertEqual(callback_count, 2)

    def test_atomic_claim_mismatch_never_adds_lease_event(self) -> None:
        private_path = Path(self.harness.runtime_directory.name) / "mismatch.sqlite3"
        private_store = DurableHandoffStore(str(private_path))
        self.addCleanup(private_store.close)
        private_registration = AgentRegistration(
            registration_id=self.REGISTRATION,
            agent_slug="agents/timmy",
            route="hosts/timmy",
            verified=True,
        )
        private_dispatcher = HandoffDispatcher(
            private_store, registrations=(private_registration,)
        )
        record = private_dispatcher.record(
            ActionableChange(
                task_slug=self.TASK,
                canonical_event_id="events/mismatched-owner",
                canonical_version="42",
                trigger="answer_received",
                assigned_to=("agents/timmy",),
                route="hosts/timmy",
                summary="A verified answer is ready.",
                occurred_at=self.NOW,
                correlation_id="correlation-mismatched-owner",
            ),
            now=self.NOW,
        )
        harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=private_store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=lambda *_args: self.registration,
            handoff_waiter=lambda _seconds: None,
        )
        baseline = private_store.query_events(limit=50, after_sequence=0).total

        status, _payload, _ = harness.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30},
            self._auth(),
        )

        self.assertEqual(status, 204)
        self.assertEqual(private_store.get(record.handoff_id).status, "queued")
        self.assertEqual(
            private_store.query_events(limit=50, after_sequence=0).total,
            baseline,
        )

    def test_claim_is_atomic_and_payload_is_redacted(self) -> None:
        self._record()

        def claim_once():
            return self._claim()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: claim_once(), range(2)))

        self.assertEqual(sorted(response[0] for response in responses), [200, 204])
        payload = next(response[1] for response in responses if response[0] == 200)
        self.assertEqual(payload["task_slug"], self.TASK)
        self.assertEqual(payload["trigger"], "answer_received")
        self.assertEqual(payload["summary"], "A verified answer is ready.")
        self.assertEqual(payload["correlation_id"], "correlation-100")
        self.assertIn("idempotency_key", payload)
        self.assertIn("registration_ref", payload)
        self.assertIn("lease_capability", payload)
        self.assertEqual(payload["lease_generation"], 1)
        rendered = json.dumps(payload)
        self.assertNotIn(self.REGISTRATION, rendered)
        self.assertNotIn("tammy-handoff-api-token", rendered)
        self.assertNotIn("thread", rendered.lower())

    def test_acknowledgements_enforce_owner_state_and_blocked_detail(self) -> None:
        allowed = ("received", "actively_executing", "still_blocked", "completed")
        for index, state in enumerate(allowed):
            with self.subTest(state=state):
                record = self._record(event=f"events/ack-{index}")
                status, claim, _ = self._claim()
                self.assertEqual(status, 200)
                headers = self._lease_headers(claim)
                headers["Idempotency-Key"] = f"mutation-ack-{index}"
                detail = "Waiting on verified approval." if state == "still_blocked" else None
                status, payload, _ = self.harness.request(
                    "POST",
                    f"/api/handoffs/{record.handoff_id}/ack",
                    {"status": state, "detail": detail},
                    headers,
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload["status"], state)

        record = self._record(event="events/blocked-empty")
        _status, claim, _ = self._claim()
        baseline = self._event_count()
        status, payload, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/ack",
            {"status": "still_blocked", "detail": None},
            self._lease_headers(claim),
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_handoff_ack")
        self.assertEqual(self._event_count(), baseline)
        self.assertEqual(self.store.get(record.handoff_id).status, "leased")

        status, payload, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/ack",
            {"status": "completed", "detail": None},
            self._lease_headers(claim, token="timmy-handoff-api-token"),
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        self.assertEqual(self._event_count(), baseline)

    def test_retryable_and_terminal_failure_routes_are_distinct(self) -> None:
        # Terminal first keeps the retryable handoff from being reclaimed ahead
        # of the second fixture under the store's oldest-first retry contract.
        for index, failure_class in enumerate(("terminal", "retryable")):
            record = self._record(event=f"events/failure-{index}")
            _status, claim, _ = self._claim()
            headers = self._lease_headers(claim)
            headers["Idempotency-Key"] = f"mutation-failure-{index}"
            status, payload, _ = self.harness.request(
                "POST",
                f"/api/handoffs/{record.handoff_id}/failure",
                {"failure_class": failure_class},
                headers,
            )
            self.assertEqual(status, 200)
            self.assertEqual(
                payload["status"],
                "retrying" if failure_class == "retryable" else "dead_letter",
            )

        record = self._record(event="events/failure-invalid")
        _status, claim, _ = self._claim()
        baseline = self._event_count()
        status, payload, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/failure",
            {"failure_class": "unknown"},
            self._lease_headers(claim),
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_handoff_failure")
        self.assertEqual(self._event_count(), baseline)

    def test_recover_rotates_authenticated_in_progress_capability(self) -> None:
        record = self._record(event="events/recover")
        _status, claim, _ = self._claim()
        headers = self._lease_headers(claim)
        headers["Idempotency-Key"] = "mutation-recover-received"
        status, received, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/ack",
            {"status": "received", "detail": None},
            headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(received["status"], "received")

        status, recovered, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": claim["lease_generation"],
            },
            self._auth(),
        )

        self.assertEqual(status, 200)
        self.assertEqual(recovered["handoff_id"], record.handoff_id)
        self.assertEqual(recovered["status"], "received")
        self.assertEqual(
            recovered["lease_generation"], claim["lease_generation"] + 1
        )
        self.assertNotEqual(
            recovered["lease_capability"], claim["lease_capability"]
        )
        rendered = json.dumps(recovered)
        self.assertNotIn(self.REGISTRATION, rendered)
        self.assertNotIn("tammy-handoff-api-token", rendered)
        self.assertNotIn("thread", rendered.lower())

    def test_runtime_recovery_internalizes_raw_registration_before_store_access(self) -> None:
        class CapturingStore(DurableHandoffStore):
            def __init__(self, path: str) -> None:
                super().__init__(path)
                self.recovery_registration_ids: list[str] = []

            def read_recovery_state(self, handoff_id, *, registration):
                self.recovery_registration_ids.append(registration.registration_id)
                return super().read_recovery_state(
                    handoff_id,
                    registration=registration,
                )

        private_path = Path(self.harness.runtime_directory.name) / "runtime-hash.sqlite3"
        store = CapturingStore(str(private_path))
        self.addCleanup(store.close)
        registration_reference = self.registration.reference
        runtime_registration = AgentRegistration.from_reference(
            registration_reference,
            agent_slug=self.registration.agent_slug,
            route=self.registration.route,
        )
        dispatcher = HandoffDispatcher(store, registrations=(runtime_registration,))
        record = dispatcher.record(
            ActionableChange(
                task_slug=self.TASK,
                canonical_event_id="events/runtime-internal-recovery",
                canonical_version="42",
                trigger="answer_received",
                assigned_to=(self.registration.agent_slug,),
                route=self.registration.route,
                summary="A verified answer is ready.",
                occurred_at=self.NOW,
                correlation_id="correlation-runtime-internal-recovery",
            ),
            now=self.NOW,
        )
        claim = store.claim(
            registration_reference,
            now=self.NOW,
            lease_seconds=30,
        )
        store.acknowledge(
            record.handoff_id,
            "received",
            registration_id=registration_reference,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id="mutation-runtime-internal-received",
            now=self.NOW,
        )
        runtime_registrations = self._runtime_registrations()
        adapter = self.MutableRuntimeAdapter(
            {"agents/tammy": "hosts/tammy", "agents/timmy": "hosts/timmy"},
            {
                registration.agent_slug: registration.reference
                for registration in runtime_registrations
            },
        )
        harness = ServerHarness(
            self,
            adapter,
            handoff_store=store,
            handoff_dispatcher_auth=self.auth,
            handoff_event_bridge=gbrain.CanonicalHandoffEventBridge(dispatcher),
        )

        status, _payload, _ = harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": claim.lease_generation,
            },
            self._auth(),
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            store.recovery_registration_ids,
            [registration_reference],
        )
        self.assertNotIn(self.REGISTRATION, repr(store.recovery_registration_ids))

    def test_recover_validates_leased_and_reconciles_crash_window_generation(self) -> None:
        record = self._record(event="events/recover-leased")
        _status, claim, _ = self._claim()

        status, first_recovery, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": claim["lease_generation"],
            },
            self._auth(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(first_recovery["status"], "leased")
        self.assertEqual(
            first_recovery["lease_generation"], claim["lease_generation"] + 1
        )

        baseline = self._event_count()
        status, authoritative, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": claim["lease_generation"],
            },
            self._auth(),
        )
        self.assertEqual(status, 409)
        self.assertEqual(authoritative["code"], "handoff_recovery_reconcile")
        self.assertEqual(authoritative["handoff_id"], record.handoff_id)
        self.assertEqual(authoritative["status"], "leased")
        self.assertEqual(
            authoritative["lease_generation"],
            first_recovery["lease_generation"],
        )
        self.assertEqual(authoritative["agent_slug"], "agents/tammy")
        self.assertEqual(
            authoritative["registration_ref"], self.registration.reference
        )
        self.assertNotIn("lease_capability", authoritative)
        self.assertNotIn(self.REGISTRATION, json.dumps(authoritative))
        self.assertEqual(self._event_count(), baseline)

        status, second_recovery, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": authoritative["lease_generation"],
            },
            self._auth(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            second_recovery["lease_generation"],
            authoritative["lease_generation"] + 1,
        )

    def test_recover_reconciles_retrying_and_terminal_without_mutation(self) -> None:
        cases = (("retryable", "retrying"), ("terminal", "dead_letter"))
        for index, (failure_class, expected_status) in enumerate(cases):
            with self.subTest(failure_class=failure_class):
                record = self._record(event=f"events/reconcile-{index}")
                _status, claim, _ = self._claim()
                headers = self._lease_headers(claim)
                headers["Idempotency-Key"] = f"mutation-reconcile-{index}"
                status, _payload, _ = self.harness.request(
                    "POST",
                    f"/api/handoffs/{record.handoff_id}/failure",
                    {"failure_class": failure_class},
                    headers,
                )
                self.assertEqual(status, 200)
                baseline = self._event_count()

                status, authoritative, _ = self.harness.request(
                    "POST",
                    f"/api/handoffs/{record.handoff_id}/recover",
                    {
                        "registration_id": self.REGISTRATION,
                        "expected_generation": claim["lease_generation"],
                    },
                    self._auth(),
                )
                self.assertEqual(status, 409)
                self.assertEqual(
                    authoritative["code"], "handoff_recovery_reconcile"
                )
                self.assertEqual(authoritative["status"], expected_status)
                self.assertEqual(
                    authoritative["lease_generation"], claim["lease_generation"]
                )
                self.assertNotIn("lease_capability", authoritative)
                self.assertEqual(self._event_count(), baseline)

                if failure_class == "retryable":
                    _claim_status, reclaimed, _ = self._claim()
                    reclaim_headers = self._lease_headers(reclaimed)
                    reclaim_headers["Idempotency-Key"] = "mutation-reconcile-cleanup"
                    self.harness.request(
                        "POST",
                        f"/api/handoffs/{record.handoff_id}/failure",
                        {"failure_class": "terminal"},
                        reclaim_headers,
                    )

    def test_recovery_wrong_owner_returns_no_authoritative_state_or_mutation(self) -> None:
        private_path = Path(self.harness.runtime_directory.name) / "recovery-owner.sqlite3"
        private_store = DurableHandoffStore(str(private_path))
        self.addCleanup(private_store.close)
        private_registration = AgentRegistration(
            registration_id=self.REGISTRATION,
            agent_slug="agents/timmy",
            route="hosts/timmy",
            verified=True,
        )
        private_dispatcher = HandoffDispatcher(
            private_store, registrations=(private_registration,)
        )
        record = private_dispatcher.record(
            ActionableChange(
                task_slug=self.TASK,
                canonical_event_id="events/recovery-owner",
                canonical_version="42",
                trigger="answer_received",
                assigned_to=("agents/timmy",),
                route="hosts/timmy",
                summary="A verified answer is ready.",
                occurred_at=self.NOW,
                correlation_id="correlation-recovery-owner",
            ),
            now=self.NOW,
        )
        private_store.claim(self.REGISTRATION, now=self.NOW, lease_seconds=30)
        baseline = private_store.query_events(limit=50, after_sequence=0).total
        harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=private_store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=lambda *_args: self.registration,
            handoff_waiter=lambda _seconds: None,
        )

        status, payload, _ = harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {"registration_id": self.REGISTRATION, "expected_generation": 1},
            self._auth(),
        )

        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        self.assertNotIn("status", payload)
        self.assertNotIn("lease_generation", payload)
        self.assertNotIn("registration_ref", payload)
        self.assertEqual(private_store.get(record.handoff_id).status, "leased")
        self.assertEqual(
            private_store.query_events(limit=50, after_sequence=0).total,
            baseline,
        )

    def test_recover_rejects_stale_wrong_or_revoked_identity_without_mutation(self) -> None:
        record = self._record(event="events/recover-rejected")
        _status, claim, _ = self._claim()
        headers = self._lease_headers(claim)
        headers["Idempotency-Key"] = "mutation-recover-active"
        status, _payload, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/ack",
            {"status": "actively_executing", "detail": None},
            headers,
        )
        self.assertEqual(status, 200)

        baseline = self._event_count()
        cases = (
            (
                self._auth("timmy-handoff-api-token"),
                {"registration_id": self.REGISTRATION, "expected_generation": 1},
                403,
            ),
            (
                self._auth(),
                {
                    "registration_id": "private-registration-other",
                    "expected_generation": 1,
                },
                403,
            ),
            (
                self._auth(),
                {
                    "registration_id": self.REGISTRATION,
                    "expected_generation": 99,
                },
                409,
            ),
            (
                self._auth(),
                {
                    "registration_id": self.REGISTRATION,
                    "expected_generation": 1,
                    "extra": True,
                },
                422,
            ),
        )
        for auth_headers, body, expected_status in cases:
            with self.subTest(body=body, expected_status=expected_status):
                status, _payload, _ = self.harness.request(
                    "POST",
                    f"/api/handoffs/{record.handoff_id}/recover",
                    body,
                    auth_headers,
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(self._event_count(), baseline)
                self.assertEqual(self.store.get(record.handoff_id).status, "actively_executing")

        revoked = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=self.store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=lambda *_args: None,
            handoff_waiter=lambda _seconds: None,
        )
        status, payload, _ = revoked.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {"registration_id": self.REGISTRATION, "expected_generation": 1},
            self._auth(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        self.assertEqual(self._event_count(), baseline)

    def test_soft_deleted_canonical_registration_blocks_claim_and_recover_without_mutation(self) -> None:
        class DeletedAgentRunner:
            def run(_self, command: str, params: dict):
                self.assertEqual(command, "get_page")
                self.assertEqual(params, {"slug": "agents/tammy"})
                return {
                    "slug": "agents/tammy",
                    "type": "agent",
                    "title": "Agent Tammy",
                    "deleted_at": "2026-08-04T18:00:00Z",
                    "frontmatter": {
                        "handoff_dispatcher": {
                            "registration_sha256": self.registration.reference,
                            "route": "hosts/tammy",
                            "verified": True,
                        }
                    },
                }

        deleted_reader = gbrain.GBrainAdapter(
            DeletedAgentRunner()
        ).read_handoff_dispatcher_registration
        queued = self._record(event="events/deleted-claim")
        deleted_harness = ServerHarness(
            self,
            FakeAdapter(),
            handoff_store=self.store,
            handoff_dispatcher_auth=self.auth,
            handoff_registration_validator=deleted_reader,
            handoff_waiter=lambda _seconds: None,
        )
        baseline = self._event_count()
        status, payload, _ = deleted_harness.request(
            "POST",
            "/api/handoffs/claim",
            {"registration_id": self.REGISTRATION, "wait_seconds": 0, "lease_seconds": 30},
            self._auth(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        self.assertEqual(self.store.get(queued.handoff_id).status, "queued")
        self.assertEqual(self._event_count(), baseline)

        _status, claim, _ = self._claim()
        headers = self._lease_headers(claim)
        headers["Idempotency-Key"] = "mutation-deleted-recover-state"
        status, _payload, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{claim['handoff_id']}/ack",
            {"status": "received", "detail": None},
            headers,
        )
        self.assertEqual(status, 200)
        baseline = self._event_count()
        status, payload, _ = deleted_harness.request(
            "POST",
            f"/api/handoffs/{claim['handoff_id']}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": claim["lease_generation"],
            },
            self._auth(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "handoff_identity_mismatch")
        self.assertEqual(self._event_count(), baseline)
        self.assertEqual(self.store.get(claim["handoff_id"]).status, "received")

    def test_recovered_api_failure_retries_and_reclaims_same_handoff(self) -> None:
        record = self._record(event="events/api-recovered-retry")
        _status, claim, _ = self._claim()
        headers = self._lease_headers(claim)
        headers["Idempotency-Key"] = "mutation-api-recovered-active"
        status, _payload, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/ack",
            {"status": "actively_executing", "detail": None},
            headers,
        )
        self.assertEqual(status, 200)
        status, recovered, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/recover",
            {
                "registration_id": self.REGISTRATION,
                "expected_generation": claim["lease_generation"],
            },
            self._auth(),
        )
        self.assertEqual(status, 200)
        failure_headers = self._lease_headers(recovered)
        failure_headers["Idempotency-Key"] = "mutation-api-recovered-retry"

        status, retried, _ = self.harness.request(
            "POST",
            f"/api/handoffs/{record.handoff_id}/failure",
            {"failure_class": "retryable"},
            failure_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(retried["status"], "retrying")
        status, reclaimed, _ = self._claim()
        self.assertEqual(status, 200)
        self.assertEqual(reclaimed["handoff_id"], record.handoff_id)
        self.assertEqual(
            reclaimed["lease_generation"], recovered["lease_generation"] + 1
        )

    def test_event_endpoints_share_deterministic_filters_counts_cursors_and_export(self) -> None:
        second_task = "tasks/22222222-2222-4222-8222-222222222222"
        self._record(event="events/log-1")
        self._record(event="events/log-2", task=second_task)
        status, page, _ = self.harness.request(
            "GET", "/api/handoff-events?limit=1&after_sequence=0&agent_slug=agents%2Ftammy"
        )
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["events"]), 1)
        self.assertIn("next_sequence", page)
        status, tail, _ = self.harness.request(
            "GET",
            f"/api/handoff-events?limit=1&after_sequence={page['next_sequence']}&agent_slug=agents%2Ftammy",
        )
        self.assertEqual(status, 200)
        self.assertEqual(tail["total"], 1)
        self.assertNotIn("next_sequence", tail)

        encoded = self.TASK.replace("/", "%2F")
        status, scoped, _ = self.harness.request(
            "GET", f"/api/tasks/{encoded}/handoff-events?limit=50&after_sequence=0"
        )
        self.assertEqual(status, 200)
        self.assertEqual(scoped["total"], 1)
        self.assertEqual(scoped["events"][0]["task_slug"], self.TASK)

        before = self._event_count()
        status, exported, _ = self.harness.request(
            "GET", "/api/handoff-events?limit=50&after_sequence=0&export=1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(exported["metadata"]["format"], "handoff-audit-v1")
        self.assertEqual(self._event_count(), before)

    def test_event_endpoints_share_bounded_timestamp_range_filter(self) -> None:
        self._record(event="events/range-current")
        encoded = self.TASK.replace("/", "%2F")
        query = (
            "limit=50&after_sequence=0"
            "&occurred_after=2026-08-04T16%3A00%3A00%2B00%3A00"
            "&occurred_before=2026-08-04T17%3A00%3A00%2B00%3A00"
        )

        status, global_page, _ = self.harness.request(
            "GET", f"/api/handoff-events?{query}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(global_page["total"], 1)
        self.assertEqual(global_page["events"][0]["task_slug"], self.TASK)

        status, scoped_page, _ = self.harness.request(
            "GET", f"/api/tasks/{encoded}/handoff-events?{query}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(scoped_page, global_page)

        status, invalid, _ = self.harness.request(
            "GET",
            "/api/handoff-events?limit=50&after_sequence=0"
            "&occurred_after=2026-08-04T17%3A00%3A00%2B00%3A00"
            "&occurred_before=2026-08-04T16%3A00%3A00%2B00%3A00",
        )
        self.assertEqual(status, 422)
        self.assertEqual(invalid["code"], "invalid_handoff_event_filter")


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
    def test_task_snapshot_exposes_one_la_rolling_window_scope_for_all_tasks(self) -> None:
        inside = new_task(
            title="Inside rolling window",
            now=datetime(2026, 8, 3, 9, 0).astimezone(),
            identity="inside-window",
            due_day=date(2026, 8, 4),
        )
        outside = new_task(
            title="Outside rolling window",
            now=datetime(2026, 8, 3, 9, 0).astimezone(),
            identity="outside-window",
            due_day=date(2026, 10, 4),
        )

        snapshot = build_task_snapshot(
            FakeAdapter(active=(inside, outside)),
            date(2026, 8, 3),
        )

        self.assertEqual(
            snapshot["task_display_scope"],
            {
                "start_day": "2026-07-03",
                "end_day": "2026-09-03",
                "timezone": "America/Los_Angeles",
            },
        )
        by_slug = {task["slug"]: task for task in snapshot["tasks"]}
        self.assertTrue(by_slug[inside.slug]["in_default_display_window"])
        self.assertFalse(by_slug[outside.slug]["in_default_display_window"])
        self.assertEqual(
            snapshot["event_bindings"]["job_applied"],
            {
                "task_slug": "tasks/562466ac-3569-4013-b105-746a64816cc6",
                "timezone": "America/Los_Angeles",
            },
        )

    def test_health_declares_read_cache_and_isolated_qa_scope(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload["qa_fixtures_root"], QA_FIXTURES_ROOT)
        self.assertEqual(payload["read_surfaces"], "last_verified_local_cache")
        self.assertEqual(
            payload["job_applied_bound_task"],
            "tasks/562466ac-3569-4013-b105-746a64816cc6",
        )

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
        self.assertEqual(payload["version"], "V0.0.76")

    def test_release_history_is_served_from_the_canonical_catalog(self) -> None:
        harness = ServerHarness(self, FakeAdapter())

        status, payload, _ = harness.request("GET", "/api/releases")

        self.assertEqual(status, 200)
        self.assertEqual(payload["current_version"], "V0.0.76")
        self.assertEqual(payload["releases"][0]["version"], "V0.0.76")
        self.assertEqual(
            [release["version"] for release in payload["releases"]],
            [
                "V0.0.76",
                "V0.0.75",
                "V0.0.74",
                "V0.0.73",
                "V0.0.72",
                "V0.0.71",
                "V0.0.70",
                "V0.0.69",
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
    def test_reads_one_canonical_qa_fixture_for_handoff_timeline(self) -> None:
        now = datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)
        fixture = replace(
            new_task(
                title="Dispatcher release canary fixture",
                detail="Isolated canonical QA fixture.",
                due_day=now.date(),
                now=now,
                identity="canaryfixture",
            ),
            slug="tasks/70cf1aeb-ac30-4d78-995a-a1fea9d5bea9",
            status="completed",
            lifecycle_root=QA_FIXTURES_ROOT,
            qa_fixture=True,
            qa_owner="mission_control_release_canary",
            qa_release="V0.0.76",
            owner_agent="agents/tammy",
            inbox=False,
            completed_at=now,
        )
        harness = ServerHarness(self, FakeAdapter(completed=(fixture,)))

        encoded = fixture.slug.replace("/", "%2F")
        status, payload, headers = harness.request("GET", f"/api/tasks/{encoded}")

        self.assertEqual(status, 200)
        self.assertEqual(payload["task"]["slug"], fixture.slug)
        self.assertEqual(payload["task"]["lifecycle_root"], QA_FIXTURES_ROOT)
        self.assertTrue(payload["task"]["qa_fixture"])
        self.assertEqual(payload["task"]["owner_agent"], "agents/tammy")
        self.assertEqual(headers["Cache-Control"], "no-store")

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

    def test_creates_automatic_job_metric_with_custom_target_and_seeded_current(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {
                "title": "Apply for more companies",
                "detail": "Continue from work already completed.",
                "priority": "high",
                "initial_todo": "Apply to the next company",
                "due_day": "2026-07-31",
                "project_slug": None,
                "goal_slug": None,
                "progress_metric": {
                    "kind": "count",
                    "label": "Job applications",
                    "target": 3,
                    "current": 2,
                    "event_binding": "job_applied",
                    "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_task")
        self.assertIn("explicit bound task", payload["error"])
        self.assertEqual(adapter.created, [])

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
                    "event_binding": None,
                    "auto_complete": False,
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
        self.assertIsNone(duplicate.progress_metric.task_day)
        self.assertIsNone(duplicate.progress_metric.event_binding)
        self.assertIsNone(duplicate.event_progress)
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
    def test_sets_custom_job_application_target_with_seeded_progress(self) -> None:
        now = datetime.fromisoformat("2026-07-30T09:00:00-07:00")
        from gtasks.job_application_binding import JOB_APPLIED_BOUND_TASK_SLUG
        task = replace(new_task(
            title="Apply for more companies",
            due_day=date(2026, 7, 30),
            now=now,
            identity="metric00",
        ), slug=JOB_APPLIED_BOUND_TASK_SLUG)
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
                    "target": 3,
                    "current": 1,
                    "event_binding": "job_applied",
                    "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["receipt"]["task"]["progress_metric"]["target"], 3)
        self.assertEqual(payload["receipt"]["task"]["progress_metric"]["current"], 1)
        self.assertEqual(
            payload["receipt"]["task"]["event_progress"],
            {"baseline_count": 1, "evidence_slugs": [], "receipt_ids": []},
        )

    def test_sets_daily_job_application_metric_with_verified_empty_evidence(
        self,
    ) -> None:
        now = datetime.fromisoformat("2026-07-30T09:00:00-07:00")
        from gtasks.job_application_binding import JOB_APPLIED_BOUND_TASK_SLUG
        task = replace(new_task(
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
        ), slug=JOB_APPLIED_BOUND_TASK_SLUG)
        adapter = FakeAdapter(active=(task,))
        harness = ServerHarness(self, adapter)
        revision = build_task_snapshot(adapter, now.date())["tasks"][0][
            "progress_metric_revision"
        ]

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

    def test_full_edit_reports_scoped_verified_minimum_without_changing_inputs(self) -> None:
        from gtasks.job_application_binding import JOB_APPLIED_BOUND_TASK_SLUG

        now = datetime.fromisoformat("2026-08-03T20:00:00-07:00")
        metric = ProgressMetric.from_value({
            "kind": "count", "label": "Job applications", "unit": "job_application",
            "target": 30, "current": 10, "event_binding": "job_applied",
            "auto_complete": True, "task_day": "2026-08-05",
            "timezone": "America/Los_Angeles",
        })
        task = replace(
            new_task(title="Apply", due_day=date(2026, 8, 5), now=now, identity="bound001"),
            slug=JOB_APPLIED_BOUND_TASK_SLUG,
            progress_metric=metric,
            event_progress=EventProgress(
                baseline_count=8,
                evidence_slugs=("applications/a", "applications/b"),
                receipt_ids=("evt-a", "evt-b"),
            ),
        )
        adapter = FakeAdapter(active=(task,))
        harness = ServerHarness(self, adapter)
        revision = build_task_snapshot(adapter, now.date())["tasks"][0][
            "progress_metric_revision"
        ]

        status, payload, _ = harness.request(
            "PATCH",
            f"/api/tasks/{JOB_APPLIED_BOUND_TASK_SLUG.replace('/', '%2F')}",
            {
                "title": task.title, "detail": task.detail, "priority": task.priority,
                "due_day": "2026-08-05", "project_slug": None, "goal_slug": None,
                "status": "active", "assignee_slug": "tony",
                "progress_metric_revision": revision,
                "progress_metric": {
                    "kind": "count", "label": "Job applications", "target": 30,
                    "current": 1, "event_binding": "job_applied", "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_task_edit")
        self.assertIn("2 distinct verified job-application events", payload["error"])
        self.assertIn("2026-08-05 (America/Los_Angeles)", payload["error"])
        self.assertIn("Set Current to 2 or higher", payload["error"])
        self.assertEqual(adapter.active[0].progress_metric.current, 10)
        self.assertEqual(adapter.active[0].progress_metric.target, 30)

    def test_full_edit_preserves_verified_receipts_and_updates_manual_baseline(self) -> None:
        from gtasks.job_application_binding import JOB_APPLIED_BOUND_TASK_SLUG

        now = datetime.fromisoformat("2026-08-03T20:00:00-07:00")
        metric = ProgressMetric.from_value({
            "kind": "count", "label": "Job applications", "unit": "job_application",
            "target": 30, "current": 10, "event_binding": "job_applied",
            "auto_complete": True, "task_day": "2026-08-05",
            "timezone": "America/Los_Angeles",
        })
        task = replace(
            new_task(title="Apply", due_day=date(2026, 8, 5), now=now, identity="bound002"),
            slug=JOB_APPLIED_BOUND_TASK_SLUG,
            progress_metric=metric,
            event_progress=EventProgress(
                baseline_count=8,
                evidence_slugs=("applications/a", "applications/b"),
                receipt_ids=("evt-a", "evt-b"),
            ),
        )
        adapter = FakeAdapter(active=(task,))
        harness = ServerHarness(self, adapter)
        revision = build_task_snapshot(adapter, now.date())["tasks"][0][
            "progress_metric_revision"
        ]

        status, payload, _ = harness.request(
            "PATCH",
            f"/api/tasks/{JOB_APPLIED_BOUND_TASK_SLUG.replace('/', '%2F')}",
            {
                "title": task.title, "detail": task.detail, "priority": task.priority,
                "due_day": "2026-08-05", "project_slug": None, "goal_slug": None,
                "status": "active", "assignee_slug": "tony",
                "progress_metric_revision": revision,
                "progress_metric": {
                    "kind": "count", "label": "Job applications", "target": 30,
                    "current": 25, "event_binding": "job_applied", "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 200)
        stored = payload["receipt"]["task"]
        self.assertEqual(stored["progress_metric"]["current"], 25)
        self.assertEqual(stored["progress_metric"]["target"], 30)
        self.assertEqual(stored["event_progress"]["baseline_count"], 23)
        self.assertEqual(stored["event_progress"]["receipt_ids"], ["evt-a", "evt-b"])

    def test_full_edit_rejects_removing_verified_event_history(self) -> None:
        from gtasks.job_application_binding import JOB_APPLIED_BOUND_TASK_SLUG

        now = datetime.fromisoformat("2026-08-03T20:00:00-07:00")
        metric = ProgressMetric.from_value({
            "kind": "count", "label": "Job applications", "unit": "job_application",
            "target": 30, "current": 10, "event_binding": "job_applied",
            "auto_complete": True, "task_day": "2026-08-05",
            "timezone": "America/Los_Angeles",
        })
        task = replace(
            new_task(title="Apply", due_day=date(2026, 8, 5), now=now, identity="bound003"),
            slug=JOB_APPLIED_BOUND_TASK_SLUG,
            progress_metric=metric,
            event_progress=EventProgress(
                baseline_count=8,
                evidence_slugs=("applications/a", "applications/b"),
                receipt_ids=("evt-a", "evt-b"),
            ),
        )
        adapter = FakeAdapter(active=(task,))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            f"/api/tasks/{JOB_APPLIED_BOUND_TASK_SLUG.replace('/', '%2F')}",
            {
                "title": task.title, "detail": task.detail, "priority": task.priority,
                "due_day": "2026-08-05", "project_slug": None, "goal_slug": None,
                "status": "active", "assignee_slug": "tony",
                "progress_metric": None,
            },
        )

        self.assertEqual(status, 422)
        self.assertIn("cannot be removed", payload["error"])
        self.assertEqual(adapter.active[0].event_progress.receipt_ids, ("evt-a", "evt-b"))

    def test_full_edit_rejects_stale_progress_revision(self) -> None:
        from gtasks.job_application_binding import JOB_APPLIED_BOUND_TASK_SLUG

        now = datetime.fromisoformat("2026-08-03T20:00:00-07:00")
        metric = ProgressMetric.from_value({
            "kind": "count", "label": "Job applications", "unit": "job_application",
            "target": 30, "current": 10, "event_binding": "job_applied",
            "auto_complete": True, "task_day": "2026-08-05",
            "timezone": "America/Los_Angeles",
        })
        task = replace(
            new_task(title="Apply", due_day=date(2026, 8, 5), now=now, identity="bound004"),
            slug=JOB_APPLIED_BOUND_TASK_SLUG,
            progress_metric=metric,
            event_progress=EventProgress(
                baseline_count=8,
                evidence_slugs=("applications/a", "applications/b"),
                receipt_ids=("evt-a", "evt-b"),
            ),
        )
        adapter = FakeAdapter(active=(task,))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "PATCH",
            f"/api/tasks/{JOB_APPLIED_BOUND_TASK_SLUG.replace('/', '%2F')}",
            {
                "title": task.title, "detail": task.detail, "priority": task.priority,
                "due_day": "2026-08-05", "project_slug": None, "goal_slug": None,
                "status": "active", "assignee_slug": "tony",
                "progress_metric_revision": "stale",
                "progress_metric": {
                    "kind": "count", "label": "Job applications", "target": 30,
                    "current": 25, "event_binding": "job_applied", "auto_complete": True,
                },
            },
        )

        self.assertEqual(status, 422)
        self.assertIn("changed after Edit opened", payload["error"])
        self.assertEqual(adapter.active[0].progress_metric.current, 10)


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


class ArtifactApiTests(unittest.TestCase):

    def test_artifact_publisher_auth_rejects_non_object_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publishers.json"
            path.write_text("[]\n", encoding="utf-8")
            path.chmod(0o600)

            with self.assertRaisesRegex(ValueError, "wrong schema"):
                ArtifactPublisherAuth.from_file(path)

    def test_artifact_publisher_auth_rejects_shared_token_hashes(self) -> None:
        digest = "a" * 64
        payload = {
            "schema_version": 1,
            "publishers": [
                {"agent_slug": "agents/tammy", "token_sha256": digest},
                {"agent_slug": "agents/toddy", "token_sha256": digest},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publishers.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

            with self.assertRaisesRegex(ValueError, "invalid"):
                ArtifactPublisherAuth.from_file(path)

    def test_explicit_missing_publisher_credentials_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"

            with self.assertRaisesRegex(ValueError, "unavailable"):
                load_artifact_publisher_auth(missing)
    @staticmethod
    def _execution_headers(agent_slug: str = "agents/toddy") -> dict[str, str]:
        token_key = agent_slug.rsplit("/", 1)[-1]
        return {
            "Authorization": f"Bearer {token_key}-test-publisher-token"
        }

    def _publish_payload(self) -> dict:
        return {
            "title": "Family care weekly review brief",
            "artifact_kind": "markdown",
            "created_by": "agents/toddy",
            "produced_for": "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
            "markdown": "# Weekly review\n\nCanonical content.",
            "attachments": [],
            "project": None,
            "goal": None,
            "git_url": None,
            "supersedes": None,
            "idempotency_key": (
                "toddy:tasks/561640dd-8e34-43e1-a03e-e3f3f270033d:"
                "weekly-review:v1"
            ),
        }

    def test_lists_artifacts_with_strict_filters_and_pagination(self) -> None:
        adapter = FakeAdapter(artifacts=(sample_artifact(),))
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "GET",
            "/api/artifacts?agent=agents%2Ftoddy&"
            "task=tasks%2F561640dd-8e34-43e1-a03e-e3f3f270033d&"
            "project=projects%2F65c2f720-fb49-5403-9a9e-76228e285277&"
            "goal=goals%2F41fb50e0-e1d7-592b-b2c3-ff1f7aacff10&"
            "kind=markdown&limit=25&cursor=0",
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            [artifact["slug"] for artifact in payload["artifacts"]],
            [sample_artifact().slug],
        )
        self.assertEqual(
            adapter.artifact_reads,
            [{
                "agent": "agents/toddy",
                "task": "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
                "project": "projects/65c2f720-fb49-5403-9a9e-76228e285277",
                "goal": "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10",
                "kind": "markdown",
                "cursor": 0,
                "limit": 25,
            }],
        )

    def test_rejects_repeated_unknown_and_out_of_range_artifact_filters(self) -> None:
        harness = ServerHarness(self, FakeAdapter())
        for path in (
            "/api/artifacts?agent=agents%2Ftoddy&agent=agents%2Ftimmy",
            "/api/artifacts?unknown=value",
            "/api/artifacts?cursor=-1",
            "/api/artifacts?limit=51",
            "/api/artifacts?task=tasks%2Ftitle-derived",
            "/api/artifacts?project=projects%2Ftitle-derived",
            "/api/artifacts?goal=goals%2Ftitle-derived",
            "/api/artifacts?task=tasks%2F6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "/api/artifacts?project=projects%2F3d813cbb-47fb-32ba-91df-831e1593ac29",
            "/api/artifacts?goal=goals%2F6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        ):
            status, payload, _ = harness.request("GET", path)
            self.assertEqual(status, 400, path)
            self.assertEqual(payload["code"], "invalid_artifact_filters")

    def test_gets_one_artifact_by_encoded_canonical_slug(self) -> None:
        artifact = sample_artifact()
        harness = ServerHarness(self, FakeAdapter(artifacts=(artifact,)))

        status, payload, _ = harness.request(
            "GET", f"/api/artifacts/{artifact.slug.replace('/', '%2F')}"
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["artifact"], artifact.to_dict())
        self.assertEqual(payload["readback"], {"verified": True})

    def test_detail_get_accepts_exact_gbrain_normalized_artifact_shape(self) -> None:
        artifact = sample_artifact()
        links = [
            {"to": artifact.agent_collection, "type": "member_of"},
            {"to": artifact.created_by, "type": "created_by"},
            {"to": artifact.produced_for, "type": "produced_for"},
            {"to": artifact.project, "type": "supports_project"},
            {"to": artifact.goal, "type": "supports_goal"},
        ]
        page = {
            "slug": artifact.slug,
            "type": "artifact",
            "title": artifact.title,
            "frontmatter": {
                "artifact_kind": artifact.artifact_kind,
                "created_by": artifact.created_by,
                "produced_for": artifact.produced_for,
                "created_at": artifact.created_at.isoformat(),
                "attachments": [],
                "git_url": None,
                "links": links,
            },
            "compiled_truth": artifact.markdown,
        }
        edges = [
            {"from_slug": artifact.slug, "to_slug": link["to"], "link_type": link["type"]}
            for link in links
        ]

        class NormalizedArtifactRunner:
            def run(self, tool, params):
                if tool == "get_page":
                    return page
                if tool == "get_links":
                    return edges
                raise AssertionError(f"unexpected tool: {tool}")

        harness = ServerHarness(self, gbrain.GBrainAdapter(NormalizedArtifactRunner()))

        status, payload, _ = harness.request(
            "GET", f"/api/artifacts/{artifact.slug.replace('/', '%2F')}"
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["artifact"], artifact.to_dict())
        self.assertEqual(payload["readback"], {"verified": True})

    def test_detail_get_rejects_normalized_frontmatter_type_conflict(self) -> None:
        artifact = sample_artifact()
        page = {
            "slug": artifact.slug,
            "type": "artifact",
            "title": artifact.title,
            "frontmatter": {"type": "task"},
            "compiled_truth": artifact.markdown,
        }

        class ConflictingArtifactRunner:
            def run(self, tool, params):
                if tool == "get_page":
                    return page
                if tool == "get_links":
                    return []
                raise AssertionError(f"unexpected tool: {tool}")

        harness = ServerHarness(self, gbrain.GBrainAdapter(ConflictingArtifactRunner()))

        status, payload, _ = harness.request(
            "GET", f"/api/artifacts/{artifact.slug.replace('/', '%2F')}"
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_artifact")

    def test_detail_get_rejects_non_opaque_artifact_slug_before_adapter_read(self) -> None:
        class TrackingAdapter(FakeAdapter):
            def __init__(self):
                super().__init__()
                self.detail_reads = []

            def get_agent_artifact(self, slug):
                self.detail_reads.append(slug)
                return super().get_agent_artifact(slug)

        adapter = TrackingAdapter()
        harness = ServerHarness(self, adapter)

        status, payload, _ = harness.request(
            "GET", "/api/artifacts/artifacts%2Ftitle-derived"
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_artifact")
        self.assertEqual(adapter.detail_reads, [])

    def test_detail_get_maps_page_not_found_to_404(self) -> None:
        class MissingAdapter(FakeAdapter):
            def get_agent_artifact(self, slug):
                raise gbrain.GBrainCommandError(self.message)

        slug = "artifacts/72a4d170-978f-4a37-bd92-b9d3bdde9339"
        for message in (
            "page_not_found",
            "PAGE_NOT_FOUND",
            "Page not found",
            "GBrain tool get_page failed: Page Not Found",
        ):
            with self.subTest(message=message):
                adapter = MissingAdapter()
                adapter.message = message
                harness = ServerHarness(self, adapter)
                status, payload, _ = harness.request(
                    "GET", f"/api/artifacts/{slug.replace('/', '%2F')}"
                )
                self.assertEqual(status, 404)
                self.assertEqual(payload["code"], "artifact_not_found")

    def test_detail_get_keeps_malformed_separate_from_dependency_failure(self) -> None:
        class FailingAdapter(FakeAdapter):
            def get_agent_artifact(self, slug):
                if self.failure == "malformed":
                    raise DomainValidationError("malformed Artifact page")
                raise gbrain.GBrainCommandError("dependency unavailable")

        slug = "artifacts/72a4d170-978f-4a37-bd92-b9d3bdde9339"
        for failure, expected_status, expected_code in (
            ("malformed", 422, "invalid_artifact"),
            ("dependency", 503, "gbrain_unavailable"),
        ):
            with self.subTest(failure=failure):
                adapter = FailingAdapter()
                adapter.failure = failure
                harness = ServerHarness(self, adapter)
                status, payload, _ = harness.request(
                    "GET", f"/api/artifacts/{slug.replace('/', '%2F')}"
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(payload["code"], expected_code)

    def test_publishes_only_after_verified_readback_and_reuses_idempotency(self) -> None:
        adapter = FakeAdapter()
        harness = ServerHarness(self, adapter)
        body = self._publish_payload()

        first_status, first, _ = harness.request(
            "POST", "/api/artifacts", body, self._execution_headers()
        )
        second_status, second, _ = harness.request(
            "POST", "/api/artifacts", body, self._execution_headers()
        )

        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 200)
        self.assertTrue(first["receipt"]["verified"])
        self.assertFalse(first["receipt"]["idempotent"])
        self.assertTrue(second["receipt"]["idempotent"])
        self.assertEqual(second["artifact"]["slug"], first["artifact"]["slug"])
        self.assertEqual(len(adapter.created_artifacts), 1)

    def test_publish_requires_matching_execution_identity_header(self) -> None:
        harness = ServerHarness(self, FakeAdapter())
        body = self._publish_payload()

        missing_status, missing, _ = harness.request(
            "POST", "/api/artifacts", body
        )
        mismatch_status, mismatch, _ = harness.request(
            "POST",
            "/api/artifacts",
            body,
            self._execution_headers("agents/timmy"),
        )

        self.assertEqual(missing_status, 403)
        self.assertEqual(mismatch_status, 403)
        self.assertEqual(missing["code"], "artifact_identity_mismatch")
        self.assertEqual(mismatch["code"], "artifact_identity_mismatch")
        self.assertEqual(missing["error"], mismatch["error"])
        self.assertNotIn("Toddy", missing["error"])
        self.assertNotIn("Timmy", missing["error"])

        accepted_status, accepted, _ = harness.request(
            "POST",
            "/api/artifacts",
            body,
            self._execution_headers("agents/toddy"),
        )
        self.assertEqual(accepted_status, 201)
        self.assertTrue(accepted["receipt"]["verified"])

    def test_rejects_unknown_publish_keys_and_malformed_relationships(self) -> None:
        harness = ServerHarness(self, FakeAdapter())
        unknown = {**self._publish_payload(), "status": "completed"}
        malformed = {
            **self._publish_payload(),
            "created_by": "agents/unknown",
        }
        publisher_verified = {**self._publish_payload(), "verified": True}

        status, payload, _ = harness.request(
            "POST", "/api/artifacts", unknown, self._execution_headers()
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_artifact")
        status, payload, _ = harness.request(
            "POST", "/api/artifacts", malformed, self._execution_headers()
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "artifact_identity_mismatch")
        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            publisher_verified,
            self._execution_headers(),
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_artifact")

    def test_maps_idempotency_conflict_partial_write_and_gbrain_failure(self) -> None:
        class FailingAdapter(FakeAdapter):
            def create_agent_artifact(
                self, artifact, *, executing_agent, idempotency_key
            ):
                if self.failure == "partial":
                    raise PartialMutationError(artifact.slug, "artifact link failed")
                if self.failure == "gbrain":
                    raise gbrain.GBrainError("GBrain unavailable")
                raise gbrain.ArtifactIdempotencyConflict("key has different content")

        for failure, expected in (
            ("conflict", 409),
            ("partial", 502),
            ("gbrain", 503),
        ):
            adapter = FailingAdapter()
            adapter.failure = failure
            harness = ServerHarness(self, adapter)
            status, payload, _ = harness.request(
                "POST",
                "/api/artifacts",
                self._publish_payload(),
                self._execution_headers(),
            )
            self.assertEqual(status, expected)
            if failure == "partial":
                self.assertTrue(payload["slug"].startswith("artifacts/"))

    def test_real_adapter_prewrite_outage_maps_to_503_without_artifact_write(self) -> None:
        calls = []

        class OfflineRunner:
            def run(self, tool, params):
                calls.append((tool, dict(params)))
                raise gbrain.GBrainCommandError("GBrain offline before publication")

        harness = ServerHarness(self, gbrain.GBrainAdapter(OfflineRunner()))
        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            self._publish_payload(),
            self._execution_headers(),
        )

        self.assertEqual(status, 503)
        self.assertEqual(payload["code"], "gbrain_unavailable")
        self.assertFalse(
            any(
                tool == "put_page"
                and str(params.get("slug", "")).startswith("artifacts/")
                for tool, params in calls
            )
        )


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


class ProjectBrowserFixtureTests(unittest.TestCase):
    def test_fixture_serves_only_synthetic_task_agent_work_and_paginated_handoffs(self) -> None:
        from tests.project_browser_fixture import build_fixture_server

        with tempfile.TemporaryDirectory() as temporary:
            external_preferences = Path(temporary) / "external-calendar-preferences.json"
            external_preferences.write_text(
                json.dumps({"selected_calendar_ids": ["real-calendar"]}),
                encoding="utf-8",
            )
            runtime = Path(temporary) / "fixture-runtime"
            with patch.dict(
                os.environ,
                {"MISSION_CONTROL_CALENDAR_PREFERENCES": str(external_preferences)},
            ):
                server, adapter = build_fixture_server(runtime, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

            def read(path: str) -> tuple[int, dict]:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=3
                )
                connection.request("GET", path)
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                connection.close()
                return response.status, payload

            task_status, tasks = read("/api/tasks")
            work_status, agent_work = read("/api/agent-work")
            release_status, releases = read("/api/releases")
            calendar_status, calendars = read("/api/ical-calendars")
            event_status, events = read("/api/handoff-events?limit=50&after_sequence=0")
            all_event_status, all_events = read(
                "/api/handoff-events?limit=200&after_sequence=0"
            )
            artifact_status, artifacts = read(
                f"/api/artifacts?task={adapter.task.slug}&limit=10&cursor=0"
            )

            self.assertEqual(task_status, 200)
            self.assertEqual(work_status, 200)
            self.assertEqual(release_status, 200)
            self.assertIn("current_version", releases)
            self.assertEqual(calendar_status, 200)
            self.assertEqual(calendars["calendars"], [])
            self.assertEqual(calendars["selected_calendar_ids"], [])
            self.assertEqual(adapter.calendar_reader.calendar_reads, 1)
            self.assertEqual(
                adapter.calendar_preferences_path,
                runtime / "calendar-preferences.json",
            )
            self.assertNotEqual(
                adapter.calendar_preferences_path,
                external_preferences,
            )
            self.assertEqual(event_status, 200)
            self.assertEqual(all_event_status, 200)
            self.assertEqual(artifact_status, 200)
            self.assertEqual(artifacts["artifacts"], [])
            self.assertEqual([task["slug"] for task in tasks["tasks"]], [adapter.task.slug])
            self.assertEqual(
                [task["slug"] for task in agent_work["tasks"]],
                [adapter.task.slug],
            )
            self.assertGreater(events["total"], 50)
            self.assertEqual(len(events["events"]), 50)
            self.assertIsNotNone(events["next_sequence"])
            self.assertEqual(
                adapter.read_cache_path,
                runtime / "read-snapshots.json",
            )
            self.assertTrue(adapter.read_cache_path.exists())
            self.assertEqual(
                {event["task_slug"] for event in events["events"]},
                {adapter.task.slug},
            )
            self.assertTrue(
                {
                    "handoff_queued",
                    "handoff_suppressed",
                    "handoff_leased",
                    "acknowledgement",
                    "capability_rotated",
                    "lease_expired",
                    "delivery_retry",
                    "delivery_terminal",
                    "correction",
                }.issubset({event["event_type"] for event in all_events["events"]})
            )
            self.assertTrue(
                {
                    "queued",
                    "suppressed",
                    "leased",
                    "received",
                    "actively_executing",
                    "still_blocked",
                    "completed",
                    "retrying",
                    "dead_letter",
                }.issubset({event["status"] for event in all_events["events"]})
            )
            self.assertGreater(
                max(len(event["summary"]) for event in all_events["events"]),
                120,
            )
            self.assertIn(
                "correlation-redacted-display",
                {event["correlation_id"] for event in all_events["events"]},
            )


class SystemTicketApiTests(unittest.TestCase):
    def test_cold_system_ticket_read_is_bounded_and_then_serves_labeled_last_verified_data(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class SlowSystemTicketAdapter(FakeAdapter):
            read_count = 0

            def list_system_tickets(self) -> SystemTicketRead:
                self.read_count += 1
                entered.set()
                release.wait(timeout=2)
                return super().list_system_tickets()

        ticket = SystemTicket(
            slug="tasks/system-tickets/cache-read-a1b2c3",
            title="Use the verified System Ticket snapshot",
            status="planned",
            verbatim_request="Keep the System Tickets surface responsive.",
            target_subsystem="mission_control",
            priority="high",
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = ReadSurfaceCache(
                ReadSnapshotStore(Path(temporary) / "reads.json"),
                background=True,
            )
            adapter = SlowSystemTicketAdapter(system_tickets=(ticket,))
            harness = ServerHarness(
                self,
                adapter,
                read_cache=cache,
            )

            cold_status, cold_payload, _ = harness.request(
                "GET", "/api/system-tickets?include_completed=0"
            )

            self.assertEqual(cold_status, 202)
            self.assertEqual(cold_payload["read_state"]["surface"], "system_tickets")
            self.assertEqual(cold_payload["read_state"]["status"], "loading")
            self.assertTrue(entered.wait(timeout=1))

            release.set()
            self.assertTrue(cache.wait_for_idle("system_tickets"))
            warm_status, warm_payload, _ = harness.request(
                "GET", "/api/system-tickets?include_completed=0"
            )

            self.assertEqual(warm_status, 200)
            self.assertEqual(
                [item["slug"] for item in warm_payload["tickets"]],
                [ticket.slug],
            )
            self.assertEqual(warm_payload["read_state"]["surface"], "system_tickets")
            self.assertEqual(warm_payload["read_state"]["status"], "fresh")
            second_status, second_payload, _ = harness.request(
                "GET", "/api/system-tickets?include_completed=0"
            )
            self.assertEqual(second_status, 200)
            self.assertEqual(second_payload["read_state"]["status"], "fresh")
            self.assertEqual(adapter.read_count, 1)

    def test_verified_system_ticket_mutation_invalidates_only_its_cached_surface(self) -> None:
        class CountingAdapter(FakeAdapter):
            read_count = 0

            def list_system_tickets(self) -> SystemTicketRead:
                self.read_count += 1
                return super().list_system_tickets()

        adapter = CountingAdapter()
        harness = ServerHarness(self, adapter)

        first_status, first_payload, _ = harness.request(
            "GET", "/api/system-tickets?include_completed=0"
        )
        created_status, created_payload, _ = harness.request(
            "POST",
            "/api/system-tickets",
            {
                "title": "Invalidate the verified System Ticket read",
                "verbatim_request": "Refresh only after the mutation is verified.",
                "target_subsystem": "mission_control",
                "priority": "normal",
                "acceptance_criteria": "The new ticket is present after readback.",
            },
        )
        second_status, second_payload, _ = harness.request(
            "GET", "/api/system-tickets?include_completed=0"
        )

        self.assertEqual((first_status, created_status, second_status), (200, 201, 200))
        self.assertEqual(first_payload["tickets"], [])
        self.assertTrue(created_payload["receipt"]["verified"])
        self.assertEqual(
            [ticket["slug"] for ticket in second_payload["tickets"]],
            [created_payload["ticket"]["slug"]],
        )
        self.assertEqual(adapter.read_count, 2)

    def test_six_of_twenty_noncompleted_tickets_use_zero_additional_remote_projection_reads_when_warm(self) -> None:
        class InstrumentedAdapter(FakeAdapter):
            projection_reads = 0

            def list_system_tickets(self) -> SystemTicketRead:
                self.projection_reads += 1
                return super().list_system_tickets()

        open_tickets = tuple(
            SystemTicket(
                slug=f"tasks/open-{index}",
                title=f"Open ticket {index}",
                status="active" if index == 0 else "planned",
                verbatim_request=f"Keep open ticket {index} visible.",
                target_subsystem="mission_control",
                priority="normal",
            )
            for index in range(6)
        )
        completed = tuple(
            SystemTicket(
                slug=f"tasks/completed-{index}",
                title=f"Completed ticket {index}",
                status="completed",
                verbatim_request=f"Keep completed ticket {index} canonical.",
                target_subsystem="mission_control",
                priority="normal",
            )
            for index in range(14)
        )
        adapter = InstrumentedAdapter(system_tickets=(*open_tickets, *completed))
        harness = ServerHarness(self, adapter)

        first_status, first_payload, _ = harness.request(
            "GET", "/api/system-tickets?include_completed=0"
        )
        reads_after_first = adapter.projection_reads
        second_status, second_payload, _ = harness.request(
            "GET", "/api/system-tickets?include_completed=0"
        )

        expected = [ticket.slug for ticket in open_tickets]
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(first_payload["issues"], [])
        self.assertEqual(second_payload["issues"], [])
        self.assertCountEqual(
            [ticket["slug"] for ticket in first_payload["tickets"]],
            expected,
        )
        self.assertCountEqual(
            [ticket["slug"] for ticket in second_payload["tickets"]],
            expected,
        )
        self.assertEqual(reads_after_first, 1)
        self.assertEqual(adapter.projection_reads, reads_after_first)

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

    def test_completed_tickets_are_excluded_by_default_and_paginated_on_request(self) -> None:
        active = SystemTicket(
            slug="tasks/system-tickets/active",
            title="Active ticket",
            status="active",
            verbatim_request="Keep this visible by default.",
            target_subsystem="mission_control",
            priority="normal",
        )
        completed = tuple(
            SystemTicket(
                slug=f"tasks/system-tickets/completed-{index}",
                title=f"Completed ticket {index}",
                status="completed",
                verbatim_request="Reveal this only on request.",
                target_subsystem="mission_control",
                priority="normal",
            )
            for index in range(7)
        )
        harness = ServerHarness(
            self,
            FakeAdapter(system_tickets=(active, *completed)),
        )

        status, default_payload, _ = harness.request(
            "GET", "/api/system-tickets?include_completed=0"
        )
        first_status, first_page, _ = harness.request(
            "GET", "/api/system-tickets?completed_only=1&offset=0&limit=5"
        )
        second_status, second_page, _ = harness.request(
            "GET", "/api/system-tickets?completed_only=1&offset=5&limit=5"
        )

        self.assertEqual((status, first_status, second_status), (200, 200, 200))
        self.assertEqual(
            [ticket["slug"] for ticket in default_payload["tickets"]],
            [active.slug],
        )
        self.assertEqual(len(first_page["tickets"]), 5)
        self.assertTrue(first_page["pagination"]["has_more"])
        self.assertEqual(len(second_page["tickets"]), 2)
        self.assertFalse(second_page["pagination"]["has_more"])

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
