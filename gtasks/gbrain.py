from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from .domain import (
    ACTIVE_ROOT,
    COMPLETED_ROOT,
    DomainValidationError,
    EDITABLE_TASK_STATUSES,
    GOALS_ROOT,
    Goal,
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

    def run(self, tool: str, params: dict[str, Any]) -> object:
        payload = json.dumps(params, separators=(",", ":"))
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

    def to_dict(self) -> dict[str, str]:
        return {"slug": self.slug, "message": self.message}


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
class MutationReceipt:
    slug: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "verified": self.verified}


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
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "status": self.status,
            "lifecycle_root": self.lifecycle_root,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
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


def _render_preserved_page(
    page: Mapping[str, Any],
    frontmatter: Mapping[str, Any],
) -> str:
    body = page.get("compiled_truth")
    if not isinstance(body, str):
        raise GBrainProtocolError("task page has no preserved body content")
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


class GBrainAdapter:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or SubprocessCommandRunner()

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        if root_slug not in APPROVED_ROOTS:
            raise ValueError("collection root is not approved for GTasks")
        raw_backlinks = self.runner.run("get_backlinks", {"slug": root_slug})
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("get_backlinks did not return a list")

        member_slugs: list[str] = []
        for backlink in raw_backlinks:
            if not isinstance(backlink, Mapping):
                continue
            if (
                backlink.get("to_slug") == root_slug
                and backlink.get("link_type") == "member_of"
                and isinstance(backlink.get("from_slug"), str)
            ):
                member_slugs.append(str(backlink["from_slug"]))

        tasks: list[Task] = []
        issues: list[CollectionIssue] = []
        for slug in dict.fromkeys(member_slugs):
            try:
                page = self.runner.run("get_page", {"slug": slug})
                if not isinstance(page, Mapping):
                    raise GBrainProtocolError("get_page did not return an object")
                edges = self.runner.run("get_links", {"slug": slug})
                if not isinstance(edges, list):
                    raise GBrainProtocolError("get_links did not return a list")
                task = Task.from_page(page, edges=edges)
                if task.lifecycle_root != root_slug:
                    raise DomainValidationError(
                        "page frontmatter does not match its lifecycle root edge"
                    )
                tasks.append(task)
            except (DomainValidationError, GBrainError) as exc:
                issues.append(CollectionIssue(slug=slug, message=str(exc)))

        return CollectionRead(
            root_slug=root_slug,
            tasks=tuple(tasks),
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

        goals: list[Goal] = []
        issues: list[CollectionIssue] = []
        for slug in dict.fromkeys(goal_slugs):
            try:
                page = self.runner.run("get_page", {"slug": slug})
                if not isinstance(page, Mapping):
                    raise GBrainProtocolError("goal get_page did not return an object")
                goals.append(Goal.from_page(page))
            except (DomainValidationError, GBrainError) as exc:
                issues.append(CollectionIssue(slug=slug, message=str(exc)))
        return GoalRead(goals=tuple(goals), issues=tuple(issues))

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
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_links, list):
            raise GBrainProtocolError("get_links did not return a list")
        try:
            task = Task.from_page(raw_page, edges=raw_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        existing_lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
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
                verified=True,
            )

        unfinished = status not in {"completed", "cancelled"}
        target_root = (
            ACTIVE_ROOT
            if task.lifecycle_root == COMPLETED_ROOT and unfinished
            else task.lifecycle_root
        )
        completed_at = now if status == "completed" else None

        raw_frontmatter = raw_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        frontmatter = deepcopy(dict(raw_frontmatter))
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
