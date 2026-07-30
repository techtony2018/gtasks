from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from threading import BoundedSemaphore
from typing import Any, Mapping, Protocol

from .domain import (
    ACTIVE_ROOT,
    AGENT_SCOPES,
    AGENT_WORK_ROOTS,
    AgentProfile,
    COMPLETED_ROOT,
    DomainValidationError,
    EDITABLE_TASK_STATUSES,
    EventProgress,
    GOALS_ROOT,
    Goal,
    LIFECYCLE_ROOTS,
    PROJECTS_ROOT,
    ProgressMetric,
    Project,
    Task,
)


APPROVED_ROOTS = frozenset({ACTIVE_ROOT, COMPLETED_ROOT})


class GBrainError(RuntimeError):
    """Base error for GBrain command, protocol, and verification failures."""


class GBrainCommandError(GBrainError):
    pass


class GBrainProtocolError(GBrainError):
    pass


class PartialMutationError(GBrainError):
    """A page may exist in GBrain, but the complete mutation was not verified."""

    def __init__(self, slug: str, message: str) -> None:
        self.slug = slug
        super().__init__(f"{message} Page slug: {slug}")


class CommandRunner(Protocol):
    def run(self, tool: str, params: dict[str, Any]) -> object: ...


class SubprocessCommandRunner:
    def __init__(self, executable: str = "gbrain", timeout_seconds: float = 30) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._concurrency = BoundedSemaphore(16)

    def run(self, tool: str, params: dict[str, Any]) -> object:
        payload = json.dumps(params, separators=(",", ":"))
        with self._concurrency:
            try:
                result = subprocess.run(
                    [self.executable, "call", tool, payload],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise GBrainCommandError(
                    f"GBrain executable not found: {self.executable}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise GBrainCommandError(
                    f"GBrain tool {tool} timed out after {self.timeout_seconds:g}s"
                ) from exc

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise GBrainCommandError(f"GBrain tool {tool} failed: {detail}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GBrainProtocolError(
                f"GBrain tool {tool} returned invalid JSON"
            ) from exc


@dataclass(frozen=True, slots=True)
class CollectionIssue:
    slug: str
    message: str
    severity: str = "error"
    task_visible: bool = False
    category: str = "core_data"
    impact: str = "This task could not be shown until its core data is corrected."
    repair_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "message": self.message,
            "severity": self.severity,
            "task_visible": self.task_visible,
            "category": self.category,
            "impact": self.impact,
            "repair_action": self.repair_action,
        }


@dataclass(frozen=True, slots=True)
class CollectionRead:
    root_slug: str
    tasks: tuple[Task, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_slug": self.root_slug,
            "tasks": [task.to_dict() for task in self.tasks],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class AgentRead:
    agents: tuple[AgentProfile, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents": [agent.to_dict() for agent in self.agents],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class AgentWorkRead:
    tasks: tuple[dict[str, Any], ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [dict(task) for task in self.tasks],
            "issues": [issue.to_dict() for issue in self.issues],
            "roots": [root for _agent, root in AGENT_SCOPES],
        }


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    slug: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "verified": self.verified}


@dataclass(frozen=True, slots=True)
class MembershipRepairReceipt:
    task_slug: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"task_slug": self.task_slug, "verified": self.verified}


@dataclass(frozen=True, slots=True)
class GoalRead:
    goals: tuple[Goal, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_slug": GOALS_ROOT,
            "goals": [goal.to_dict() for goal in self.goals],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class ProjectRead:
    projects: tuple[Project, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_slug": PROJECTS_ROOT,
            "projects": [project.to_dict() for project in self.projects],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class GoalRelationshipRead:
    goal_slug: str
    task_slugs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_slug": self.goal_slug,
            "task_slugs": list(self.task_slugs),
        }


@dataclass(frozen=True, slots=True)
class GoalLinkReceipt:
    task_slug: str
    goal_slug: str | None
    verified: bool
    reciprocal_verified: bool = True
    reconciled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "goal_slug": self.goal_slug,
            "verified": self.verified,
            "reciprocal_verified": self.reciprocal_verified,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True, slots=True)
class StatusMutationReceipt:
    task_slug: str
    status: str
    lifecycle_root: str
    completed_at: datetime | None
    task: Task
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "status": self.status,
            "lifecycle_root": self.lifecycle_root,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "task": self.task.to_dict(),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class NextActionMutationReceipt:
    task_slug: str
    next_action: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "next_action": self.next_action,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class TaskProgressMetricReceipt:
    task_slug: str
    task: Task
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "task": self.task.to_dict(),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class TaskProgressEventReceipt:
    task_slug: str
    task: Task
    duplicate: bool
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "task": self.task.to_dict(),
            "duplicate": self.duplicate,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class ProjectMutationReceipt:
    project_slug: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"project_slug": self.project_slug, "verified": self.verified}


@dataclass(frozen=True, slots=True)
class GoalMutationReceipt:
    goal_slug: str
    goal: Goal
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_slug": self.goal_slug,
            "goal": self.goal.to_dict(),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class GoalDeletionReceipt:
    goal_slug: str
    removed_task_links: tuple[str, ...]
    recoverable_until_hours: int
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_slug": self.goal_slug,
            "removed_task_links": list(self.removed_task_links),
            "recoverable_until_hours": self.recoverable_until_hours,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class ProjectAssignmentReceipt:
    task_slug: str
    project_slug: str | None
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "project_slug": self.project_slug,
            "verified": self.verified,
        }


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value), ensure_ascii=False)


def render_task_page(task: Task) -> str:
    links = [
        {
            "to": task.lifecycle_root,
            "type": "member_of",
            "context": "GTasks lifecycle membership.",
        }
    ]
    if task.project:
        links.append(
            {
                "to": task.project,
                "type": "member_of",
                "context": "GTasks project membership.",
            }
        )
    if task.parent:
        links.append(
            {
                "to": task.parent,
                "type": "child_of",
                "context": "GTasks parent task.",
            }
        )
    links.extend(
        {
            "to": dependency,
            "type": "depends_on",
            "context": "GTasks task dependency.",
        }
        for dependency in task.dependencies
    )
    links.extend(
        {
            "to": blocker,
            "type": "blocked_by",
            "context": "GTasks task blocker.",
        }
        for blocker in task.blockers
    )

    lines = [
        "---",
        "type: task",
        f"title: {_yaml_scalar(task.title)}",
        f"status: {_yaml_scalar(task.status)}",
        f"summary: {_yaml_scalar(task.summary)}",
        f"detail: {_yaml_scalar(task.detail)}",
        f"priority: {_yaml_scalar(task.priority)}",
        f"next_action: {_yaml_scalar(task.next_action)}",
        f"due_day: {_yaml_scalar(task.due_day.isoformat() if task.due_day else None)}",
        f"due_at: {_yaml_scalar(task.due_at.isoformat() if task.due_at else None)}",
        (
            "scheduled_day: "
            + _yaml_scalar(task.scheduled_day.isoformat() if task.scheduled_day else None)
        ),
        f"inbox: {_yaml_scalar(task.inbox)}",
        (
            "completed_at: "
            + _yaml_scalar(task.completed_at.isoformat() if task.completed_at else None)
        ),
        f"created_at: {_yaml_scalar(task.created_at.isoformat() if task.created_at else None)}",
        f"updated_at: {_yaml_scalar(task.updated_at.isoformat() if task.updated_at else None)}",
        (
            "progress_metric: "
            + json.dumps(
                task.progress_metric.to_dict() if task.progress_metric else None,
                ensure_ascii=False,
            )
        ),
        (
            "event_progress: "
            + json.dumps(
                task.event_progress.to_dict() if task.event_progress else None,
                ensure_ascii=False,
            )
        ),
        "links:",
    ]
    for link in links:
        lines.extend(
            [
                f"  - to: {_yaml_scalar(link['to'])}",
                f"    type: {_yaml_scalar(link['type'])}",
                f"    context: {_yaml_scalar(link['context'])}",
            ]
        )
    lines.extend(["---", "", f"# {task.title}", ""])
    if task.detail:
        lines.extend([task.detail, ""])
    return "\n".join(lines)


def render_project_page(project: Project) -> str:
    return "\n".join(
        [
            "---",
            "type: project",
            f"title: {_yaml_scalar(project.title)}",
            f"status: {_yaml_scalar(project.status)}",
            f"summary: {_yaml_scalar(project.summary)}",
            (
                "created_at: "
                + _yaml_scalar(
                    project.created_at.isoformat() if project.created_at else None
                )
            ),
            (
                "updated_at: "
                + _yaml_scalar(
                    project.updated_at.isoformat() if project.updated_at else None
                )
            ),
            "links:",
            f"  - to: {_yaml_scalar(PROJECTS_ROOT)}",
            "    type: member_of",
            "    context: This project is explicitly owned by GTasks.",
            "---",
            "",
            f"# {project.title}",
            "",
        ]
    )


