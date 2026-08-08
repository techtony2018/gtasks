from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from gtasks.domain import (
    ACTIVE_ROOT,
    COMPLETED_ROOT,
    AgentProfile,
    Project,
    new_inbox_task,
)
from gtasks.gbrain import (
    AgentRead,
    AgentWorkRead,
    ArtifactRead,
    CollectionIssue,
    CollectionRead,
    GoalDeletionReceipt,
    GoalMutationReceipt,
    GoalRead,
    GoalRelationshipRead,
    MutationReceipt,
    ProjectAssignmentReceipt,
    ProjectMutationReceipt,
    ProjectRead,
    ProposalRead,
    StatusMutationReceipt,
    SystemTicketRead,
    TodoRead,
)
from gtasks.server import build_server
from gtasks.handoff_dispatcher import (
    ActionableChange,
    AgentRegistration,
    DurableHandoffStore,
    HandoffDispatcher,
)
from gtasks.ical import CalendarPreferences
from gtasks.operational_logs import OperationalLogReader, OperationalLogStore
from gtasks.read_cache import ReadSnapshotStore, ReadSurfaceCache
from gtasks.warnings import WarningDismissalStore


class SyntheticCalendarReader:
    def __init__(self) -> None:
        self.calendar_reads = 0

    def status(self) -> dict:
        return {"status": "not_determined"}

    def calendars(self) -> dict:
        self.calendar_reads += 1
        return {"status": "not_determined", "calendars": []}

    def read(self, start, end, *, calendar_ids=()) -> dict:
        return {"status": "not_determined", "events": []}

    def request_full_access(self) -> dict:
        return {"status": "not_determined"}


class IsolatedProjectAdapter:
    def __init__(self, *, read_cache_path: Path) -> None:
        self.read_cache_path = read_cache_path
        self.task = replace(new_inbox_task(
            "Isolated project task",
            datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
            "fixture1",
        ), owner_agent="agents/tammy")
        self.projects: tuple[Project, ...] = ()
        self.goals = ()
        self.agents = (
            AgentProfile(
                slug="agents/tammy",
                name="Tammy",
                title="Agent Tammy",
                summary="Synthetic browser QA agent.",
                work_root="collections/tammys-tasks",
                default_goal_slugs=(),
                avatar_value="TA",
            ),
        )

    def get_tony_profile(self) -> dict:
        return {
            "slug": "people/tony-guan",
            "name": "Tony",
            "avatar": {"kind": "initials", "value": "TG"},
        }

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        tasks = (self.task,) if root_slug == ACTIVE_ROOT else ()
        return CollectionRead(root_slug=root_slug, tasks=tasks)

    def list_goals(self) -> GoalRead:
        return GoalRead(goals=self.goals)

    def list_agent_profiles(self) -> AgentRead:
        return AgentRead(agents=self.agents)

    def list_agent_work(self) -> AgentWorkRead:
        return AgentWorkRead(
            tasks=({**self.task.to_dict(), "open_todos": 0},),
            roots=(self.agents[0].work_root,),
        )

    def list_task_todos(
        self,
        task_slug: str,
        *,
        status: str | None,
        cursor: int,
        limit: int,
    ) -> TodoRead:
        if task_slug != self.task.slug:
            raise ValueError("unknown isolated task")
        return TodoRead(todos=())

    def list_agent_artifacts(self, **_filters) -> ArtifactRead:
        return ArtifactRead(artifacts=())

    def list_proposals(self) -> ProposalRead:
        return ProposalRead(proposals=())

    def list_system_tickets(self, *, include_completed: bool = False) -> SystemTicketRead:
        return SystemTicketRead(tickets=())

    def create_goal(self, goal) -> GoalMutationReceipt:
        self.goals = (*self.goals, goal)
        return GoalMutationReceipt(goal_slug=goal.slug, goal=goal, verified=True)

    def set_goal_paused(self, goal_slug: str) -> GoalMutationReceipt:
        goal = next(goal for goal in self.goals if goal.slug == goal_slug)
        paused = replace(goal, status="paused")
        self.goals = tuple(
            paused if candidate.slug == goal_slug else candidate
            for candidate in self.goals
        )
        return GoalMutationReceipt(goal_slug=goal_slug, goal=paused, verified=True)

    def delete_goal(self, goal_slug: str) -> GoalDeletionReceipt:
        self.goals = tuple(goal for goal in self.goals if goal.slug != goal_slug)
        return GoalDeletionReceipt(
            goal_slug=goal_slug,
            removed_task_links=(),
            recoverable_until_hours=72,
            verified=True,
        )

    def read_goal_relationships(self, goal_slug: str) -> GoalRelationshipRead:
        return GoalRelationshipRead(goal_slug=goal_slug, task_slugs=())

    def list_projects(self) -> ProjectRead:
        return ProjectRead(
            projects=self.projects,
            issues=(
                CollectionIssue(
                    slug="projects/malformed-fixture",
                    message="projects/malformed-fixture is not a project page",
                    category="project_data",
                    impact=(
                        "This scoped project is not counted or offered for task "
                        "assignment until its core project data is repaired."
                    ),
                ),
            ),
        )

    def create_project(self, project: Project) -> ProjectMutationReceipt:
        self.projects = (*self.projects, project)
        return ProjectMutationReceipt(project_slug=project.slug, verified=True)

    def set_task_project(
        self,
        task_slug: str,
        project_slug: str | None,
    ) -> ProjectAssignmentReceipt:
        if task_slug != self.task.slug:
            raise ValueError("unknown isolated task")
        if project_slug and project_slug not in {
            project.slug for project in self.projects
        }:
            raise ValueError("unknown isolated project")
        self.task = replace(self.task, project=project_slug)
        return ProjectAssignmentReceipt(
            task_slug=task_slug,
            project_slug=project_slug,
            verified=True,
        )

    def create_inbox(self, task) -> MutationReceipt:
        self.task = task
        return MutationReceipt(slug=task.slug, verified=True)

    def set_task_status(
        self,
        task_slug: str,
        status: str,
        now: datetime,
    ) -> StatusMutationReceipt:
        if task_slug != self.task.slug:
            raise ValueError("unknown isolated task")
        completed_at = now if status == "completed" else None
        lifecycle_root = COMPLETED_ROOT if status == "completed" else ACTIVE_ROOT
        self.task = replace(
            self.task,
            status=status,
            completed_at=completed_at,
            lifecycle_root=lifecycle_root,
        )
        return StatusMutationReceipt(
            task_slug=task_slug,
            status=status,
            lifecycle_root=lifecycle_root,
            completed_at=completed_at,
            task=self.task,
            verified=True,
        )


