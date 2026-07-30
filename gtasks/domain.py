from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping


ACTIVE_ROOT = "collections/tonys-tasks"
COMPLETED_ROOT = "collections/tonys-completed-tasks"
GOALS_ROOT = "collections/tonys-goals"
LIFECYCLE_ROOTS = frozenset({ACTIVE_ROOT, COMPLETED_ROOT})

TASK_STATUSES = frozenset(
    {"planned", "active", "waiting", "blocked", "completed", "cancelled"}
)
TASK_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
TASK_RELATIONSHIPS = frozenset(
    {"member_of", "child_of", "depends_on", "blocked_by", "advances_goal"}
)
GOAL_STATUSES = frozenset({"planned", "active", "paused", "completed", "cancelled"})


class DomainValidationError(ValueError):
    """Raised when a GBrain page cannot safely be treated as a GTasks task."""


def _optional_date(value: Any, field_name: str) -> date | None:
    if value is None or value == "" or value == "none":
        return None
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be YYYY-MM-DD or none")
    try:
        # Existing GBrain task pages may carry a midnight ISO timestamp even
        # though the collection contract calls this a calendar day.
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise DomainValidationError(
            f"{field_name} must be YYYY-MM-DD or none"
        ) from exc


def _optional_datetime(value: Any, field_name: str) -> datetime | None:
    if value is None or value == "" or value == "none":
        return None
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be an ISO-8601 datetime or none")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainValidationError(
            f"{field_name} must be an ISO-8601 datetime or none"
        ) from exc


