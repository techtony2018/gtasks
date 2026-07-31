from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Iterable, Mapping


ACTIVE_ROOT = "collections/tonys-tasks"
COMPLETED_ROOT = "collections/tonys-completed-tasks"
GOALS_ROOT = "collections/tonys-goals"
PROJECTS_ROOT = "collections/tonys-projects"
PROPOSALS_ROOT = "collections/gtasks-proposed-work"
AGENT_SCOPES = (
    ("agents/toddy", "collections/toddys-tasks"),
    ("agents/timmy", "collections/timmys-tasks"),
    ("agents/tammy", "collections/tammys-tasks"),
)
AGENT_WORK_ROOTS = frozenset(root for _agent, root in AGENT_SCOPES)
LIFECYCLE_ROOTS = frozenset({ACTIVE_ROOT, COMPLETED_ROOT})
TASK_SCOPE_ROOTS = frozenset({*LIFECYCLE_ROOTS, *AGENT_WORK_ROOTS})
AGENT_BY_WORK_ROOT = {
    work_root: agent_slug for agent_slug, work_root in AGENT_SCOPES
}

TASK_STATUSES = frozenset(
    {"proposed", "planned", "active", "waiting", "blocked", "completed", "cancelled"}
)
EDITABLE_TASK_STATUSES = frozenset(
    {"planned", "active", "blocked", "completed", "cancelled"}
)
TASK_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
TASK_RELATIONSHIPS = frozenset(
    {"member_of", "child_of", "depends_on", "blocked_by", "advances_goal"}
)
GOAL_STATUSES = frozenset({"planned", "active", "paused", "completed", "cancelled"})
PROJECT_STATUSES = frozenset({"planned", "active", "paused", "completed", "cancelled"})
PROPOSAL_STATUSES = frozenset({"proposed", "review", "approved", "rejected"})
PROPOSAL_RECIPIENTS = frozenset({"tony", "agent"})


class DomainValidationError(ValueError):
    """Raised when a GBrain page cannot safely be treated as a GTasks task."""


@dataclass(frozen=True, slots=True)
class AgentProfile:
    slug: str
    name: str
    title: str
    summary: str
    work_root: str
    default_goal_slugs: tuple[str, ...]
    avatar_kind: str = "initials"
    avatar_value: str = ""
    chat_url: str | None = None

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        *,
        work_root: str,
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "AgentProfile":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("agents/"):
            raise DomainValidationError("agent slug must start with agents/")
        if page.get("type") != "agent":
            raise DomainValidationError(f"{slug} is not an agent page")
        title = page.get("title")
        if not isinstance(title, str) or not title.strip():
            raise DomainValidationError("agent title is required")
        name = title.strip()
        if name.lower().startswith("agent "):
            name = name[6:].strip()
        summary = page.get("compiled_truth", "")
        if not isinstance(summary, str):
            summary = ""
        frontmatter = page.get("frontmatter")
        frontmatter = frontmatter if isinstance(frontmatter, Mapping) else {}
        chat_url = frontmatter.get("chat_url")
        if chat_url is not None and (
            not isinstance(chat_url, str)
            or not chat_url.startswith(("https://", "http://127.0.0.1:"))
        ):
            raise DomainValidationError(
                "agent chat_url must be an approved HTTP(S) URL when present"
            )
        avatar = frontmatter.get("avatar")
        avatar_kind = "initials"
        name_parts = [part for part in name.split() if part]
        avatar_value = (
            "".join(part[0].upper() for part in name_parts)[:2]
            if len(name_parts) > 1
            else name[:2].upper()
        ) or "A"
        if isinstance(avatar, Mapping):
            candidate_kind = avatar.get("kind")
            candidate_value = avatar.get("value")
            if candidate_kind in {"initials", "identicon", "attachment"} and isinstance(
                candidate_value, str
            ) and candidate_value.strip():
                normalized_value = candidate_value.strip()
                if candidate_kind != "attachment" or normalized_value.startswith("/media/"):
                    avatar_kind = candidate_kind
                    avatar_value = normalized_value[:512]
        goals = tuple(
            dict.fromkeys(
                str(edge["to_slug"])
                for edge in edges
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == slug
                and edge.get("link_type") == "default_agent_for"
                and isinstance(edge.get("to_slug"), str)
                and str(edge["to_slug"]).startswith("goals/")
            )
        )
        return cls(
            slug=slug,
            name=name,
            title=title.strip(),
            summary=summary.strip(),
            work_root=work_root,
            default_goal_slugs=goals,
            avatar_kind=avatar_kind,
            avatar_value=avatar_value,
            chat_url=chat_url,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "title": self.title,
            "summary": self.summary,
            "work_root": self.work_root,
            "default_goal_slugs": list(self.default_goal_slugs),
            "avatar": {
                "kind": self.avatar_kind,
                "value": self.avatar_value,
            },
            "chat_url": self.chat_url,
        }


