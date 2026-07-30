from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from gtasks.domain import ACTIVE_ROOT, COMPLETED_ROOT, Project, new_inbox_task
from gtasks.gbrain import (
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
    StatusMutationReceipt,
)
from gtasks.server import build_server
from gtasks.operational_logs import OperationalLogReader, OperationalLogStore
from gtasks.warnings import WarningDismissalStore


class IsolatedProjectAdapter:
    def __init__(self) -> None:
        self.task = new_inbox_task(
            "Isolated project task",
            datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
            "fixture1",
        )
        self.projects: tuple[Project, ...] = ()
        self.goals = ()

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        tasks = (self.task,) if root_slug == ACTIVE_ROOT else ()
        return CollectionRead(root_slug=root_slug, tasks=tasks)

    def list_goals(self) -> GoalRead:
        return GoalRead(goals=self.goals)

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


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        server = build_server(
            host="127.0.0.1",
            port=4182,
            adapter=IsolatedProjectAdapter(),
            identity_factory=lambda: "fixture2",
            clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
            warning_store=WarningDismissalStore(
                Path(directory) / "warning-state.json",
                user_id="fixture-user",
            ),
            log_reader=OperationalLogReader(
                gtasks_store=OperationalLogStore(
                    Path(directory) / "operational-events.jsonl"
                ),
                queue_path=Path(directory) / "reader-observability.json",
                queue_health=lambda: {
                    "status": "unavailable",
                    "broker_connected": False,
                    "message": (
                        "Event Queue Reader status is unavailable. "
                        "GTasks remains available."
                    ),
                },
            ),
        )
        server.serve_forever()
