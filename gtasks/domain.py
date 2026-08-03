from __future__ import annotations

import re
import unicodedata
import uuid
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit

from .handoff import DomainValidationError, TaskHandoff, validate_task_handoff


ACTIVE_ROOT = "collections/tonys-tasks"
COMPLETED_ROOT = "collections/tonys-completed-tasks"
GOALS_ROOT = "collections/tonys-goals"
PROJECTS_ROOT = "collections/tonys-projects"
PROPOSALS_ROOT = "collections/gtasks-proposed-work"
SYSTEM_TICKETS_ROOT = "collections/mission-control-system-tickets"
QA_FIXTURES_ROOT = "collections/mission-control-qa-fixtures"
ARTIFACTS_ROOT = "collections/mission-control-artifacts"
ARTIFACT_AGENT_SCOPES = (
    ("agents/tammy", "collections/tammys-artifacts"),
    ("agents/timmy", "collections/timmys-artifacts"),
    ("agents/toddy", "collections/toddys-artifacts"),
)
ARTIFACT_BY_AGENT = dict(ARTIFACT_AGENT_SCOPES)
ARTIFACT_BY_COLLECTION = {
    collection: agent for agent, collection in ARTIFACT_AGENT_SCOPES
}
ARTIFACT_KINDS = frozenset({"markdown", "image", "pdf", "git", "file"})
AGENT_SCOPES = (
    ("agents/toddy", "collections/toddys-tasks"),
    ("agents/timmy", "collections/timmys-tasks"),
    ("agents/tammy", "collections/tammys-tasks"),
)
AGENT_WORK_ROOTS = frozenset(root for _agent, root in AGENT_SCOPES)
LIFECYCLE_ROOTS = frozenset({ACTIVE_ROOT, COMPLETED_ROOT})
TASK_SCOPE_ROOTS = frozenset({*LIFECYCLE_ROOTS, *AGENT_WORK_ROOTS, QA_FIXTURES_ROOT})
AGENT_BY_WORK_ROOT = {
    work_root: agent_slug for agent_slug, work_root in AGENT_SCOPES
}

TASK_STATUSES = frozenset(
    {"proposed", "planned", "active", "blocked", "completed", "cancelled"}
)
EDITABLE_TASK_STATUSES = frozenset(
    {"planned", "active", "blocked", "completed", "cancelled"}
)
TASK_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
SYSTEM_TICKET_STATUSES = frozenset({
    "planned", "active", "blocked", "completed", "cancelled",
})
SYSTEM_TICKET_TARGETS = frozenset({
    "mission_control", "memory_stargraph", "career_path", "unknown",
})
TASK_RELATIONSHIPS = frozenset(
    {"member_of", "child_of", "depends_on", "blocked_by", "advances_goal"}
)
GOAL_STATUSES = frozenset({"planned", "active", "paused", "completed", "cancelled"})
PROJECT_STATUSES = frozenset({"planned", "active", "paused", "completed", "cancelled"})
PROPOSAL_STATUSES = frozenset({"proposed", "review", "approved", "rejected"})
PROPOSAL_RECIPIENTS = frozenset({"tony", "agent"})
TODO_STATUSES = frozenset({"not_done", "done"})
TODO_KINDS = frozenset({"action", "question", "blocker"})
TODO_EVENT_TYPES = frozenset(
    {"created", "edited", "status_changed", "comment_added", "legacy_migrated"}
)


@dataclass(frozen=True, slots=True)
class NextActionHistoryEntry:
    action: str
    completed_at: datetime

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "NextActionHistoryEntry":
        if not isinstance(value, Mapping):
            raise DomainValidationError("next_action_history entries must be objects")
        action = value.get("action")
        if (
            not isinstance(action, str)
            or not action.strip()
            or len(action.strip()) > 240
            or "\n" in action
            or "\r" in action
        ):
            raise DomainValidationError(
                "next_action_history action must be one concise line of 240 characters or fewer"
            )
        completed_at = _optional_datetime(
            value.get("completed_at"),
            "next_action_history completed_at",
        )
        if completed_at is None or completed_at.tzinfo is None:
            raise DomainValidationError(
                "next_action_history completed_at must include a timezone"
            )
        return cls(action=action.strip(), completed_at=completed_at)

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "completed_at": self.completed_at.isoformat(),
        }


def _child_record_parent(
    slug: str,
    frontmatter: Mapping[str, Any],
    edges: Iterable[Mapping[str, Any]],
    *,
    field: str,
    link_type: str,
    parent_prefix: str,
) -> str:
    declared = frontmatter.get(field)
    if not isinstance(declared, str) or not declared.startswith(parent_prefix):
        raise DomainValidationError(f"{field} must be a canonical {parent_prefix} slug")
    parents = tuple(
        dict.fromkeys(
            str(edge["to_slug"])
            for edge in edges
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == slug
            and edge.get("link_type") == link_type
            and isinstance(edge.get("to_slug"), str)
        )
    )
    parents = tuple(
        dict.fromkeys(
            (*parents, *(
                link["to"]
                for link in _links_from(frontmatter)
                if link["type"] == link_type
            ))
        )
    )
    if len(parents) != 1 or parents[0] != declared:
        raise DomainValidationError(
            f"{slug} requires exactly one {link_type} relationship matching {field}"
        )
    return declared


def _required_zoned_datetime(value: Any, field: str) -> datetime:
    parsed = _optional_datetime(value, field)
    if parsed is None or parsed.tzinfo is None:
        raise DomainValidationError(f"{field} must include a timezone")
    return parsed


def _required_text(
    value: Any,
    field: str,
    *,
    maximum: int,
    single_line: bool = False,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field} is required")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DomainValidationError(f"{field} must be {maximum} characters or fewer")
    if single_line and ("\n" in normalized or "\r" in normalized):
        raise DomainValidationError(f"{field} must be one line")
    return normalized