def render_goal_page(goal: Goal) -> str:
    return "\n".join(
        [
            "---",
            "type: goal",
            f"title: {_yaml_scalar(goal.title)}",
            f"status: {_yaml_scalar(goal.status)}",
            f"outcome: {_yaml_scalar(goal.outcome)}",
            f"success_criteria: {_yaml_scalar(goal.success_criteria)}",
            f"target_day: {_yaml_scalar(goal.target_day.isoformat())}",
            f"strategy: {_yaml_scalar(goal.strategy)}",
            f"review_cadence: {_yaml_scalar(goal.review_cadence)}",
            f"constraints: {_yaml_scalar(goal.constraints)}",
            f"collection: {_yaml_scalar(GOALS_ROOT)}",
            "links:",
            f"  - to: {_yaml_scalar(GOALS_ROOT)}",
            "    type: member_of",
            "    context: This goal belongs to Tony's Goals.",
            "---",
            "",
            f"# {goal.title}",
            "",
            goal.outcome,
            "",
        ]
    )


def render_projects_collection_page() -> str:
    return "\n".join(
        [
            "---",
            "type: collection",
            "title: Tony's Projects",
            "owner: people/tony-guan",
            "status: active",
            "visibility: private",
            "required_project_fields:",
            "  - status",
            "  - summary",
            "---",
            "",
            "# Tony's Projects",
            "",
            "Canonical scope collection for projects explicitly created in GTasks.",
            "",
            "A project is visible in GTasks only when it has a typed "
            "`member_of` relationship to this collection.",
            "",
        ]
    )


def _render_preserved_page(
    page: Mapping[str, Any],
    frontmatter: Mapping[str, Any],
) -> str:
    body = page.get("compiled_truth")
    if not isinstance(body, str):
        raise GBrainProtocolError("page has no preserved body content")
    preserved = dict(frontmatter)
    title = page.get("title")
    if "title" not in preserved and isinstance(title, str) and title.strip():
        preserved["title"] = title.strip()
    lines = ["---"]
    for key, value in preserved.items():
        lines.append(
            f"{json.dumps(str(key), ensure_ascii=False)}: "
            f"{json.dumps(value, ensure_ascii=False)}"
        )
    lines.extend(["---", "", body.rstrip(), ""])
    return "\n".join(lines)


def _lifecycle_edges(
    task_slug: str,
    links: list[object],
) -> list[Mapping[str, Any]]:
    return [
        link
        for link in links
        if isinstance(link, Mapping)
        and link.get("from_slug") == task_slug
        and link.get("to_slug") in APPROVED_ROOTS
        and link.get("link_type") == "member_of"
    ]


def _visible_warning(
    slug: str,
    message: str,
    *,
    category: str,
    impact: str,
    repair_action: str | None = None,
) -> CollectionIssue:
    return CollectionIssue(
        slug=slug,
        message=message,
        severity="warning",
        task_visible=True,
        category=category,
        impact=impact,
        repair_action=repair_action,
    )


def _normalize_collection_task(
    page: Mapping[str, Any],
    edges: list[object],
    root_slug: str,
    *,
    legacy_untyped_backlink: bool,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], list[CollectionIssue]]:
    slug = page.get("slug")
    if not isinstance(slug, str):
        return page, [], []
    raw_frontmatter = page.get("frontmatter")
    if not isinstance(raw_frontmatter, Mapping):
        return page, [], []

    normalized_page = deepcopy(dict(page))
    frontmatter = deepcopy(dict(raw_frontmatter))
    normalized_page["frontmatter"] = frontmatter
    warnings: list[CollectionIssue] = []

    raw_links = frontmatter.get("links", [])
    valid_links: list[dict[str, Any]] = []
    if isinstance(raw_links, list):
        for link in raw_links:
            if (
                isinstance(link, Mapping)
                and isinstance(link.get("to"), str)
                and str(link.get("to")).strip()
                and isinstance(link.get("type"), str)
                and str(link.get("type")).strip()
            ):
                valid_links.append(deepcopy(dict(link)))
            else:
                warnings.append(
                    _visible_warning(
                        slug,
                        "One malformed optional frontmatter relationship was ignored.",
                        category="optional_relationship",
                        impact="The task is shown, but the malformed relationship is unavailable.",
                    )
                )
    elif raw_links is not None:
        warnings.append(
            _visible_warning(
                slug,
                "The optional frontmatter relationship list is invalid and was ignored.",
                category="optional_relationship",
                impact="The task is shown using its valid core fields.",
            )
        )

    lifecycle_links = [
        link
        for link in valid_links
        if link.get("type") == "member_of"
        and link.get("to") in LIFECYCLE_ROOTS
    ]
    graph_has_typed_membership = any(
        isinstance(edge, Mapping)
        and edge.get("from_slug") == slug
        and edge.get("to_slug") == root_slug
        and edge.get("link_type") == "member_of"
        for edge in edges
    )
    collection_matches = frontmatter.get("collection") == root_slug
    if not lifecycle_links and (
        graph_has_typed_membership
        or (legacy_untyped_backlink and collection_matches)
    ):
        valid_links.append({"to": root_slug, "type": "member_of"})
        if legacy_untyped_backlink:
            warnings.append(
                _visible_warning(
                    slug,
                    (
                        f"Legacy untyped collection membership is being treated as "
                        f"{root_slug} because the page collection matches exactly."
                    ),
                    category="lifecycle_relationship",
                    impact=(
                        "The task is shown normally; repairing makes its active "
                        "membership explicit and typed."
                    ),
                    repair_action=(
                        "repair_active_membership"
                        if root_slug == ACTIVE_ROOT
                        else None
                    ),
                )
            )
        else:
            warnings.append(
                _visible_warning(
                    slug,
                    "The typed collection edge is missing from task frontmatter.",
                    category="lifecycle_relationship",
                    impact="The task is shown from its verified graph membership.",
                    repair_action=(
                        "repair_active_membership"
                        if root_slug == ACTIVE_ROOT
                        else None
                    ),
                )
            )

    project_links = [
        link
        for link in valid_links
        if link.get("type") == "member_of"
        and link.get("to") not in LIFECYCLE_ROOTS
    ]
    if len({str(link.get("to")) for link in project_links}) > 1:
        valid_links = [
            link
            for link in valid_links
            if not (
                link.get("type") == "member_of"
                and link.get("to") not in LIFECYCLE_ROOTS
            )
        ]
        warnings.append(
            _visible_warning(
                slug,
                "Multiple project relationships are ambiguous and were not selected.",
                category="optional_relationship",
                impact="The task is shown without a project until you choose one.",
            )
        )
    elif project_links:
        project_slug = str(project_links[0]["to"])
        graph_project_verified = any(
            isinstance(edge, Mapping)
            and edge.get("from_slug") == slug
            and edge.get("to_slug") == project_slug
            and edge.get("link_type") == "member_of"
            for edge in edges
        )
        if not graph_project_verified:
            valid_links = [
                link
                for link in valid_links
                if not (
                    link.get("type") == "member_of"
                    and link.get("to") not in LIFECYCLE_ROOTS
                )
            ]
            frontmatter["project"] = None
            warnings.append(
                _visible_warning(
                    slug,
                    "The task project link is not verified in the GBrain graph.",
                    category="optional_relationship",
                    impact=(
                        "The task is shown without a project; choose a durable "
                        "project in task details to repair the assignment."
                    ),
                )
            )
    frontmatter["links"] = valid_links

    has_verified_task_shape = (
        slug.startswith("tasks/")
        and any(
            link.get("type") == "member_of" and link.get("to") == root_slug
            for link in valid_links
        )
        and all(
            field in frontmatter
            for field in ("summary", "detail", "status", "due_day")
        )
    )
    original_type = page.get("type")
    if original_type != "task" and has_verified_task_shape:
        normalized_page["type"] = "task"
        warnings.append(
            _visible_warning(
                slug,
                (
                    f"The page type is {original_type or 'missing'}, but its task slug, "
                    "collection membership, and required task fields are valid."
                ),
                category="core_metadata",
                impact=(
                    "The task is shown using the task contract; repair the page type "
                    "before relying on broader type-based queries."
                ),
            )
        )

    normalized_edges = [
        edge for edge in edges if isinstance(edge, Mapping)
    ]
    goal_edges = [
        edge
        for edge in normalized_edges
        if edge.get("from_slug") == slug
        and edge.get("link_type") == "advances_goal"
        and isinstance(edge.get("to_slug"), str)
        and str(edge.get("to_slug")).startswith("goals/")
    ]
    if len({str(edge.get("to_slug")) for edge in goal_edges}) > 1:
        normalized_edges = [
            edge
            for edge in normalized_edges
            if not (
                edge.get("from_slug") == slug
                and edge.get("link_type") == "advances_goal"
            )
        ]
        warnings.append(
            _visible_warning(
                slug,
                "Multiple goal relationships are ambiguous and were not selected.",
                category="optional_relationship",
                impact="The task is shown without a goal until you choose one.",
            )
        )

    return normalized_page, normalized_edges, warnings