def _seed_handoff_events(
    store: DurableHandoffStore,
    adapter: IsolatedProjectAdapter,
    *,
    now: datetime,
) -> None:
    registration = AgentRegistration(
        registration_id="fixture-registration",
        agent_slug="agents/tammy",
        route="hosts/tammy",
        verified=True,
    )
    dispatcher = HandoffDispatcher(
        store,
        registrations=(registration,),
        delegations=(),
    )
    long_summary = (
        "This intentionally long privacy safe summary verifies narrow viewport wrapping "
        "without exposing private payloads and remains readable across the event card."
    )
    def record_actionable(sequence: int, *, summary: str | None = None):
        return dispatcher.record(
            ActionableChange(
                task_slug=adapter.task.slug,
                canonical_event_id=f"events/fixture-{sequence:03d}",
                canonical_version=f"v{sequence:03d}",
                trigger=("task_activated", "todo_added", "answer_received")[sequence % 3],
                assigned_to=("agents/tammy",),
                route="hosts/tammy",
                summary=summary or f"Synthetic queued handoff event {sequence}.",
                occurred_at=now,
                correlation_id=(
                    "correlation-redacted-display"
                    if sequence == 1
                    else "correlation-fixture-task"
                ),
            ),
            now=now,
        )

    completed_record = record_actionable(1, summary=long_summary)
    completed = store.claim(registration.registration_id, now=now, lease_seconds=30)
    if completed is not None:
        for index, (status, detail) in enumerate(
            (
                ("received", None),
                ("actively_executing", None),
                ("still_blocked", "Waiting for the synthetic QA dependency."),
                ("actively_executing", None),
                ("completed", None),
            ),
            start=1,
        ):
            store.acknowledge(
                completed_record.handoff_id,
                status,
                registration_id=registration.registration_id,
                lease_token=completed.lease_token,
                lease_generation=completed.lease_generation,
                mutation_id=f"mutations/fixture-ack-{index}",
                detail=detail,
                now=now,
            )

    rotated_record = record_actionable(2)
    rotated = store.claim(registration.registration_id, now=now, lease_seconds=30)
    if rotated is not None:
        recovered = store.recover_in_progress(
            rotated_record.handoff_id,
            registration=registration,
            expected_generation=rotated.lease_generation,
            now=now,
        )
        store.acknowledge(
            rotated_record.handoff_id,
            "completed",
            registration_id=registration.registration_id,
            lease_token=recovered.lease_token,
            lease_generation=recovered.lease_generation,
            mutation_id="mutations/fixture-rotated-completed",
            now=now,
        )

    retry_record = record_actionable(3)
    expiring = store.claim(registration.registration_id, now=now, lease_seconds=1)
    if expiring is not None:
        store.reconcile_expired_leases(now=now + timedelta(seconds=2))
        retrying = store.claim(
            registration.registration_id,
            now=now + timedelta(seconds=2),
            lease_seconds=30,
        )
        store.record_failure(
            retry_record.handoff_id,
            registration_id=registration.registration_id,
            lease_token=retrying.lease_token,
            lease_generation=retrying.lease_generation,
            mutation_id="mutations/fixture-retrying",
            retryable=True,
            summary="Synthetic delivery will retry after a safe delay.",
            now=now + timedelta(seconds=2),
        )
        terminal = store.claim(
            registration.registration_id,
            now=now + timedelta(seconds=2),
            lease_seconds=30,
        )
        store.record_failure(
            retry_record.handoff_id,
            registration_id=registration.registration_id,
            lease_token=terminal.lease_token,
            lease_generation=terminal.lease_generation,
            mutation_id="mutations/fixture-terminal",
            retryable=False,
            summary="Synthetic delivery reached the final safe retry.",
            now=now + timedelta(seconds=2),
        )

    for sequence in range(4, 9):
        record = record_actionable(sequence)
        claim = store.claim(
            registration.registration_id,
            now=now + timedelta(seconds=3),
            lease_seconds=30,
        )
        store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=registration.registration_id,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id=f"mutations/fixture-bulk-complete-{sequence}",
            now=now + timedelta(seconds=3),
        )

    for offset, trigger in enumerate(
        ("presentation_only", "duplicate_save", "stale_cache_refresh", "stable_blocker"),
        start=9,
    ):
        dispatcher.record(
            ActionableChange(
                task_slug=adapter.task.slug,
                canonical_event_id=f"events/fixture-{offset:03d}",
                canonical_version=f"v{offset:03d}",
                trigger=trigger,
                assigned_to=("agents/tammy",),
                route="hosts/tammy",
                summary=f"Synthetic suppressed handoff event for {trigger.replace('_', ' ')}.",
                occurred_at=now,
                correlation_id="correlation-fixture-task",
            ),
            now=now,
        )

    first_event = store.query_events(
        limit=1,
        after_sequence=0,
        task_slug=adapter.task.slug,
    ).events[0]
    store.append_correction(
        first_event.handoff_id,
        supersedes_event_id=first_event.event_id,
        summary="Correction preserves the immutable original event for audit.",
        now=now + timedelta(seconds=3),
    )