@dataclass(frozen=True, slots=True)
class TaskProposal:
    slug: str
    title: str
    status: str
    recipient: str
    proposing_agent: str
    rationale: str
    proposed_next_step: str
    due_day: date
    submitted_at: datetime
    updated_at: datetime
    linked_goal: str | None = None
    linked_task: str | None = None
    approved_task: str | None = None
    reviewed_at: datetime | None = None
    decision_note: str = ""
    source_kind: str = "legacy"

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "TaskProposal":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("proposals/"):
            raise DomainValidationError("proposal slug must start with proposals/")
        if page.get("type") != "task_proposal":
            raise DomainValidationError(f"{slug} is not a task proposal page")
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise DomainValidationError(f"{slug} has no frontmatter")
        title = page.get("title") or frontmatter.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise DomainValidationError(
                "proposal title must be 1 to 160 characters"
            )
        status = frontmatter.get("status")
        if status not in PROPOSAL_STATUSES:
            raise DomainValidationError("proposal status is invalid")
        recipient = frontmatter.get("recipient")
        if recipient not in PROPOSAL_RECIPIENTS:
            raise DomainValidationError("proposal recipient is invalid")
        proposing_agent = frontmatter.get("proposing_agent")
        if (
            not isinstance(proposing_agent, str)
            or proposing_agent not in {agent for agent, _root in AGENT_SCOPES}
        ):
            raise DomainValidationError(
                "proposal proposing_agent is not an approved agent"
            )
        rationale = frontmatter.get("rationale")
        next_step = frontmatter.get("proposed_next_step")
        if not isinstance(rationale, str) or not rationale.strip():
            raise DomainValidationError("proposal rationale is required")
        if not isinstance(next_step, str) or not next_step.strip():
            raise DomainValidationError(
                "proposal proposed_next_step is required"
            )
        due_day = _optional_date(frontmatter.get("due_day"), "proposal due_day")
        if due_day is None:
            raise DomainValidationError("proposal due_day is required")
        submitted_at = _optional_datetime(
            frontmatter.get("submitted_at"),
            "proposal submitted_at",
        )
        updated_at = _optional_datetime(
            frontmatter.get("updated_at"),
            "proposal updated_at",
        )
        if submitted_at is None or updated_at is None:
            raise DomainValidationError(
                "proposal submitted_at and updated_at are required"
            )
        normalized_edges = [
            edge for edge in edges if isinstance(edge, Mapping)
        ]
        if not any(
            edge.get("from_slug") == slug
            and edge.get("to_slug") == PROPOSALS_ROOT
            and edge.get("link_type") == "member_of"
            for edge in normalized_edges
        ):
            raise DomainValidationError(
                "proposal requires typed proposed-work collection membership"
            )
        if not any(
            edge.get("from_slug") == slug
            and edge.get("to_slug") == proposing_agent
            and edge.get("link_type") == "proposed_by"
            for edge in normalized_edges
        ):
            raise DomainValidationError(
                "proposal requires typed proposed_by agent relationship"
            )

        def one_target(link_type: str, prefix: str) -> str | None:
            targets = tuple(
                dict.fromkeys(
                    str(edge["to_slug"])
                    for edge in normalized_edges
                    if edge.get("from_slug") == slug
                    and edge.get("link_type") == link_type
                    and isinstance(edge.get("to_slug"), str)
                    and str(edge["to_slug"]).startswith(prefix)
                )
            )
            if len(targets) > 1:
                raise DomainValidationError(
                    f"proposal has multiple {link_type} relationships"
                )
            return targets[0] if targets else None

        linked_goal = one_target("serves_goal", "goals/")
        linked_task = one_target("proposes_for_task", "tasks/")
        approved_task = one_target("approved_as", "tasks/")
        if linked_goal is None and linked_task is None:
            raise DomainValidationError(
                "proposal must link to a goal or Tony task"
            )
        if status == "approved" and approved_task is None:
            raise DomainValidationError(
                "approved proposal requires an approved_as task relationship"
            )
        reviewed_at = _optional_datetime(
            frontmatter.get("reviewed_at"),
            "proposal reviewed_at",
        )
        decision_note = frontmatter.get("decision_note", "")
        if not isinstance(decision_note, str):
            raise DomainValidationError("proposal decision_note must be text")
        return cls(
            slug=slug,
            title=title.strip(),
            status=status,
            recipient=recipient,
            proposing_agent=proposing_agent,
            rationale=rationale.strip(),
            proposed_next_step=next_step.strip(),
            due_day=due_day,
            submitted_at=submitted_at,
            updated_at=updated_at,
            linked_goal=linked_goal,
            linked_task=linked_task,
            approved_task=approved_task,
            reviewed_at=reviewed_at,
            decision_note=decision_note.strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "status": self.status,
            "recipient": self.recipient,
            "proposing_agent": self.proposing_agent,
            "rationale": self.rationale,
            "proposed_next_step": self.proposed_next_step,
            "due_day": self.due_day.isoformat(),
            "submitted_at": self.submitted_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "linked_goal": self.linked_goal,
            "linked_task": self.linked_task,
            "approved_task": self.approved_task,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "decision_note": self.decision_note,
            "source_kind": self.source_kind,
        }


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
class ProgressMetric:
    kind: str
    label: str | None
    unit: str
    target: int
    current: int
    event_binding: str | None
    auto_complete: bool
    task_day: date | None
    timezone: str | None

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "ProgressMetric":
        if not isinstance(value, Mapping):
            raise DomainValidationError("progress_metric must be an object")
        if value.get("kind") != "count":
            raise DomainValidationError("progress metric kind must be count")
        label = value.get("label")
        if label is not None and (
            not isinstance(label, str) or not label.strip()
        ):
            raise DomainValidationError(
                "progress metric label must be a nonempty string when present"
            )
        if isinstance(label, str) and len(label.strip()) > 160:
            raise DomainValidationError(
                "progress metric label must be 160 characters or fewer"
            )
        unit = value.get("unit")
        if (
            not isinstance(unit, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", unit)
        ):
            raise DomainValidationError(
                "progress metric unit must be a lowercase machine label"
            )
        target = value.get("target")
        if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
            raise DomainValidationError(
                "progress metric target must be a positive whole number"
            )
        current = value.get("current")
        if (
            isinstance(current, bool)
            or not isinstance(current, int)
            or current < 0
            or current > target
        ):
            raise DomainValidationError(
                "progress metric current must be between 0 and target"
            )
        event_binding = value.get("event_binding")
        if event_binding is not None and (
            not isinstance(event_binding, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event_binding)
        ):
            raise DomainValidationError(
                "progress metric event binding must be a lowercase event type"
            )
        auto_complete = value.get("auto_complete")
        if not isinstance(auto_complete, bool):
            raise DomainValidationError(
                "progress metric auto_complete must be true or false"
            )
        if auto_complete and event_binding is None:
            raise DomainValidationError(
                "progress metric automatic completion requires an event binding"
            )
        task_day = _optional_date(value.get("task_day"), "progress metric task_day")
        timezone_name = value.get("timezone")
        if event_binding is None:
            if task_day is not None or timezone_name not in (None, "", "none"):
                raise DomainValidationError(
                    "manual progress metrics cannot declare event timing"
                )
            timezone_name = None
        else:
            if task_day is None:
                raise DomainValidationError(
                    "event-bound progress metric task_day is required"
                )
            if timezone_name != "America/Los_Angeles":
                raise DomainValidationError(
                    "event-bound progress metric timezone must be America/Los_Angeles"
                )
            if event_binding == "job_applied" and (
                unit != "job_application"
                or target != 5
                or not auto_complete
            ):
                raise DomainValidationError(
                    "job_applied requires unit job_application, target 5, "
                    "and automatic completion"
                )
        return cls(
            kind="count",
            label=label.strip() if isinstance(label, str) else None,
            unit=unit,
            target=target,
            current=current,
            event_binding=event_binding,
            auto_complete=auto_complete,
            task_day=task_day,
            timezone=timezone_name,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "unit": self.unit,
            "target": self.target,
            "current": self.current,
            "event_binding": self.event_binding,
            "auto_complete": self.auto_complete,
            "task_day": self.task_day.isoformat() if self.task_day else None,
            "timezone": self.timezone,
        }


@dataclass(frozen=True, slots=True)
class EventProgress:
    evidence_slugs: tuple[str, ...] = ()
    receipt_ids: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "EventProgress":
        if not isinstance(value, Mapping):
            raise DomainValidationError("event_progress must be an object")
        evidence_slugs = value.get("evidence_slugs")
        receipt_ids = value.get("receipt_ids")
        for field_name, raw_items in (
            ("evidence_slugs", evidence_slugs),
            ("receipt_ids", receipt_ids),
        ):
            if (
                not isinstance(raw_items, list)
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in raw_items
                )
                or len(set(raw_items)) != len(raw_items)
            ):
                raise DomainValidationError(
                    f"event progress {field_name} must contain unique text values"
                )
        if len(evidence_slugs) != len(receipt_ids):
            raise DomainValidationError(
                "event progress evidence and receipts must stay paired"
            )
        return cls(
            evidence_slugs=tuple(evidence_slugs),
            receipt_ids=tuple(receipt_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_slugs": list(self.evidence_slugs),
            "receipt_ids": list(self.receipt_ids),
        }


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
    owner_agent: str | None = None
    project: str | None = None
    parent: str | None = None
    dependencies: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    goal: str | None = None
    progress_metric: ProgressMetric | None = None
    event_progress: EventProgress | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    proposal_recipient: str | None = None
    proposal_submitted_at: datetime | None = None
    proposal_decision_note: str = ""

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
            if link["type"] == "member_of" and link["to"] in TASK_SCOPE_ROOTS
        ]
        if len(lifecycle_roots) != 1:
            raise DomainValidationError(
                "task must belong to exactly one GTasks lifecycle root or "
                "agent work scope"
            )
        lifecycle_root = lifecycle_roots[0]
        if (
            lifecycle_root in LIFECYCLE_ROOTS
            and status not in {"completed", "cancelled"}
            and lifecycle_root != ACTIVE_ROOT
        ):
            raise DomainValidationError("unfinished task must belong to the active lifecycle root")
        if status == "proposed" and lifecycle_root not in AGENT_WORK_ROOTS:
            raise DomainValidationError("proposed tasks must remain in an agent work collection")

        project_links = [
            link["to"]
            for link in links
            if link["type"] == "member_of" and link["to"] not in TASK_SCOPE_ROOTS
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

        assigned_agents = tuple(
            dict.fromkeys(
                str(edge["to_slug"])
                for edge in edges
                if edge.get("from_slug") == slug
                and edge.get("link_type") == "assigned_to"
                and isinstance(edge.get("to_slug"), str)
                and str(edge["to_slug"]).startswith("agents/")
            )
        )
        expected_agent = AGENT_BY_WORK_ROOT.get(lifecycle_root)
        if expected_agent is not None:
            if assigned_agents != (expected_agent,):
                raise DomainValidationError(
                    "agent task requires exactly one assigned_to relationship "
                    "matching its work collection"
                )
            owner_agent = expected_agent
        else:
            if assigned_agents:
                raise DomainValidationError(
                    "Tony task cannot retain an agent assigned_to relationship"
                )
            owner_agent = None

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
        raw_progress_metric = frontmatter.get("progress_metric")
        progress_metric = (
            None
            if raw_progress_metric is None
            else ProgressMetric.from_value(raw_progress_metric)
        )
        raw_event_progress = frontmatter.get("event_progress")
        event_progress = (
            None
            if raw_event_progress is None
            else EventProgress.from_value(raw_event_progress)
        )
        if progress_metric is None and event_progress is not None:
            raise DomainValidationError(
                "event progress requires a configured progress metric"
            )
        if progress_metric and progress_metric.event_binding:
            if event_progress is None:
                raise DomainValidationError(
                    "event-bound progress metric requires event_progress"
                )
            if progress_metric.current != len(event_progress.receipt_ids):
                raise DomainValidationError(
                    "event-bound metric current must match unique event evidence"
                )
        elif event_progress is not None:
            raise DomainValidationError(
                "manual progress metric cannot contain event progress"
            )

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
            owner_agent=owner_agent,
            project=project,
            parent=parents[0] if parents else None,
            dependencies=dependencies,
            blockers=blockers,
            goal=goals[0] if goals else None,
            progress_metric=progress_metric,
            event_progress=event_progress,
            completed_at=_optional_datetime(
                frontmatter.get("completed_at"), "completed_at"
            ),
            created_at=_optional_datetime(frontmatter.get("created_at"), "created_at"),
            updated_at=_optional_datetime(frontmatter.get("updated_at"), "updated_at"),
            proposal_recipient=(
                frontmatter.get("proposal_recipient")
                if frontmatter.get("proposal_recipient") in {"tony", "agent"}
                else None
            ),
            proposal_submitted_at=_optional_datetime(
                frontmatter.get("proposal_submitted_at"), "proposal_submitted_at"
            ),
            proposal_decision_note=(
                frontmatter.get("proposal_decision_note", "").strip()
                if isinstance(frontmatter.get("proposal_decision_note", ""), str)
                else ""
            ),
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
            "owner_agent": self.owner_agent,
            "project": self.project,
            "parent": self.parent,
            "dependencies": list(self.dependencies),
            "blockers": list(self.blockers),
            "goal": self.goal,
            "progress_metric": (
                self.progress_metric.to_dict() if self.progress_metric else None
            ),
            "event_progress": (
                self.event_progress.to_dict() if self.event_progress else None
            ),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "proposal_recipient": self.proposal_recipient,
            "proposal_submitted_at": self.proposal_submitted_at.isoformat() if self.proposal_submitted_at else None,
            "proposal_decision_note": self.proposal_decision_note,
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


def new_task(
    *,
    title: str,
    now: datetime,
    identity: str,
    due_day: date | None = None,
    detail: str = "",
    priority: str = "normal",
    next_action: str = "",
    project: str | None = None,
    goal: str | None = None,
    progress_metric: ProgressMetric | None = None,
    event_progress: EventProgress | None = None,
) -> Task:
    summary = title.strip()
    if not summary:
        raise DomainValidationError("title is required")
    if len(summary) > 160:
        raise DomainValidationError("title must be 160 characters or fewer")
    if not isinstance(detail, str):
        raise DomainValidationError("detail must be text")
    if priority not in TASK_PRIORITIES:
        raise DomainValidationError(
            f"priority must be one of {', '.join(sorted(TASK_PRIORITIES))}"
        )
    if not isinstance(next_action, str):
        raise DomainValidationError("next_action must be text")
    normalized_action = next_action.strip()
    if len(normalized_action) > 240 or "\n" in normalized_action:
        raise DomainValidationError(
            "next_action must be one concise line of 240 characters or fewer"
        )
    if project is not None and (
        not isinstance(project, str) or not project.startswith("projects/")
    ):
        raise DomainValidationError("project must be a project slug or none")
    if goal is not None and (
        not isinstance(goal, str) or not goal.startswith("goals/")
    ):
        raise DomainValidationError("goal must be a goal slug or none")
    if progress_metric and progress_metric.event_binding:
        event_progress = event_progress or EventProgress()
        if progress_metric.current != len(event_progress.receipt_ids):
            raise DomainValidationError(
                "event-bound metric current must match unique event evidence"
            )
    elif event_progress is not None:
        raise DomainValidationError(
            "event progress requires an event-bound progress metric"
        )
    safe_identity = re.sub(r"[^a-z0-9]", "", identity.lower())[:12]
    if len(safe_identity) < 6:
        raise DomainValidationError(
            "identity must contain at least 6 letters or numbers"
        )

    local_day = now.date()
    return Task(
        slug=(
            f"tasks/{local_day.year}/"
            f"{local_day.isoformat()}-{_slugify_title(summary)}-{safe_identity}"
        ),
        title=summary,
        summary=summary,
        detail=detail.strip(),
        status="planned",
        priority=priority,
        next_action=normalized_action,
        due_day=due_day or local_day,
        due_at=None,
        scheduled_day=None,
        inbox=True,
        lifecycle_root=ACTIVE_ROOT,
        project=project,
        goal=goal,
        progress_metric=progress_metric,
        event_progress=event_progress,
        created_at=now,
        updated_at=now,
    )


def duplicate_task(
    source: Task,
    *,
    due_day: date,
    now: datetime,
    identity: str,
) -> Task:
    metric = (
        replace(
            source.progress_metric,
            current=0,
            task_day=due_day if source.progress_metric.event_binding else None,
        )
        if source.progress_metric
        else None
    )
    return new_task(
        title=source.title,
        detail=source.detail,
        priority=source.priority,
        next_action=source.next_action,
        due_day=due_day,
        project=source.project,
        goal=source.goal,
        progress_metric=metric,
        event_progress=EventProgress() if metric and metric.event_binding else None,
        now=now,
        identity=identity,
    )


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
    advanced_by: tuple[str, ...] = ()

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "Goal":
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
        advanced_by = tuple(
            dict.fromkeys(
                str(edge["to_slug"])
                for edge in edges
                if edge.get("from_slug") == slug
                and edge.get("link_type") == "advanced_by"
                and isinstance(edge.get("to_slug"), str)
                and str(edge["to_slug"]).startswith("tasks/")
            )
        )

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
            advanced_by=advanced_by,
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
            "advanced_by": list(self.advanced_by),
        }