class GBrainAdapter:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or SubprocessCommandRunner()

    def _bounded_map(self, function: Any, values: list[Any]) -> list[Any]:
        if len(values) < 2 or not isinstance(self.runner, SubprocessCommandRunner):
            return [function(value) for value in values]
        with ThreadPoolExecutor(max_workers=min(8, len(values))) as executor:
            return list(executor.map(function, values))

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        if root_slug not in APPROVED_ROOTS:
            raise ValueError("collection root is not approved for GTasks")
        raw_backlinks = self.runner.run("get_backlinks", {"slug": root_slug})
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("get_backlinks did not return a list")

        member_slugs: dict[str, bool] = {}
        for backlink in raw_backlinks:
            if not isinstance(backlink, Mapping):
                continue
            if (
                backlink.get("to_slug") == root_slug
                and isinstance(backlink.get("from_slug"), str)
            ):
                link_type = backlink.get("link_type")
                if link_type == "member_of":
                    member_slugs[str(backlink["from_slug"])] = False
                elif link_type in {"", None}:
                    member_slugs.setdefault(str(backlink["from_slug"]), True)

        def read_task(
            item: tuple[str, bool],
        ) -> tuple[Task | None, list[CollectionIssue]]:
            slug, legacy_untyped = item
            item_issues: list[CollectionIssue] = []
            try:
                page = self.runner.run("get_page", {"slug": slug})
                if not isinstance(page, Mapping):
                    raise GBrainProtocolError("get_page did not return an object")
                frontmatter = page.get("frontmatter")
                if (
                    legacy_untyped
                    and (
                        not isinstance(frontmatter, Mapping)
                        or frontmatter.get("collection") != root_slug
                    )
                ):
                    return None, []
                relationship_warning: CollectionIssue | None = None
                try:
                    raw_edges = self.runner.run("get_links", {"slug": slug})
                    if not isinstance(raw_edges, list):
                        raise GBrainProtocolError("get_links did not return a list")
                    edges = raw_edges
                except GBrainError:
                    edges = []
                    relationship_warning = _visible_warning(
                        slug,
                        "Optional task relationships could not be read from GBrain.",
                        category="optional_relationship",
                        impact=(
                            "The task is shown from its core fields, but goal, project, "
                            "dependency, and blocker links may be incomplete."
                        ),
                    )
                normalized_page, normalized_edges, warnings = (
                    _normalize_collection_task(
                        page,
                        edges,
                        root_slug,
                        legacy_untyped_backlink=legacy_untyped,
                    )
                )
                task = Task.from_page(normalized_page, edges=normalized_edges)
                if task.lifecycle_root != root_slug:
                    raise DomainValidationError(
                        "page frontmatter does not match its lifecycle root edge"
                    )
                item_issues.extend(warnings)
                if relationship_warning is not None:
                    item_issues.append(relationship_warning)
                return task, item_issues
            except (DomainValidationError, GBrainError) as exc:
                item_issues.append(
                    CollectionIssue(
                        slug=slug,
                        message=str(exc),
                        impact=(
                            "This linked page is not shown because a required task "
                            "field or lifecycle rule is invalid."
                        ),
                    )
                )
                return None, item_issues

        tasks: list[Task] = []
        issues: list[CollectionIssue] = []
        for task, item_issues in self._bounded_map(
            read_task,
            list(member_slugs.items()),
        ):
            if task is not None:
                tasks.append(task)
            issues.extend(item_issues)

        return CollectionRead(
            root_slug=root_slug,
            tasks=tuple(tasks),
            issues=tuple(issues),
        )

    def list_agent_profiles(self) -> AgentRead:
        def read_agent(
            scope: tuple[str, str],
        ) -> tuple[AgentProfile | None, CollectionIssue | None]:
            agent_slug, work_root = scope
            try:
                page = self.runner.run("get_page", {"slug": agent_slug})
                edges = self.runner.run("get_links", {"slug": agent_slug})
                if not isinstance(page, Mapping) or not isinstance(edges, list):
                    raise GBrainProtocolError(
                        "agent profile readback was not structured"
                    )
                return (
                    AgentProfile.from_page(
                        page,
                        work_root=work_root,
                        edges=edges,
                    ),
                    None,
                )
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(
                    slug=agent_slug,
                    message=str(exc),
                    impact=(
                        "This agent profile is unavailable until its canonical "
                        "GBrain page is repaired."
                    ),
                )

        agents: list[AgentProfile] = []
        issues: list[CollectionIssue] = []
        for agent, issue in self._bounded_map(read_agent, list(AGENT_SCOPES)):
            if agent is not None:
                agents.append(agent)
            if issue is not None:
                issues.append(issue)
        return AgentRead(agents=tuple(agents), issues=tuple(issues))

    def list_agent_work(self) -> AgentWorkRead:
        profiles = self.list_agent_profiles()
        tasks: list[dict[str, Any]] = []
        issues: list[CollectionIssue] = list(profiles.issues)
        for agent in profiles.agents:
            root_slug = agent.work_root
            try:
                raw_backlinks = self.runner.run(
                    "get_backlinks",
                    {"slug": root_slug},
                )
            except GBrainError as exc:
                issues.append(
                    CollectionIssue(
                        slug=root_slug,
                        message=str(exc),
                        impact=(
                            f"{agent.name}'s work could not be read. Tony's "
                            "personal tasks remain unaffected."
                        ),
                    )
                )
                continue
            if not isinstance(raw_backlinks, list):
                issues.append(
                    CollectionIssue(
                        slug=root_slug,
                        message="agent work backlinks were not a list",
                        impact=(
                            f"{agent.name}'s work could not be read. Tony's "
                            "personal tasks remain unaffected."
                        ),
                    )
                )
                continue
            member_slugs = tuple(
                dict.fromkeys(
                    str(edge["from_slug"])
                    for edge in raw_backlinks
                    if isinstance(edge, Mapping)
                    and edge.get("to_slug") == root_slug
                    and edge.get("link_type") == "member_of"
                    and isinstance(edge.get("from_slug"), str)
                )
            )
            for slug in member_slugs:
                try:
                    page = self.runner.run("get_page", {"slug": slug})
                    edges = self.runner.run("get_links", {"slug": slug})
                    if not isinstance(page, Mapping) or not isinstance(edges, list):
                        raise GBrainProtocolError(
                            "agent task readback was not structured"
                        )
                    frontmatter = page.get("frontmatter")
                    if not isinstance(frontmatter, Mapping):
                        raise DomainValidationError(
                            f"{slug} has no frontmatter"
                        )
                    normalized_page = deepcopy(dict(page))
                    normalized_frontmatter = deepcopy(dict(frontmatter))
                    raw_links = normalized_frontmatter.get("links", [])
                    raw_links = raw_links if isinstance(raw_links, list) else []
                    safe_links = [
                        deepcopy(dict(link))
                        for link in raw_links
                        if isinstance(link, Mapping)
                        and isinstance(link.get("to"), str)
                        and isinstance(link.get("type"), str)
                        and not (
                            link.get("type") == "member_of"
                            and (
                                link.get("to") in AGENT_WORK_ROOTS
                                or link.get("to") in LIFECYCLE_ROOTS
                            )
                        )
                    ]
                    safe_links.append({"to": ACTIVE_ROOT, "type": "member_of"})
                    normalized_frontmatter["links"] = safe_links
                    normalized_page["frontmatter"] = normalized_frontmatter
                    normalized_edges = [
                        deepcopy(dict(edge))
                        for edge in edges
                        if isinstance(edge, Mapping)
                        and not (
                            edge.get("from_slug") == slug
                            and edge.get("link_type") == "member_of"
                            and edge.get("to_slug") in AGENT_WORK_ROOTS
                        )
                    ]
                    normalized_edges.append(
                        {
                            "from_slug": slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    )
                    task = Task.from_page(
                        normalized_page,
                        edges=normalized_edges,
                    )
                    tasks.append(
                        {
                            **task.to_dict(),
                            "lifecycle_root": root_slug,
                            "owner": {
                                "slug": agent.slug,
                                "name": agent.name,
                                "avatar": {
                                    "kind": agent.avatar_kind,
                                    "value": agent.avatar_value,
                                },
                            },
                            "agent_work": True,
                            "read_only": True,
                        }
                    )
                except (DomainValidationError, GBrainError) as exc:
                    issues.append(
                        CollectionIssue(
                            slug=slug,
                            message=str(exc),
                            impact=(
                                f"This malformed {agent.name} work item is "
                                "reported in Inbox and is not shown on Board."
                            ),
                        )
                    )
        deduped: dict[str, dict[str, Any]] = {}
        for task in tasks:
            deduped.setdefault(str(task["slug"]), task)
        return AgentWorkRead(
            tasks=tuple(deduped.values()),
            issues=tuple(issues),
        )

    def list_goals(self) -> GoalRead:
        raw_backlinks = self.runner.run("get_backlinks", {"slug": GOALS_ROOT})
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("goals get_backlinks did not return a list")
        goal_slugs = [
            str(backlink["from_slug"])
            for backlink in raw_backlinks
            if isinstance(backlink, Mapping)
            and backlink.get("to_slug") == GOALS_ROOT
            and isinstance(backlink.get("from_slug"), str)
            and str(backlink["from_slug"]).startswith("goals/")
        ]

        def read_goal(slug: str) -> tuple[Goal | None, CollectionIssue | None]:
            try:
                page = self.runner.run("get_page", {"slug": slug})
                if not isinstance(page, Mapping):
                    raise GBrainProtocolError("goal get_page did not return an object")
                return Goal.from_page(page), None
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(slug=slug, message=str(exc))

        goals: list[Goal] = []
        issues: list[CollectionIssue] = []
        for goal, issue in self._bounded_map(
            read_goal,
            list(dict.fromkeys(goal_slugs)),
        ):
            if goal is not None:
                goals.append(goal)
            if issue is not None:
                issues.append(issue)
        return GoalRead(goals=tuple(goals), issues=tuple(issues))

    def create_goal(self, goal: Goal) -> GoalMutationReceipt:
        self.runner.run(
            "put_page",
            {"slug": goal.slug, "content": render_goal_page(goal)},
        )
        try:
            page = self.runner.run("get_page", {"slug": goal.slug})
            if not isinstance(page, Mapping):
                raise GBrainProtocolError("goal page readback was not an object")
            stored_goal = Goal.from_page(page)
            if stored_goal.to_dict() != goal.to_dict():
                raise GBrainProtocolError("goal page readback did not match the write")
            self.runner.run(
                "add_link",
                {
                    "from": goal.slug,
                    "to": GOALS_ROOT,
                    "link_type": "member_of",
                    "context": "This goal belongs to Tony's Goals.",
                    "link_source": "gtasks",
                },
            )
            links = self.runner.run("get_links", {"slug": goal.slug})
            if not isinstance(links, list) or not any(
                isinstance(link, Mapping)
                and link.get("from_slug") == goal.slug
                and link.get("to_slug") == GOALS_ROOT
                and link.get("link_type") == "member_of"
                for link in links
            ):
                raise GBrainProtocolError(
                    "goal collection relationship readback was not verified"
                )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                goal.slug,
                (
                    "Goal creation was not fully verified. "
                    "Do not retry until this slug is inspected: "
                    f"{exc}"
                ),
            ) from exc
        return GoalMutationReceipt(
            goal_slug=goal.slug,
            goal=stored_goal,
            verified=True,
        )

    def set_goal_paused(self, goal_slug: str) -> GoalMutationReceipt:
        page = self.runner.run("get_page", {"slug": goal_slug})
        links = self.runner.run("get_links", {"slug": goal_slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("goal pause snapshot was not structured")
        goal = Goal.from_page(page, edges=links)
        if goal.status == "paused":
            return GoalMutationReceipt(
                goal_slug=goal_slug,
                goal=goal,
                verified=True,
            )
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise GBrainProtocolError("goal page has no frontmatter")
        original_frontmatter = deepcopy(dict(frontmatter))
        original_frontmatter["type"] = "goal"
        original_content = _render_preserved_page(page, original_frontmatter)
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_frontmatter["status"] = "paused"
        write_succeeded = False
        try:
            self.runner.run(
                "put_page",
                {
                    "slug": goal_slug,
                    "content": _render_preserved_page(page, desired_frontmatter),
                },
            )
            write_succeeded = True
            stored_page = self.runner.run("get_page", {"slug": goal_slug})
            stored_links = self.runner.run("get_links", {"slug": goal_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError("goal pause readback was not structured")
            stored_goal = Goal.from_page(stored_page, edges=stored_links)
            if stored_page.get("type") != "goal" or stored_goal.status != "paused":
                raise GBrainProtocolError("goal pause readback did not match")
            for expected in links:
                if not isinstance(expected, Mapping):
                    continue
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "a goal relationship was missing after pause"
                    )
        except (DomainValidationError, GBrainError) as exc:
            if not write_succeeded:
                raise
            rollback_verified = False
            try:
                self.runner.run(
                    "put_page",
                    {"slug": goal_slug, "content": original_content},
                )
                rollback_page = self.runner.run("get_page", {"slug": goal_slug})
                rollback_goal = Goal.from_page(rollback_page)
                rollback_verified = (
                    isinstance(rollback_page, Mapping)
                    and rollback_page.get("type") == "goal"
                    and rollback_goal.status == goal.status
                )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the goal before retrying."
            )
            raise PartialMutationError(
                goal_slug,
                f"Goal pause was not verified. {outcome}",
            ) from exc
        return GoalMutationReceipt(
            goal_slug=goal_slug,
            goal=stored_goal,
            verified=True,
        )

    def delete_goal(self, goal_slug: str) -> GoalDeletionReceipt:
        page = self.runner.run("get_page", {"slug": goal_slug})
        outgoing = self.runner.run("get_links", {"slug": goal_slug})
        incoming = self.runner.run("get_backlinks", {"slug": goal_slug})
        if (
            not isinstance(page, Mapping)
            or not isinstance(outgoing, list)
            or not isinstance(incoming, list)
        ):
            raise GBrainProtocolError("goal delete snapshot was not structured")
        Goal.from_page(page, edges=outgoing)
        forward_tasks = {
            str(edge["from_slug"])
            for edge in incoming
            if isinstance(edge, Mapping)
            and edge.get("to_slug") == goal_slug
            and edge.get("link_type") == "advances_goal"
            and isinstance(edge.get("from_slug"), str)
            and str(edge["from_slug"]).startswith("tasks/")
        }
        reverse_tasks = {
            str(edge["to_slug"])
            for edge in outgoing
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == goal_slug
            and edge.get("link_type") == "advanced_by"
            and isinstance(edge.get("to_slug"), str)
            and str(edge["to_slug"]).startswith("tasks/")
        }
        task_slugs = tuple(sorted(forward_tasks | reverse_tasks))
        unlinked_tasks: list[str] = []
        delete_succeeded = False
        try:
            for task_slug in task_slugs:
                self.set_task_goal(task_slug, None)
                unlinked_tasks.append(task_slug)
            remaining_outgoing = self.runner.run("get_links", {"slug": goal_slug})
            remaining_incoming = self.runner.run("get_backlinks", {"slug": goal_slug})
            if not isinstance(remaining_outgoing, list) or not isinstance(
                remaining_incoming, list
            ):
                raise GBrainProtocolError(
                    "goal relationship removal readback was not structured"
                )
            if any(
                isinstance(edge, Mapping)
                and edge.get("link_type") == "advanced_by"
                and edge.get("to_slug") in task_slugs
                for edge in remaining_outgoing
            ) or any(
                isinstance(edge, Mapping)
                and edge.get("link_type") == "advances_goal"
                and edge.get("from_slug") in task_slugs
                for edge in remaining_incoming
            ):
                raise GBrainProtocolError(
                    "goal task relationships remained after removal"
                )
            self.runner.run("delete_page", {"slug": goal_slug})
            delete_succeeded = True
            deleted_page = self.runner.run(
                "get_page",
                {"slug": goal_slug, "include_deleted": True},
            )
            if (
                not isinstance(deleted_page, Mapping)
                or deleted_page.get("slug") != goal_slug
                or not deleted_page.get("deleted_at")
            ):
                raise GBrainProtocolError("goal soft-delete readback was not verified")
        except (DomainValidationError, ValueError, GBrainError) as exc:
            rollback_verified = False
            try:
                if delete_succeeded:
                    self.runner.run("restore_page", {"slug": goal_slug})
                for task_slug in unlinked_tasks:
                    self.set_task_goal(task_slug, goal_slug)
                restored_page = self.runner.run("get_page", {"slug": goal_slug})
                Goal.from_page(restored_page)
                restored_outgoing = self.runner.run(
                    "get_links", {"slug": goal_slug}
                )
                restored_incoming = self.runner.run(
                    "get_backlinks", {"slug": goal_slug}
                )
                rollback_verified = (
                    isinstance(restored_outgoing, list)
                    and isinstance(restored_incoming, list)
                    and all(
                        any(
                            isinstance(edge, Mapping)
                            and edge.get("from_slug") == task_slug
                            and edge.get("to_slug") == goal_slug
                            and edge.get("link_type") == "advances_goal"
                            for edge in restored_incoming
                        )
                        and any(
                            isinstance(edge, Mapping)
                            and edge.get("from_slug") == goal_slug
                            and edge.get("to_slug") == task_slug
                            and edge.get("link_type") == "advanced_by"
                            for edge in restored_outgoing
                        )
                        for task_slug in unlinked_tasks
                    )
                )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the goal before retrying."
            )
            raise PartialMutationError(
                goal_slug,
                f"Goal deletion was not verified. {outcome}",
            ) from exc
        return GoalDeletionReceipt(
            goal_slug=goal_slug,
            removed_task_links=task_slugs,
            recoverable_until_hours=72,
            verified=True,
        )

    def list_projects(self) -> ProjectRead:
        raw_backlinks = self.runner.run("get_backlinks", {"slug": PROJECTS_ROOT})
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("projects get_backlinks did not return a list")
        project_slugs = list(
            dict.fromkeys(
                str(backlink["from_slug"])
                for backlink in raw_backlinks
                if isinstance(backlink, Mapping)
                and backlink.get("to_slug") == PROJECTS_ROOT
                and backlink.get("link_type") == "member_of"
                and isinstance(backlink.get("from_slug"), str)
                and str(backlink["from_slug"]).startswith("projects/")
            )
        )
        def read_project(
            slug: str,
        ) -> tuple[Project | None, CollectionIssue | None]:
            try:
                page = self.runner.run("get_page", {"slug": slug})
                links = self.runner.run("get_links", {"slug": slug})
                if not isinstance(page, Mapping) or not isinstance(links, list):
                    raise GBrainProtocolError(
                        "project page or relationship readback was not structured"
                    )
                return Project.from_page(page, edges=links), None
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(
                    slug=slug,
                    message=str(exc),
                    category="project_data",
                    impact=(
                        "This scoped project is not counted or offered for task "
                        "assignment until its core project data is repaired."
                    ),
                )

        projects: list[Project] = []
        issues: list[CollectionIssue] = []
        for project, issue in self._bounded_map(read_project, project_slugs):
            if project is not None:
                projects.append(project)
            if issue is not None:
                issues.append(issue)
        projects.sort(key=lambda project: project.title.casefold())
        return ProjectRead(projects=tuple(projects), issues=tuple(issues))

    def _ensure_projects_root(self) -> None:
        try:
            page = self.runner.run("get_page", {"slug": PROJECTS_ROOT})
        except GBrainCommandError as exc:
            if "page_not_found" not in str(exc):
                raise
            self.runner.run(
                "put_page",
                {
                    "slug": PROJECTS_ROOT,
                    "content": render_projects_collection_page(),
                },
            )
            page = self.runner.run("get_page", {"slug": PROJECTS_ROOT})
        if not isinstance(page, Mapping):
            raise GBrainProtocolError(
                "Tony's Projects collection readback was not an object"
            )
        if (
            page.get("slug") != PROJECTS_ROOT
            or page.get("type") != "collection"
            or page.get("title") not in {"Tony's Projects", "Tony’s Projects"}
        ):
            raise GBrainProtocolError(
                f"{PROJECTS_ROOT} is not the canonical Tony's Projects collection"
            )

    def create_project(self, project: Project) -> ProjectMutationReceipt:
        try:
            self._ensure_projects_root()
        except GBrainError as exc:
            raise PartialMutationError(
                PROJECTS_ROOT,
                (
                    "Project creation did not start because the GTasks project "
                    f"scope collection could not be verified: {exc}"
                ),
            ) from exc
        content = render_project_page(project)
        self.runner.run(
            "put_page",
            {"slug": project.slug, "content": content},
        )
        try:
            page = self.runner.run("get_page", {"slug": project.slug})
            if not isinstance(page, Mapping):
                raise GBrainProtocolError("project page readback was not an object")
            stored_project = Project.from_page(page)
            if (
                stored_project.slug != project.slug
                or stored_project.title != project.title
                or stored_project.status != project.status
            ):
                raise GBrainProtocolError(
                    "project page readback did not match the write"
                )
            self.runner.run(
                "add_link",
                {
                    "from": project.slug,
                    "to": PROJECTS_ROOT,
                    "link_type": "member_of",
                    "context": "This project is explicitly owned by GTasks.",
                    "link_source": "gtasks",
                },
            )
            links = self.runner.run("get_links", {"slug": project.slug})
            if not isinstance(links, list) or not any(
                isinstance(link, Mapping)
                and link.get("from_slug") == project.slug
                and link.get("to_slug") == PROJECTS_ROOT
                and link.get("link_type") == "member_of"
                for link in links
            ):
                raise GBrainProtocolError(
                    "project collection relationship readback was not verified"
                )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                project.slug,
                (
                    "Project creation was not fully verified. "
                    "Do not retry until this slug is inspected: "
                    f"{exc}"
                ),
            ) from exc
        return ProjectMutationReceipt(project_slug=project.slug, verified=True)

    def read_goal_relationships(self, goal_slug: str) -> GoalRelationshipRead:
        page = self.runner.run("get_page", {"slug": goal_slug})
        if not isinstance(page, Mapping):
            raise GBrainProtocolError("goal get_page did not return an object")
        edges = self.runner.run("get_links", {"slug": goal_slug})
        if not isinstance(edges, list):
            raise GBrainProtocolError("goal get_links did not return a list")
        goal = Goal.from_page(page, edges=edges)
        return GoalRelationshipRead(
            goal_slug=goal.slug,
            task_slugs=goal.advanced_by,
        )

    def create_inbox(self, task: Task) -> MutationReceipt:
        if task.lifecycle_root != ACTIVE_ROOT:
            raise ValueError("Inbox task must belong to the active GTasks root")
        if task.status != "planned" or not task.inbox:
            raise ValueError("Inbox task must be planned and marked inbox")
        if task.due_day is None:
            raise ValueError("Inbox task must have a due date")

        self.runner.run(
            "put_page",
            {"slug": task.slug, "content": render_task_page(task)},
        )
        try:
            raw_page = self.runner.run("get_page", {"slug": task.slug})
            if not isinstance(raw_page, Mapping):
                raise GBrainProtocolError("get_page did not return an object")
            stored_task = Task.from_page(raw_page)
            expected = (
                task.slug,
                task.summary,
                task.status,
                task.due_day,
                task.lifecycle_root,
                task.inbox,
            )
            actual = (
                stored_task.slug,
                stored_task.summary,
                stored_task.status,
                stored_task.due_day,
                stored_task.lifecycle_root,
                stored_task.inbox,
            )
            if actual != expected:
                raise GBrainProtocolError("task page readback did not match the write")
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task.slug,
                f"Task page was written but page readback failed: {exc}",
            ) from exc

        self.runner.run(
            "add_link",
            {
                "from": task.slug,
                "to": ACTIVE_ROOT,
                "link_type": "member_of",
                "context": "GTasks active task membership.",
                "link_source": "gtasks",
            },
        )
        try:
            raw_links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(raw_links, list):
                raise GBrainProtocolError("get_links did not return a list")
            verified = any(
                isinstance(link, Mapping)
                and link.get("from_slug") == task.slug
                and link.get("to_slug") == ACTIVE_ROOT
                and link.get("link_type") == "member_of"
                for link in raw_links
            )
            if not verified:
                raise GBrainProtocolError("active membership edge was not found")
        except GBrainError as exc:
            raise PartialMutationError(
                task.slug,
                f"Task page exists but membership readback failed: {exc}",
            ) from exc

        return MutationReceipt(slug=task.slug, verified=True)

    def create_task(self, task: Task) -> MutationReceipt:
        if task.project:
            project_page = self.runner.run("get_page", {"slug": task.project})
            project_links = self.runner.run("get_links", {"slug": task.project})
            if not isinstance(project_page, Mapping) or not isinstance(
                project_links, list
            ):
                raise ValueError("selected project could not be verified")
            try:
                Project.from_page(project_page, edges=project_links)
            except DomainValidationError as exc:
                raise ValueError(
                    "project is not a durable member of Tony's Projects"
                ) from exc
        if task.goal:
            goal_page = self.runner.run("get_page", {"slug": task.goal})
            if not isinstance(goal_page, Mapping):
                raise ValueError("selected goal could not be verified")
            try:
                Goal.from_page(goal_page)
            except DomainValidationError as exc:
                raise ValueError("goal is not a member of Tony's Goals") from exc

        receipt = self.create_inbox(task)
        try:
            if task.project:
                self.runner.run(
                    "add_link",
                    {
                        "from": task.slug,
                        "to": task.project,
                        "link_type": "member_of",
                        "context": "GTasks project membership.",
                        "link_source": "gtasks",
                    },
                )
            if task.goal:
                self.runner.run(
                    "add_link",
                    {
                        "from": task.slug,
                        "to": task.goal,
                        "link_type": "advances_goal",
                        "context": "This task advances the linked Tony goal.",
                        "link_source": "gtasks",
                    },
                )
                self.runner.run(
                    "add_link",
                    {
                        "from": task.goal,
                        "to": task.slug,
                        "link_type": "advanced_by",
                        "context": "This goal is advanced by the linked GTasks task.",
                        "link_source": "gtasks",
                    },
                )

            stored_page = self.runner.run("get_page", {"slug": task.slug})
            stored_links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "full task creation readback was not structured"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            expected = (
                task.summary,
                task.detail,
                task.status,
                task.priority,
                task.next_action,
                task.due_day,
                task.inbox,
                task.lifecycle_root,
                task.project,
                task.goal,
                task.progress_metric,
                task.event_progress,
            )
            actual = (
                stored_task.summary,
                stored_task.detail,
                stored_task.status,
                stored_task.priority,
                stored_task.next_action,
                stored_task.due_day,
                stored_task.inbox,
                stored_task.lifecycle_root,
                stored_task.project,
                stored_task.goal,
                stored_task.progress_metric,
                stored_task.event_progress,
            )
            if actual != expected:
                raise GBrainProtocolError(
                    "full task page readback did not match the requested task"
                )
            typed_edges = {
                (
                    edge.get("from_slug"),
                    edge.get("to_slug"),
                    edge.get("link_type"),
                )
                for edge in stored_links
                if isinstance(edge, Mapping)
            }
            if (
                task.project
                and (task.slug, task.project, "member_of") not in typed_edges
            ):
                raise GBrainProtocolError(
                    "task project relationship readback was not verified"
                )
            if task.goal:
                if (
                    task.slug,
                    task.goal,
                    "advances_goal",
                ) not in typed_edges:
                    raise GBrainProtocolError(
                        "task goal relationship readback was not verified"
                    )
                goal_links = self.runner.run("get_links", {"slug": task.goal})
                if not isinstance(goal_links, list) or not any(
                    isinstance(edge, Mapping)
                    and edge.get("from_slug") == task.goal
                    and edge.get("to_slug") == task.slug
                    and edge.get("link_type") == "advanced_by"
                    for edge in goal_links
                ):
                    raise GBrainProtocolError(
                        "goal reciprocal relationship readback was not verified"
                    )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task.slug,
                (
                    "Task creation relationships were not fully verified. "
                    f"Inspect this task before retrying: {exc}"
                ),
            ) from exc
        return receipt

    def duplicate_task(
        self,
        source_slug: str,
        task: Task,
    ) -> MutationReceipt:
        self._approved_task(source_slug)
        if task.slug == source_slug:
            raise ValueError("duplicate task must receive a new identity")
        if (
            task.status != "planned"
            or not task.inbox
            or task.completed_at is not None
            or task.lifecycle_root != ACTIVE_ROOT
        ):
            raise ValueError(
                "duplicate task must start planned in the active Inbox "
                "without completion history"
            )
        if task.event_progress and (
            task.event_progress.evidence_slugs
            or task.event_progress.receipt_ids
        ):
            raise ValueError(
                "duplicate task may not copy event evidence or receipts"
            )
        if (
            task.progress_metric
            and task.progress_metric.event_binding
            and task.progress_metric.current != 0
        ):
            raise ValueError(
                "duplicate event-bound task progress must start at 0"
            )
        return self.create_task(task)

    def repair_active_membership(
        self,
        task_slug: str,
    ) -> MembershipRepairReceipt:
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        if not isinstance(raw_page, Mapping):
            raise GBrainProtocolError("membership repair get_page was not an object")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; "
                "repair the task type before repairing membership"
            )
        raw_frontmatter = raw_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise ValueError("task is not eligible for active membership repair")
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_links, list):
            raise GBrainProtocolError("membership repair get_links was not a list")

        legacy_edges = [
            edge
            for edge in raw_links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == task_slug
            and edge.get("to_slug") == ACTIVE_ROOT
            and edge.get("link_type") in {"", None}
        ]
        typed_edges = _lifecycle_edges(task_slug, raw_links)
        if (
            raw_frontmatter.get("collection") != ACTIVE_ROOT
            or len(legacy_edges) != 1
            or typed_edges
        ):
            raise ValueError("task is not eligible for active membership repair")

        repaired_frontmatter = deepcopy(dict(raw_frontmatter))
        repaired_frontmatter["type"] = "task"
        repaired_links = repaired_frontmatter.get("links")
        if repaired_links is None:
            repaired_links = []
        if not isinstance(repaired_links, list):
            raise ValueError("task is not eligible for active membership repair")
        repaired_links = deepcopy(repaired_links)
        repaired_links.append({"to": ACTIVE_ROOT, "type": "member_of"})
        repaired_frontmatter["links"] = repaired_links

        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        original_content = _render_preserved_page(raw_page, original_frontmatter)
        repaired_content = _render_preserved_page(raw_page, repaired_frontmatter)
        typed_descriptor = {
            "from": task_slug,
            "to": ACTIVE_ROOT,
            "link_type": "member_of",
            "context": "GTasks active task membership repair.",
            "link_source": "gtasks",
        }
        legacy_descriptor = {
            "from": task_slug,
            "to": ACTIVE_ROOT,
            "link_type": "",
        }
        journal: list[str] = []
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": repaired_content},
            )
            journal.append("put_page")
            self.runner.run("add_link", typed_descriptor)
            journal.append("add_typed")
            self.runner.run("remove_link", legacy_descriptor)
            journal.append("remove_legacy")

            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "membership repair readback was not structured"
                )
            if stored_page.get("type") != "task":
                raise GBrainProtocolError(
                    "membership repair changed the page type away from task"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            verified_typed = _lifecycle_edges(task_slug, stored_links)
            if (
                stored_task.lifecycle_root != ACTIVE_ROOT
                or len(verified_typed) != 1
                or any(
                    isinstance(edge, Mapping)
                    and edge.get("from_slug") == task_slug
                    and edge.get("to_slug") == ACTIVE_ROOT
                    and edge.get("link_type") in {"", None}
                    for edge in stored_links
                )
            ):
                raise GBrainProtocolError(
                    "membership repair readback did not match the requested state"
                )
        except (DomainValidationError, GBrainError) as exc:
            rollback_verified = False
            try:
                if "remove_legacy" in journal:
                    self.runner.run(
                        "add_link",
                        {
                            **legacy_descriptor,
                            "context": "Restored legacy GTasks collection link.",
                            "link_source": "gtasks",
                        },
                    )
                if "add_typed" in journal:
                    self.runner.run(
                        "remove_link",
                        {
                            "from": task_slug,
                            "to": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                    )
                if "put_page" in journal:
                    self.runner.run(
                        "put_page",
                        {"slug": task_slug, "content": original_content},
                    )
                rollback_page = self.runner.run("get_page", {"slug": task_slug})
                rollback_links = self.runner.run("get_links", {"slug": task_slug})
                rollback_verified = (
                    isinstance(rollback_page, Mapping)
                    and rollback_page.get("type") == "task"
                    and isinstance(rollback_links, list)
                    and any(
                        isinstance(edge, Mapping)
                        and edge.get("from_slug") == task_slug
                        and edge.get("to_slug") == ACTIVE_ROOT
                        and edge.get("link_type") in {"", None}
                        for edge in rollback_links
                    )
                    and not _lifecycle_edges(task_slug, rollback_links)
                )
            except GBrainError:
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Active membership repair was not verified. {outcome}",
            ) from exc

        return MembershipRepairReceipt(task_slug=task_slug, verified=True)

    def _approved_task(self, task_slug: str) -> Task:
        for root_slug in (ACTIVE_ROOT, COMPLETED_ROOT):
            result = self.list_collection_tasks(root_slug)
            for task in result.tasks:
                if task.slug == task_slug:
                    return task
        raise ValueError("task is not a member of an approved GTasks root")

    def set_task_status(
        self,
        task_slug: str,
        status: str,
        now: datetime,
    ) -> StatusMutationReceipt:
        if status not in EDITABLE_TASK_STATUSES:
            raise ValueError(
                f"status must be one of {', '.join(sorted(EDITABLE_TASK_STATUSES))}"
            )
        if now.tzinfo is None:
            raise ValueError("status update time must include Tony's local timezone")

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        if not isinstance(raw_page, Mapping):
            raise GBrainProtocolError("get_page did not return an object")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; "
                "repair the task type before changing status"
            )
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_links, list):
            raise GBrainProtocolError("get_links did not return a list")
        initial_lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(initial_lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        initial_root = str(initial_lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            initial_root,
            legacy_untyped_backlink=False,
        )
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        existing_lifecycle_edges = initial_lifecycle_edges
        if (
            len(existing_lifecycle_edges) != 1
            or existing_lifecycle_edges[0].get("to_slug") != task.lifecycle_root
        ):
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )

        if task.status == status:
            return StatusMutationReceipt(
                task_slug=task_slug,
                status=status,
                lifecycle_root=task.lifecycle_root,
                completed_at=task.completed_at,
                task=task,
                verified=True,
            )

        unfinished = status not in {"completed", "cancelled"}
        target_root = (
            ACTIVE_ROOT
            if task.lifecycle_root == COMPLETED_ROOT and unfinished
            else task.lifecycle_root
        )
        completed_at = now if status == "completed" else None

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        frontmatter = deepcopy(dict(raw_frontmatter))
        frontmatter["type"] = "task"
        frontmatter["status"] = status
        frontmatter["completed_at"] = (
            completed_at.isoformat() if completed_at else None
        )
        frontmatter["updated_at"] = now.isoformat()

        if target_root != task.lifecycle_root:
            raw_frontmatter_links = frontmatter.get("links")
            if not isinstance(raw_frontmatter_links, list):
                raise GBrainProtocolError("task frontmatter links must be a list")
            replaced = 0
            for link in raw_frontmatter_links:
                if (
                    isinstance(link, dict)
                    and link.get("type") == "member_of"
                    and link.get("to") == task.lifecycle_root
                ):
                    link["to"] = target_root
                    replaced += 1
            if replaced != 1:
                raise GBrainProtocolError(
                    "task frontmatter lifecycle link could not be updated safely"
                )

        content = _render_preserved_page(raw_page, frontmatter)
        self.runner.run("put_page", {"slug": task_slug, "content": content})
        try:
            if target_root != task.lifecycle_root:
                self.runner.run(
                    "add_link",
                    {
                        "from": task_slug,
                        "to": target_root,
                        "link_type": "member_of",
                        "context": "GTasks active task membership.",
                        "link_source": "gtasks",
                    },
                )
                self.runner.run(
                    "remove_link",
                    {
                        "from": task_slug,
                        "to": task.lifecycle_root,
                        "link_type": "member_of",
                    },
                )

            stored_page = self.runner.run("get_page", {"slug": task_slug})
            if not isinstance(stored_page, Mapping):
                raise GBrainProtocolError("status get_page readback was not an object")
            if stored_page.get("type") != "task":
                raise GBrainProtocolError(
                    "status write changed the canonical page type away from task"
                )
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_links, list):
                raise GBrainProtocolError("status get_links readback was not a list")
            stored_task = Task.from_page(stored_page, edges=stored_links)
            if (
                stored_task.status != status
                or stored_task.lifecycle_root != target_root
                or stored_task.completed_at != completed_at
            ):
                raise GBrainProtocolError(
                    "status page readback did not match the requested update"
                )
            verified_lifecycle_edges = _lifecycle_edges(task_slug, stored_links)
            if (
                len(verified_lifecycle_edges) != 1
                or verified_lifecycle_edges[0].get("to_slug") != target_root
            ):
                raise GBrainProtocolError(
                    "status lifecycle edge readback did not match the task page"
                )

            unrelated_edges = [
                link
                for link in raw_links
                if isinstance(link, Mapping)
                and link not in existing_lifecycle_edges
            ]
            for expected in unrelated_edges:
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "an unrelated task relationship was missing after readback"
                    )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task_slug,
                f"Task status write was not verified by page and link readback: {exc}",
            ) from exc

        return StatusMutationReceipt(
            task_slug=task_slug,
            status=status,
            lifecycle_root=target_root,
            completed_at=completed_at,
            task=stored_task,
            verified=True,
        )

    def set_task_next_action(
        self,
        task_slug: str,
        next_action: str,
        now: datetime,
    ) -> NextActionMutationReceipt:
        if not isinstance(next_action, str):
            raise ValueError("next_action must be text")
        normalized_action = next_action.strip()
        if len(normalized_action) > 240:
            raise ValueError("next_action must be 240 characters or fewer")
        if "\n" in normalized_action or "\r" in normalized_action:
            raise ValueError("next_action must be a single concise line")
        if now.tzinfo is None:
            raise ValueError("next action update time must include Tony's local timezone")

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        if not isinstance(raw_page, Mapping):
            raise GBrainProtocolError("get_page did not return an object")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; "
                "repair the task type before changing its next action"
            )
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_links, list):
            raise GBrainProtocolError("get_links did not return a list")
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        if task.lifecycle_root != lifecycle_root:
            raise ValueError(
                "task lifecycle relationship does not match its canonical page"
            )
        if task.next_action == normalized_action:
            return NextActionMutationReceipt(
                task_slug=task_slug,
                next_action=normalized_action,
                verified=True,
            )

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_frontmatter["next_action"] = normalized_action
        desired_frontmatter["updated_at"] = now.isoformat()
        original_content = _render_preserved_page(raw_page, original_frontmatter)
        desired_content = _render_preserved_page(raw_page, desired_frontmatter)

        write_succeeded = False
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": desired_content},
            )
            write_succeeded = True
            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "next action readback was not structured"
                )
            if stored_page.get("type") != "task":
                raise GBrainProtocolError(
                    "next action write changed the canonical page type away from task"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            verified_lifecycle_edges = _lifecycle_edges(task_slug, stored_links)
            if (
                stored_task.next_action != normalized_action
                or stored_task.lifecycle_root != lifecycle_root
                or len(verified_lifecycle_edges) != 1
                or verified_lifecycle_edges[0].get("to_slug") != lifecycle_root
            ):
                raise GBrainProtocolError(
                    "next action page and lifecycle readback did not match the request"
                )
            unrelated_edges = [
                link
                for link in raw_links
                if isinstance(link, Mapping) and link not in lifecycle_edges
            ]
            for expected in unrelated_edges:
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "an unrelated task relationship was missing after readback"
                    )
        except (DomainValidationError, GBrainError) as exc:
            if not write_succeeded:
                raise
            rollback_verified = False
            try:
                self.runner.run(
                    "put_page",
                    {"slug": task_slug, "content": original_content},
                )
                rollback_page = self.runner.run("get_page", {"slug": task_slug})
                rollback_links = self.runner.run("get_links", {"slug": task_slug})
                if isinstance(rollback_page, Mapping) and isinstance(
                    rollback_links, list
                ):
                    rollback_task = Task.from_page(
                        rollback_page,
                        edges=rollback_links,
                    )
                    rollback_lifecycle = _lifecycle_edges(
                        task_slug,
                        rollback_links,
                    )
                    rollback_verified = (
                        rollback_page.get("type") == "task"
                        and rollback_task.next_action == task.next_action
                        and rollback_task.lifecycle_root == lifecycle_root
                        and len(rollback_lifecycle) == 1
                        and rollback_lifecycle[0].get("to_slug") == lifecycle_root
                    )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Task next action write was not verified. {outcome}",
            ) from exc

        return NextActionMutationReceipt(
            task_slug=task_slug,
            next_action=normalized_action,
            verified=True,
        )

    def set_task_progress_metric(
        self,
        task_slug: str,
        progress_metric: ProgressMetric | None,
        event_progress: EventProgress | None,
        now: datetime,
    ) -> TaskProgressMetricReceipt:
        if now.tzinfo is None:
            raise ValueError("progress metric update time must include timezone")
        if progress_metric is None and event_progress is not None:
            raise ValueError("event progress requires a progress metric")
        if progress_metric and progress_metric.event_binding:
            if (
                event_progress is None
                or progress_metric.current != len(event_progress.receipt_ids)
            ):
                raise ValueError(
                    "event-bound metric current must match unique event evidence"
                )
        elif event_progress is not None:
            raise ValueError(
                "event progress requires an event-bound progress metric"
            )

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("task metric snapshot was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}"
            )
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        if (
            progress_metric
            and progress_metric.event_binding
            and progress_metric.task_day != task.due_day
        ):
            raise ValueError(
                "event-bound progress metric task_day must match the task due day"
            )
        if (
            task.progress_metric == progress_metric
            and task.event_progress == event_progress
        ):
            return TaskProgressMetricReceipt(
                task_slug=task_slug,
                task=task,
                verified=True,
            )

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_frontmatter["progress_metric"] = (
            progress_metric.to_dict() if progress_metric else None
        )
        desired_frontmatter["event_progress"] = (
            event_progress.to_dict() if event_progress else None
        )
        desired_frontmatter["updated_at"] = now.isoformat()
        original_content = _render_preserved_page(
            raw_page,
            original_frontmatter,
        )
        desired_content = _render_preserved_page(
            raw_page,
            desired_frontmatter,
        )

        write_succeeded = False
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": desired_content},
            )
            write_succeeded = True
            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if (
                not isinstance(stored_page, Mapping)
                or stored_page.get("type") != "task"
                or not isinstance(stored_links, list)
            ):
                raise GBrainProtocolError(
                    "task metric readback was not structured"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            stored_lifecycle = _lifecycle_edges(task_slug, stored_links)
            if (
                stored_task.progress_metric != progress_metric
                or stored_task.event_progress != event_progress
                or stored_task.lifecycle_root != lifecycle_root
                or len(stored_lifecycle) != 1
                or stored_lifecycle[0].get("to_slug") != lifecycle_root
            ):
                raise GBrainProtocolError(
                    "task metric page and lifecycle readback did not match"
                )
            for expected in raw_links:
                if not isinstance(expected, Mapping):
                    continue
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "an existing task relationship was missing after metric update"
                    )
        except (DomainValidationError, GBrainError) as exc:
            if not write_succeeded:
                raise
            rollback_verified = False
            try:
                self.runner.run(
                    "put_page",
                    {"slug": task_slug, "content": original_content},
                )
                rollback_page = self.runner.run(
                    "get_page",
                    {"slug": task_slug},
                )
                rollback_links = self.runner.run(
                    "get_links",
                    {"slug": task_slug},
                )
                if isinstance(rollback_page, Mapping) and isinstance(
                    rollback_links, list
                ):
                    rollback_task = Task.from_page(
                        rollback_page,
                        edges=rollback_links,
                    )
                    rollback_verified = (
                        rollback_page.get("type") == "task"
                        and rollback_task.progress_metric
                        == task.progress_metric
                        and rollback_task.event_progress
                        == task.event_progress
                    )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Task progress metric write was not verified. {outcome}",
            ) from exc

        return TaskProgressMetricReceipt(
            task_slug=task_slug,
            task=stored_task,
            verified=True,
        )

    def apply_task_progress_event(
        self,
        task_slug: str,
        *,
        event_binding: str,
        evidence_slug: str,
        receipt_id: str,
        now: datetime,
    ) -> TaskProgressEventReceipt:
        if now.tzinfo is None:
            raise ValueError("progress event time must include timezone")
        for field_name, value in (
            ("event_binding", event_binding),
            ("evidence_slug", evidence_slug),
            ("receipt_id", receipt_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("progress event task snapshot was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}"
            )
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        metric = task.progress_metric
        progress = task.event_progress
        if (
            metric is None
            or metric.event_binding != event_binding
            or progress is None
        ):
            raise ValueError("task progress event binding does not match")
        if metric.task_day != now.date():
            raise ValueError("task progress event does not match task_day")

        evidence_present = evidence_slug in progress.evidence_slugs
        receipt_present = receipt_id in progress.receipt_ids
        if evidence_present != receipt_present:
            raise ValueError(
                "progress event conflicts with existing evidence or receipt"
            )
        if evidence_present and receipt_present:
            if (
                progress.evidence_slugs.index(evidence_slug)
                != progress.receipt_ids.index(receipt_id)
            ):
                raise ValueError(
                    "progress event evidence and receipt pairing conflicts"
                )
            verified_task = task
            if (
                metric.current == metric.target
                and metric.auto_complete
                and task.status != "completed"
            ):
                verified_task = self.set_task_status(
                    task_slug,
                    "completed",
                    now,
                ).task
            return TaskProgressEventReceipt(
                task_slug=task_slug,
                task=verified_task,
                duplicate=True,
                verified=True,
            )
        if task.status in {"completed", "cancelled"}:
            raise ValueError("finished task cannot accept new progress events")
        if metric.current >= metric.target:
            raise ValueError("task progress target is already reached")

        updated_progress = EventProgress(
            evidence_slugs=(*progress.evidence_slugs, evidence_slug),
            receipt_ids=(*progress.receipt_ids, receipt_id),
        )
        updated_metric = deepcopy(metric)
        updated_metric = ProgressMetric(
            kind=updated_metric.kind,
            label=updated_metric.label,
            unit=updated_metric.unit,
            target=updated_metric.target,
            current=updated_metric.current + 1,
            event_binding=updated_metric.event_binding,
            auto_complete=updated_metric.auto_complete,
            task_day=updated_metric.task_day,
            timezone=updated_metric.timezone,
        )
        metric_receipt = self.set_task_progress_metric(
            task_slug,
            updated_metric,
            updated_progress,
            now,
        )
        verified_task = metric_receipt.task
        if (
            updated_metric.current == updated_metric.target
            and updated_metric.auto_complete
        ):
            verified_task = self.set_task_status(
                task_slug,
                "completed",
                now,
            ).task
        return TaskProgressEventReceipt(
            task_slug=task_slug,
            task=verified_task,
            duplicate=False,
            verified=True,
        )

    def set_task_project(
        self,
        task_slug: str,
        project_slug: str | None,
    ) -> ProjectAssignmentReceipt:
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("task project snapshot was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}"
            )
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        task = Task.from_page(normalized_page, edges=normalized_links)
        approved_projects = {
            project.slug for project in self.list_projects().projects
        }
        if project_slug is not None and project_slug not in approved_projects:
            raise ValueError("project is not a durable member of Tony's Projects")
        current_project = task.project
        if current_project == project_slug:
            return ProjectAssignmentReceipt(
                task_slug=task_slug,
                project_slug=project_slug,
                verified=True,
            )

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_links = desired_frontmatter.get("links")
        if not isinstance(desired_links, list):
            raise GBrainProtocolError("task frontmatter links must be a list")
        desired_links = [
            link
            for link in deepcopy(desired_links)
            if not (
                isinstance(link, Mapping)
                and link.get("type") == "member_of"
                and link.get("to") not in LIFECYCLE_ROOTS
            )
        ]
        if project_slug is not None:
            desired_links.append({"to": project_slug, "type": "member_of"})
        desired_frontmatter["links"] = desired_links
        desired_frontmatter["project"] = project_slug
        original_content = _render_preserved_page(raw_page, original_frontmatter)
        desired_content = _render_preserved_page(raw_page, desired_frontmatter)
        journal: list[str] = []
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": desired_content},
            )
            journal.append("put_page")
            if project_slug is not None:
                self.runner.run(
                    "add_link",
                    {
                        "from": task_slug,
                        "to": project_slug,
                        "link_type": "member_of",
                        "context": "GTasks task project assignment.",
                        "link_source": "gtasks",
                    },
                )
                journal.append("add_new")
            if current_project is not None:
                self.runner.run(
                    "remove_link",
                    {
                        "from": task_slug,
                        "to": current_project,
                        "link_type": "member_of",
                    },
                )
                journal.append("remove_old")
            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "task project readback was not structured"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            verified_project_edges = [
                edge
                for edge in stored_links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == task_slug
                and edge.get("link_type") == "member_of"
                and edge.get("to_slug") not in APPROVED_ROOTS
            ]
            expected_projects = [project_slug] if project_slug else []
            if (
                stored_page.get("type") != "task"
                or stored_task.project != project_slug
                or [edge.get("to_slug") for edge in verified_project_edges]
                != expected_projects
                or len(_lifecycle_edges(task_slug, stored_links)) != 1
            ):
                raise GBrainProtocolError(
                    "task project page and relationship readback did not match"
                )
        except (DomainValidationError, GBrainError) as exc:
            rollback_verified = False
            try:
                if "remove_old" in journal and current_project is not None:
                    self.runner.run(
                        "add_link",
                        {
                            "from": task_slug,
                            "to": current_project,
                            "link_type": "member_of",
                            "context": "Restored GTasks project assignment.",
                            "link_source": "gtasks",
                        },
                    )
                if "add_new" in journal and project_slug is not None:
                    self.runner.run(
                        "remove_link",
                        {
                            "from": task_slug,
                            "to": project_slug,
                            "link_type": "member_of",
                        },
                    )
                if "put_page" in journal:
                    self.runner.run(
                        "put_page",
                        {"slug": task_slug, "content": original_content},
                    )
                rollback_page = self.runner.run("get_page", {"slug": task_slug})
                rollback_links = self.runner.run("get_links", {"slug": task_slug})
                rollback_task = Task.from_page(
                    rollback_page,
                    edges=rollback_links,
                )
                rollback_verified = rollback_task.project == current_project
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Task project assignment was not verified. {outcome}",
            ) from exc
        return ProjectAssignmentReceipt(
            task_slug=task_slug,
            project_slug=project_slug,
            verified=True,
        )

    def set_task_goal(
        self,
        task_slug: str,
        goal_slug: str | None,
    ) -> GoalLinkReceipt:
        task = self._approved_task(task_slug)
        goal_read = self.list_goals()
        approved_goals = {goal.slug: goal for goal in goal_read.goals}
        if goal_slug is not None and goal_slug not in approved_goals:
            raise ValueError("goal is not a member of Tony's Goals")
        if task.goal is not None and task.goal not in approved_goals:
            raise ValueError("current goal is not a member of Tony's Goals")

        pre_forward = {task.goal} if task.goal else set()
        desired = {goal_slug} if goal_slug else set()
        relevant = pre_forward | desired
        pre_reverse: set[str] = set()
        for selected in [
            goal.slug for goal in goal_read.goals if goal.slug in relevant
        ]:
            raw_goal_links = self.runner.run("get_links", {"slug": selected})
            if not isinstance(raw_goal_links, list):
                raise GBrainProtocolError(
                    "goal reciprocal relationship snapshot was not a list"
                )
            if any(
                isinstance(link, Mapping)
                and link.get("from_slug") == selected
                and link.get("to_slug") == task_slug
                and link.get("link_type") == "advanced_by"
                for link in raw_goal_links
            ):
                pre_reverse.add(selected)
        involved = [
            goal.slug
            for goal in goal_read.goals
            if goal.slug in pre_reverse | desired | pre_forward
        ]

        forward_descriptor = lambda selected: {
            "from": task_slug,
            "to": selected,
            "link_type": "advances_goal",
            "context": "This task advances the linked Tony goal.",
            "link_source": "gtasks",
        }
        reverse_descriptor = lambda selected: {
            "from": selected,
            "to": task_slug,
            "link_type": "advanced_by",
            "context": "This goal is advanced by the linked GTasks task.",
            "link_source": "gtasks",
        }
        journal: list[tuple[str, dict[str, Any]]] = []

        def apply(action: str, descriptor: dict[str, Any]) -> None:
            params = dict(descriptor)
            if action == "remove_link":
                params.pop("context", None)
                params.pop("link_source", None)
            self.runner.run(action, params)
            journal.append((action, descriptor))

        def read_state() -> tuple[set[str], set[str]]:
            raw_task_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(raw_task_links, list):
                raise GBrainProtocolError(
                    "task goal relationship readback was not a list"
                )
            forward = {
                str(link["to_slug"])
                for link in raw_task_links
                if isinstance(link, Mapping)
                and link.get("from_slug") == task_slug
                and link.get("link_type") == "advances_goal"
                and isinstance(link.get("to_slug"), str)
            }
            reverse: set[str] = set()
            for selected in involved:
                raw_goal_links = self.runner.run(
                    "get_links",
                    {"slug": selected},
                )
                if not isinstance(raw_goal_links, list):
                    raise GBrainProtocolError(
                        "goal reciprocal relationship readback was not a list"
                    )
                if any(
                    isinstance(link, Mapping)
                    and link.get("from_slug") == selected
                    and link.get("to_slug") == task_slug
                    and link.get("link_type") == "advanced_by"
                    for link in raw_goal_links
                ):
                    reverse.add(selected)
            return forward, reverse

        try:
            for selected in desired - pre_forward:
                apply("add_link", forward_descriptor(selected))
            for selected in desired - pre_reverse:
                apply("add_link", reverse_descriptor(selected))
            for selected in pre_forward - desired:
                apply("remove_link", forward_descriptor(selected))
            for selected in pre_reverse - desired:
                apply("remove_link", reverse_descriptor(selected))

            final_forward, final_reverse = read_state()
            if final_forward != desired or final_reverse != desired:
                raise GBrainProtocolError(
                    "final bidirectional goal readback did not match selection"
                )
        except GBrainError as exc:
            rollback_commands_ok = True
            for action, descriptor in reversed(journal):
                inverse = "remove_link" if action == "add_link" else "add_link"
                params = dict(descriptor)
                if inverse == "remove_link":
                    params.pop("context", None)
                    params.pop("link_source", None)
                try:
                    self.runner.run(inverse, params)
                except GBrainError:
                    rollback_commands_ok = False
            rollback_verified = False
            if rollback_commands_ok:
                try:
                    rollback_forward, rollback_reverse = read_state()
                    rollback_verified = (
                        rollback_forward == pre_forward
                        and rollback_reverse == pre_reverse
                    )
                except GBrainError:
                    rollback_verified = False
            rollback_message = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified."
            )
            raise PartialMutationError(
                task_slug,
                f"Bidirectional goal relationship write failed: {exc} "
                f"{rollback_message}",
            ) from exc

        return GoalLinkReceipt(
            task_slug=task_slug,
            goal_slug=goal_slug,
            verified=True,
            reciprocal_verified=True,
            reconciled=(task.goal == goal_slug and bool(journal)),
        )
