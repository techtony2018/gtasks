from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from gtasks.domain import ACTIVE_ROOT, COMPLETED_ROOT, Project, new_inbox_task
from gtasks.gbrain import (
    CollectionIssue,
    CollectionRead,
    GoalRead,
    MutationReceipt,
    ProjectAssignmentReceipt,
    ProjectMutationReceipt,
    ProjectRead,
    StatusMutationReceipt,
)
from gtasks.server import build_server


class IsolatedProjectAdapter:
    def __init__(self) -> None:
        self.task = new_inbox_task(
            "Isolated project task",
            datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
            "fixture1",
        )
        self.projects: tuple[Project, ...] = ()

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        tasks = (self.task,) if root_slug == ACTIVE_ROOT else ()
        return CollectionRead(root_slug=root_slug, tasks=tasks)

    def list_goals(self) -> GoalRead:
        return GoalRead(goals=())

    def list_projects(self) -> ProjectRead:
        return ProjectRead(
            projects=self.projects,
            issues=(
                CollectionIssue(
                    slug="projects/malformed-fixture",
                    message="projects/malformed-fixture is not a project page",
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
    server = build_server(
        host="127.0.0.1",
        port=4182,
        adapter=IsolatedProjectAdapter(),
        identity_factory=lambda: "fixture2",
        clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
    )
    server.serve_forever()