def build_fixture_server(runtime_directory: Path, *, port: int = 4182):
    runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fixture_now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    read_cache_path = runtime_directory / "read-snapshots.json"
    calendar_preferences_path = runtime_directory / "calendar-preferences.json"
    adapter = IsolatedProjectAdapter(read_cache_path=read_cache_path)
    adapter.calendar_preferences_path = calendar_preferences_path
    adapter.calendar_reader = SyntheticCalendarReader()
    handoff_store = DurableHandoffStore(
        str(runtime_directory / "handoff-events.sqlite3"),
        retention_days=30,
    )
    _seed_handoff_events(handoff_store, adapter, now=fixture_now)
    server = build_server(
        host="127.0.0.1",
        port=port,
        adapter=adapter,
        identity_factory=lambda: "fixture2",
        clock=lambda: fixture_now,
        warning_store=WarningDismissalStore(
            runtime_directory / "warning-state.json",
            user_id="fixture-user",
        ),
        log_reader=OperationalLogReader(
            gtasks_store=OperationalLogStore(
                runtime_directory / "operational-events.jsonl"
            ),
            queue_path=runtime_directory / "reader-observability.json",
            queue_health=lambda: {
                "status": "unavailable",
                "broker_connected": False,
                "message": (
                    "Event Queue Reader status is unavailable. "
                    "GTasks remains available."
                ),
            },
        ),
        read_cache=ReadSurfaceCache(
            ReadSnapshotStore(read_cache_path),
            background=False,
        ),
        ical_reader=adapter.calendar_reader,
        calendar_preferences=CalendarPreferences(calendar_preferences_path),
        handoff_store=handoff_store,
    )
    return server, adapter


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        server, _adapter = build_fixture_server(Path(directory))
        server.serve_forever()