@dataclass(frozen=True, slots=True)
class Project:
    slug: str
    title: str
    status: str
    summary: str
    supporting_goal_slugs: tuple[str, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "Project":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("projects/"):
            raise DomainValidationError("project slug must start with projects/")
        if slug == PROJECTS_ROOT or page.get("type") != "project":
            raise DomainValidationError(f"{slug} is not a project page")
        title = page.get("title")
        if not isinstance(title, str) or not title.strip():
            raise DomainValidationError("project title is required")
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise DomainValidationError(f"{slug} has no frontmatter")
        links = _links_from(frontmatter)
        frontmatter_member = any(
            link["to"] == PROJECTS_ROOT and link["type"] == "member_of"
            for link in links
        )
        graph_member = any(
            edge.get("from_slug") == slug
            and edge.get("to_slug") == PROJECTS_ROOT
            and edge.get("link_type") == "member_of"
            for edge in edges
        )
        if not (frontmatter_member or graph_member):
            raise DomainValidationError(
                f"project must belong to {PROJECTS_ROOT}"
            )
        status = frontmatter.get("status", "active")
        if status not in PROJECT_STATUSES:
            raise DomainValidationError(
                f"project status must be one of {', '.join(sorted(PROJECT_STATUSES))}"
            )
        summary = frontmatter.get("summary", title)
        if not isinstance(summary, str):
            raise DomainValidationError("project summary must be text")
        supporting_goals = tuple(
            dict.fromkeys(
                str(edge["to_slug"])
                for edge in edges
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == slug
                and edge.get("link_type") == "supports_goal"
                and isinstance(edge.get("to_slug"), str)
                and str(edge["to_slug"]).startswith("goals/")
            )
        )
        return cls(
            slug=slug,
            title=title.strip(),
            status=status,
            summary=summary.strip() or title.strip(),
            supporting_goal_slugs=supporting_goals,
            created_at=_optional_datetime(frontmatter.get("created_at"), "created_at"),
            updated_at=_optional_datetime(frontmatter.get("updated_at"), "updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "supporting_goal_slugs": list(self.supporting_goal_slugs),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
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


def new_project(
    title: str,
    now: datetime,
    identity: str,
    supporting_goal_slugs: tuple[str, ...] = (),
) -> Project:
    clean_title = title.strip()
    if not clean_title:
        raise DomainValidationError("project title is required")
    if len(clean_title) > 160:
        raise DomainValidationError("project title must be 160 characters or fewer")
    safe_identity = re.sub(r"[^a-z0-9]", "", identity.lower())[:12]
    if len(safe_identity) < 6:
        raise DomainValidationError("identity must contain at least 6 letters or numbers")
    return Project(
        slug=f"projects/{_slugify_title(clean_title)}-{safe_identity}",
        title=clean_title,
        status="active",
        summary=clean_title,
        supporting_goal_slugs=tuple(dict.fromkeys(supporting_goal_slugs)),
        created_at=now,
        updated_at=now,
    )


def new_goal(
    *,
    title: str,
    outcome: str,
    success_criteria: str,
    strategy: str,
    review_cadence: str,
    constraints: str,
    now: datetime,
    identity: str,
    target_day: date | None = None,
) -> Goal:
    values = {
        "title": title.strip(),
        "outcome": outcome.strip(),
        "success_criteria": success_criteria.strip(),
        "strategy": strategy.strip(),
        "review_cadence": review_cadence.strip(),
        "constraints": constraints.strip(),
    }
    for field_name, value in values.items():
        if not value:
            raise DomainValidationError(f"goal {field_name} is required")
    if len(values["title"]) > 160:
        raise DomainValidationError("goal title must be 160 characters or fewer")
    safe_identity = re.sub(r"[^a-z0-9]", "", identity.lower())[:12]
    if len(safe_identity) < 6:
        raise DomainValidationError("identity must contain at least 6 letters or numbers")
    return Goal(
        slug=f"goals/{_slugify_title(values['title'])}-{safe_identity}",
        title=values["title"],
        status="planned",
        outcome=values["outcome"],
        success_criteria=values["success_criteria"],
        target_day=target_day or default_goal_target_day(now.date()),
        strategy=values["strategy"],
        review_cadence=values["review_cadence"],
        constraints=values["constraints"],
    )