@dataclass(frozen=True, slots=True)
class TodoComment:
    slug: str
    todo_slug: str
    body: str
    author: str | None
    source: str
    created_at: datetime
    idempotency_key: str

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "TodoComment":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("todo-comments/"):
            raise DomainValidationError("todo comment slug must start with todo-comments/")
        if page.get("type") != "todo_comment":
            raise DomainValidationError(f"{slug} is not a todo_comment page")
        frontmatter = _compiled_frontmatter(page)
        todo_slug = _child_record_parent(
            slug,
            frontmatter,
            edges,
            field="todo_slug",
            link_type="comment_on",
            parent_prefix="todos/",
        )
        author = frontmatter.get("author")
        if author is not None and (not isinstance(author, str) or not author.strip()):
            raise DomainValidationError("todo comment author must be text or null")
        source = _required_text(frontmatter.get("source"), "todo comment source", maximum=120)
        return cls(
            slug=slug,
            todo_slug=todo_slug,
            body=_required_text(frontmatter.get("body"), "todo comment body", maximum=4000),
            author=author.strip() if isinstance(author, str) else None,
            source=source,
            created_at=_required_zoned_datetime(
                frontmatter.get("created_at"), "todo comment created_at"
            ),
            idempotency_key=_required_text(
                frontmatter.get("idempotency_key"),
                "todo comment idempotency_key",
                maximum=200,
                single_line=True,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "todo_slug": self.todo_slug,
            "body": self.body,
            "author": self.author,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class TodoEvent:
    slug: str
    todo_slug: str
    event_type: str
    actor: str | None
    source: str
    occurred_at: datetime
    idempotency_key: str
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    comment_slug: str | None = None

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "TodoEvent":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("todo-events/"):
            raise DomainValidationError("todo event slug must start with todo-events/")
        if page.get("type") != "todo_event":
            raise DomainValidationError(f"{slug} is not a todo_event page")
        frontmatter = _compiled_frontmatter(page)
        todo_slug = _child_record_parent(
            slug,
            frontmatter,
            edges,
            field="todo_slug",
            link_type="event_for",
            parent_prefix="todos/",
        )
        event_type = frontmatter.get("event_type")
        if event_type not in TODO_EVENT_TYPES:
            raise DomainValidationError("todo event_type is invalid")
        actor = frontmatter.get("actor")
        if actor is not None and (not isinstance(actor, str) or not actor.strip()):
            raise DomainValidationError("todo event actor must be text or null")
        before = frontmatter.get("before")
        after = frontmatter.get("after")
        if before is not None and not isinstance(before, Mapping):
            raise DomainValidationError("todo event before must be an object or null")
        if after is not None and not isinstance(after, Mapping):
            raise DomainValidationError("todo event after must be an object or null")
        comment_slug = frontmatter.get("comment_slug")
        if comment_slug is not None and (
            not isinstance(comment_slug, str)
            or not comment_slug.startswith("todo-comments/")
        ):
            raise DomainValidationError("todo event comment_slug is invalid")
        return cls(
            slug=slug,
            todo_slug=todo_slug,
            event_type=str(event_type),
            actor=actor.strip() if isinstance(actor, str) else None,
            source=_required_text(frontmatter.get("source"), "todo event source", maximum=120),
            occurred_at=_required_zoned_datetime(
                frontmatter.get("occurred_at"), "todo event occurred_at"
            ),
            idempotency_key=_required_text(
                frontmatter.get("idempotency_key"),
                "todo event idempotency_key",
                maximum=200,
                single_line=True,
            ),
            before=dict(before) if isinstance(before, Mapping) else None,
            after=dict(after) if isinstance(after, Mapping) else None,
            comment_slug=comment_slug,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "todo_slug": self.todo_slug,
            "event_type": self.event_type,
            "actor": self.actor,
            "source": self.source,
            "occurred_at": self.occurred_at.isoformat(),
            "idempotency_key": self.idempotency_key,
            "before": dict(self.before) if self.before is not None else None,
            "after": dict(self.after) if self.after is not None else None,
            "comment_slug": self.comment_slug,
        }


@dataclass(frozen=True, slots=True)
class TodoItem:
    slug: str
    parent_task: str
    text: str
    detail: str
    status: str
    kind: str
    created_at: datetime
    updated_at: datetime
    creator: str | None
    source: str
    comment_slugs: tuple[str, ...] = ()
    event_slugs: tuple[str, ...] = ()
    comments: tuple[TodoComment, ...] = ()
    events: tuple[TodoEvent, ...] = ()
    legacy_provenance: Mapping[str, Any] | None = None

    @property
    def status_label(self) -> str:
        return "Done" if self.status == "done" else "Not Done"

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
        *,
        comments: Iterable[TodoComment] = (),
        events: Iterable[TodoEvent] = (),
    ) -> "TodoItem":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("todos/"):
            raise DomainValidationError("todo slug must start with todos/")
        if page.get("type") != "todo":
            raise DomainValidationError(f"{slug} is not a todo page")
        frontmatter = _compiled_frontmatter(page)
        parent_task = _child_record_parent(
            slug,
            frontmatter,
            edges,
            field="parent_task",
            link_type="todo_for",
            parent_prefix="tasks/",
        )
        status = frontmatter.get("status")
        if status not in TODO_STATUSES:
            raise DomainValidationError("todo status must be not_done or done")
        kind = frontmatter.get("kind", "action")
        if kind not in TODO_KINDS:
            raise DomainValidationError("todo kind is invalid")
        detail = frontmatter.get("detail", "")
        if not isinstance(detail, str) or len(detail.strip()) > 5000:
            raise DomainValidationError("todo detail must be text up to 5000 characters")
        created_at = _required_zoned_datetime(
            frontmatter.get("created_at"), "todo created_at"
        )
        updated_at = _required_zoned_datetime(
            frontmatter.get("updated_at"), "todo updated_at"
        )
        if updated_at < created_at:
            raise DomainValidationError("todo updated_at cannot precede created_at")
        creator = frontmatter.get("creator")
        if creator is not None and (
            not isinstance(creator, str) or not creator.strip()
        ):
            raise DomainValidationError("todo creator must be text or null")
        def slug_list(field: str, prefix: str) -> tuple[str, ...]:
            raw = frontmatter.get(field, [])
            if raw is None:
                raw = []
            if (
                not isinstance(raw, list)
                or any(not isinstance(value, str) or not value.startswith(prefix) for value in raw)
                or len(set(raw)) != len(raw)
            ):
                raise DomainValidationError(f"todo {field} contains invalid identities")
            return tuple(raw)
        comment_slugs = slug_list("comment_slugs", "todo-comments/")
        event_slugs = slug_list("event_slugs", "todo-events/")
        parsed_comments = tuple(comments)
        parsed_events = tuple(events)
        if parsed_comments and tuple(item.slug for item in parsed_comments) != comment_slugs:
            raise DomainValidationError("todo comment references do not match readback")
        if parsed_events and tuple(item.slug for item in parsed_events) != event_slugs:
            raise DomainValidationError("todo event references do not match readback")
        if any(item.todo_slug != slug for item in (*parsed_comments, *parsed_events)):
            raise DomainValidationError("todo child record references another item")
        if any(
            left.created_at > right.created_at
            for left, right in zip(parsed_comments, parsed_comments[1:])
        ):
            raise DomainValidationError("todo comments must be in deterministic order")
        if any(
            left.occurred_at > right.occurred_at
            for left, right in zip(parsed_events, parsed_events[1:])
        ):
            raise DomainValidationError("todo events must be in deterministic order")
        legacy = frontmatter.get("legacy_provenance")
        if legacy is not None and not isinstance(legacy, Mapping):
            raise DomainValidationError("todo legacy_provenance must be an object or null")
        return cls(
            slug=slug,
            parent_task=parent_task,
            text=_required_text(
                frontmatter.get("text"), "todo text", maximum=240, single_line=True
            ),
            detail=detail.strip(),
            status=str(status),
            kind=str(kind),
            created_at=created_at,
            updated_at=updated_at,
            creator=creator.strip() if isinstance(creator, str) else None,
            source=_required_text(frontmatter.get("source"), "todo source", maximum=120),
            comment_slugs=comment_slugs,
            event_slugs=event_slugs,
            comments=parsed_comments,
            events=parsed_events,
            legacy_provenance=dict(legacy) if isinstance(legacy, Mapping) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "parent_task": self.parent_task,
            "text": self.text,
            "detail": self.detail,
            "status": self.status,
            "status_label": self.status_label,
            "kind": self.kind,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "creator": self.creator,
            "source": self.source,
            "comment_slugs": list(self.comment_slugs),
            "event_slugs": list(self.event_slugs),
            "comments": [comment.to_dict() for comment in self.comments],
            "events": [event.to_dict() for event in self.events],
            "legacy_provenance": (
                dict(self.legacy_provenance)
                if self.legacy_provenance is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class SystemTicket:
    """A normal canonical task scoped only to Mission Control System Tickets."""

    slug: str
    title: str
    status: str
    verbatim_request: str
    target_subsystem: str
    priority: str
    acceptance_criteria: str = ""
    linked_evidence: tuple[str, ...] = ()
    implementation_receipts: tuple[str, ...] = ()
    qa_receipts: tuple[str, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_page(cls, page: Mapping[str, Any], edges: Iterable[Mapping[str, Any]] = ()) -> "SystemTicket":
        slug = page.get("slug")
        if not isinstance(slug, str) or not slug.startswith("tasks/"):
            raise DomainValidationError("system ticket slug must start with tasks/")
        if page.get("type") != "task":
            raise DomainValidationError(f"{slug} is not a task page")
        frontmatter = _compiled_frontmatter(page)
        if not frontmatter:
            raise DomainValidationError(f"{slug} has no canonical frontmatter")
        links = _links_from(frontmatter)
        typed_member = any(link["to"] == SYSTEM_TICKETS_ROOT and link["type"] == "member_of" for link in links) or any(
            isinstance(edge, Mapping) and edge.get("from_slug") == slug and edge.get("to_slug") == SYSTEM_TICKETS_ROOT and edge.get("link_type") == "member_of" for edge in edges)
        if not typed_member:
            raise DomainValidationError("system ticket requires typed System Tickets membership")
        # GBrain's raw row title can be a stale storage label.  The compiled
        # frontmatter is the canonical display contract, just as it is for
        # Goals, so a title edit remains visible after a readback.
        title = frontmatter.get("title") or page.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise DomainValidationError("system ticket title must be 1 to 160 characters")
        # The first manually captured System Ticket predates the explicit
        # verbatim_request field.  Its task detail is already Tony's original
        # request, so read it without rewriting or inventing new content.
        request = (
            frontmatter.get("verbatim_request")
            or frontmatter.get("detail")
            or frontmatter.get("summary")
        )
        if not isinstance(request, str) or not request.strip():
            raise DomainValidationError("system ticket verbatim_request is required")
        status = frontmatter.get("status")
        if status not in SYSTEM_TICKET_STATUSES:
            raise DomainValidationError("system ticket status is invalid")
        target = frontmatter.get("target_subsystem", "unknown")
        # Compatibility for the pre-UI ticket capture.  New Ticket emits the
        # stable enum, while this narrow map keeps the existing calendar
        # request visible and dispatchable without a live migration.
        if target == "mission-control-calendar":
            target = "mission_control"
        if target not in SYSTEM_TICKET_TARGETS:
            raise DomainValidationError("system ticket target_subsystem is invalid")
        priority = frontmatter.get("priority", "normal")
        if priority not in TASK_PRIORITIES:
            raise DomainValidationError("system ticket priority is invalid")
        def strings(field: str) -> tuple[str, ...]:
            value = frontmatter.get(field, [])
            if value is None: return ()
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise DomainValidationError(f"system ticket {field} must be a list of text values")
            return tuple(dict.fromkeys(item.strip() for item in value))
        criteria = frontmatter.get("acceptance_criteria", "")
        if not isinstance(criteria, str):
            raise DomainValidationError("system ticket acceptance_criteria must be text")
        return cls(slug, title.strip(), status, request.strip(), target, priority, criteria.strip(), strings("linked_evidence"), strings("implementation_receipts"), strings("qa_receipts"), _optional_datetime(frontmatter.get("created_at") or page.get("created_at"), "created_at"), _optional_datetime(frontmatter.get("updated_at") or page.get("updated_at"), "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {"slug":self.slug,"title":self.title,"status":self.status,"verbatim_request":self.verbatim_request,"target_subsystem":self.target_subsystem,"priority":self.priority,"acceptance_criteria":self.acceptance_criteria,"linked_evidence":list(self.linked_evidence),"implementation_receipts":list(self.implementation_receipts),"qa_receipts":list(self.qa_receipts),"created_at":self.created_at.isoformat() if self.created_at else None,"updated_at":self.updated_at.isoformat() if self.updated_at else None}


def _artifact_frontmatter_targets(
    slug: str,
    frontmatter: Mapping[str, Any],
    link_type: str,
) -> tuple[str, ...]:
    return tuple(
        link["to"] for link in _links_from(frontmatter) if link["type"] == link_type
    )


def _artifact_graph_targets(
    slug: str,
    edges: Iterable[Mapping[str, Any]],
    link_type: str,
) -> tuple[str, ...]:
    return tuple(
        str(edge["to_slug"])
        for edge in edges
        if isinstance(edge, Mapping)
        and edge.get("from_slug") == slug
        and edge.get("link_type") == link_type
        and isinstance(edge.get("to_slug"), str)
    )


def _artifact_uuid_slug(value: Any, field: str) -> str:
    if not isinstance(value, str) or "/" not in value:
        raise DomainValidationError(f"{field} must use an opaque UUID slug")
    namespace, suffix = value.split("/", 1)
    expected_namespace = "artifacts" if field != "produced_for" else "tasks"
    if namespace != expected_namespace:
        raise DomainValidationError(f"{field} must use an opaque UUID slug")
    try:
        parsed = uuid.UUID(suffix)
    except (AttributeError, ValueError) as exc:
        raise DomainValidationError(f"{field} must use an opaque UUID slug") from exc
    if str(parsed) != suffix.lower() or (
        expected_namespace == "artifacts" and parsed.version != 4
    ):
        raise DomainValidationError(f"{field} must use an opaque UUID slug")
    if expected_namespace != "artifacts" and parsed.version not in {4, 5}:
        raise DomainValidationError(
            f"{field} must use a canonical UUIDv4 or UUIDv5 slug"
        )
    return value


_GIT_COMMIT_ID = re.compile(r"[0-9a-fA-F]{7,64}")


def is_safe_git_commit_url(value: object) -> bool:
    if not isinstance(value, str) or not value or "%" in value:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host == "github.com":
        return (
            len(parts) == 4
            and parts[2] == "commit"
            and _GIT_COMMIT_ID.fullmatch(parts[3]) is not None
        )
    if host == "gitlab.com":
        return (
            len(parts) >= 5
            and parts[-3:-1] == ["-", "commit"]
            and _GIT_COMMIT_ID.fullmatch(parts[-1]) is not None
        )
    if host == "bitbucket.org":
        return (
            len(parts) == 4
            and parts[2] == "commits"
            and _GIT_COMMIT_ID.fullmatch(parts[3]) is not None
        )
    return False


@dataclass(frozen=True, slots=True)
class AgentArtifact:
    slug: str
    title: str
    artifact_kind: str
    created_by: str
    agent_collection: str
    produced_for: str
    markdown: str
    attachments: tuple[str, ...]
    project: str | None
    goal: str | None
    git_url: str | None
    supersedes: str | None
    created_at: datetime

    @classmethod
    def from_page(
        cls,
        page: Mapping[str, Any],
        edges: Iterable[Mapping[str, Any]] = (),
    ) -> "AgentArtifact":
        slug = _artifact_uuid_slug(page.get("slug"), "artifact slug")
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise DomainValidationError(f"{slug} is not a canonical artifact page")
        top_level_type = page.get("type")
        frontmatter_type = frontmatter.get("type")
        if not (
            (top_level_type == "concept" and frontmatter_type == "artifact")
            or (
                top_level_type == "artifact"
                and frontmatter_type in {None, "artifact"}
            )
        ):
            raise DomainValidationError(f"{slug} is not a canonical artifact page")

        top_level_title = page.get("title")
        frontmatter_title = frontmatter.get("title")
        if top_level_title is None and frontmatter_title is None:
            raise DomainValidationError("artifact title is required")
        validated_top_level_title = (
            _required_text(
                top_level_title,
                "artifact title",
                maximum=160,
                single_line=True,
            )
            if top_level_title is not None
            else None
        )
        validated_frontmatter_title = (
            _required_text(
                frontmatter_title,
                "artifact title",
                maximum=160,
                single_line=True,
            )
            if frontmatter_title is not None
            else None
        )
        if (
            validated_top_level_title is not None
            and validated_frontmatter_title is not None
            and validated_top_level_title != validated_frontmatter_title
        ):
            raise DomainValidationError(
                "artifact title conflicts between top-level and frontmatter"
            )
        title = validated_frontmatter_title or validated_top_level_title
        assert title is not None
        artifact_kind = frontmatter.get("artifact_kind")
        if artifact_kind not in ARTIFACT_KINDS:
            raise DomainValidationError("artifact_kind is invalid")
        created_by = frontmatter.get("created_by")
        if created_by not in ARTIFACT_BY_AGENT:
            raise DomainValidationError("artifact created_by is not an approved Agent")
        produced_for = _artifact_uuid_slug(
            frontmatter.get("produced_for"), "produced_for"
        )

        graph_edges = tuple(edges)
        verify_graph = bool(graph_edges)

        def required_link(link_type: str, expected: str) -> tuple[str, ...]:
            declared = _artifact_frontmatter_targets(slug, frontmatter, link_type)
            if declared != (expected,):
                raise DomainValidationError(
                    f"artifact frontmatter requires exactly one {link_type} relationship"
                )
            if verify_graph and _artifact_graph_targets(
                slug, graph_edges, link_type
            ) != (expected,):
                raise DomainValidationError(
                    f"artifact graph requires exactly one {link_type} relationship"
                )
            return declared

        memberships = _artifact_frontmatter_targets(slug, frontmatter, "member_of")
        if len(memberships) != 1:
            raise DomainValidationError(
                "artifact frontmatter requires exactly one typed member_of relationship"
            )
        agent_collection = memberships[0]
        if agent_collection != ARTIFACT_BY_AGENT[created_by]:
            raise DomainValidationError(
                "artifact Agent collection does not match created_by"
            )
        required_link("member_of", agent_collection)
        required_link("created_by", created_by)
        required_link("produced_for", produced_for)

        def optional_link(link_type: str, namespace: str) -> str | None:
            targets = _artifact_frontmatter_targets(slug, frontmatter, link_type)
            if len(targets) > 1:
                raise DomainValidationError(
                    f"artifact frontmatter permits at most one {link_type} relationship"
                )
            if targets:
                target = targets[0]
                if not target.startswith(f"{namespace}/"):
                    raise DomainValidationError(
                        f"artifact {link_type} relationship must use a canonical UUID slug"
                    )
                suffix = target.split("/", 1)[1]
                try:
                    parsed = uuid.UUID(suffix)
                except (AttributeError, ValueError) as exc:
                    raise DomainValidationError(
                        f"artifact {link_type} relationship must use a canonical UUID slug"
                    ) from exc
                if str(parsed) != suffix.lower() or (
                    namespace in {"projects", "goals"}
                    and parsed.version not in {4, 5}
                ):
                    raise DomainValidationError(
                        f"artifact {link_type} relationship must use a canonical UUIDv4 or UUIDv5 slug"
                    )
            if verify_graph and _artifact_graph_targets(
                slug, graph_edges, link_type
            ) != targets:
                raise DomainValidationError(
                    f"artifact graph {link_type} relationships must exactly match frontmatter"
                )
            return targets[0] if targets else None

        project = optional_link("supports_project", "projects")
        goal = optional_link("supports_goal", "goals")
        supersedes = optional_link("supersedes", "artifacts")
        if supersedes is not None:
            _artifact_uuid_slug(supersedes, "artifact supersedes")

        def safe_media_reference(reference: object) -> bool:
            if not isinstance(reference, str):
                return False
            parsed = urlsplit(reference)
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or parsed.path != reference
                or not parsed.path.startswith("/media/")
                or re.search(r"%(?![0-9a-fA-F]{2})", parsed.path)
                or re.search(r"%(?:2f|5c)", parsed.path, re.IGNORECASE)
            ):
                return False
            decoded = unquote(parsed.path)
            if (
                not decoded.startswith("/media/")
                or "\\" in decoded
                or any(ord(character) < 32 for character in decoded)
            ):
                return False
            segments = decoded.split("/")
            return all(segment not in {"", ".", ".."} for segment in segments[2:])

        raw_attachments = frontmatter.get("attachments", [])
        if not isinstance(raw_attachments, list) or any(
            not safe_media_reference(reference)
            for reference in raw_attachments
        ):
            raise DomainValidationError(
                "artifact attachments must use verified /media references"
            )
        attachments = tuple(dict.fromkeys(raw_attachments))
        git_url = frontmatter.get("git_url")
        if git_url in {None, "", "none"}:
            git_url = None
        if git_url is not None and not is_safe_git_commit_url(git_url):
            raise DomainValidationError(
                "artifact git_url must be an allowlisted HTTPS commit URL"
            )
        if artifact_kind == "git" and git_url is None:
            raise DomainValidationError("git artifact requires an HTTPS commit URL")

        markdown = page.get("compiled_markdown")
        if markdown is None:
            markdown = page.get("compiled_truth", "")
        if not isinstance(markdown, str):
            raise DomainValidationError("artifact Markdown must be text")
        created_at = _required_zoned_datetime(
            frontmatter.get("created_at"), "artifact created_at"
        )
        return cls(
            slug=slug,
            title=title,
            artifact_kind=artifact_kind,
            created_by=created_by,
            agent_collection=agent_collection,
            produced_for=produced_for,
            markdown=markdown.strip(),
            attachments=attachments,
            project=project,
            goal=goal,
            git_url=git_url,
            supersedes=supersedes,
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "slug": self.slug,
            "title": self.title,
            "artifact_kind": self.artifact_kind,
            "created_by": self.created_by,
            "agent_collection": self.agent_collection,
            "produced_for": self.produced_for,
            "markdown": self.markdown,
            "attachments": list(self.attachments),
            "project": self.project,
            "goal": self.goal,
            "git_url": self.git_url,
            "supersedes": self.supersedes,
            "created_at": self.created_at.isoformat(),
        }
        return result


def new_agent_artifact(
    *,
    title: str,
    artifact_kind: str,
    created_by: str,
    produced_for: str,
    markdown: str,
    attachments: Iterable[str] = (),
    project: str | None = None,
    goal: str | None = None,
    git_url: str | None = None,
    supersedes: str | None = None,
    now: datetime,
) -> AgentArtifact:
    slug = f"artifacts/{uuid.uuid4()}"
    collection = ARTIFACT_BY_AGENT.get(created_by)
    if collection is None:
        raise DomainValidationError("artifact created_by is not an approved Agent")
    links = [
        {"to": collection, "type": "member_of"},
        {"to": created_by, "type": "created_by"},
        {"to": produced_for, "type": "produced_for"},
    ]
    for target, relation in (
        (project, "supports_project"),
        (goal, "supports_goal"),
        (supersedes, "supersedes"),
    ):
        if target:
            links.append({"to": target, "type": relation})
    return AgentArtifact.from_page(
        {
            "slug": slug,
            "type": "concept",
            "frontmatter": {
                "type": "artifact",
                "title": title.strip(),
                "artifact_kind": artifact_kind,
                "created_by": created_by,
                "produced_for": produced_for,
                "attachments": list(dict.fromkeys(attachments)),
                "git_url": git_url,
                "created_at": now.isoformat(),
                "links": links,
            },
            "compiled_markdown": markdown.strip(),
        },
        edges=(),
    )


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
    decision: str | None = None
    decision_at: datetime | None = None
    resulting_status: str | None = None
    decision_events: tuple[ProposalDecisionEvent, ...] = ()

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
            decision=(
                "approve"
                if status == "approved"
                else "reject" if status == "rejected" else None
            ),
            decision_at=reviewed_at if status in {"approved", "rejected"} else None,
            resulting_status=(
                "planned"
                if status == "approved"
                else "cancelled" if status == "rejected" else None
            ),
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
            "decision": self.decision,
            "decision_at": self.decision_at.isoformat() if self.decision_at else None,
            "resulting_status": self.resulting_status,
            "decision_events": [event.to_dict() for event in self.decision_events],
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


def _compiled_frontmatter(page: Mapping[str, Any]) -> Mapping[str, Any]:
    """Parse the small scalar Goal contract when GBrain returns raw Markdown."""
    supplied = page.get("frontmatter")
    supplied_values = dict(supplied) if isinstance(supplied, Mapping) else {}
    # Structured frontmatter is canonical for tasks, projects, agents, and
    # ordinary goal rows. Only GBrain's documented raw concept + compiled Goal
    # shape needs recovery from compiled Markdown. Parsing arbitrary task YAML
    # here would turn nested links/metrics into strings and corrupt identity.
    if page.get("type") != "concept":
        return supplied_values
    body = page.get("compiled_truth")
    if not isinstance(body, str) or not body.startswith("---\n"):
        return supplied_values
    end = body.find("\n---", 4)
    if end < 0:
        return {}
    parsed: dict[str, str] = {}
    for line in body[4:end].splitlines():
        if not line or line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if not value:
            continue
        if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]
        parsed[key.strip()] = value
    if parsed.get("type") != "goal":
        return supplied_values
    # Raw get_page.frontmatter may contain only ingestion provenance while the
    # canonical Goal schema lives in compiled Markdown. Canonical compiled
    # fields win; provenance-only raw fields are retained.
    return {**supplied_values, **parsed}


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
                or not auto_complete
            ):
                raise DomainValidationError(
                    "job_applied requires unit job_application and automatic completion"
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
    baseline_count: int = 0
    evidence_slugs: tuple[str, ...] = ()
    receipt_ids: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "EventProgress":
        if not isinstance(value, Mapping):
            raise DomainValidationError("event_progress must be an object")
        evidence_slugs = value.get("evidence_slugs")
        receipt_ids = value.get("receipt_ids")
        baseline_count = value.get("baseline_count", 0)
        if (
            isinstance(baseline_count, bool)
            or not isinstance(baseline_count, int)
            or baseline_count < 0
        ):
            raise DomainValidationError(
                "event progress baseline_count must be a nonnegative whole number"
            )
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
            baseline_count=baseline_count,
            evidence_slugs=tuple(evidence_slugs),
            receipt_ids=tuple(receipt_ids),
        )

    @property
    def derived_current(self) -> int:
        return self.baseline_count + len(self.receipt_ids)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "evidence_slugs": list(self.evidence_slugs),
            "receipt_ids": list(self.receipt_ids),
        }
        if self.baseline_count:
            value["baseline_count"] = self.baseline_count
        return value


@dataclass(frozen=True, slots=True)
class ProposalDecisionEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    actor: str
    source: str
    decision: str
    decision_note: str
    previous_status: str
    resulting_status: str
    proposal_slug: str

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "ProposalDecisionEvent":
        if not isinstance(value, Mapping):
            raise DomainValidationError("proposal decision event must be an object")
        event_id = value.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 240:
            raise DomainValidationError("proposal decision event_id is required")
        if value.get("event_type") != "proposal_decision":
            raise DomainValidationError("proposal decision event_type is invalid")
        occurred_at = _optional_datetime(
            value.get("occurred_at"), "proposal decision occurred_at"
        )
        if occurred_at is None or occurred_at.tzinfo is None:
            raise DomainValidationError(
                "proposal decision occurred_at must include a timezone"
            )
        actor = value.get("actor")
        source = value.get("source")
        if not isinstance(actor, str) or not actor.strip():
            raise DomainValidationError("proposal decision actor is required")
        if not isinstance(source, str) or not source.strip():
            raise DomainValidationError("proposal decision source is required")
        decision = value.get("decision")
        if decision not in {"approve", "reject"}:
            raise DomainValidationError("proposal decision must be approve or reject")
        note = value.get("decision_note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise DomainValidationError(
                "proposal decision_note must be text up to 1000 characters"
            )
        expected_result = "planned" if decision == "approve" else "cancelled"
        if (
            value.get("previous_status") != "proposed"
            or value.get("resulting_status") != expected_result
        ):
            raise DomainValidationError("proposal decision status transition is invalid")
        proposal_slug = value.get("proposal_slug")
        if not isinstance(proposal_slug, str) or not proposal_slug.startswith("tasks/"):
            raise DomainValidationError("proposal decision proposal_slug is invalid")
        return cls(
            event_id=event_id.strip(),
            event_type="proposal_decision",
            occurred_at=occurred_at,
            actor=actor.strip(),
            source=source.strip(),
            decision=decision,
            decision_note=note.strip(),
            previous_status="proposed",
            resulting_status=expected_result,
            proposal_slug=proposal_slug,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "actor": self.actor,
            "source": self.source,
            "decision": self.decision,
            "decision_note": self.decision_note,
            "previous_status": self.previous_status,
            "resulting_status": self.resulting_status,
            "proposal_slug": self.proposal_slug,
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
    qa_fixture: bool = False
    qa_owner: str | None = None
    qa_release: str | None = None
    next_action_history: tuple[NextActionHistoryEntry, ...] = ()
    todos: tuple[TodoItem, ...] = ()
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
    handoff: TaskHandoff | None = None
    proposal_recipient: str | None = None
    proposal_submitted_at: datetime | None = None
    proposal_decision: str | None = None
    proposal_decided_at: datetime | None = None
    proposal_decision_note: str = ""
    proposal_decision_events: tuple[ProposalDecisionEvent, ...] = ()

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

        frontmatter = _compiled_frontmatter(page)
        if not frontmatter:
            raise DomainValidationError(f"{slug} has no canonical frontmatter")

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
        # Read-only compatibility for old GBrain pages. A future explicit task
        # edit normalizes the stored field; merely viewing never writes it.
        if status == "waiting":
            status = "blocked"
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
        qa_fixture = frontmatter.get("qa_fixture", False)
        qa_owner = frontmatter.get("qa_owner")
        qa_release = frontmatter.get("qa_release")
        if not isinstance(qa_fixture, bool):
            raise DomainValidationError("qa_fixture must be true or false")
        if qa_owner is not None and (
            not isinstance(qa_owner, str)
            or not qa_owner.strip()
            or len(qa_owner.strip()) > 120
        ):
            raise DomainValidationError("qa_owner must be concise text or null")
        if qa_release is not None and (
            not isinstance(qa_release, str)
            or not qa_release.strip()
            or len(qa_release.strip()) > 40
        ):
            raise DomainValidationError("qa_release must be concise text or null")
        has_qa_metadata = qa_fixture or qa_owner is not None or qa_release is not None
        if lifecycle_root == QA_FIXTURES_ROOT:
            if not qa_fixture or not isinstance(qa_owner, str) or not qa_owner.strip():
                raise DomainValidationError(
                    "QA fixture collection requires explicit QA ownership"
                )
        elif has_qa_metadata:
            raise DomainValidationError(
                "QA fixture metadata requires the QA fixture collection"
            )
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
        elif lifecycle_root == QA_FIXTURES_ROOT:
            approved_agents = {agent for agent, _work_root in AGENT_SCOPES}
            if len(assigned_agents) > 1 or any(
                agent not in approved_agents for agent in assigned_agents
            ):
                raise DomainValidationError(
                    "QA fixture permits at most one executing Agent"
                )
            owner_agent = assigned_agents[0] if assigned_agents else None
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
        raw_next_action_history = frontmatter.get("next_action_history", [])
        if raw_next_action_history is None:
            raw_next_action_history = []
        if not isinstance(raw_next_action_history, list):
            raise DomainValidationError("next_action_history must be a list")
        next_action_history = tuple(
            NextActionHistoryEntry.from_value(entry)
            for entry in raw_next_action_history
        )

        title = frontmatter.get("title") or page.get("title")
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
            if progress_metric.current != event_progress.derived_current:
                raise DomainValidationError(
                    "event-bound metric current must match baseline plus unique event evidence"
                )
        elif event_progress is not None:
            raise DomainValidationError(
                "manual progress metric cannot contain event progress"
            )

        proposal_decision = frontmatter.get("proposal_decision")
        if proposal_decision not in (None, "approve", "reject"):
            raise DomainValidationError(
                "proposal_decision must be approve, reject, or null"
            )
        proposal_decided_at = _optional_datetime(
            frontmatter.get("proposal_decided_at"), "proposal_decided_at"
        )
        raw_decision_events = frontmatter.get("proposal_decision_events", [])
        if raw_decision_events is None:
            raw_decision_events = []
        if not isinstance(raw_decision_events, list):
            raise DomainValidationError("proposal_decision_events must be a list")
        proposal_decision_events = tuple(
            ProposalDecisionEvent.from_value(value)
            for value in raw_decision_events
        )
        if len({event.event_id for event in proposal_decision_events}) != len(
            proposal_decision_events
        ):
            raise DomainValidationError(
                "proposal decision event identities must be unique"
            )
        if any(event.proposal_slug != slug for event in proposal_decision_events):
            raise DomainValidationError(
                "proposal decision event must reference its own task"
            )
        if proposal_decision_events:
            latest = proposal_decision_events[-1]
            if (
                proposal_decision != latest.decision
                or proposal_decided_at != latest.occurred_at
                or status != latest.resulting_status
            ):
                raise DomainValidationError(
                    "proposal decision projections must match the canonical event"
                )

        raw_handoff = frontmatter.get("handoff")
        handoff = (
            None if raw_handoff is None else TaskHandoff.from_value(raw_handoff)
        )

        task = cls(
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
            qa_fixture=qa_fixture,
            qa_owner=qa_owner.strip() if isinstance(qa_owner, str) else None,
            qa_release=qa_release.strip() if isinstance(qa_release, str) else None,
            next_action_history=next_action_history,
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
            handoff=handoff,
            proposal_recipient=(
                frontmatter.get("proposal_recipient")
                if frontmatter.get("proposal_recipient") in {"tony", "agent"}
                else None
            ),
            proposal_submitted_at=_optional_datetime(
                frontmatter.get("proposal_submitted_at"), "proposal_submitted_at"
            ),
            proposal_decision=proposal_decision,
            proposal_decided_at=proposal_decided_at,
            proposal_decision_note=(
                frontmatter.get("proposal_decision_note", "").strip()
                if isinstance(frontmatter.get("proposal_decision_note", ""), str)
                else ""
            ),
            proposal_decision_events=proposal_decision_events,
        )
        validate_task_handoff(task)
        return task

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "summary": self.summary,
            "detail": self.detail,
            "status": self.status,
            "priority": self.priority,
            "next_action": self.next_action,
            "next_action_history": [
                entry.to_dict() for entry in self.next_action_history
            ],
            "todos": [todo.to_dict() for todo in self.todos],
            "due_day": self.due_day.isoformat() if self.due_day else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "scheduled_day": (
                self.scheduled_day.isoformat() if self.scheduled_day else None
            ),
            "inbox": self.inbox,
            "lifecycle_root": self.lifecycle_root,
            "qa_fixture": self.qa_fixture,
            "qa_owner": self.qa_owner,
            "qa_release": self.qa_release,
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
            "handoff": self.handoff.to_dict() if self.handoff else None,
            "proposal_recipient": self.proposal_recipient,
            "proposal_submitted_at": self.proposal_submitted_at.isoformat() if self.proposal_submitted_at else None,
            "proposal_decision": self.proposal_decision,
            "proposal_decided_at": self.proposal_decided_at.isoformat() if self.proposal_decided_at else None,
            "proposal_decision_note": self.proposal_decision_note,
            "proposal_decision_events": [
                event.to_dict() for event in self.proposal_decision_events
            ],
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


def _shift_calendar_month(day: date, months: int) -> date:
    month_index = day.year * 12 + day.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def task_display_window(as_of: date) -> tuple[date, date]:
    """Return the inclusive one-calendar-month Mission Control task scope."""
    if not isinstance(as_of, date):
        raise DomainValidationError("task display as_of must be a local date")
    return (
        _shift_calendar_month(as_of, -1),
        _shift_calendar_month(as_of, 1),
    )


def task_is_in_default_display_window(task: Task, as_of: date) -> bool:
    """Keep urgent and undated work visible; otherwise scope by its task date."""
    if task.status in {"active", "blocked"}:
        return True
    relevant_day = task.scheduled_day or task.due_day
    if relevant_day is None:
        return True
    start_day, end_day = task_display_window(as_of)
    return start_day <= relevant_day <= end_day


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
        elif task.status == "blocked":
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


def _opaque_slug(namespace: str) -> str:
    """Return a permanent label-independent canonical identity."""
    return f"{namespace}/{uuid.uuid4()}"


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
        if progress_metric.current != event_progress.derived_current:
            raise DomainValidationError(
                "event-bound metric current must match baseline plus unique event evidence"
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
        slug=_opaque_slug("tasks"),
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
        frontmatter = _compiled_frontmatter(page)
        if not frontmatter:
            raise DomainValidationError(f"{slug} has no canonical frontmatter")
        # GBrain's raw row can retain the generic concept type even when the
        # compiled canonical Markdown contract is a validated Goal. Goal
        # identity therefore comes from its slug + frontmatter contract, not
        # from the raw storage row alone.
        if frontmatter.get("type") != "goal" and page.get("type") != "goal":
            raise DomainValidationError(f"{slug} is not a canonical goal page")
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

        title = frontmatter.get("title") or page.get("title")
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
    slug = _opaque_slug("tasks")
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
        slug=_opaque_slug("projects"),
        title=clean_title,
        status="active",
        summary=clean_title,
        supporting_goal_slugs=tuple(dict.fromkeys(supporting_goal_slugs)),
        created_at=now,
        updated_at=now,
    )


def new_system_ticket(*, title: str, verbatim_request: str, target_subsystem: str,
                      priority: str, now: datetime, identity: str,
                      acceptance_criteria: str = "") -> SystemTicket:
    clean_title, request = title.strip(), verbatim_request.strip()
    if not clean_title or len(clean_title) > 160:
        raise DomainValidationError("system ticket title must be 1 to 160 characters")
    if not request:
        raise DomainValidationError("system ticket verbatim_request is required")
    if target_subsystem not in SYSTEM_TICKET_TARGETS:
        raise DomainValidationError("system ticket target_subsystem is invalid")
    if priority not in TASK_PRIORITIES:
        raise DomainValidationError("system ticket priority is invalid")
    safe_identity = re.sub(r"[^a-z0-9]", "", identity.lower())[:12]
    if len(safe_identity) < 6:
        raise DomainValidationError("identity must contain at least 6 letters or numbers")
    return SystemTicket(_opaque_slug("tasks"), clean_title, "planned", request, target_subsystem, priority, acceptance_criteria.strip(), created_at=now, updated_at=now)


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
        slug=_opaque_slug("goals"),
        title=values["title"],
        status="planned",
        outcome=values["outcome"],
        success_criteria=values["success_criteria"],
        target_day=target_day or default_goal_target_day(now.date()),
        strategy=values["strategy"],
        review_cadence=values["review_cadence"],
        constraints=values["constraints"],
    )