def _links_from(frontmatter: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    raw_links = frontmatter.get("links", [])
    if raw_links is None:
        return ()
    if not isinstance(raw_links, list):
        raise DomainValidationError("links must be a list")

    links: list[dict[str, str]] = []
    for raw_link in raw_links:
        if not isinstance(raw_link, Mapping):
            raise DomainValidationError("each link must be an object")
        target = raw_link.get("to")
        relation_type = raw_link.get("type")
        if not isinstance(target, str) or not target.strip():
            raise DomainValidationError("each link requires a target slug")
        if not isinstance(relation_type, str) or not relation_type.strip():
            raise DomainValidationError("each link requires a relationship type")
        links.append({"to": target.strip(), "type": relation_type.strip()})
    return tuple(links)


@dataclass(frozen=True, slots=True)
class Task:
    slug: str
    title: str
    summary: str
    detail: str
    status: str
    priority: str
    next_action: str
    due_day: date | None
    due_at: datetime | None
    scheduled_day: date | None
    inbox: bool
    lifecycle_root: str
    project: str | None = None
    parent: str | None = None
    dependencies: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    goal: str | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "Task":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            raise DomainValidationError("task slug is required")
        if page.get("type") != "task":
            raise DomainValidationError(f"{slug} is not a task page")

        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise DomainValidationError(f"{slug} has no frontmatter")

        if "detail" not in frontmatter:
            raise DomainValidationError("detail is required")
        summary = frontmatter.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise DomainValidationError("summary is required")
        summary = summary.strip()
        if len(summary) > 160:
            raise DomainValidationError("summary must be 160 characters or fewer")

        detail = frontmatter.get("detail")
        if not isinstance(detail, str):
            raise DomainValidationError("detail must be text")

        status = frontmatter.get("status")
        if status not in TASK_STATUSES:
            raise DomainValidationError(
                f"status must be one of {', '.join(sorted(TASK_STATUSES))}"
            )
        priority = frontmatter.get("priority", "normal")
        if priority not in TASK_PRIORITIES:
            raise DomainValidationError(
                f"priority must be one of {', '.join(sorted(TASK_PRIORITIES))}"
            )

        links = _links_from(frontmatter)
        lifecycle_roots = [
            link["to"]
            for link in links
            if link["type"] == "member_of" and link["to"] in LIFECYCLE_ROOTS
        ]
        if len(lifecycle_roots) != 1:
            raise DomainValidationError(
                "task must belong to exactly one GTasks lifecycle root"
            )
        lifecycle_root = lifecycle_roots[0]
        if status not in {"completed", "cancelled"} and lifecycle_root != ACTIVE_ROOT:
            raise DomainValidationError("unfinished task must belong to the active lifecycle root")

        project_links = [
            link["to"]
            for link in links
            if link["type"] == "member_of" and link["to"] not in LIFECYCLE_ROOTS
        ]
        explicit_project = frontmatter.get("project")
        if explicit_project in (None, "", "none"):
            project = project_links[0] if project_links else None
        elif isinstance(explicit_project, str):
            project = explicit_project
        else:
            raise DomainValidationError("project must be a slug or none")
        if len(set(project_links)) > 1:
            raise DomainValidationError("task can belong to only one project")

        parents = [
            link["to"] for link in links if link["type"] == "child_of"
        ]
        if len(set(parents)) > 1:
            raise DomainValidationError("task can have only one parent")
        dependencies = tuple(
            dict.fromkeys(link["to"] for link in links if link["type"] == "depends_on")
        )
        blockers = tuple(
            dict.fromkeys(link["to"] for link in links if link["type"] == "blocked_by")
        )
        self_targets = set(parents) | set(dependencies) | set(blockers)
        if slug in self_targets:
            raise DomainValidationError("task cannot relate to itself")

        goals = tuple(
            dict.fromkeys(
                str(edge["to_slug"])
                for edge in edges
                if edge.get("from_slug") == slug
                and edge.get("link_type") == "advances_goal"
                and isinstance(edge.get("to_slug"), str)
                and str(edge["to_slug"]).startswith("goals/")
            )
        )
        if len(goals) > 1:
            raise DomainValidationError("task can advance only one goal")

        inbox = frontmatter.get("inbox", False)
        if not isinstance(inbox, bool):
            raise DomainValidationError("inbox must be true or false")
        next_action = frontmatter.get("next_action", "")
        if not isinstance(next_action, str):
            raise DomainValidationError("next_action must be text")

        title = page.get("title")
        if not isinstance(title, str) or not title.strip():
            title = summary

        parsed_due_day = _optional_date(frontmatter.get("due_day"), "due_day")
        if parsed_due_day is None:
            raise DomainValidationError("due_day is required for every task")

        return cls(
            slug=slug,
            title=title.strip(),
            summary=summary,
            detail=detail,
            status=status,
            priority=priority,
            next_action=next_action.strip(),
            due_day=parsed_due_day,
            due_at=_optional_datetime(frontmatter.get("due_at"), "due_at"),
            scheduled_day=_optional_date(
                frontmatter.get("scheduled_day"), "scheduled_day"
            ),
            inbox=inbox,
            lifecycle_root=lifecycle_root,
            project=project,
            parent=parents[0] if parents else None,
            dependencies=dependencies,
            blockers=blockers,
            goal=goals[0] if goals else None,
            completed_at=_optional_datetime(
                frontmatter.get("completed_at"), "completed_at"
            ),
            created_at=_optional_datetime(frontmatter.get("created_at"), "created_at"),
            updated_at=_optional_datetime(frontmatter.get("updated_at"), "updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "summary": self.summary,
            "detail": self.detail,
            "status": self.status,
            "priority": self.priority,
            "next_action": self.next_action,
            "due_day": self.due_day.isoformat() if self.due_day else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "scheduled_day": (
                self.scheduled_day.isoformat() if self.scheduled_day else None
            ),
            "inbox": self.inbox,
            "lifecycle_root": self.lifecycle_root,
            "project": self.project,
            "parent": self.parent,
            "dependencies": list(self.dependencies),
            "blockers": list(self.blockers),
            "goal": self.goal,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class TodayGroups:
    in_progress: tuple[Task, ...]
    todays_actions: tuple[Task, ...]
    waiting_and_blocked: tuple[Task, ...]
    overdue: tuple[Task, ...]
    in_progress_overflow: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_progress": [task.to_dict() for task in self.in_progress],
            "todays_actions": [task.to_dict() for task in self.todays_actions],
            "waiting_and_blocked": [
                task.to_dict() for task in self.waiting_and_blocked
            ],
            "overdue": [task.to_dict() for task in self.overdue],
            "in_progress_overflow": self.in_progress_overflow,
        }


def group_today(tasks: Iterable[Task], today: date) -> TodayGroups:
    in_progress: list[Task] = []
    todays_actions: list[Task] = []
    waiting_and_blocked: list[Task] = []
    overdue: list[Task] = []

    for task in tasks:
        if task.status in {"completed", "cancelled"}:
            continue
        if task.status == "active":
            in_progress.append(task)
        elif task.status in {"waiting", "blocked"}:
            waiting_and_blocked.append(task)
        elif task.due_day == today or task.scheduled_day == today:
            todays_actions.append(task)
        elif task.due_day is not None and task.due_day < today:
            overdue.append(task)

    visible_in_progress = tuple(in_progress[:3])
    return TodayGroups(
        in_progress=visible_in_progress,
        todays_actions=tuple(todays_actions),
        waiting_and_blocked=tuple(waiting_and_blocked),
        overdue=tuple(overdue),
        in_progress_overflow=max(0, len(in_progress) - len(visible_in_progress)),
    )


def _slugify_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")
    return slug[:64].rstrip("-") or "task"


@dataclass(frozen=True, slots=True)
class Goal:
    slug: str
    title: str
    status: str
    outcome: str
    success_criteria: str
    target_day: date
    strategy: str
    review_cadence: str
    constraints: str

    @classmethod
    def from_page(cls, page: Mapping[str, Any]) -> "Goal":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("goals/"):
            raise DomainValidationError("goal slug must start with goals/")
        if page.get("type") != "goal":
            raise DomainValidationError(f"{slug} is not a goal page")
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise DomainValidationError(f"{slug} has no frontmatter")
        if frontmatter.get("collection") != GOALS_ROOT:
            raise DomainValidationError(
                f"goal collection must be {GOALS_ROOT}"
            )
        status = frontmatter.get("status")
        if status not in GOAL_STATUSES:
            raise DomainValidationError(
                f"goal status must be one of {', '.join(sorted(GOAL_STATUSES))}"
            )

        required_text: dict[str, str] = {}
        for field_name in (
            "outcome",
            "success_criteria",
            "strategy",
            "review_cadence",
            "constraints",
        ):
            value = frontmatter.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"goal {field_name} is required")
            required_text[field_name] = value.strip()
        target_day = _optional_date(frontmatter.get("target_day"), "target_day")
        if target_day is None:
            raise DomainValidationError("goal target_day is required")

        title = page.get("title")
        if not isinstance(title, str) or not title.strip():
            title = required_text["outcome"].rstrip(".")

        return cls(
            slug=slug,
            title=title.strip(),
            status=status,
            outcome=required_text["outcome"],
            success_criteria=required_text["success_criteria"],
            target_day=target_day,
            strategy=required_text["strategy"],
            review_cadence=required_text["review_cadence"],
            constraints=required_text["constraints"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "status": self.status,
            "outcome": self.outcome,
            "success_criteria": self.success_criteria,
            "target_day": self.target_day.isoformat(),
            "strategy": self.strategy,
            "review_cadence": self.review_cadence,
            "constraints": self.constraints,
        }


def default_goal_target_day(creation_day: date) -> date:
    quarter_end_month = ((creation_day.month - 1) // 3 + 1) * 3
    return date(
        creation_day.year,
        quarter_end_month,
        monthrange(creation_day.year, quarter_end_month)[1],
    )


def new_inbox_task(
    title: str,
    now: datetime,
    identity: str,
    due_day: date | None = None,
) -> Task:
    summary = title.strip()
    if not summary:
        raise DomainValidationError("title is required")
    if len(summary) > 160:
        raise DomainValidationError("title must be 160 characters or fewer")
    safe_identity = re.sub(r"[^a-z0-9]", "", identity.lower())[:12]
    if len(safe_identity) < 6:
        raise DomainValidationError("identity must contain at least 6 letters or numbers")

    local_day = now.date()
    slug = (
        f"tasks/{local_day.year}/"
        f"{local_day.isoformat()}-{_slugify_title(summary)}-{safe_identity}"
    )
    return Task(
        slug=slug,
        title=summary,
        summary=summary,
        detail="",
        status="planned",
        priority="normal",
        next_action="",
        due_day=due_day or local_day,
        due_at=None,
        scheduled_day=None,
        inbox=True,
        lifecycle_root=ACTIVE_ROOT,
        created_at=now,
        updated_at=now,
    )
