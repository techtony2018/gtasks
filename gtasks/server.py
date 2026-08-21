from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

from . import __version__
from .domain import (
    ACTIVE_ROOT,
    AGENT_SCOPES,
    EXISTING_CODEX_AGENT_SCOPES,
    ARTIFACT_BY_AGENT,
    ARTIFACT_KINDS,
    ArtifactExecutionClaim,
    COMPLETED_ROOT,
    DomainValidationError,
    EDITABLE_TASK_STATUSES,
    EventProgress,
    GOALS_ROOT,
    PROJECTS_ROOT,
    QA_FIXTURES_ROOT,
    SYSTEM_TICKET_TARGETS,
    SYSTEM_TICKET_STATUSES,
    TASK_PRIORITIES,
    SystemTicket,
    PROPOSALS_ROOT,
    ProgressMetric,
    Task,
    group_today,
    new_goal,
    new_agent_artifact,
    new_inbox_task,
    new_project,
    new_task,
    new_system_ticket,
    task_display_window,
    task_is_in_default_display_window,
)
from .gbrain import (
    ArtifactIdempotencyConflict,
    CanonicalHandoffEventBridge,
    ConcurrentTodoUpdateError,
    GBrainAdapter,
    GBrainCommandError,
    GBrainError,
    is_page_not_found_error,
    LifecycleIntegrityError,
    PartialMutationError,
    TONY_PROFILE_SLUG,
    ConcurrentAgentDelegationUpdateError,
)
from .delegation import (
    AgentDelegationLease,
    DelegationState,
    agent_route_group_is_approved,
    lease_state_at,
)
from .ical import CalendarPreferences, ICalendarError, ICalendarReader
from .job_application_binding import (
    JOB_APPLIED_BOUND_TASK_SLUG,
    JOB_APPLIED_TIMEZONE,
    progress_revision,
)
from .handoff_dispatcher import (
    AgentRegistration,
    DurableHandoffStore,
    HandoffDispatcher,
    HandoffOwnershipError,
)
from .local_handoff_dispatcher import CLAIM_SCHEMA_VERSION
from .buzz_coordination import BuzzCoordinationOutbox, build_handoff_coordination_sink
from .operational_logs import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SEVERITIES,
    COMPONENT_PATTERN,
    OperationalLogReader,
    OperationalLogStore,
)
from .releases import release_payload
from .read_cache import ReadSnapshotStore, ReadSurfaceCache
from .warnings import WarningDismissalStore


MAX_REQUEST_BYTES = 16 * 1024
MAX_ARTIFACT_REQUEST_BYTES = 256 * 1024
MAX_AVATAR_BYTES = 5 * 1024 * 1024
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def default_agent_delegation_lock_path() -> Path:
    configured = os.environ.get("GTASKS_AGENT_DELEGATION_LOCK_FILE")
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "GTasks"
        / "agent-delegations.lock"
    )


class AgentDelegationMutationLock:
    """Process-shared, slug-keyed writer lock for Mission Control mutations."""

    _registry_guard = Lock()
    _thread_locks: dict[str, Lock] = {}

    def __init__(self, base_path: Path) -> None:
        self.base_path = Path(base_path)

    @contextmanager
    def hold(self, slug: str):
        digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()
        lock_path = self.base_path.with_name(f"{self.base_path.name}.{digest}")
        if not lock_path.parent.exists():
            lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if lock_path.parent.stat().st_mode & 0o077:
            raise PermissionError(
                "agent delegation lock directory must be private (mode 0700)"
            )
        key = str(lock_path.resolve())
        with self._registry_guard:
            thread_lock = self._thread_locks.setdefault(key, Lock())
        with thread_lock:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
SNAPSHOT_CACHE_SECONDS = 30
PROPOSAL_CACHE_SECONDS = 5 * 60
SYSTEM_TICKET_CACHE_SECONDS = 5 * 60
DEFAULT_ARTIFACT_PUBLISHER_CREDENTIALS = (
    Path.home()
    / ".codex"
    / "services"
    / "all-things-codex-dashboard"
    / "state"
    / "gtasks"
    / "artifact-publisher-credentials.json"
)
DEFAULT_HANDOFF_STORE = (
    Path.home()
    / ".codex"
    / "services"
    / "all-things-codex-dashboard"
    / "state"
    / "gtasks"
    / "handoff-dispatcher.sqlite3"
)
DEFAULT_HANDOFF_DISPATCHER_CREDENTIALS = (
    Path.home()
    / ".codex"
    / "services"
    / "all-things-codex-dashboard"
    / "state"
    / "gtasks"
    / "handoff-dispatcher-credentials.json"
)


@dataclass(frozen=True, slots=True)
class HandoffDispatcherIdentity:
    agent_slug: str
    registration_id: str


class HandoffDispatcherAuth:
    """Resolve a bearer credential to one pseudonymized dispatcher identity."""

    def __init__(
        self,
        identities: tuple[tuple[HandoffDispatcherIdentity, str], ...] = (),
    ) -> None:
        registration_hashes = [identity.registration_id for identity, _ in identities]
        token_hashes = [token_hash for _, token_hash in identities]
        agent_slugs = [identity.agent_slug for identity, _ in identities]
        if (
            len(set(registration_hashes)) != len(registration_hashes)
            or len(set(token_hashes)) != len(token_hashes)
            or len(set(agent_slugs)) != len(agent_slugs)
        ):
            raise ValueError(
                "Dispatcher identity, registration, and token hashes must be unique"
            )
        self._identities = identities

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _valid_digest(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @classmethod
    def from_plaintext_tokens_for_tests(
        cls,
        credentials: dict[str, tuple[str, str]],
    ) -> "HandoffDispatcherAuth":
        return cls(
            tuple(
                (
                    HandoffDispatcherIdentity(
                        agent_slug, cls._digest(registration_id)
                    ),
                    cls._digest(token),
                )
                for agent_slug, (registration_id, token) in credentials.items()
            )
        )

    @classmethod
    def from_file(cls, path: Path) -> "HandoffDispatcherAuth":
        try:
            mode = path.stat().st_mode & 0o777
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Handoff dispatcher credentials are unavailable") from exc
        if mode != 0o600:
            raise ValueError("Handoff dispatcher credentials must use mode 0600")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "identities"}
            or payload.get("schema_version") != 1
        ):
            raise ValueError("Handoff dispatcher credentials have the wrong schema")
        entries = payload.get("identities")
        if not isinstance(entries, list) or len(entries) not in {3, 6}:
            raise ValueError("Handoff dispatcher credentials have the wrong schema")
        identities: list[tuple[HandoffDispatcherIdentity, str]] = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "agent_slug",
                "registration_sha256",
                "token_sha256",
            }:
                raise ValueError("Handoff dispatcher credential entry is invalid")
            agent_slug = entry.get("agent_slug")
            registration_hash = entry.get("registration_sha256")
            token_hash = entry.get("token_sha256")
            if (
                not isinstance(agent_slug, str)
                or re.fullmatch(r"agents/[a-z0-9][a-z0-9._-]{0,63}", agent_slug)
                is None
                or not cls._valid_digest(registration_hash)
                or not cls._valid_digest(token_hash)
            ):
                raise ValueError("Handoff dispatcher credential entry is invalid")
            identities.append(
                (HandoffDispatcherIdentity(agent_slug, registration_hash), token_hash)
            )
        configured_agents = frozenset(
            identity.agent_slug for identity, _token_hash in identities
        )
        legacy_codex_agents = frozenset(
            agent_slug for agent_slug, _collection in EXISTING_CODEX_AGENT_SCOPES
        )
        all_approved_agents = frozenset(
            agent_slug for agent_slug, _collection in AGENT_SCOPES
        )
        if configured_agents not in {legacy_codex_agents, all_approved_agents}:
            raise ValueError("Handoff dispatcher credentials have the wrong schema")
        return cls(tuple(identities))

    def resolve(
        self, authorization: str | None
    ) -> HandoffDispatcherIdentity | None:
        if not isinstance(authorization, str) or not authorization.startswith(
            "Bearer "
        ):
            return None
        token = authorization[7:]
        if not token or len(token) > 512 or "\n" in token or "\r" in token:
            return None
        supplied = self._digest(token)
        match = None
        for identity, expected in self._identities:
            if hmac.compare_digest(supplied, expected):
                match = identity
        return match

    @property
    def identities(self) -> tuple[HandoffDispatcherIdentity, ...]:
        return tuple(identity for identity, _token_hash in self._identities)


class _HandoffIdentityMismatch(ValueError):
    pass


class ArtifactPublisherAuth:
    def __init__(self, token_hashes: dict[str, str] | None = None) -> None:
        self._token_hashes = dict(token_hashes or {})

    @classmethod
    def from_plaintext_tokens_for_tests(
        cls, tokens: dict[str, str]
    ) -> "ArtifactPublisherAuth":
        return cls(
            {
                agent: hashlib.sha256(token.encode("utf-8")).hexdigest()
                for agent, token in tokens.items()
            }
        )

    @classmethod
    def from_file(cls, path: Path) -> "ArtifactPublisherAuth":
        try:
            mode = path.stat().st_mode & 0o777
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Artifact publisher credentials are unavailable") from exc
        if mode != 0o600:
            raise ValueError("Artifact publisher credentials must use mode 0600")
        publishers = payload.get("publishers") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(publishers, list)
        ):
            raise ValueError("Artifact publisher credentials have the wrong schema")
        token_hashes: dict[str, str] = {}
        for publisher in publishers:
            if not isinstance(publisher, dict):
                raise ValueError("Artifact publisher credential entry is invalid")
            agent = publisher.get("agent_slug")
            digest = publisher.get("token_sha256")
            if (
                agent not in ARTIFACT_BY_AGENT
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or agent in token_hashes
                or digest in token_hashes.values()
            ):
                raise ValueError("Artifact publisher credential entry is invalid")
            token_hashes[agent] = digest
        return cls(token_hashes)

    def resolve(self, authorization: str | None) -> str | None:
        if not isinstance(authorization, str) or not authorization.startswith(
            "Bearer "
        ):
            return None
        token = authorization[7:]
        if not token or len(token) > 512 or "\n" in token or "\r" in token:
            return None
        supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
        match = None
        for agent, expected in self._token_hashes.items():
            if hmac.compare_digest(supplied, expected):
                match = agent
        return match


def load_artifact_publisher_auth(
    configured_path: Path | None,
) -> ArtifactPublisherAuth:
    path = configured_path or DEFAULT_ARTIFACT_PUBLISHER_CREDENTIALS
    if not path.exists() and configured_path is None:
        return ArtifactPublisherAuth()
    return ArtifactPublisherAuth.from_file(path)


def load_handoff_dispatcher_auth(
    configured_path: Path | None,
) -> HandoffDispatcherAuth:
    path = configured_path or DEFAULT_HANDOFF_DISPATCHER_CREDENTIALS
    if not path.exists() and configured_path is None:
        return HandoffDispatcherAuth()
    return HandoffDispatcherAuth.from_file(path)


def _canonical_executor_priority_snapshot(
    adapter: GBrainAdapter, executor_agent: str
) -> tuple[bool, str]:
    """Read and version the exact canonical owned-work priority projection."""
    try:
        work = adapter.list_agent_work(include_todos=False)
    except TypeError:
        work = adapter.list_agent_work()
    relevant: list[dict[str, Any]] = []
    for value in work.tasks:
        task = dict(value)
        owner = task.get("owner_agent")
        if not isinstance(owner, str):
            owner_value = task.get("owner")
            owner = owner_value.get("slug") if isinstance(owner_value, dict) else None
        if owner == executor_agent:
            relevant.append(task)
    relevant.sort(key=lambda task: str(task.get("slug", "")))
    relevant_issues = tuple(
        issue
        for issue in work.issues
        if issue.owner_agent in {None, executor_agent}
    )
    owned_work_ready = bool(relevant_issues) or any(
        task.get("status") in {"planned", "active"} for task in relevant
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "executor_agent": executor_agent,
                "tasks": relevant,
                "issues": [issue.to_dict() for issue in relevant_issues],
                "roots": list(work.roots),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return owned_work_ready, f"priority/{fingerprint}"


def build_runtime_handoff_event_bridge(
    adapter: GBrainAdapter,
    store: DurableHandoffStore,
    auth: HandoffDispatcherAuth,
) -> CanonicalHandoffEventBridge:
    """Build a fail-closed bridge from hash-only canonical route readback."""
    reader = getattr(
        adapter,
        "read_handoff_dispatcher_registration_by_reference",
        None,
    )
    registrations: list[AgentRegistration] = []
    if callable(reader):
        for identity in auth.identities:
            try:
                registration = reader(identity.agent_slug, identity.registration_id)
            except (GBrainError, ValueError):
                continue
            if (
                not isinstance(registration, AgentRegistration)
                or registration.verified is not True
                or registration.agent_slug != identity.agent_slug
                or registration.registration_id != identity.registration_id
                or registration.reference != identity.registration_id
                or registration.lease_identity != identity.registration_id
            ):
                continue
            registrations.append(registration)

    route_agents: dict[str, set[str]] = {}
    for registration in registrations:
        route_agents.setdefault(registration.route, set()).add(
            registration.agent_slug
        )
    approved_routes = {
        route
        for route, agents in route_agents.items()
        if agent_route_group_is_approved(agents)
    }
    unambiguous = tuple(
        registration
        for registration in registrations
        if registration.route in approved_routes
    )
    delegation_reader = getattr(adapter, "list_agent_delegations", lambda: ())
    buzz_outbox_dir = os.environ.get("MISSION_CONTROL_BUZZ_OUTBOX_DIR", "").strip()
    coordination_sink = (
        build_handoff_coordination_sink(BuzzCoordinationOutbox(Path(buzz_outbox_dir)))
        if buzz_outbox_dir
        else None
    )
    return CanonicalHandoffEventBridge(
        HandoffDispatcher(
            store,
            registrations=unambiguous,
            delegations=delegation_reader,
            owned_work_snapshot=lambda executor: _canonical_executor_priority_snapshot(
                adapter, executor
            ),
            coordination_sink=coordination_sink,
        )
    )


def _canonical_uuid_slug(
    value: object, namespace: str, *, required_version: int | None = None
) -> str:
    if not isinstance(value, str) or not value.startswith(f"{namespace}/"):
        raise ValueError("Artifact filter must use a canonical UUID slug")
    suffix = value.split("/", 1)[1]
    try:
        parsed = uuid.UUID(suffix)
    except (AttributeError, ValueError) as exc:
        raise ValueError("Artifact filter must use a canonical UUID slug") from exc
    if str(parsed) != suffix.lower() or (
        required_version is not None and parsed.version != required_version
    ) or (
        required_version is None and parsed.version not in {4, 5}
    ):
        raise ValueError("Artifact filter must use a canonical UUID slug")
    return value


def _lifecycle_attention_payload(exc: LifecycleIntegrityError) -> dict[str, object]:
    """Return a safe, actionable error without exposing graph internals."""
    return {
        "error": str(exc),
        "code": "lifecycle_membership_needs_attention",
        "slug": exc.task_slug,
        "lifecycle_edge_count": exc.edge_count,
        "repair_url": (
            "http://127.0.0.1:8788/?slug=" + quote(exc.task_slug, safe="")
        ),
    }


def _manual_metric_unit(label: str) -> str:
    unit = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return unit[:64] or "count"


def _progress_metric_from_request(
    raw: object,
    *,
    due_day: date,
    task_slug: str | None = None,
) -> tuple[ProgressMetric | None, EventProgress | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise DomainValidationError("progress_metric must be an object")
    allowed = {
        "kind",
        "label",
        "target",
        "current",
        "event_binding",
        "auto_complete",
    }
    if set(raw) - allowed:
        raise DomainValidationError(
            "progress_metric contains unsupported fields"
        )
    kind = raw.get("kind", "count")
    label = raw.get("label")
    target = raw.get("target")
    current = raw.get("current", 0)
    binding = raw.get("event_binding")
    auto_complete = raw.get("auto_complete", binding == "job_applied")
    if not isinstance(label, str):
        raise DomainValidationError("progress metric label is required")
    if binding not in (None, "", "job_applied"):
        raise DomainValidationError(
            "job_applied is the only supported automatic event binding"
        )
    binding = binding or None
    if binding == "job_applied" and task_slug != JOB_APPLIED_BOUND_TASK_SLUG:
        raise DomainValidationError(
            "Automatic job-applied events are available only for the explicit "
            f"bound task {JOB_APPLIED_BOUND_TASK_SLUG}. This task was not changed."
        )
    value = {
        "kind": kind,
        "label": label,
        "unit": (
            "job_application"
            if binding == "job_applied"
            else _manual_metric_unit(label)
        ),
        "target": target,
        "current": current,
        "event_binding": binding,
        "auto_complete": auto_complete if binding else False,
        "task_day": due_day.isoformat() if binding else None,
        "timezone": "America/Los_Angeles" if binding else None,
    }
    metric = ProgressMetric.from_value(value)
    return (
        metric,
        EventProgress(baseline_count=current) if binding else None,
    )


def _dedupe_tasks(tasks: list[Task]) -> list[Task]:
    seen: set[str] = set()
    result: list[Task] = []
    for task in tasks:
        if task.slug in seen:
            continue
        seen.add(task.slug)
        result.append(task)
    return result


def _parent_slug_from_request(
    payload: dict[str, Any],
    *,
    task_slug: str | None = None,
) -> str | None:
    raw_parent = payload.get("parent_slug")
    if raw_parent in (None, ""):
        return None
    if not isinstance(raw_parent, str) or not raw_parent.startswith("tasks/"):
        raise DomainValidationError("parent_slug must be a task slug or null")
    if task_slug is not None and raw_parent == task_slug:
        raise DomainValidationError("task cannot be its own parent")
    return raw_parent


def exact_task_api_payload(adapter: GBrainAdapter, task_slug: str) -> dict[str, Any]:
    """Expose an optional display projection without changing Task authority."""
    projector = getattr(adapter, "get_task_api_payload", None)
    if callable(projector):
        return dict(projector(task_slug))
    return adapter.get_task(task_slug).to_dict()


def build_task_snapshot(adapter: GBrainAdapter, today: date) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=4) as executor:
        active_future = executor.submit(adapter.list_collection_tasks, ACTIVE_ROOT)
        completed_future = executor.submit(
            adapter.list_collection_tasks,
            COMPLETED_ROOT,
        )
        goals_future = executor.submit(adapter.list_goals)
        owner_future = executor.submit(adapter.get_tony_profile)
        active_read = active_future.result()
        completed_read = completed_future.result()
        goal_read = goals_future.result()
        try:
            owner = owner_future.result()
        except (DomainValidationError, GBrainError):
            # Personal-avatar presentation must never block canonical tasks.
            owner = {
                "slug": "people/tony-guan",
                "name": "Tony",
                "avatar": {"kind": "initials", "value": "T"},
            }
    active = _dedupe_tasks(list(active_read.tasks))
    archived = _dedupe_tasks(list(completed_read.tasks))
    todo_issues = ()
    enrich_todos = getattr(adapter, "enrich_tasks_with_todos", None)
    if callable(enrich_todos):
        active, active_todo_issues = enrich_todos(active)
        archived, archived_todo_issues = enrich_todos(archived)
        active = list(active)
        archived = list(archived)
        todo_issues = active_todo_issues + archived_todo_issues
    all_tasks = _dedupe_tasks(active + archived)
    display_start, display_end = task_display_window(today)
    all_task_payloads = [
        {
            **task.to_dict(),
            "progress_metric_revision": progress_revision(task),
            "in_default_display_window": task_is_in_default_display_window(
                task,
                today,
            ),
        }
        for task in all_tasks
    ]
    completed = [
        task
        for task in all_tasks
        if task.status == "completed" or task.lifecycle_root == COMPLETED_ROOT
    ]
    goal_progress: list[dict[str, Any]] = []
    for goal in goal_read.goals:
        linked = [task for task in all_tasks if task.goal == goal.slug]
        active_goal_tasks = [
            task
            for task in linked
            if task.status not in {"completed", "cancelled"}
        ]
        completed_goal_tasks = [
            task for task in linked if task.status == "completed"
        ]
        linked_count = len(linked)
        completed_count = len(completed_goal_tasks)
        progress_percent = (
            round(completed_count / linked_count * 100) if linked_count else 0
        )
        goal_progress.append(
            {
                **goal.to_dict(),
                "legacy_one_way_tasks": [],
                "relationship_warning": False,
                "active_tasks": [task.to_dict() for task in active_goal_tasks],
                "completed_tasks": [
                    task.to_dict() for task in completed_goal_tasks
                ],
                "progress": {
                    "active": len(active_goal_tasks),
                    "completed": completed_count,
                    "linked": linked_count,
                    "percent": progress_percent,
                },
            }
        )

    return {
        "as_of": today.isoformat(),
        "default_due_day": "task_creation_day",
        "default_goal_target_day": "end_of_creation_quarter",
        "task_display_scope": {
            "start_day": display_start.isoformat(),
            "end_day": display_end.isoformat(),
            "timezone": "America/Los_Angeles",
        },
        "roots": {
            "active": ACTIVE_ROOT,
            "completed": COMPLETED_ROOT,
            "goals": GOALS_ROOT,
        },
        "event_bindings": {
            "job_applied": {
                "task_slug": JOB_APPLIED_BOUND_TASK_SLUG,
                "timezone": JOB_APPLIED_TIMEZONE,
            }
        },
        "owner": owner,
        "tasks": all_task_payloads,
        "goals": goal_progress,
        "today": group_today(active, today).to_dict(),
        "views": {
            "inbox": [
                task.to_dict()
                for task in active
                if task.inbox and task.status not in {"completed", "cancelled"}
            ],
            "blocked": [
                task.to_dict()
                for task in active
                if task.status == "blocked"
            ],
            "projects": [
                task.to_dict() for task in active if task.project is not None
            ],
            "completed": [task.to_dict() for task in completed],
        },
        "issues": [
            issue.to_dict()
            for issue in (
                active_read.issues
                + completed_read.issues
                + goal_read.issues
                + todo_issues
            )
        ],
    }


def _handler_class(
    adapter: GBrainAdapter,
    clock: Callable[[], datetime],
    identity_factory: Callable[[], str],
    static_dir: Path,
    warning_store: WarningDismissalStore,
    log_reader: OperationalLogReader,
    stargraph_url: str,
    ical_reader: ICalendarReader | None = None,
    calendar_preferences: CalendarPreferences | None = None,
    read_cache: ReadSurfaceCache | None = None,
    artifact_publisher_auth: ArtifactPublisherAuth | None = None,
    handoff_store: DurableHandoffStore | None = None,
    handoff_dispatcher_auth: HandoffDispatcherAuth | None = None,
    handoff_registration_validator: Callable[
        [str, str], AgentRegistration | None
    ]
    | None = None,
    handoff_waiter: Callable[[float], None] | None = None,
    handoff_event_bridge: CanonicalHandoffEventBridge | None = None,
    delegation_lock_path: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    active_read_cache = read_cache or ReadSurfaceCache(ReadSnapshotStore())
    active_ical_reader = ical_reader or ICalendarReader()
    active_calendar_preferences = calendar_preferences or CalendarPreferences()
    active_artifact_publisher_auth = (
        artifact_publisher_auth or ArtifactPublisherAuth()
    )
    active_handoff_auth = handoff_dispatcher_auth or HandoffDispatcherAuth()
    active_handoff_registration_validator = handoff_registration_validator or getattr(
        adapter, "read_handoff_dispatcher_registration", None
    )
    active_handoff_registration_reference_reader = getattr(
        adapter,
        "read_handoff_dispatcher_registration_by_reference",
        None,
    )
    active_handoff_waiter = handoff_waiter or time.sleep
    active_handoff_event_bridge = handoff_event_bridge
    active_delegation_lock = AgentDelegationMutationLock(
        delegation_lock_path or default_agent_delegation_lock_path()
    )

    def foreground_operation():
        runner = getattr(adapter, "runner", None)
        priority = getattr(runner, "foreground_operation", None)
        return priority() if callable(priority) else nullcontext()

    def decorate_issues(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            decorated = warning_store.decorate(payload.get("issues", []))
            error = None
        except RuntimeError as exc:
            decorated = [
                {
                    **dict(issue),
                    "fingerprint": None,
                    "dismissed": False,
                }
                for issue in payload.get("issues", [])
            ]
            error = str(exc)
        return {
            **payload,
            "issues": decorated,
            "warning_state_error": error,
        }

    def invalidate_snapshot() -> None:
        # Keep the last verified projection usable while the authoritative
        # refresh runs. Mutations mark both task and proposal projections stale
        # because an agent proposal decision changes the same canonical task.
        active_read_cache.invalidate("tasks", "proposals")

    def invalidate_system_tickets() -> None:
        active_read_cache.invalidate("system_tickets", "system_tickets_all")

    def canonical_mapping(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            rendered = to_dict()
            if isinstance(rendered, dict):
                return rendered
        return {}

    def delegation_payload(lease: AgentDelegationLease) -> dict[str, Any]:
        effective_state = lease_state_at(lease, clock().astimezone(timezone.utc))
        return {
            "slug": lease.slug,
            "source_agent": lease.source_agent,
            "executor_agent": lease.executor_agent,
            "authorized_by": lease.authorized_by,
            "starts_at": lease.starts_at.isoformat(),
            "ends_at": lease.ends_at.isoformat(),
            "display_timezone": lease.display_timezone,
            "allowed_operations": list(lease.allowed_operations),
            "state": effective_state.value,
            "created_at": lease.created_at.isoformat(),
            "updated_at": lease.updated_at.isoformat(),
            "version": lease.updated_at.isoformat(),
        }

    def delegation_response(
        lease: AgentDelegationLease, receipt: object
    ) -> dict[str, Any] | None:
        receipt_value = canonical_mapping(receipt)
        if receipt_value.get("verified") is not True or receipt_value.get("slug") != lease.slug:
            return None
        readback = next(
            (
                item
                for item in adapter.list_agent_delegations()
                if item.slug == lease.slug
            ),
            None,
        )
        if readback != lease:
            return None
        return {
            "lease": delegation_payload(readback),
            "version": readback.updated_at.isoformat(),
            "receipt": receipt_value,
        }

    def verified_artifact_execution_claim(
        *,
        task_slug: str,
        executor_agent: str,
        delegation_ref: str,
    ) -> ArtifactExecutionClaim:
        if handoff_store is None:
            raise DomainValidationError(
                "Artifact delegation claim storage is unavailable"
            )
        claim_reader = getattr(handoff_store, "get_execution_claim", None)
        if not callable(claim_reader):
            raise DomainValidationError(
                "Artifact delegation claim storage is unavailable"
            )
        now = clock().astimezone(timezone.utc)
        claim = claim_reader(task_slug, include_terminal=False)
        completed_at = None
        if claim is None:
            claim = claim_reader(task_slug, include_terminal=True)
            terminal_state = getattr(claim, "terminal_state", None)
            terminal_at = getattr(claim, "terminal_at", None)
            if (
                claim is None
                or terminal_state != "completed"
                or not isinstance(terminal_at, datetime)
                or terminal_at.tzinfo is None
                or terminal_at.astimezone(timezone.utc) < now - timedelta(minutes=5)
                or terminal_at.astimezone(timezone.utc) > now
            ):
                raise DomainValidationError(
                    "Artifact delegation claim is not active or narrowly just completed"
                )
            completed_at = terminal_at
        elif (
            getattr(claim, "terminal_state", None) is not None
            or claim.expires_at <= now
        ):
            raise DomainValidationError("Artifact delegation claim is expired or terminal")
        try:
            leases = {
                lease.slug: lease
                for lease in adapter.list_agent_delegations()
            }
        except (GBrainError, ValueError) as exc:
            raise DomainValidationError(
                "Artifact delegation lease could not be verified"
            ) from exc
        lease = leases.get(delegation_ref)
        if (
            claim.task_slug != task_slug
            or claim.executor_agent != executor_agent
            or claim.delegation_slug != delegation_ref
            or lease is None
            or lease.executor_agent != executor_agent
            or lease.source_agent != claim.permanent_owner
            or "artifact" not in lease.allowed_operations
            or lease_state_at(lease, completed_at or now)
            not in {DelegationState.ACTIVE, DelegationState.COMPLETED}
        ):
            raise DomainValidationError(
                "Artifact delegation claim does not match task, executor, owner, and lease"
            )
        return ArtifactExecutionClaim(
            task_slug=claim.task_slug,
            executor_agent=claim.executor_agent,
            permanent_owner=claim.permanent_owner,
            delegation_ref=delegation_ref,
            requested_operation=claim.requested_operation,
            claimed_at=claim.claimed_at,
            expires_at=claim.expires_at,
            completed_at=completed_at,
        )

    @contextmanager
    def reserve_verified_artifact_execution(
        initial: ArtifactExecutionClaim,
        *,
        publication_key: str,
    ):
        if handoff_store is None:
            raise DomainValidationError(
                "Artifact delegation claim storage is unavailable"
            )
        reserve = getattr(handoff_store, "reserve_artifact_publication", None)
        observe = getattr(handoff_store, "observe_delegation_authority", None)
        if not callable(reserve) or not callable(observe):
            raise DomainValidationError(
                "Artifact delegation claim reservation is unavailable"
            )
        with active_delegation_lock.hold(initial.delegation_ref):
            now = clock().astimezone(timezone.utc)
            matches = tuple(
                lease
                for lease in adapter.list_agent_delegations()
                if lease.slug == initial.delegation_ref
            )
            if len(matches) != 1 or not isinstance(matches[0], AgentDelegationLease):
                raise DomainValidationError(
                    "Artifact delegation claim lease could not be re-read at the write boundary"
                )
            lease = matches[0]
            authority_at = initial.completed_at or now
            if (
                lease.source_agent != initial.permanent_owner
                or lease.executor_agent != initial.executor_agent
                or "artifact" not in lease.allowed_operations
                or lease_state_at(lease, authority_at)
                not in {DelegationState.ACTIVE, DelegationState.COMPLETED}
            ):
                raise DomainValidationError(
                    "Artifact delegation claim lease changed before the write boundary"
                )
            observe(lease, observed_at=now)
            try:
                with reserve(
                    initial.task_slug,
                    executor_agent=initial.executor_agent,
                    permanent_owner=initial.permanent_owner,
                    delegation_slug=initial.delegation_ref,
                    publication_key=publication_key,
                    now=now,
                ) as claim:
                    yield ArtifactExecutionClaim(
                        task_slug=claim.task_slug,
                        executor_agent=claim.executor_agent,
                        permanent_owner=claim.permanent_owner,
                        delegation_ref=claim.delegation_slug,
                        requested_operation=claim.requested_operation,
                        claimed_at=claim.claimed_at,
                        expires_at=claim.expires_at,
                        completed_at=(
                            claim.terminal_at
                            if claim.terminal_state == "completed"
                            else None
                        ),
                    )
            except (RuntimeError, sqlite3.Error, ValueError) as exc:
                raise DomainValidationError(
                    "Artifact delegation claim reservation was lost or invalid"
                ) from exc

    def decorate_agent_work_execution(payload: dict[str, Any]) -> dict[str, Any]:
        """Project only currently verified, non-terminal per-task claims."""
        if handoff_store is None:
            return payload
        claim_reader = getattr(handoff_store, "get_execution_claim", None)
        delegation_reader = getattr(adapter, "list_agent_delegations", None)
        if not callable(claim_reader) or not callable(delegation_reader):
            return payload
        now = clock().astimezone(timezone.utc)
        try:
            leases = {
                lease.slug: lease
                for lease in delegation_reader()
                if lease_state_at(lease, now) == DelegationState.ACTIVE
            }
        except (DomainValidationError, GBrainError, ValueError):
            return payload
        tasks: list[dict[str, Any]] = []
        for raw_task in payload.get("tasks", []):
            task = dict(raw_task) if isinstance(raw_task, dict) else raw_task
            if not isinstance(task, dict):
                tasks.append(task)
                continue
            slug = task.get("slug")
            owner = task.get("owner")
            permanent_owner = task.get("owner_agent") or (
                owner.get("slug") if isinstance(owner, dict) else None
            )
            try:
                claim = (
                    claim_reader(slug, include_terminal=False)
                    if isinstance(slug, str)
                    else None
                )
            except (RuntimeError, ValueError):
                claim = None
            lease = leases.get(claim.delegation_slug) if claim is not None else None
            if (
                claim is not None
                and lease is not None
                and claim.expires_at > now
                and claim.permanent_owner == permanent_owner
                and claim.executor_agent == lease.executor_agent
                and claim.permanent_owner == lease.source_agent
            ):
                task["temporary_execution"] = {
                    "executor_agent": claim.executor_agent,
                    "permanent_owner": claim.permanent_owner,
                    "delegation_slug": claim.delegation_slug,
                    "claimed_at": claim.claimed_at.isoformat(),
                    "expires_at": claim.expires_at.isoformat(),
                }
            tasks.append(task)
        return {**payload, "tasks": tasks}

    def refresh_handoff_execution_authority(
        record: object,
        *,
        observed_at: datetime,
        include_task: bool = False,
    ) -> None:
        """Refresh canonical delegation and priority controls just before execution."""
        if handoff_store is None:
            raise RuntimeError("handoff storage is unavailable")
        if include_task:
            task_slug = getattr(record, "task_slug", None)
            if not isinstance(task_slug, str):
                raise ValueError("handoff task identity is invalid")
            task = canonical_mapping(adapter.get_task(task_slug))
            owner_agent = task.get("owner_agent")
            status = task.get("status")
            if task.get("slug") != task_slug or not isinstance(status, str):
                raise ValueError("canonical task authority readback is invalid")
            if owner_agent is not None and not isinstance(owner_agent, str):
                raise ValueError("canonical task owner readback is invalid")
            task_version = "task/" + hashlib.sha256(
                json.dumps(
                    {
                        "slug": task_slug,
                        "owner_agent": owner_agent,
                        "status": status,
                        "updated_at": task.get("updated_at"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            handoff_store.observe_task_authority(
                task_slug,
                owner_agent=owner_agent,
                status=status,
                version=task_version,
                observed_at=observed_at,
            )
        delegation_slug = getattr(record, "delegation_slug", None)
        if delegation_slug is None:
            return
        leases = tuple(adapter.list_agent_delegations())
        matches = tuple(lease for lease in leases if lease.slug == delegation_slug)
        if len(matches) != 1:
            handoff_store.observe_delegation_absence(
                delegation_slug, observed_at=observed_at
            )
            return
        lease = matches[0]
        if not isinstance(lease, AgentDelegationLease):
            raise ValueError("canonical delegation readback is invalid")
        handoff_store.observe_delegation_authority(lease, observed_at=observed_at)
        owned_work_ready, version = _canonical_executor_priority_snapshot(
            adapter, lease.executor_agent
        )
        handoff_store.observe_executor_priority(
            lease.executor_agent,
            owned_work_ready=owned_work_ready,
            version=version,
            observed_at=observed_at,
        )

    def mutation_snapshot(
        task: object,
        *,
        todo: object | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        task_value = canonical_mapping(task)
        owner = task_value.get("owner_agent")
        handoff = task_value.get("handoff")
        if not isinstance(owner, str) and isinstance(handoff, dict):
            owner = handoff.get("resume_owner")
        assigned_to = [owner] if isinstance(owner, str) else []
        return {
            "task_slug": task_value.get("slug"),
            "task": {**task_value, "assigned_to": assigned_to},
            "todo": canonical_mapping(todo) if todo is not None else None,
            "route": route,
        }

    def read_todo_mutation_snapshot(todo_slug: str) -> dict[str, Any]:
        todo = adapter.get_todo(todo_slug)
        todo_value = canonical_mapping(todo)
        task = adapter.get_task(str(todo_value.get("parent_task")))
        return mutation_snapshot(task, todo=todo)

    def after_canonical_mutation(
        before: dict[str, Any],
        after: dict[str, Any],
        receipt: object,
        *,
        mutation_kind: str,
    ) -> None:
        if active_handoff_event_bridge is None:
            return
        receipt_value = canonical_mapping(receipt)
        receipt_value["mutation_kind"] = mutation_kind
        try:
            active_handoff_event_bridge.after_verified_mutation(
                before, after, receipt_value, clock()
            )
        except Exception:
            # The canonical write is already verified. Dispatcher persistence or
            # delivery is operational evidence and must never roll it back.
            log_reader.append_gtasks(
                severity="error",
                message="Verified canonical mutation could not enter the handoff dispatcher.",
                now=clock(),
            )

    def partial_mutation_attention(
        before: dict[str, Any] | None,
        *,
        slug: str,
        mutation_kind: str,
    ) -> None:
        if active_handoff_event_bridge is None or before is None:
            return
        after_canonical_mutation(
            before,
            before,
            {
                "verified": False,
                "canonical_event_id": slug,
            },
            mutation_kind=mutation_kind,
        )

    def after_verified_todo_mutation(
        before: dict[str, Any],
        receipt: object,
        *,
        mutation_kind: str,
        task_slug: str | None = None,
        todo_slug: str | None = None,
    ) -> None:
        if active_handoff_event_bridge is None:
            return
        receipt_value = canonical_mapping(receipt)
        receipt_todo = canonical_mapping(receipt_value.get("todo"))
        attention_slug = (
            receipt_todo.get("slug")
            or todo_slug
            or task_slug
            or str(before.get("task_slug") or "tasks/unknown")
        )
        try:
            if todo_slug is not None:
                after = read_todo_mutation_snapshot(todo_slug)
            else:
                after_task = adapter.get_task(str(task_slug))
                after = mutation_snapshot(after_task, todo=receipt_todo)
        except (DomainValidationError, GBrainError, KeyError, ValueError):
            partial_mutation_attention(
                before,
                slug=str(attention_slug),
                mutation_kind=f"{mutation_kind}_post_write_readback",
            )
            return
        after_canonical_mutation(
            before,
            after,
            receipt_value,
            mutation_kind=mutation_kind,
        )

    def read_snapshot(force: bool = False):
        return active_read_cache.read(
            "tasks",
            lambda: build_task_snapshot(adapter, clock().date()),
            ttl_seconds=SNAPSHOT_CACHE_SECONDS,
            force=force,
        )

    def read_proposals(force: bool = False):
        def load() -> dict[str, Any]:
            # A task refresh is the action-first surface. Let an already-running
            # task read finish before the much larger proposal projection starts
            # consuming the shared, rate-safe GBrain CLI lane.
            active_read_cache.wait_for_idle(
                "tasks",
                timeout_seconds=35,
            )
            return adapter.list_proposals().to_dict()

        return active_read_cache.read(
            "proposals",
            load,
            ttl_seconds=PROPOSAL_CACHE_SECONDS,
            force=force,
        )

    def read_system_tickets(
        force: bool = False,
        *,
        include_completed: bool = True,
    ):
        cache_key = "system_tickets_all" if include_completed else "system_tickets"
        return active_read_cache.read(
            cache_key,
            lambda: adapter.list_system_tickets(
                include_completed=include_completed,
            ).to_dict(),
            ttl_seconds=SYSTEM_TICKET_CACHE_SECONDS,
            force=force,
        )

    class GTasksHandler(BaseHTTPRequestHandler):
        server_version = f"GTasks/{__version__}"

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data: blob: http://127.0.0.1:8788; connect-src 'self'; frame-ancestors 'none'",
            )

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            operational_messages = {
                "gbrain_unavailable": "A GBrain operation was unavailable.",
                "gbrain_refresh_delayed": "A GBrain operation was unavailable.",
                "partial_write": (
                    "A GBrain mutation needs verification before it is retried."
                ),
                "warning_state_unavailable": (
                    "Inbox warning preference storage is unavailable."
                ),
            }
            code = payload.get("code")
            if status >= 500 and code in operational_messages:
                log_reader.append_gtasks(
                    severity="error" if status >= 502 else "warning",
                    message=operational_messages[code],
                    now=clock(),
                )
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()

        def _handoff_identity(self) -> HandoffDispatcherIdentity | None:
            identity = active_handoff_auth.resolve(self.headers.get("Authorization"))
            if identity is None:
                self._json(
                    HTTPStatus.UNAUTHORIZED,
                    {
                        "error": "A valid dispatcher bearer credential is required.",
                        "code": "handoff_auth_required",
                    },
                )
            return identity

        def _canonical_handoff_registration(
            self,
            identity: HandoffDispatcherIdentity,
            registration_id: str,
        ) -> AgentRegistration:
            if active_handoff_event_bridge is not None:
                if not callable(active_handoff_registration_reference_reader):
                    raise RuntimeError("canonical registration reader unavailable")
                registrations: dict[str, AgentRegistration] = {}
                route_agents: dict[str, set[str]] = {}
                for configured_identity in active_handoff_auth.identities:
                    canonical = active_handoff_registration_reference_reader(
                        configured_identity.agent_slug,
                        configured_identity.registration_id,
                    )
                    if (
                        not isinstance(canonical, AgentRegistration)
                        or canonical.verified is not True
                        or canonical.agent_slug != configured_identity.agent_slug
                        or canonical.registration_id
                        != configured_identity.registration_id
                        or canonical.reference != configured_identity.registration_id
                        or canonical.lease_identity
                        != configured_identity.registration_id
                    ):
                        raise _HandoffIdentityMismatch(
                            "canonical dispatcher registration does not match"
                        )
                    registrations[canonical.agent_slug] = canonical
                    route_agents.setdefault(canonical.route, set()).add(
                        canonical.agent_slug
                    )
                if any(
                    not agent_route_group_is_approved(agents)
                    for agents in route_agents.values()
                ):
                    raise _HandoffIdentityMismatch(
                        "canonical dispatcher registration does not match"
                    )
                canonical = registrations.get(identity.agent_slug)
                if (
                    canonical is None
                    or not hmac.compare_digest(
                        hashlib.sha256(registration_id.encode("utf-8")).hexdigest(),
                        identity.registration_id,
                    )
                ):
                    raise _HandoffIdentityMismatch(
                        "canonical dispatcher registration does not match"
                    )
                return canonical
            if not callable(active_handoff_registration_validator):
                raise RuntimeError("canonical registration reader unavailable")
            canonical = active_handoff_registration_validator(
                identity.agent_slug, registration_id
            )
            if (
                not isinstance(canonical, AgentRegistration)
                or canonical.verified is not True
                or canonical.agent_slug != identity.agent_slug
                or canonical.registration_id != registration_id
                or canonical.reference != identity.registration_id
            ):
                raise _HandoffIdentityMismatch(
                    "canonical dispatcher registration does not match"
                )
            return canonical

        def _handoff_mutation_headers(
            self,
            identity: HandoffDispatcherIdentity,
        ) -> tuple[str, str, int, str] | None:
            registration_id = self.headers.get("X-Handoff-Registration-ID")
            capability = self.headers.get("X-Handoff-Lease-Capability")
            raw_generation = self.headers.get("X-Handoff-Lease-Generation")
            mutation_id = self.headers.get("Idempotency-Key")
            try:
                generation = int(raw_generation or "")
            except ValueError:
                generation = 0
            if (
                not isinstance(registration_id, str)
                or not registration_id
                or not hmac.compare_digest(
                    hashlib.sha256(registration_id.encode("utf-8")).hexdigest(),
                    identity.registration_id,
                )
                or not isinstance(capability, str)
                or not capability
                or len(capability) > 512
                or generation < 1
                or not isinstance(mutation_id, str)
                or not mutation_id
            ):
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {
                        "error": "A valid lease capability, generation, and idempotency key are required.",
                        "code": "invalid_handoff_lease",
                    },
                )
                return None
            lease_identity = (
                identity.registration_id
                if active_handoff_event_bridge is not None
                else registration_id
            )
            return lease_identity, capability, generation, mutation_id

        def _read_handoff_events(
            self,
            *,
            task_slug: str | None,
        ) -> None:
            if handoff_store is None:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": "Handoff audit storage is unavailable.",
                        "code": "handoff_store_unavailable",
                    },
                )
                return
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            allowed = {
                "limit",
                "after_sequence",
                "agent_slug",
                "status",
                "event_type",
                "correlation_id",
                "occurred_after",
                "occurred_before",
                "export",
            }
            if any(key not in allowed or len(values) != 1 for key, values in query.items()):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "Unsupported or repeated handoff event filter.",
                        "code": "invalid_handoff_event_filter",
                    },
                )
                return
            try:
                limit = int(query.get("limit", ["50"])[0])
                after_sequence = int(query.get("after_sequence", ["0"])[0])
                export = query.get("export", ["0"])[0]
                if export not in {"0", "1"}:
                    raise ValueError("export must be 0 or 1")
                def timestamp_filter(name: str) -> datetime | None:
                    raw = query.get(name, [None])[0] or None
                    if raw is None:
                        return None
                    try:
                        return datetime.fromisoformat(raw)
                    except ValueError as exc:
                        raise ValueError(f"{name} must be an ISO 8601 timestamp") from exc

                filters = {
                    "limit": limit,
                    "after_sequence": after_sequence,
                    "task_slug": task_slug,
                    "agent_slug": query.get("agent_slug", [None])[0] or None,
                    "status": query.get("status", [None])[0] or None,
                    "event_type": query.get("event_type", [None])[0] or None,
                    "correlation_id": query.get("correlation_id", [None])[0]
                    or None,
                    "occurred_after": timestamp_filter("occurred_after"),
                    "occurred_before": timestamp_filter("occurred_before"),
                }
                payload = (
                    handoff_store.export_events(**filters)
                    if export == "1"
                    else handoff_store.query_events(**filters).to_dict()
                )
                if export != "1":
                    payload = {
                        **payload,
                        "events": [
                            {
                                **event,
                                "task_ref": self._handoff_task_ref(
                                    event.get("task_slug")
                                ),
                            }
                            for event in payload.get("events", [])
                            if isinstance(event, dict)
                        ],
                    }
            except ValueError as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(exc), "code": "invalid_handoff_event_filter"},
                )
                return
            self._json(HTTPStatus.OK, payload)

        def _handoff_task_ref(self, raw_slug: object) -> dict[str, object]:
            unavailable: dict[str, object] = {
                "available": False,
                "label": "Task unavailable",
                "reason": "Mission Control could not verify a navigable canonical Task for this handoff event.",
            }
            if not isinstance(raw_slug, str) or not raw_slug.startswith("tasks/"):
                return unavailable

            try:
                system_result = read_system_tickets(force=False)
                tickets = (
                    system_result.payload.get("tickets", [])
                    if isinstance(system_result.payload, dict)
                    else []
                )
                for ticket in tickets:
                    if (
                        isinstance(ticket, dict)
                        and ticket.get("slug") == raw_slug
                        and isinstance(ticket.get("title"), str)
                        and ticket["title"].strip()
                    ):
                        return {
                            "available": True,
                            "slug": raw_slug,
                            "title": ticket["title"].strip(),
                            "surface": "system_ticket",
                        }
            except Exception:
                # A System Ticket read problem must not make handoff history
                # unusable.  Fall through to the normal task verifier.
                pass

            try:
                with foreground_operation():
                    task = adapter.get_task(raw_slug)
            except Exception:
                return unavailable
            title = getattr(task, "title", "")
            slug = getattr(task, "slug", "")
            if slug != raw_slug or not isinstance(title, str) or not title.strip():
                return unavailable
            return {
                "available": True,
                "slug": raw_slug,
                "title": title.strip(),
                "surface": "task",
            }

        def _read_json(
            self, *, max_request_bytes: int = MAX_REQUEST_BYTES
        ) -> dict[str, Any] | None:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length."})
                return None
            if length <= 0 or length > max_request_bytes:
                self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    if length > max_request_bytes
                    else HTTPStatus.BAD_REQUEST,
                    {"error": "Request body size is invalid."},
                )
                return None
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Request body must be valid JSON."},
                )
                return None
            if not isinstance(payload, dict):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Request body must be a JSON object."},
                )
                return None
            return payload

        def _read_avatar_upload(self) -> tuple[str, bytes, str] | None:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data;") or "boundary=" not in content_type:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Avatar upload must be a multipart image file."})
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid upload length."})
                return None
            if length <= 0 or length > MAX_AVATAR_BYTES + 8192:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Avatar images must be 5 MB or smaller."})
                return None
            boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
            body = self.rfile.read(length)
            marker = b"--" + boundary
            for part in body.split(marker):
                if b'name="file"' not in part:
                    continue
                try:
                    header, file_bytes = part.split(b"\r\n\r\n", 1)
                except ValueError:
                    continue
                file_bytes = file_bytes.rsplit(b"\r\n", 1)[0]
                match = re.search(br'filename="([^"\r\n]+)"', header)
                mime_match = re.search(br'Content-Type:\s*([^\r\n;]+)', header, re.I)
                filename = (match.group(1).decode("utf-8", "replace") if match else "avatar")
                mime = (mime_match.group(1).decode("ascii", "replace").lower() if mime_match else "")
                if mime not in ALLOWED_AVATAR_TYPES or not file_bytes or len(file_bytes) > MAX_AVATAR_BYTES:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Choose a PNG, JPEG, GIF, or WebP avatar up to 5 MB."})
                    return None
                magic = (
                    (mime == "image/jpeg" and file_bytes.startswith(b"\xff\xd8\xff"))
                    or (mime == "image/png" and file_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
                    or (mime == "image/gif" and file_bytes.startswith((b"GIF87a", b"GIF89a")))
                    or (mime == "image/webp" and file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP")
                )
                if not magic:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "The selected file does not match its image type."})
                    return None
                return filename, file_bytes, mime
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Choose an avatar image file."})
            return None

        def _attach_avatar(self, agent_slug: str, filename: str, data: bytes, mime: str) -> dict[str, Any]:
            boundary = f"gtasks-{uuid.uuid4().hex}"
            payload = b"".join((
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{filename.replace(chr(34), "")}"\r\n'.encode(),
                f"Content-Type: {mime}\r\n\r\n".encode(), data, b"\r\n",
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"description\"\r\n\r\nGTasks agent avatar\r\n".encode(),
                f"--{boundary}--\r\n".encode(),
            ))
            endpoint = f"{stargraph_url}/api/entity-attach-file/{quote(agent_slug, safe='')}"
            request = Request(endpoint, data=payload, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
            try:
                with urlopen(request, timeout=30) as response:
                    result = json.loads(response.read())
            except Exception as exc:
                raise GBrainError("Memory Stargraph avatar storage is unavailable") from exc
            local_media = result.get("local_media") if isinstance(result, dict) else None
            served_url = local_media.get("served_url") if isinstance(local_media, dict) else None
            if not result.get("ok") or not isinstance(served_url, str) or not served_url.startswith("/media/"):
                raise GBrainError("Memory Stargraph did not return durable avatar evidence")
            return result

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": __version__,
                        "canonical_store": "gbrain",
                        "default_due_day": "task_creation_day",
                        "default_goal_target_day": "end_of_creation_quarter",
                        "mutations": "explicit_user_actions_only",
                        "active_root": ACTIVE_ROOT,
                        "completed_root": COMPLETED_ROOT,
                        "goals_root": GOALS_ROOT,
                        "projects_root": PROJECTS_ROOT,
                        "warning_dismissals": "user_scoped_local_state",
                        "operational_logs": "privacy_safe_read_only",
                        "queue_reader_dependency": "optional",
                        "job_applied_bound_task": JOB_APPLIED_BOUND_TASK_SLUG,
                        "agent_work_roots": [
                            root for _agent, root in EXISTING_CODEX_AGENT_SCOPES
                        ],
                        "proposals_root": PROPOSALS_ROOT,
                        "qa_fixtures_root": QA_FIXTURES_ROOT,
                        "read_surfaces": "last_verified_local_cache",
                    },
                )
                return
            if path == "/api/agent-delegations":
                try:
                    with foreground_operation():
                        leases = adapter.list_agent_delegations()
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_agent_delegation"},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "delegations": [delegation_payload(lease) for lease in leases],
                    },
                )
                return
            if path == "/api/releases":
                self._json(HTTPStatus.OK, release_payload())
                return
            if path == "/api/handoff-events":
                self._read_handoff_events(task_slug=None)
                return
            handoff_event_prefix = "/api/tasks/"
            handoff_event_suffix = "/handoff-events"
            if path.startswith(handoff_event_prefix) and path.endswith(
                handoff_event_suffix
            ):
                task_slug = unquote(
                    path[
                        len(handoff_event_prefix) : -len(handoff_event_suffix)
                    ]
                )
                self._read_handoff_events(task_slug=task_slug)
                return
            todo_list_prefix = "/api/tasks/"
            todo_list_suffix = "/todos"
            if path.startswith(todo_list_prefix) and path.endswith(todo_list_suffix):
                task_slug = unquote(
                    path[len(todo_list_prefix) : -len(todo_list_suffix)]
                )
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if any(
                    key not in {"status", "cursor", "limit"} or len(values) != 1
                    for key, values in query.items()
                ):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Unsupported or repeated TODO filter."},
                    )
                    return
                status_filter = query.get("status", [None])[0] or None
                try:
                    cursor = int(query.get("cursor", ["0"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    with foreground_operation():
                        payload = adapter.list_task_todos(
                            task_slug,
                            status=status_filter,
                            cursor=cursor,
                            limit=limit,
                        ).to_dict()
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_todo"},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, payload)
                return
            if path == "/api/agents":
                try:
                    payload = adapter.list_agent_profiles().to_dict()
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            if path == "/api/agent-work":
                try:
                    payload = decorate_agent_work_execution(
                        adapter.list_agent_work().to_dict()
                    )
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            if path == "/api/proposals":
                force = urlsplit(self.path).query == "refresh=1"
                result = read_proposals(force=force)
                if result.payload is None:
                    if result.state.get("status") == "error":
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "error": result.state.get("error") or "The canonical GBrain proposal refresh failed.",
                                "code": "gbrain_refresh_delayed",
                                "read_state": result.state,
                            },
                        )
                        return
                    self._json(
                        HTTPStatus.ACCEPTED,
                        {
                            "proposals": [],
                            "issues": [],
                            "read_state": result.state,
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    decorate_issues(
                        {**result.payload, "read_state": result.state}
                    ),
                )
                return
            if path == "/api/artifacts":
                query = parse_qs(
                    urlsplit(self.path).query, keep_blank_values=True
                )
                allowed = {
                    "agent", "task", "project", "goal", "kind", "cursor", "limit"
                }
                if any(
                    key not in allowed or len(values) != 1
                    for key, values in query.items()
                ):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": "Artifact filters are unsupported or repeated.",
                            "code": "invalid_artifact_filters",
                        },
                    )
                    return
                try:
                    cursor = int(query.get("cursor", ["0"])[0])
                    limit = int(query.get("limit", ["25"])[0])
                    if cursor < 0 or limit < 1 or limit > 50:
                        raise ValueError
                    kind = query.get("kind", [None])[0] or None
                    if kind is not None and kind not in ARTIFACT_KINDS:
                        raise ValueError
                    agent_filter = query.get("agent", [None])[0] or None
                    if agent_filter is not None and agent_filter not in dict(AGENT_SCOPES):
                        raise ValueError
                    task_filter = query.get("task", [None])[0] or None
                    project_filter = query.get("project", [None])[0] or None
                    goal_filter = query.get("goal", [None])[0] or None
                    for value, namespace in (
                        (task_filter, "tasks"),
                        (project_filter, "projects"),
                        (goal_filter, "goals"),
                    ):
                        if value is not None:
                            _canonical_uuid_slug(value, namespace)
                    with foreground_operation():
                        payload = adapter.list_agent_artifacts(
                            agent=agent_filter,
                            task=task_filter,
                            project=project_filter,
                            goal=goal_filter,
                            kind=kind,
                            cursor=cursor,
                            limit=limit,
                        ).to_dict()
                except (DomainValidationError, ValueError):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": "Artifact cursor must be nonnegative, limit must be 1 to 50, and filters must use canonical values.",
                            "code": "invalid_artifact_filters",
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            artifact_prefix = "/api/artifacts/"
            if path.startswith(artifact_prefix):
                artifact_slug = unquote(path[len(artifact_prefix) :])
                try:
                    _canonical_uuid_slug(
                        artifact_slug, "artifacts", required_version=4
                    )
                except ValueError:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "Artifact slug is invalid.", "code": "invalid_artifact"},
                    )
                    return
                try:
                    with foreground_operation():
                        artifact = adapter.get_agent_artifact(artifact_slug)
                except StopIteration:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Artifact was not found.", "code": "artifact_not_found"},
                    )
                    return
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_artifact"},
                    )
                    return
                except GBrainCommandError as exc:
                    if is_page_not_found_error(exc):
                        self._json(
                            HTTPStatus.NOT_FOUND,
                            {
                                "error": "Artifact was not found.",
                                "code": "artifact_not_found",
                            },
                        )
                    else:
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {"error": str(exc), "code": "gbrain_unavailable"},
                        )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "artifact": artifact.to_dict(),
                        "readback": {"verified": True},
                    },
                )
                return
            if path == "/api/logs":
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if any(
                    key not in {"severity", "component", "cursor", "limit"}
                    or len(values) != 1
                    for key, values in query.items()
                ):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Unsupported or repeated log filter."},
                    )
                    return
                severity = query.get("severity", [None])[0] or None
                component = query.get("component", [None])[0] or None
                if severity is not None and severity not in SEVERITIES:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Unknown log severity filter."},
                    )
                    return
                if (
                    component is not None
                    and not COMPONENT_PATTERN.fullmatch(component)
                ):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Invalid log component filter."},
                    )
                    return
                try:
                    cursor = int(query.get("cursor", ["0"])[0])
                    limit = int(
                        query.get("limit", [str(DEFAULT_PAGE_SIZE)])[0]
                    )
                except ValueError:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Log cursor and limit must be integers."},
                    )
                    return
                if cursor < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                f"Log cursor must be nonnegative and limit must "
                                f"be between 1 and {MAX_PAGE_SIZE}."
                            )
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    log_reader.page(
                        severity=severity,
                        component=component,
                        cursor=cursor,
                        limit=limit,
                    ),
                )
                return
            if path == "/api/tasks":
                force = urlsplit(self.path).query == "refresh=1"
                result = read_snapshot(force=force)
                if result.payload is None:
                    if result.state.get("status") == "error":
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "error": result.state.get("error") or "The canonical GBrain task refresh failed.",
                                "code": "gbrain_refresh_delayed",
                                "read_state": result.state,
                            },
                        )
                        return
                    self._json(
                        HTTPStatus.ACCEPTED,
                        {"read_state": result.state},
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    decorate_issues(
                        {**result.payload, "read_state": result.state}
                    ),
                )
                return
            exact_task_prefix = "/api/tasks/"
            if (
                path.startswith(exact_task_prefix)
                and "/" not in path[len(exact_task_prefix) :]
            ):
                task_slug = unquote(path[len(exact_task_prefix) :])
                if not task_slug.startswith("tasks/"):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "A canonical task slug is required.", "code": "invalid_task"},
                    )
                    return
                try:
                    with foreground_operation():
                        task_payload = exact_task_api_payload(adapter, task_slug)
                except GBrainCommandError as exc:
                    if is_page_not_found_error(exc):
                        self._json(
                            HTTPStatus.NOT_FOUND,
                            {"error": "Task not found.", "code": "task_not_found"},
                        )
                        return
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_task"},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, {"task": task_payload})
                return
            if path == "/api/projects":
                try:
                    payload = adapter.list_projects().to_dict()
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            if path == "/api/ical-events":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    start = date.fromisoformat(query.get("start", [""])[0])
                    end = date.fromisoformat(query.get("end", [""])[0])
                    if end <= start or (end - start).days > 45:
                        raise ValueError
                except ValueError:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Calendar range must be a valid local range of up to 45 days.", "code": "invalid_calendar_range"})
                    return
                try:
                    payload = active_ical_reader.read(
                        start,
                        end,
                        calendar_ids=active_calendar_preferences.selected_calendar_ids(),
                    )
                    payload["selected_calendar_ids"] = list(
                        active_calendar_preferences.selected_calendar_ids()
                    )
                    self._json(HTTPStatus.OK, payload)
                except ICalendarError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "calendar_unavailable"})
                return
            if path == "/api/ical-calendars":
                try:
                    payload = active_ical_reader.calendars()
                    payload["selected_calendar_ids"] = list(
                        active_calendar_preferences.selected_calendar_ids()
                    )
                    self._json(HTTPStatus.OK, payload)
                except ICalendarError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "calendar_unavailable"})
                return
            system_ticket_read_prefix = "/api/system-tickets/"
            if path.startswith(system_ticket_read_prefix) and "/" not in path[len(system_ticket_read_prefix) :]:
                ticket_slug = unquote(path[len(system_ticket_read_prefix) :])
                try:
                    result = read_system_tickets(force=False)
                    if result.payload is None:
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "error": result.state.get("error")
                                or "The canonical System Ticket could not be read.",
                                "code": "gbrain_refresh_delayed",
                                "read_state": result.state,
                            },
                        )
                        return
                    ticket = next(
                        (
                            item
                            for item in result.payload.get("tickets", [])
                            if isinstance(item, dict) and item.get("slug") == ticket_slug
                        ),
                        None,
                    )
                    if ticket is None:
                        self._json(
                            HTTPStatus.NOT_FOUND,
                            {"error": "System Ticket was not found.", "code": "system_ticket_not_found"},
                        )
                        return
                    self._json(
                        HTTPStatus.OK,
                        {"ticket": ticket, "read_state": result.state},
                    )
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                return
            if path == "/api/system-tickets":
                try:
                    query = parse_qs(urlsplit(self.path).query)
                    completed_only = query.get("completed_only", ["0"])[0] == "1"
                    include_completed = query.get("include_completed", ["0"])[0] == "1"
                    offset = int(query.get("offset", ["0"])[0])
                    limit = int(query.get("limit", ["5"])[0])
                    if offset < 0 or limit < 1 or limit > 5:
                        raise ValueError
                    force = query.get("refresh", ["0"])[0] == "1"
                    result = read_system_tickets(
                        force=force,
                        include_completed=(include_completed or completed_only),
                    )
                    if result.payload is None:
                        if result.state.get("status") == "error":
                            self._json(
                                HTTPStatus.SERVICE_UNAVAILABLE,
                                {
                                    "error": result.state.get("error")
                                    or "The canonical System Ticket refresh failed.",
                                    "code": "gbrain_refresh_delayed",
                                    "read_state": result.state,
                                },
                            )
                            return
                        self._json(
                            HTTPStatus.ACCEPTED,
                            {
                                "tickets": [],
                                "issues": [],
                                "read_state": result.state,
                            },
                        )
                        return
                    payload = {**result.payload, "read_state": result.state}
                    tickets = list(payload.get("tickets", []))
                    if completed_only:
                        completed = [
                            ticket
                            for ticket in tickets
                            if ticket.get("status") == "completed"
                        ]
                        page = completed[offset : offset + limit]
                        payload["tickets"] = page
                        payload["pagination"] = {
                            "offset": offset,
                            "limit": limit,
                            "has_more": offset + len(page) < len(completed),
                        }
                    elif not include_completed:
                        payload["tickets"] = [
                            ticket
                            for ticket in tickets
                            if ticket.get("status") != "completed"
                        ]
                    self._json(HTTPStatus.OK, decorate_issues(payload))
                except ValueError:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "System Ticket pagination requires offset >= 0 and limit from 1 to 5.",
                            "code": "invalid_system_ticket_page",
                        },
                    )
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                return
            goal_prefix = "/api/goals/"
            goal_suffix = "/relationships"
            if path.startswith(goal_prefix) and path.endswith(goal_suffix):
                goal_slug = unquote(path[len(goal_prefix) : -len(goal_suffix)])
                try:
                    relationship_read = adapter.read_goal_relationships(goal_slug)
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_goal"},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, relationship_read.to_dict())
                return
            self._serve_static(path)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/artifact-review-references":
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) != {"task_slug", "artifact_slug"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Artifact review references require canonical task_slug and artifact_slug.",
                            "code": "invalid_artifact_review_reference",
                        },
                    )
                    return
                try:
                    task_slug = _canonical_uuid_slug(payload["task_slug"], "tasks")
                    artifact_slug = _canonical_uuid_slug(
                        payload["artifact_slug"], "artifacts", required_version=4
                    )
                    with foreground_operation():
                        receipt = adapter.add_artifact_review_reference(
                            task_slug, artifact_slug
                        )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_artifact_review_reference"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(
                    HTTPStatus.OK if receipt.idempotent else HTTPStatus.CREATED,
                    {"receipt": receipt.to_dict()},
                )
                return
            if path == "/api/agent-delegations":
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "source_agent",
                    "executor_agent",
                    "starts_at",
                    "ends_at",
                    "display_timezone",
                    "allowed_operations",
                }
                if set(payload) != required:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "agent delegation requires the exact confirmation fields.",
                            "code": "invalid_agent_delegation",
                        },
                    )
                    return
                try:
                    source_agent = payload["source_agent"]
                    executor_agent = payload["executor_agent"]
                    display_timezone = payload["display_timezone"]
                    raw_operations = payload["allowed_operations"]
                    if (
                        not isinstance(source_agent, str)
                        or not isinstance(executor_agent, str)
                        or not isinstance(display_timezone, str)
                        or not isinstance(raw_operations, list)
                        or not raw_operations
                        or any(not isinstance(item, str) for item in raw_operations)
                        or len(set(raw_operations)) != len(raw_operations)
                        or not set(raw_operations).issubset(
                            {"task_status", "todo", "comment", "artifact"}
                        )
                    ):
                        raise ValueError("agent delegation fields are invalid")
                    starts_at = datetime.fromisoformat(
                        str(payload["starts_at"]).replace("Z", "+00:00")
                    )
                    ends_at = datetime.fromisoformat(
                        str(payload["ends_at"]).replace("Z", "+00:00")
                    )
                    if (
                        starts_at.tzinfo is None
                        or starts_at.utcoffset() is None
                        or ends_at.tzinfo is None
                        or ends_at.utcoffset() is None
                    ):
                        raise ValueError(
                            "starts_at and ends_at must be aware UTC instants"
                        )
                    starts_at = starts_at.astimezone(timezone.utc)
                    ends_at = ends_at.astimezone(timezone.utc)
                    now = clock().astimezone(timezone.utc)
                    if ends_at <= now:
                        raise ValueError("agent delegation must end in the future")
                    canonical_input = json.dumps(
                        {
                            "source_agent": source_agent,
                            "executor_agent": executor_agent,
                            "starts_at": starts_at.isoformat(),
                            "ends_at": ends_at.isoformat(),
                            "display_timezone": display_timezone,
                            "allowed_operations": raw_operations,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    slug = "agent-delegations/" + str(
                        uuid.uuid5(uuid.NAMESPACE_URL, canonical_input)
                    )
                    with active_delegation_lock.hold(slug):
                        existing = next(
                            (
                                item
                                for item in adapter.list_agent_delegations()
                                if item.slug == slug
                            ),
                            None,
                        )
                        if existing is not None:
                            requested = (
                                source_agent,
                                executor_agent,
                                starts_at,
                                ends_at,
                                display_timezone,
                                tuple(raw_operations),
                            )
                            canonical = (
                                existing.source_agent,
                                existing.executor_agent,
                                existing.starts_at,
                                existing.ends_at,
                                existing.display_timezone,
                                existing.allowed_operations,
                            )
                            if requested != canonical:
                                self._json(
                                    HTTPStatus.CONFLICT,
                                    {
                                        "error": "agent delegation idempotency input conflicts with canonical readback.",
                                        "code": "delegation_conflict",
                                    },
                                )
                                return
                            lease = existing
                            with foreground_operation():
                                receipt = adapter.create_agent_delegation(existing)
                                response = delegation_response(existing, receipt)
                            response_status = HTTPStatus.OK
                        else:
                            state = (
                                DelegationState.SCHEDULED
                                if starts_at > now
                                else DelegationState.ACTIVE
                            )
                            lease = AgentDelegationLease(
                                slug=slug,
                                source_agent=source_agent,
                                executor_agent=executor_agent,
                                authorized_by=TONY_PROFILE_SLUG,
                                starts_at=starts_at,
                                ends_at=ends_at,
                                display_timezone=display_timezone,
                                allowed_operations=tuple(raw_operations),
                                state=state,
                                created_at=now,
                                updated_at=now,
                            )
                            with foreground_operation():
                                receipt = adapter.create_agent_delegation(lease)
                                response = delegation_response(lease, receipt)
                            response_status = HTTPStatus.CREATED
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_agent_delegation"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                except OSError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "delegation_lock_unavailable"},
                    )
                    return
                if response is None:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": "agent delegation mutation lacked exact verified canonical readback.",
                            "code": "delegation_not_verified",
                            "slug": lease.slug,
                        },
                    )
                    return
                self._json(response_status, response)
                return
            if path == "/api/handoffs/preflight":
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                registration_id = payload.get("registration_id")
                if set(payload) != {"registration_id"} or not isinstance(
                    registration_id, str
                ) or not registration_id:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Dispatcher preflight requires one registration identity.",
                            "code": "invalid_handoff_preflight",
                        },
                    )
                    return
                registration_ref = hashlib.sha256(
                    registration_id.encode("utf-8")
                ).hexdigest()
                if not hmac.compare_digest(identity.registration_id, registration_ref):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher registration does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                try:
                    canonical = self._canonical_handoff_registration(
                        identity, registration_id
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except Exception:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff route readback is unavailable.",
                            "code": "handoff_route_unavailable",
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "verified": True,
                        "agent_slug": canonical.agent_slug,
                        "registration_ref": canonical.reference,
                        "route": canonical.route,
                    },
                )
                return
            if path == "/api/handoffs/claim":
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) != {
                    "registration_id",
                    "wait_seconds",
                    "lease_seconds",
                }:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "A handoff claim requires the exact supported fields.",
                            "code": "invalid_handoff_claim",
                        },
                    )
                    return
                registration_id = payload.get("registration_id")
                wait_seconds = payload.get("wait_seconds")
                lease_seconds = payload.get("lease_seconds")
                if (
                    not isinstance(registration_id, str)
                    or not registration_id
                    or isinstance(wait_seconds, bool)
                    or not isinstance(wait_seconds, int)
                    or wait_seconds < 0
                    or wait_seconds > 25
                    or isinstance(lease_seconds, bool)
                    or not isinstance(lease_seconds, int)
                    or lease_seconds < 5
                    or lease_seconds > 120
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Claim wait must be 0 to 25 seconds and lease must be 5 to 120 seconds.",
                            "code": "invalid_handoff_claim",
                        },
                    )
                    return
                registration_ref = hashlib.sha256(
                    registration_id.encode("utf-8")
                ).hexdigest()
                if not hmac.compare_digest(
                    identity.registration_id, registration_ref
                ):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher registration does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                if handoff_store is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff route readback is unavailable.",
                            "code": "handoff_route_unavailable",
                        },
                    )
                    return
                def verified_claim():
                    canonical = self._canonical_handoff_registration(
                        identity, registration_id
                    )
                    return handoff_store.claim(
                        canonical.lease_identity,
                        now=clock().astimezone(timezone.utc),
                        lease_seconds=lease_seconds,
                        expected_agent_slug=canonical.agent_slug,
                        expected_registration_ref=canonical.reference,
                        expected_route=canonical.route,
                    )

                try:
                    claim = verified_claim()
                    if claim is None and wait_seconds:
                        active_handoff_waiter(wait_seconds)
                        claim = verified_claim()
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except Exception:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff route readback is unavailable.",
                            "code": "handoff_route_unavailable",
                        },
                    )
                    return
                if claim is None:
                    self._empty(HTTPStatus.NO_CONTENT)
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        **claim.record.to_dict(),
                        "claim_schema_version": CLAIM_SCHEMA_VERSION,
                        "lease_capability": claim.lease_token,
                        "lease_generation": claim.lease_generation,
                    },
                )
                return
            wake_match = re.fullmatch(r"/api/handoffs/([^/]+)/wake", path)
            if wake_match:
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) != {"wake_token"} or not isinstance(
                    payload.get("wake_token"), str
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Wake authorization requires one stable wake token.",
                            "code": "invalid_handoff_wake",
                        },
                    )
                    return
                if handoff_store is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff storage is unavailable.",
                            "code": "handoff_store_unavailable",
                        },
                    )
                    return
                handoff_id = unquote(wake_match.group(1))
                try:
                    current = handoff_store.get(handoff_id)
                except KeyError:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Handoff was not found.", "code": "handoff_not_found"},
                    )
                    return
                if (
                    current.agent_slug != identity.agent_slug
                    or current.registration_ref is None
                    or not hmac.compare_digest(
                        current.registration_ref, identity.registration_id
                    )
                ):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Handoff identity does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                lease = self._handoff_mutation_headers(identity)
                if lease is None:
                    return
                registration_id, capability, generation, _mutation_id = lease
                observed_at = clock().astimezone(timezone.utc)
                try:
                    self._canonical_handoff_registration(identity, registration_id)
                    refresh_handoff_execution_authority(
                        current, observed_at=observed_at
                    )
                    self._canonical_handoff_registration(identity, registration_id)
                    refresh_handoff_execution_authority(
                        current,
                        observed_at=observed_at + timedelta(microseconds=1),
                    )
                    result = handoff_store.authorize_wake(
                        handoff_id,
                        registration_id=registration_id,
                        lease_token=capability,
                        lease_generation=generation,
                        wake_token=payload["wake_token"],
                        now=observed_at,
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except (GBrainError, RuntimeError):
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff authority readback is unavailable.",
                            "code": "handoff_authority_unavailable",
                        },
                    )
                    return
                except (TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_handoff_wake"},
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "handoff_id": result.handoff_id,
                        "status": result.status,
                        "wake_authorized": result.status == "leased",
                    },
                )
                return
            execution_start_match = re.fullmatch(
                r"/api/handoffs/([^/]+)/execution-start", path
            )
            if execution_start_match:
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                if (
                    set(payload) != {"wake_token", "launch_id"}
                    or not isinstance(payload.get("wake_token"), str)
                    or not isinstance(payload.get("launch_id"), str)
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "Execution start requires exactly one stable wake token "
                                "and launch id."
                            ),
                            "code": "invalid_handoff_execution_start",
                        },
                    )
                    return
                if handoff_store is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff storage is unavailable.",
                            "code": "handoff_store_unavailable",
                        },
                    )
                    return
                handoff_id = unquote(execution_start_match.group(1))
                try:
                    current = handoff_store.get(handoff_id)
                except KeyError:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Handoff was not found.", "code": "handoff_not_found"},
                    )
                    return
                if (
                    current.agent_slug != identity.agent_slug
                    or current.registration_ref is None
                    or not hmac.compare_digest(
                        current.registration_ref, identity.registration_id
                    )
                ):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Handoff identity does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                lease = self._handoff_mutation_headers(identity)
                if lease is None:
                    return
                registration_id, capability, generation, _mutation_id = lease
                observed_at = clock().astimezone(timezone.utc)
                try:
                    self._canonical_handoff_registration(identity, registration_id)
                    refresh_handoff_execution_authority(
                        current,
                        observed_at=observed_at,
                        include_task=True,
                    )
                    self._canonical_handoff_registration(identity, registration_id)
                    refresh_handoff_execution_authority(
                        current,
                        observed_at=observed_at + timedelta(microseconds=1),
                        include_task=True,
                    )
                    result = handoff_store.start_execution(
                        handoff_id,
                        registration_id=registration_id,
                        lease_token=capability,
                        lease_generation=generation,
                        wake_token=payload["wake_token"],
                        launch_id=payload["launch_id"],
                        now=observed_at,
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except (GBrainError, RuntimeError, StopIteration):
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff execution-start readback is unavailable.",
                            "code": "handoff_authority_unavailable",
                        },
                    )
                    return
                except (TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_handoff_execution_start",
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    result.to_dict(),
                )
                return
            execution_abandon_match = re.fullmatch(
                r"/api/handoffs/([^/]+)/execution-abandon", path
            )
            if execution_abandon_match:
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                if (
                    set(payload) != {"launch_id", "reason"}
                    or not isinstance(payload.get("launch_id"), str)
                    or not isinstance(payload.get("reason"), str)
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "Execution abandon requires exactly one launch id "
                                "and command-not-started reason."
                            ),
                            "code": "invalid_handoff_execution_abandon",
                        },
                    )
                    return
                if handoff_store is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff storage is unavailable.",
                            "code": "handoff_store_unavailable",
                        },
                    )
                    return
                handoff_id = unquote(execution_abandon_match.group(1))
                try:
                    current = handoff_store.get(handoff_id)
                except KeyError:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Handoff was not found.", "code": "handoff_not_found"},
                    )
                    return
                if (
                    current.agent_slug != identity.agent_slug
                    or current.registration_ref is None
                    or not hmac.compare_digest(
                        current.registration_ref, identity.registration_id
                    )
                ):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Handoff identity does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                lease = self._handoff_mutation_headers(identity)
                if lease is None:
                    return
                registration_id, capability, generation, mutation_id = lease
                observed_at = clock().astimezone(timezone.utc)
                try:
                    self._canonical_handoff_registration(identity, registration_id)
                    result = handoff_store.abandon_unstarted_execution(
                        handoff_id,
                        registration_id=registration_id,
                        lease_token=capability,
                        lease_generation=generation,
                        launch_id=payload["launch_id"],
                        mutation_id=mutation_id,
                        reason=payload["reason"],
                        now=observed_at,
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except (GBrainError, RuntimeError):
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff execution-abandon readback is unavailable.",
                            "code": "handoff_authority_unavailable",
                        },
                    )
                    return
                except (TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_handoff_execution_abandon",
                        },
                    )
                    return
                self._json(HTTPStatus.OK, result.to_dict())
                return
            execution_checkpoint_match = re.fullmatch(
                r"/api/handoffs/([^/]+)/execution-checkpoint", path
            )
            if execution_checkpoint_match:
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                if (
                    set(payload) != {"launch_id", "reason"}
                    or not isinstance(payload.get("launch_id"), str)
                    or not isinstance(payload.get("reason"), str)
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "Execution checkpoint requires exactly one launch id "
                                "and reason."
                            ),
                            "code": "invalid_handoff_execution_checkpoint",
                        },
                    )
                    return
                if handoff_store is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff storage is unavailable.",
                            "code": "handoff_store_unavailable",
                        },
                    )
                    return
                handoff_id = unquote(execution_checkpoint_match.group(1))
                try:
                    current = handoff_store.get(handoff_id)
                except KeyError:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Handoff was not found.", "code": "handoff_not_found"},
                    )
                    return
                if (
                    current.agent_slug != identity.agent_slug
                    or current.registration_ref is None
                    or not hmac.compare_digest(
                        current.registration_ref, identity.registration_id
                    )
                ):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Handoff identity does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                lease = self._handoff_mutation_headers(identity)
                if lease is None:
                    return
                registration_id, capability, generation, mutation_id = lease
                observed_at = clock().astimezone(timezone.utc)
                try:
                    self._canonical_handoff_registration(identity, registration_id)
                    result = handoff_store.checkpoint_started_execution(
                        handoff_id,
                        registration_id=registration_id,
                        lease_token=capability,
                        lease_generation=generation,
                        launch_id=payload["launch_id"],
                        mutation_id=mutation_id,
                        reason=payload["reason"],
                        now=observed_at,
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except (GBrainError, RuntimeError):
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff checkpoint readback is unavailable.",
                            "code": "handoff_authority_unavailable",
                        },
                    )
                    return
                except (TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_handoff_execution_checkpoint",
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "handoff_id": result.handoff_id,
                        "status": result.status,
                        "launch_id": payload["launch_id"],
                        "checkpointed": result.status == "suppressed",
                    },
                )
                return
            recover_match = re.fullmatch(r"/api/handoffs/([^/]+)/recover", path)
            if recover_match:
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) != {"registration_id", "expected_generation"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Recovery requires exactly registration_id and expected_generation.",
                            "code": "invalid_handoff_recovery",
                        },
                    )
                    return
                registration_id = payload.get("registration_id")
                expected_generation = payload.get("expected_generation")
                if (
                    not isinstance(registration_id, str)
                    or not registration_id
                    or isinstance(expected_generation, bool)
                    or not isinstance(expected_generation, int)
                    or expected_generation < 1
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Recovery registration and generation are invalid.",
                            "code": "invalid_handoff_recovery",
                        },
                    )
                    return
                registration_ref = hashlib.sha256(
                    registration_id.encode("utf-8")
                ).hexdigest()
                if not hmac.compare_digest(
                    identity.registration_id, registration_ref
                ):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher registration does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                if handoff_store is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff storage is unavailable.",
                            "code": "handoff_store_unavailable",
                        },
                    )
                    return
                try:
                    canonical = self._canonical_handoff_registration(
                        identity, registration_id
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except Exception:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff route readback is unavailable.",
                            "code": "handoff_route_unavailable",
                        },
                    )
                    return
                handoff_id = unquote(recover_match.group(1))
                recovery_now = clock().astimezone(timezone.utc)

                def reconcile(state) -> None:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": "Persisted handoff claim requires authoritative reconciliation.",
                            "code": "handoff_recovery_reconcile",
                            **state.to_dict(),
                        },
                    )

                try:
                    current = handoff_store.get(handoff_id)
                    refresh_handoff_execution_authority(
                        current, observed_at=recovery_now
                    )
                    canonical = self._canonical_handoff_registration(
                        identity, registration_id
                    )
                    state = handoff_store.read_recovery_state(
                        handoff_id,
                        registration=canonical,
                        now=recovery_now,
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except HandoffOwnershipError:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Handoff identity does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except KeyError:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Handoff was not found.", "code": "handoff_not_found"},
                    )
                    return
                except Exception:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff authority readback is unavailable.",
                            "code": "handoff_authority_unavailable",
                        },
                    )
                    return
                if (
                    state.lease_generation != expected_generation
                    or state.status
                    not in {
                        "leased",
                        "received",
                        "execution_started",
                        "actively_executing",
                        "still_blocked",
                    }
                ):
                    reconcile(state)
                    return
                try:
                    canonical = self._canonical_handoff_registration(
                        identity, registration_id
                    )
                    recovered = handoff_store.recover_in_progress(
                        handoff_id,
                        registration=canonical,
                        expected_generation=expected_generation,
                        now=recovery_now,
                    )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except HandoffOwnershipError:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Handoff identity does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except ValueError:
                    try:
                        canonical = self._canonical_handoff_registration(
                            identity, registration_id
                        )
                        state = handoff_store.read_recovery_state(
                            handoff_id,
                            registration=canonical,
                            now=recovery_now,
                        )
                    except _HandoffIdentityMismatch:
                        self._json(
                            HTTPStatus.FORBIDDEN,
                            {
                                "error": "Dispatcher route no longer matches its registration.",
                                "code": "handoff_identity_mismatch",
                            },
                        )
                        return
                    except Exception:
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "error": "Handoff route readback is unavailable.",
                                "code": "handoff_route_unavailable",
                            },
                        )
                        return
                    reconcile(state)
                    return
                except Exception:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff route readback is unavailable.",
                            "code": "handoff_route_unavailable",
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        **recovered.record.to_dict(),
                        "claim_schema_version": CLAIM_SCHEMA_VERSION,
                        "lease_capability": recovered.lease_token,
                        "lease_generation": recovered.lease_generation,
                    },
                )
                return
            ack_match = re.fullmatch(r"/api/handoffs/([^/]+)/ack", path)
            failure_match = re.fullmatch(r"/api/handoffs/([^/]+)/failure", path)
            if ack_match or failure_match:
                identity = self._handoff_identity()
                if identity is None:
                    return
                payload = self._read_json()
                if payload is None:
                    return
                if handoff_store is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff storage is unavailable.",
                            "code": "handoff_store_unavailable",
                        },
                    )
                    return
                handoff_id = unquote((ack_match or failure_match).group(1))
                try:
                    current = handoff_store.get(handoff_id)
                except KeyError:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Handoff was not found.", "code": "handoff_not_found"},
                    )
                    return
                if (
                    current.agent_slug != identity.agent_slug
                    or current.registration_ref is None
                    or not hmac.compare_digest(
                        current.registration_ref, identity.registration_id
                    )
                ):
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Handoff identity does not match its credential.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                lease = self._handoff_mutation_headers(identity)
                if lease is None:
                    return
                registration_id, capability, generation, mutation_id = lease
                mutation_now = clock().astimezone(timezone.utc)
                try:
                    if ack_match:
                        if set(payload) != {"status", "detail"}:
                            raise ValueError(
                                "Acknowledgement requires exactly status and detail"
                            )
                        self._canonical_handoff_registration(identity, registration_id)
                        refresh_handoff_execution_authority(
                            current, observed_at=mutation_now
                        )
                        self._canonical_handoff_registration(identity, registration_id)
                        refresh_handoff_execution_authority(
                            current,
                            observed_at=mutation_now + timedelta(microseconds=1),
                        )
                        result = handoff_store.acknowledge(
                            handoff_id,
                            payload.get("status"),
                            registration_id=registration_id,
                            lease_token=capability,
                            lease_generation=generation,
                            mutation_id=mutation_id,
                            now=mutation_now,
                            detail=payload.get("detail"),
                        )
                    else:
                        if set(payload) != {"failure_class"} or payload.get(
                            "failure_class"
                        ) not in {"retryable", "terminal"}:
                            raise ValueError(
                                "Failure class must be retryable or terminal"
                            )
                        retryable = payload["failure_class"] == "retryable"
                        result = handoff_store.record_failure(
                            handoff_id,
                            registration_id=registration_id,
                            lease_token=capability,
                            lease_generation=generation,
                            mutation_id=mutation_id,
                            retryable=retryable,
                            summary=(
                                "Dispatcher delivery will retry."
                                if retryable
                                else "Dispatcher delivery stopped after terminal failure."
                            ),
                            now=mutation_now,
                        )
                except _HandoffIdentityMismatch:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": "Dispatcher route no longer matches its registration.",
                            "code": "handoff_identity_mismatch",
                        },
                    )
                    return
                except (GBrainError, RuntimeError):
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "Handoff authority readback is unavailable.",
                            "code": "handoff_authority_unavailable",
                        },
                    )
                    return
                except (TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": (
                                "invalid_handoff_ack"
                                if ack_match
                                else "invalid_handoff_failure"
                            ),
                        },
                    )
                    return
                self._json(HTTPStatus.OK, result.to_dict())
                return
            if path == "/api/artifacts":
                payload = self._read_json(
                    max_request_bytes=MAX_ARTIFACT_REQUEST_BYTES
                )
                if payload is None:
                    return
                required = {
                    "title",
                    "artifact_kind",
                    "created_by",
                    "produced_for",
                    "markdown",
                    "attachments",
                    "project",
                    "goal",
                    "git_url",
                    "supersedes",
                    "idempotency_key",
                }
                optional = {"delegation_ref"}
                if set(payload) not in (required, required | optional):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Artifact publication requires the exact supported fields.",
                            "code": "invalid_artifact",
                        },
                    )
                    return
                attachments = payload.get("attachments")
                optional_slugs = {
                    "project": "projects/",
                    "goal": "goals/",
                    "supersedes": "artifacts/",
                }
                if (
                    not isinstance(attachments, list)
                    or not all(isinstance(item, str) for item in attachments)
                    or any(
                        payload.get(field) is not None
                        and (
                            not isinstance(payload.get(field), str)
                            or not payload[field].startswith(prefix)
                        )
                        for field, prefix in optional_slugs.items()
                    )
                    or payload.get("git_url") is not None
                    and not isinstance(payload.get("git_url"), str)
                    or not isinstance(payload.get("idempotency_key"), str)
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "Artifact fields are invalid.", "code": "invalid_artifact"},
                    )
                    return
                try:
                    executing_agent = active_artifact_publisher_auth.resolve(
                        self.headers.get("Authorization")
                    )
                    if (
                        executing_agent != payload.get("created_by")
                        or ARTIFACT_BY_AGENT.get(executing_agent)
                        != ARTIFACT_BY_AGENT.get(payload.get("created_by"))
                    ):
                        self._json(
                            HTTPStatus.FORBIDDEN,
                            {
                                "error": (
                                    "Artifact publisher identity does not match its "
                                    "installed execution contract."
                                ),
                                "code": "artifact_identity_mismatch",
                            },
                        )
                        return
                    execution_claim = None
                    if payload.get("delegation_ref") is not None:
                        execution_claim = verified_artifact_execution_claim(
                            task_slug=payload["produced_for"],
                            executor_agent=executing_agent,
                            delegation_ref=payload["delegation_ref"],
                        )
                    artifact = new_agent_artifact(
                        title=payload["title"],
                        artifact_kind=payload["artifact_kind"],
                        created_by=payload["created_by"],
                        produced_for=payload["produced_for"],
                        markdown=payload["markdown"],
                        attachments=attachments,
                        project=payload["project"],
                        goal=payload["goal"],
                        git_url=payload["git_url"],
                        supersedes=payload["supersedes"],
                        delegation_ref=payload.get("delegation_ref"),
                        now=clock(),
                    )
                    with foreground_operation():
                        artifact_arguments = {
                            "executing_agent": executing_agent,
                            "idempotency_key": payload["idempotency_key"],
                        }
                        if execution_claim is None:
                            receipt = adapter.create_agent_artifact(
                                artifact, **artifact_arguments
                            )
                        else:
                            with reserve_verified_artifact_execution(
                                execution_claim,
                                publication_key=payload["idempotency_key"],
                            ) as reserved_claim:
                                artifact_arguments["execution_claim"] = reserved_claim
                                receipt = adapter.create_agent_artifact(
                                    artifact, **artifact_arguments
                                )
                except ArtifactIdempotencyConflict as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "artifact_idempotency_conflict"},
                    )
                    return
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": (
                                "invalid_delegation_claim"
                                if "delegation claim" in str(exc).lower()
                                else "invalid_artifact"
                            ),
                        },
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                body = {"artifact": receipt.artifact.to_dict(), "receipt": receipt.to_dict()}
                self._json(
                    HTTPStatus.OK if receipt.idempotent else HTTPStatus.CREATED,
                    body,
                )
                return
            task_todo_prefix = "/api/tasks/"
            question_suffix = "/questions"
            if path.startswith(task_todo_prefix) and path.endswith(question_suffix):
                task_slug = unquote(
                    path[len(task_todo_prefix) : -len(question_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "question",
                    "question_detail",
                    "resume_action",
                    "agent_slug",
                    "idempotency_key",
                }
                if set(payload) != required:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Blocking question requires exact handoff context.",
                            "code": "invalid_handoff",
                        },
                    )
                    return
                try:
                    with foreground_operation():
                        receipt = adapter.request_agent_input(
                            task_slug,
                            question=payload["question"],
                            question_detail=payload["question_detail"],
                            resume_action=payload["resume_action"],
                            agent_slug=payload["agent_slug"],
                            idempotency_key=payload["idempotency_key"],
                            now=clock(),
                        )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_handoff"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.CREATED, receipt.to_dict())
                return
            acknowledge_suffix = "/handoff/acknowledge"
            if path.startswith(task_todo_prefix) and path.endswith(acknowledge_suffix):
                task_slug = unquote(
                    path[len(task_todo_prefix) : -len(acknowledge_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) != {"actor"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Agent acknowledgement requires the exact actor.",
                            "code": "invalid_handoff",
                        },
                    )
                    return
                try:
                    with foreground_operation():
                        receipt = adapter.acknowledge_agent_handoff(
                            task_slug,
                            actor=payload["actor"],
                            now=clock(),
                        )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_handoff"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, receipt.to_dict())
                return
            answer_prefix = "/api/todos/"
            answer_suffix = "/answer"
            if path.startswith(answer_prefix) and path.endswith(answer_suffix):
                todo_slug = unquote(path[len(answer_prefix) : -len(answer_suffix)])
                before_snapshot = None
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "answer",
                    "expected_updated_at",
                    "actor",
                    "source",
                    "idempotency_key",
                }
                if set(payload) != required:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Answer and handoff requires exact mutation context.",
                            "code": "invalid_handoff",
                        },
                    )
                    return
                try:
                    expected = datetime.fromisoformat(
                        str(payload["expected_updated_at"]).replace("Z", "+00:00")
                    )
                    with foreground_operation():
                        if active_handoff_event_bridge is not None:
                            before_todo = adapter.get_todo(todo_slug)
                            before_todo_value = canonical_mapping(before_todo)
                            before_task = adapter.get_task(
                                str(before_todo_value.get("parent_task"))
                            )
                            before_snapshot = mutation_snapshot(
                                before_task, todo=before_todo
                            )
                        receipt = adapter.answer_agent_question(
                            todo_slug,
                            answer=payload["answer"],
                            expected_updated_at=expected,
                            actor=payload["actor"],
                            source=payload["source"],
                            idempotency_key=payload["idempotency_key"],
                            now=clock(),
                        )
                except ConcurrentTodoUpdateError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "todo_changed", "slug": exc.todo_slug},
                    )
                    return
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_handoff"},
                    )
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="answer_agent_question",
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                receipt_value = canonical_mapping(receipt)
                after_canonical_mutation(
                    before_snapshot,
                    mutation_snapshot(
                        receipt_value.get("task"),
                        todo=receipt_value.get("todo"),
                    ),
                    receipt_value,
                    mutation_kind="answer_agent_question",
                )
                invalidate_snapshot()
                self._json(HTTPStatus.OK, receipt.to_dict())
                return
            migration_suffix = "/todos/migrate"
            if path.startswith(task_todo_prefix) and path.endswith(migration_suffix):
                task_slug = unquote(
                    path[len(task_todo_prefix) : -len(migration_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                if payload:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "TODO migration takes no options.", "code": "invalid_todo"},
                    )
                    return
                try:
                    result = adapter.migrate_legacy_next_actions(
                        task_slug,
                        now=clock(),
                    )
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_todo"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {**result.to_dict(), "verified": True})
                return
            todo_comment_prefix = "/api/todos/"
            todo_comment_suffix = "/comments"
            if path.startswith(todo_comment_prefix) and path.endswith(todo_comment_suffix):
                todo_slug = unquote(
                    path[len(todo_comment_prefix) : -len(todo_comment_suffix)]
                )
                before_snapshot = None
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "body", "expected_updated_at", "author", "source", "idempotency_key"
                }
                if set(payload) != required:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "TODO comment requires exact mutation context.", "code": "invalid_todo"},
                    )
                    return
                try:
                    expected = datetime.fromisoformat(
                        str(payload["expected_updated_at"]).replace("Z", "+00:00")
                    )
                    with foreground_operation():
                        if active_handoff_event_bridge is not None:
                            before_snapshot = read_todo_mutation_snapshot(todo_slug)
                        receipt = adapter.add_todo_comment(
                            todo_slug,
                            body=payload["body"],
                            expected_updated_at=expected,
                            author=payload["author"],
                            source=payload["source"],
                            idempotency_key=payload["idempotency_key"],
                            now=clock(),
                        )
                except ConcurrentTodoUpdateError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "todo_changed", "slug": exc.todo_slug},
                    )
                    return
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_todo"},
                    )
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="todo_comment",
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                after_verified_todo_mutation(
                    before_snapshot,
                    receipt,
                    mutation_kind="todo_comment",
                    todo_slug=todo_slug,
                )
                invalidate_snapshot()
                self._json(HTTPStatus.CREATED, {"receipt": receipt.to_dict()})
                return
            todo_create_suffix = "/todos"
            if path.startswith(task_todo_prefix) and path.endswith(todo_create_suffix):
                task_slug = unquote(
                    path[len(task_todo_prefix) : -len(todo_create_suffix)]
                )
                before_snapshot = None
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "text", "detail", "kind", "actor", "source", "idempotency_key"
                }
                if set(payload) != required:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "TODO creation requires exact identity and actor context.", "code": "invalid_todo"},
                    )
                    return
                try:
                    with foreground_operation():
                        if active_handoff_event_bridge is not None:
                            before_task = adapter.get_task(task_slug)
                            before_snapshot = mutation_snapshot(before_task)
                        receipt = adapter.create_todo(
                            task_slug,
                            text=payload["text"],
                            detail=payload["detail"],
                            kind=payload["kind"],
                            actor=payload["actor"],
                            source=payload["source"],
                            idempotency_key=payload["idempotency_key"],
                            now=clock(),
                        )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_todo"},
                    )
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="todo_created",
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                after_verified_todo_mutation(
                    before_snapshot,
                    receipt,
                    mutation_kind="todo_created",
                    task_slug=task_slug,
                )
                invalidate_snapshot()
                self._json(HTTPStatus.CREATED, {"receipt": receipt.to_dict()})
                return
            if path == "/api/ical-access":
                try:
                    self._json(HTTPStatus.OK, active_ical_reader.request_full_access())
                except ICalendarError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "calendar_unavailable"})
                return
            if path == "/api/ical-preferences":
                payload = self._read_json()
                if payload is None:
                    return
                selected = payload.get("selected_calendar_ids")
                if (
                    set(payload) != {"selected_calendar_ids"}
                    or not isinstance(selected, list)
                    or not all(isinstance(item, str) and item for item in selected)
                ):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "selected_calendar_ids must be a list of calendar identifiers.", "code": "invalid_calendar_preferences"})
                    return
                try:
                    saved = active_calendar_preferences.save_selected_calendar_ids(selected)
                except (OSError, ValueError) as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Calendar selections could not be saved locally.", "code": "calendar_preferences_unavailable"})
                    return
                self._json(HTTPStatus.OK, {"selected_calendar_ids": list(saved), "verified": True})
                return
            avatar_prefix = "/api/agents/"
            avatar_suffix = "/avatar"
            default_goals_suffix = "/default-goals"
            if path.startswith(avatar_prefix) and path.endswith(default_goals_suffix):
                agent_slug = unquote(path[len(avatar_prefix) : -len(default_goals_suffix)])
                payload = self._read_json()
                if payload is None:
                    return
                goal_slug = payload.get("goal_slug")
                action = payload.get("action")
                if not isinstance(goal_slug, str) or action not in {"assign", "remove"}:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Choose a goal and an assignment action.", "code": "invalid_goal_assignment"})
                    return
                try:
                    agent = adapter.set_agent_default_goal(
                        agent_slug,
                        goal_slug,
                        assigned=action == "assign",
                    )
                except (DomainValidationError, ValueError) as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_goal_assignment"})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "goal_assignment_readback_failed"})
                    return
                self._json(HTTPStatus.OK, {"agent": agent.to_dict(), "verified": True})
                return
            if path.startswith(avatar_prefix) and path.endswith(avatar_suffix):
                agent_slug = unquote(path[len(avatar_prefix) : -len(avatar_suffix)])
                try:
                    # Validate the exact roster slug and typed agent page before
                    # sending any bytes to Memory Stargraph. This deliberately
                    # never derives a slug from the display name.
                    adapter.get_agent_profile(agent_slug)
                except (DomainValidationError, ValueError, GBrainError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "This Agent Directory profile cannot accept an avatar yet. "
                                + str(exc)
                            ),
                            "code": "invalid_agent_profile",
                        },
                    )
                    return
                upload = self._read_avatar_upload()
                if upload is None:
                    return
                filename, data, mime = upload
                try:
                    attachment = self._attach_avatar(agent_slug, filename, data, mime)
                    local_media = attachment["local_media"]
                    agent = adapter.set_agent_avatar(agent_slug, local_media["served_url"])
                except (ValueError, GBrainError) as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "avatar_upload_unavailable"})
                    return
                self._json(HTTPStatus.OK, {"agent": agent.to_dict(), "verified": True, "avatar": {"served_url": local_media["served_url"], "remote_read_verified": bool(local_media.get("remote_read_verified"))}})
                return
            proposal_prefix = "/api/proposals/"
            proposal_decision_suffix = "/decision"
            if (
                path.startswith(proposal_prefix)
                and path.endswith(proposal_decision_suffix)
            ):
                proposal_slug = unquote(
                    path[
                        len(proposal_prefix) : -len(proposal_decision_suffix)
                    ]
                )
                before_snapshot = None
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) - {"action", "decision_note"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "proposal decision contains unsupported fields.",
                            "code": "invalid_proposal_decision",
                        },
                    )
                    return
                action = payload.get("action")
                decision_note = payload.get("decision_note", "")
                if action not in {"approve", "reject"} or not isinstance(
                    decision_note, str
                ) or len(decision_note) > 1000:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "action must be approve or reject and the "
                                "optional decision note must be text."
                            ),
                            "code": "invalid_proposal_decision",
                        },
                    )
                    return
                try:
                    if action == "approve" and active_handoff_event_bridge is not None:
                        before_snapshot = mutation_snapshot(
                            adapter.get_task(proposal_slug)
                        )
                    receipt = adapter.decide_proposal(
                        proposal_slug,
                        action=action,
                        decision_note=decision_note,
                        now=clock(),
                    )
                except LifecycleIntegrityError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        _lifecycle_attention_payload(exc),
                    )
                    return
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "proposal_not_decidable"},
                    )
                    return
                except PartialMutationError as exc:
                    if action == "approve":
                        partial_mutation_attention(
                            before_snapshot,
                            slug=exc.slug,
                            mutation_kind="proposal_decision",
                        )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                receipt_value = canonical_mapping(receipt)
                approved_task = canonical_mapping(receipt_value.get("created_task"))
                if (
                    action == "approve"
                    and approved_task.get("proposal_decision") == "approve"
                ):
                    after_canonical_mutation(
                        before_snapshot,
                        mutation_snapshot(approved_task),
                        receipt_value,
                        mutation_kind="proposal_decision",
                    )
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if path in {"/api/warnings/dismiss", "/api/warnings/restore"}:
                payload = self._read_json()
                if payload is None:
                    return
                fingerprint = payload.get("fingerprint")
                if not isinstance(fingerprint, str) or len(payload) != 1:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "fingerprint must be the only field.",
                            "code": "invalid_warning_fingerprint",
                        },
                    )
                    return
                try:
                    dismissed = path.endswith("/dismiss")
                    verified = (
                        warning_store.dismiss(fingerprint)
                        if dismissed
                        else warning_store.restore(fingerprint)
                    )
                except ValueError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_warning_fingerprint",
                        },
                    )
                    return
                except (OSError, RuntimeError) as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": str(exc),
                            "code": "warning_state_unavailable",
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "fingerprint": fingerprint,
                        "dismissed": dismissed,
                        "verified": verified,
                    },
                )
                return
            if path == "/api/goals":
                payload = self._read_json()
                if payload is None:
                    return
                required_fields = (
                    "title",
                    "outcome",
                    "success_criteria",
                    "strategy",
                    "review_cadence",
                    "constraints",
                )
                if any(
                    not isinstance(payload.get(field), str)
                    for field in required_fields
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "all goal text fields must be text.",
                            "code": "invalid_goal",
                        },
                    )
                    return
                target_day = None
                raw_target_day = payload.get("target_day")
                if raw_target_day not in (None, ""):
                    if not isinstance(raw_target_day, str):
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {
                                "error": "target_day must be YYYY-MM-DD or omitted.",
                                "code": "invalid_goal",
                            },
                        )
                        return
                    try:
                        target_day = date.fromisoformat(raw_target_day)
                    except ValueError:
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {
                                "error": "target_day must be YYYY-MM-DD or omitted.",
                                "code": "invalid_goal",
                            },
                        )
                        return
                try:
                    goal = new_goal(
                        title=payload["title"],
                        outcome=payload["outcome"],
                        success_criteria=payload["success_criteria"],
                        strategy=payload["strategy"],
                        review_cadence=payload["review_cadence"],
                        constraints=payload["constraints"],
                        target_day=target_day,
                        now=clock(),
                        identity=identity_factory(),
                    )
                    receipt = adapter.create_goal(goal)
                except DomainValidationError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_goal"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(
                    HTTPStatus.CREATED,
                    {"goal": goal.to_dict(), "receipt": receipt.to_dict()},
                )
                return
            if path == "/api/projects":
                payload = self._read_json()
                if payload is None:
                    return
                raw_title = payload.get("title", "")
                raw_goals = payload.get("supporting_goal_slugs", [])
                if not isinstance(raw_title, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "project title must be text."},
                    )
                    return
                if raw_goals is not None and (not isinstance(raw_goals, list) or not all(isinstance(item, str) and item.startswith("goals/") for item in raw_goals)):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "supporting_goal_slugs must be a list of canonical goal slugs."})
                    return
                try:
                    valid_goals = {goal.slug for goal in adapter.list_goals().goals}
                    if not set(raw_goals).issubset(valid_goals):
                        raise DomainValidationError("Each supporting goal must be a current canonical goal.")
                    project = new_project(
                        raw_title,
                        now=clock(),
                        identity=identity_factory(),
                        supporting_goal_slugs=tuple(dict.fromkeys(raw_goals)),
                    )
                    receipt = adapter.create_project(project)
                except DomainValidationError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_project"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(
                    HTTPStatus.CREATED,
                    {
                        "project": project.to_dict(),
                        "receipt": receipt.to_dict(),
                    },
                )
                return
            if path == "/api/system-tickets":
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) - {"title", "verbatim_request", "target_subsystem", "priority", "acceptance_criteria"}:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error":"System Ticket contains unsupported fields.", "code":"invalid_system_ticket"}); return
                try:
                    ticket = new_system_ticket(title=payload.get("title", ""), verbatim_request=payload.get("verbatim_request", ""), target_subsystem=payload.get("target_subsystem", "unknown"), priority=payload.get("priority", "normal"), acceptance_criteria=payload.get("acceptance_criteria", ""), now=clock(), identity=identity_factory())
                    receipt = adapter.create_system_ticket(ticket)
                except DomainValidationError as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error":str(exc), "code":"invalid_system_ticket"}); return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error":str(exc), "code":"partial_write", "slug":exc.slug}); return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error":str(exc), "code":"gbrain_unavailable"}); return
                invalidate_system_tickets()
                self._json(HTTPStatus.CREATED, {"ticket": ticket.to_dict(), "receipt": receipt.to_dict()}); return
            if path == "/api/tasks/archive-completed-boundary":
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) - {"task_slugs"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "archive request contains unsupported fields.",
                            "code": "invalid_archive_request",
                        },
                    )
                    return
                raw_slugs = payload.get("task_slugs")
                task_slugs: tuple[str, ...] | None
                if raw_slugs is None:
                    task_slugs = None
                elif isinstance(raw_slugs, list) and all(
                    isinstance(slug, str) and slug.startswith("tasks/")
                    for slug in raw_slugs
                ):
                    task_slugs = tuple(raw_slugs)
                else:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "task_slugs must be a list of canonical task slugs.",
                            "code": "invalid_archive_request",
                        },
                    )
                    return
                try:
                    receipt = adapter.archive_due_completed_tony_tasks(
                        clock(),
                        task_slugs=task_slugs,
                    )
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                if receipt.archived_slugs:
                    invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            duplicate_prefix = "/api/tasks/"
            duplicate_suffix = "/duplicate"
            if (
                path.startswith(duplicate_prefix)
                and path.endswith(duplicate_suffix)
            ):
                source_slug = unquote(
                    path[len(duplicate_prefix) : -len(duplicate_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                allowed_fields = {
                    "title",
                    "due_day",
                    "detail",
                    "priority",
                    "initial_todo",
                    "project_slug",
                    "goal_slug",
                    "parent_slug",
                    "progress_metric",
                    "assignee_slug",
                }
                if set(payload) - allowed_fields:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "duplicate task request contains unsupported fields.",
                            "code": "invalid_task",
                        },
                    )
                    return
                now = clock()
                initial_todo_payload = None
                initial_todo = payload.get("initial_todo", "")
                if not isinstance(initial_todo, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "initial_todo must be text.", "code": "invalid_task"},
                    )
                    return
                raw_due_day = payload.get("due_day")
                due_day = now.date()
                due_source = "task_creation_day"
                if raw_due_day not in (None, ""):
                    if not isinstance(raw_due_day, str):
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {"error": "due_day must be YYYY-MM-DD."},
                        )
                        return
                    try:
                        due_day = date.fromisoformat(raw_due_day)
                    except ValueError:
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {"error": "due_day must be YYYY-MM-DD."},
                        )
                        return
                    due_source = "explicit"
                try:
                    assignee_slug = payload.get("assignee_slug", "tony")
                    available_agents = {
                        agent.slug: agent.work_root
                        for agent in adapter.list_agent_profiles().agents
                    }
                    if assignee_slug != "tony" and assignee_slug not in available_agents:
                        raise DomainValidationError("assignee is not an active GTasks agent")
                    progress_metric, event_progress = (
                        _progress_metric_from_request(
                            payload.get("progress_metric"),
                            due_day=due_day,
                            task_slug=None,
                        )
                    )
                    parent_slug = _parent_slug_from_request(payload)
                    task = new_task(
                        title=payload.get("title", ""),
                        detail=payload.get("detail", ""),
                        priority=payload.get("priority", "normal"),
                        next_action="",
                        due_day=due_day,
                        project=payload.get("project_slug") or None,
                        goal=payload.get("goal_slug") or None,
                        progress_metric=progress_metric,
                        event_progress=event_progress,
                        now=now,
                        identity=identity_factory(),
                    )
                    task = replace(task, parent=parent_slug)
                    if assignee_slug != "tony":
                        task = replace(
                            task,
                            lifecycle_root=available_agents[assignee_slug],
                            owner_agent=assignee_slug,
                        )
                        receipt = adapter.create_agent_task(task, assignee_slug)
                    else:
                        receipt = adapter.duplicate_task(source_slug, task)
                    if initial_todo.strip():
                        todo_receipt = adapter.create_todo(
                            task.slug,
                            text=initial_todo,
                            detail="",
                            kind="action",
                            actor=TONY_PROFILE_SLUG,
                            source="mission_control",
                            idempotency_key="task-create-initial-todo",
                            now=now,
                        )
                        initial_todo_payload = (
                            todo_receipt.todo.to_dict()
                            if hasattr(todo_receipt.todo, "to_dict")
                            else todo_receipt.todo
                        )
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_task"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(
                    HTTPStatus.CREATED,
                    {
                        "source_slug": source_slug,
                        "task": {
                            **task.to_dict(),
                            "next_action": (
                                initial_todo_payload["text"]
                                if initial_todo_payload is not None
                                else task.next_action
                            ),
                            "next_action_history": [],
                            "todos": (
                                [initial_todo_payload]
                                if initial_todo_payload is not None
                                else []
                            ),
                        },
                        "receipt": receipt.to_dict(),
                        "due_day_source": due_source,
                    },
                )
                return
            if path != "/api/tasks":
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            payload = self._read_json()
            if payload is None:
                return

            now = clock()
            raw_due_day = payload.get("due_day")
            due_source = "task_creation_day"
            due_day = now.date()
            if raw_due_day not in (None, ""):
                if not isinstance(raw_due_day, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "due_day must be YYYY-MM-DD."},
                    )
                    return
                try:
                    due_day = date.fromisoformat(raw_due_day)
                except ValueError:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "due_day must be YYYY-MM-DD."},
                    )
                    return
                due_source = "explicit"

            raw_title = payload.get("title", "")
            if not isinstance(raw_title, str):
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "title must be text."},
                )
                return
            quick_add_fields = {"title", "due_day"}
            full_task_fields = {
                *quick_add_fields,
                "detail",
                "priority",
                "initial_todo",
                "project_slug",
                "goal_slug",
                "parent_slug",
                "progress_metric",
                "assignee_slug",
            }
            unsupported = set(payload) - full_task_fields
            if unsupported:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {
                        "error": "task request contains unsupported fields.",
                        "code": "invalid_task",
                    },
                )
                return
            is_full_creation = bool(set(payload) - quick_add_fields)
            initial_todo_payload = None
            try:
                if is_full_creation:
                    initial_todo = payload.get("initial_todo", "")
                    if not isinstance(initial_todo, str):
                        raise DomainValidationError("initial_todo must be text")
                    progress_metric, event_progress = (
                        _progress_metric_from_request(
                            payload.get("progress_metric"),
                            due_day=due_day,
                            task_slug=None,
                        )
                    )
                    project_slug = payload.get("project_slug") or None
                    goal_slug = payload.get("goal_slug") or None
                    parent_slug = _parent_slug_from_request(payload)
                    assignee_slug = payload.get("assignee_slug", "tony")
                    available_agents = {
                        agent.slug: agent.work_root
                        for agent in adapter.list_agent_profiles().agents
                    }
                    if assignee_slug != "tony" and assignee_slug not in available_agents:
                        raise DomainValidationError(
                            "assignee must be Tony or an active Agent Directory profile"
                        )
                    task = new_task(
                        title=raw_title,
                        detail=payload.get("detail", ""),
                        priority=payload.get("priority", "normal"),
                        next_action="",
                        due_day=due_day,
                        project=project_slug,
                        goal=goal_slug,
                        progress_metric=progress_metric,
                        event_progress=event_progress,
                        now=now,
                        identity=identity_factory(),
                    )
                    task = replace(task, parent=parent_slug)
                    if assignee_slug == "tony":
                        receipt = adapter.create_task(task)
                    else:
                        work_root = available_agents[assignee_slug]
                        task = replace(
                            task,
                            lifecycle_root=work_root,
                            owner_agent=assignee_slug,
                        )
                        receipt = adapter.create_agent_task(
                            task,
                            assignee_slug,
                        )
                    if initial_todo.strip():
                        todo_receipt = adapter.create_todo(
                            task.slug,
                            text=initial_todo,
                            detail="",
                            kind="action",
                            actor=TONY_PROFILE_SLUG,
                            source="mission_control",
                            idempotency_key="task-create-initial-todo",
                            now=now,
                        )
                        initial_todo_payload = (
                            todo_receipt.todo.to_dict()
                            if hasattr(todo_receipt.todo, "to_dict")
                            else todo_receipt.todo
                        )
                else:
                    task = new_inbox_task(
                        raw_title,
                        now=now,
                        identity=identity_factory(),
                        due_day=due_day,
                    )
                    receipt = adapter.create_inbox(task)
            except LifecycleIntegrityError as exc:
                self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                return
            except (DomainValidationError, ValueError) as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(exc), "code": "invalid_task"},
                )
                return
            except PartialMutationError as exc:
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": str(exc),
                        "code": "partial_write",
                        "slug": exc.slug,
                    },
                )
                return
            except GBrainError as exc:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc), "code": "gbrain_unavailable"},
                )
                return

            invalidate_snapshot()
            self._json(
                HTTPStatus.CREATED,
                {
                    "task": {
                        **task.to_dict(),
                        "next_action": (
                            initial_todo_payload["text"]
                            if initial_todo_payload is not None
                            else task.next_action
                        ),
                        "next_action_history": [],
                        "todos": (
                            [initial_todo_payload]
                            if initial_todo_payload is not None
                            else []
                        ),
                    },
                    "receipt": receipt.to_dict(),
                    "due_day_source": due_source,
                },
            )

        def do_PATCH(self) -> None:
            path = urlsplit(self.path).path
            delegation_prefix = "/api/agent-delegations/"
            if path.startswith(delegation_prefix) and "/" not in path[len(delegation_prefix) :]:
                slug = unquote(path[len(delegation_prefix) :])
                payload = self._read_json()
                if payload is None:
                    return
                extension_fields = {"ends_at", "expected_version"}
                action_fields = {"action", "expected_version"}
                if frozenset(payload) not in {
                    frozenset(extension_fields),
                    frozenset(action_fields),
                }:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "delegation change requires one extension or terminal action with expected_version.",
                            "code": "invalid_agent_delegation",
                        },
                    )
                    return
                try:
                    expected_version = payload["expected_version"]
                    if not isinstance(expected_version, str) or not expected_version:
                        raise ValueError("expected_version must be the canonical lease version")
                    with active_delegation_lock.hold(slug):
                        current = next(
                            (
                                item
                                for item in adapter.list_agent_delegations()
                                if item.slug == slug
                            ),
                            None,
                        )
                        if current is None:
                            self._json(
                                HTTPStatus.NOT_FOUND,
                                {
                                    "error": "agent delegation was not found.",
                                    "code": "delegation_not_found",
                                },
                            )
                            return
                        if current.updated_at.isoformat() != expected_version:
                            raise ConcurrentAgentDelegationUpdateError(slug)
                        operation_time = clock().astimezone(timezone.utc)
                        effective_state = lease_state_at(current, operation_time)
                        if effective_state in {
                            DelegationState.COMPLETED,
                            DelegationState.EXPIRED,
                            DelegationState.REVOKED,
                        }:
                            raise ValueError(
                                f"{effective_state.value} agent delegation cannot be changed"
                            )
                        updated_at = operation_time
                        if updated_at <= current.updated_at:
                            updated_at = current.updated_at + timedelta(microseconds=1)
                        if set(payload) == extension_fields:
                            ends_at = datetime.fromisoformat(
                                str(payload["ends_at"]).replace("Z", "+00:00")
                            )
                            if ends_at.tzinfo is None or ends_at.utcoffset() is None:
                                raise ValueError("ends_at must be an aware UTC instant")
                            ends_at = ends_at.astimezone(timezone.utc)
                            if ends_at <= current.ends_at:
                                raise ValueError(
                                    "agent delegation extension must advance ends_at"
                                )
                            updated = replace(
                                current,
                                ends_at=ends_at,
                                state=effective_state,
                                updated_at=updated_at,
                            )
                        else:
                            action = payload.get("action")
                            if action not in {"complete", "revoke"}:
                                raise ValueError(
                                    "agent delegation action must be complete or revoke"
                                )
                            updated = replace(
                                current,
                                state=(
                                    DelegationState.COMPLETED
                                    if action == "complete"
                                    else DelegationState.REVOKED
                                ),
                                updated_at=updated_at,
                            )
                        with foreground_operation():
                            receipt = adapter.update_agent_delegation(
                                updated,
                                expected_version=expected_version,
                            )
                            response = delegation_response(updated, receipt)
                except ConcurrentAgentDelegationUpdateError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "delegation_changed", "slug": exc.slug},
                    )
                    return
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_agent_delegation"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                except OSError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "delegation_lock_unavailable"},
                    )
                    return
                if response is None:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": "agent delegation mutation lacked exact verified canonical readback.",
                            "code": "delegation_not_verified",
                            "slug": updated.slug,
                        },
                    )
                    return
                self._json(HTTPStatus.OK, response)
                return
            todo_prefix = "/api/todos/"
            todo_status_suffix = "/status"
            if path.startswith(todo_prefix) and path.endswith(todo_status_suffix):
                todo_slug = unquote(path[len(todo_prefix) : -len(todo_status_suffix)])
                before_snapshot = None
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "status", "expected_updated_at", "actor", "source", "idempotency_key"
                }
                if set(payload) != required:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "TODO status change requires exact mutation context.", "code": "invalid_todo"},
                    )
                    return
                if payload.get("status") not in {"not_done", "done"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "TODO status must be not_done or done.", "code": "invalid_todo"},
                    )
                    return
                try:
                    expected = datetime.fromisoformat(
                        str(payload["expected_updated_at"]).replace("Z", "+00:00")
                    )
                    with foreground_operation():
                        if active_handoff_event_bridge is not None:
                            before_snapshot = read_todo_mutation_snapshot(todo_slug)
                        if (
                            payload["status"] == "done"
                            and adapter.is_active_handoff_question(todo_slug)
                        ):
                            self._json(
                                HTTPStatus.CONFLICT,
                                {
                                    "error": (
                                        "This TODO is the task's active blocking question. "
                                        "Use Answer and Hand Back so the answer and task lifecycle "
                                        "change are verified together."
                                    ),
                                    "code": "handoff_answer_required",
                                    "slug": todo_slug,
                                },
                            )
                            return
                        receipt = adapter.set_todo_status(
                            todo_slug,
                            status=payload["status"],
                            expected_updated_at=expected,
                            actor=payload["actor"],
                            source=payload["source"],
                            idempotency_key=payload["idempotency_key"],
                            now=clock(),
                        )
                except ConcurrentTodoUpdateError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "todo_changed", "slug": exc.todo_slug},
                    )
                    return
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_todo"},
                    )
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="todo_status",
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                after_verified_todo_mutation(
                    before_snapshot,
                    receipt,
                    mutation_kind="todo_status",
                    todo_slug=todo_slug,
                )
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if path.startswith(todo_prefix) and "/" not in path[len(todo_prefix) :]:
                todo_slug = unquote(path[len(todo_prefix) :])
                before_snapshot = None
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "text", "detail", "expected_updated_at", "actor", "source", "idempotency_key"
                }
                if set(payload) != required:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "TODO edit requires exact mutation context.", "code": "invalid_todo"},
                    )
                    return
                try:
                    expected = datetime.fromisoformat(
                        str(payload["expected_updated_at"]).replace("Z", "+00:00")
                    )
                    with foreground_operation():
                        if active_handoff_event_bridge is not None:
                            before_snapshot = read_todo_mutation_snapshot(todo_slug)
                        receipt = adapter.edit_todo(
                            todo_slug,
                            text=payload["text"],
                            detail=payload["detail"],
                            expected_updated_at=expected,
                            actor=payload["actor"],
                            source=payload["source"],
                            idempotency_key=payload["idempotency_key"],
                            now=clock(),
                        )
                except ConcurrentTodoUpdateError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "todo_changed", "slug": exc.todo_slug},
                    )
                    return
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_todo"},
                    )
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="todo_edited",
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": str(exc), "code": "partial_write", "slug": exc.slug},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                after_verified_todo_mutation(
                    before_snapshot,
                    receipt,
                    mutation_kind="todo_edited",
                    todo_slug=todo_slug,
                )
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            system_ticket_prefix = "/api/system-tickets/"
            if path.startswith(system_ticket_prefix) and "/" not in path[len(system_ticket_prefix) :]:
                ticket_slug = unquote(path[len(system_ticket_prefix) :])
                payload = self._read_json()
                if payload is None:
                    return
                required = {
                    "title", "status", "priority", "target_subsystem",
                    "verbatim_request", "acceptance_criteria",
                }
                if set(payload) != required:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "System Ticket edit requires exactly the editable ticket fields.", "code": "invalid_system_ticket"})
                    return
                try:
                    existing = next(ticket for ticket in adapter.list_system_tickets().tickets if ticket.slug == ticket_slug)
                    title = payload["title"]
                    request = payload["verbatim_request"]
                    criteria = payload["acceptance_criteria"]
                    status = payload["status"]
                    priority = payload["priority"]
                    target = payload["target_subsystem"]
                    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
                        raise DomainValidationError("system ticket title must be 1 to 160 characters")
                    if not isinstance(request, str) or not request.strip():
                        raise DomainValidationError("system ticket verbatim_request is required")
                    if not isinstance(criteria, str):
                        raise DomainValidationError("system ticket acceptance_criteria must be text")
                    if status not in SYSTEM_TICKET_STATUSES:
                        raise DomainValidationError("system ticket status is invalid")
                    if priority not in TASK_PRIORITIES:
                        raise DomainValidationError("system ticket priority is invalid")
                    if target not in SYSTEM_TICKET_TARGETS:
                        raise DomainValidationError("system ticket target_subsystem is invalid")
                    ticket = replace(
                        existing,
                        title=title.strip(), status=status, priority=priority,
                        target_subsystem=target, verbatim_request=request.strip(),
                        acceptance_criteria=criteria.strip(), updated_at=clock(),
                    )
                    receipt = adapter.update_system_ticket(ticket)
                except StopIteration:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "System Ticket was not found."})
                    return
                except DomainValidationError as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_system_ticket"})
                    return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "partial_write", "slug": exc.slug})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                    return
                invalidate_system_tickets()
                self._json(HTTPStatus.OK, {"ticket": ticket.to_dict(), "receipt": receipt.to_dict()})
                return
            project_prefix = "/api/projects/"
            if path.startswith(project_prefix) and "/" not in path[len(project_prefix) :]:
                project_slug = unquote(path[len(project_prefix) :])
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) - {"title", "summary", "status", "supporting_goal_slugs"}:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "project edit contains unsupported fields.", "code": "invalid_project"})
                    return
                raw_goals = payload.get("supporting_goal_slugs", [])
                if not isinstance(raw_goals, list) or not all(isinstance(item, str) and item.startswith("goals/") for item in raw_goals):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "supporting_goal_slugs must be a list of canonical goal slugs.", "code": "invalid_project"})
                    return
                try:
                    existing = next(project for project in adapter.list_projects().projects if project.slug == project_slug)
                    raw_goals = existing.supporting_goal_slugs if raw_goals is None else raw_goals
                    valid_goals = {goal.slug for goal in adapter.list_goals().goals}
                    if not set(raw_goals).issubset(valid_goals):
                        raise DomainValidationError("Each supporting goal must be a current canonical goal.")
                    title = payload.get("title", existing.title)
                    summary = payload.get("summary", existing.summary)
                    status = payload.get("status", existing.status)
                    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
                        raise DomainValidationError("project title must be 1 to 160 characters")
                    if not isinstance(summary, str) or len(summary.strip()) > 500:
                        raise DomainValidationError("project summary must be 500 characters or fewer")
                    if status not in {"planned", "active", "paused", "completed", "cancelled"}:
                        raise DomainValidationError("project status is invalid")
                    project = replace(existing, title=title.strip(), summary=summary.strip() or title.strip(), status=status, supporting_goal_slugs=tuple(dict.fromkeys(raw_goals)), updated_at=clock())
                    receipt = adapter.update_project(project)
                except StopIteration:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Project was not found."})
                    return
                except DomainValidationError as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_project"})
                    return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "partial_write", "slug": exc.slug})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                    return
                self._json(HTTPStatus.OK, {"project": project.to_dict(), "receipt": receipt.to_dict()})
                return
            proposal_prefix = "/api/proposals/"
            proposal_review_suffix = "/review"
            if (
                path.startswith(proposal_prefix)
                and path.endswith(proposal_review_suffix)
            ):
                proposal_slug = unquote(
                    path[len(proposal_prefix) : -len(proposal_review_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) != {
                    "title",
                    "rationale",
                    "proposed_next_step",
                    "due_day",
                }:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "proposal review requires title, rationale, "
                                "proposed_next_step, and due_day."
                            ),
                            "code": "invalid_proposal_review",
                        },
                    )
                    return
                try:
                    raw_due = payload["due_day"]
                    if not isinstance(raw_due, str):
                        raise ValueError("proposal due_day must be YYYY-MM-DD")
                    due_day = date.fromisoformat(raw_due)
                    receipt = adapter.review_proposal(
                        proposal_slug,
                        title=payload["title"],
                        rationale=payload["rationale"],
                        proposed_next_step=payload["proposed_next_step"],
                        due_day=due_day,
                        now=clock(),
                    )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_proposal_review"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            goal_prefix = "/api/goals/"
            if path.startswith(goal_prefix) and "/" not in path[len(goal_prefix):]:
                goal_slug = unquote(path[len(goal_prefix):])
                payload = self._read_json()
                if payload is None:
                    return
                required = {"title", "outcome", "success_criteria", "strategy", "review_cadence", "constraints", "target_day"}
                if set(payload) != required or any(not isinstance(payload[field], str) for field in required):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Goal edit requires all goal fields and a target date.", "code": "invalid_goal"})
                    return
                try:
                    receipt = adapter.update_goal(
                        goal_slug, title=payload["title"], outcome=payload["outcome"],
                        success_criteria=payload["success_criteria"], strategy=payload["strategy"],
                        review_cadence=payload["review_cadence"], constraints=payload["constraints"],
                        target_day=date.fromisoformat(payload["target_day"]),
                    )
                except (DomainValidationError, ValueError) as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_goal"})
                    return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "partial_write", "slug": exc.slug})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"goal": receipt.goal.to_dict(), "receipt": receipt.to_dict()})
                return
            goal_status_suffix = "/status"
            if path.startswith(goal_prefix) and path.endswith(goal_status_suffix):
                goal_slug = unquote(
                    path[len(goal_prefix) : -len(goal_status_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                if payload.get("status") != "paused" or len(payload) != 1:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "goal status action supports only paused.",
                            "code": "invalid_goal_status",
                        },
                    )
                    return
                try:
                    receipt = adapter.set_goal_paused(goal_slug)
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_goal"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            prefix = "/api/tasks/"
            if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                task_slug = unquote(path[len(prefix) :])
                before_snapshot = None
                payload = self._read_json()
                if payload is None:
                    return
                allowed = {
                    "title", "detail", "priority", "due_day",
                    "project_slug", "goal_slug", "parent_slug", "status", "assignee_slug",
                    "progress_metric", "progress_metric_revision", "handoff_reason", "complete_when_target_reached",
                }
                if set(payload) - allowed:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "task edit contains unsupported fields.", "code": "invalid_task_edit"})
                    return
                try:
                    due_day = date.fromisoformat(payload.get("due_day", ""))
                    current = adapter.get_task(task_slug)
                    before_snapshot = mutation_snapshot(current)
                    raw_metric = payload.get("progress_metric")
                    existing_verified_history = bool(
                        current.event_progress
                        and (
                            current.event_progress.evidence_slugs
                            or current.event_progress.receipt_ids
                        )
                    )
                    requested_binding = (
                        raw_metric.get("event_binding")
                        if isinstance(raw_metric, dict)
                        else None
                    )
                    if existing_verified_history and requested_binding != "job_applied":
                        raise DomainValidationError(
                            "Verified job-application evidence cannot be removed or converted "
                            "to a manual metric. The task was not changed."
                        )
                    if isinstance(raw_metric, dict) and raw_metric.get("event_binding") == "job_applied":
                        if task_slug != JOB_APPLIED_BOUND_TASK_SLUG:
                            raise DomainValidationError(
                                "Automatic job-applied events are explicitly bound to "
                                f"{JOB_APPLIED_BOUND_TASK_SLUG}. This task was not changed."
                            )
                        event_progress = current.event_progress
                        expected_revision = progress_revision(current)
                        supplied_revision = payload.get("progress_metric_revision")
                        if expected_revision is not None and supplied_revision != expected_revision:
                            raise DomainValidationError(
                                "Verified job-application progress changed after Edit opened. "
                                "Your entered values were not changed; refresh or reopen Edit and try again."
                            )
                        current_value = raw_metric.get("current")
                        if (
                            event_progress is None
                            and current.progress_metric is not None
                            and current.progress_metric.event_binding is None
                        ):
                            event_progress = EventProgress()
                        verified_count = (
                            len(event_progress.receipt_ids)
                            if event_progress is not None
                            else 0
                        )
                        if (
                            event_progress is None
                            or isinstance(current_value, bool)
                            or not isinstance(current_value, int)
                            or current_value < verified_count
                        ):
                            task_day = (
                                current.progress_metric.task_day
                                if current.progress_metric is not None
                                else due_day
                            )
                            raise DomainValidationError(
                                f"This task has {verified_count} distinct verified job-application "
                                f"event{'s' if verified_count != 1 else ''} for {task_day.isoformat()} "
                                f"({JOB_APPLIED_TIMEZONE}). Current progress cannot be lower than "
                                f"{verified_count}. Set Current to {verified_count} or higher; "
                                "your entered values were not changed."
                            )
                        event_progress = replace(
                            event_progress,
                            baseline_count=current_value - verified_count,
                        )
                        progress_metric = ProgressMetric(
                            kind=raw_metric.get("kind", "count"), label=raw_metric.get("label"),
                            unit="job_application", target=raw_metric.get("target"), current=current_value,
                            event_binding="job_applied", auto_complete=bool(raw_metric.get("auto_complete", True)),
                            task_day=due_day, timezone="America/Los_Angeles",
                        )
                    else:
                        progress_metric, event_progress = _progress_metric_from_request(
                            raw_metric, due_day=due_day, task_slug=task_slug
                        )
                    requested_status = payload.get("status")
                    if requested_status not in EDITABLE_TASK_STATUSES | {"proposed"}:
                        raise DomainValidationError("status must be a supported task status")
                    if current.status == "proposed":
                        if requested_status != "proposed":
                            raise DomainValidationError("Approve or reject proposed work through the explicit review action.")
                        if payload.get("assignee_slug", current.owner_agent) != current.owner_agent:
                            raise DomainValidationError("Proposed work keeps its assigned agent until approved.")
                    if progress_metric and progress_metric.current >= progress_metric.target and requested_status not in {"completed", "cancelled"}:
                        if payload.get("complete_when_target_reached") is not True:
                            raise DomainValidationError("Metric target is reached. Confirm completion or choose a completed status explicitly.")
                        requested_status = "completed"
                    receipt = adapter.edit_task(
                        task_slug,
                        title=payload.get("title", ""), detail=payload.get("detail", ""),
                        priority=payload.get("priority", "normal"), due_day=due_day,
                        next_action=current.next_action,
                        project_slug=payload.get("project_slug") or None,
                        goal_slug=payload.get("goal_slug") or None,
                        parent_slug=_parent_slug_from_request(payload, task_slug=task_slug),
                        status=requested_status,
                        assignee_slug=payload.get("assignee_slug", "tony"),
                        progress_metric=progress_metric, event_progress=event_progress,
                        handoff_reason=payload.get("handoff_reason", ""), now=clock(),
                    )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_task_edit"})
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="task_edit",
                    )
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "partial_write", "slug": exc.slug})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                    return
                receipt_value = canonical_mapping(receipt)
                after_canonical_mutation(
                    before_snapshot,
                    mutation_snapshot(receipt_value.get("task")),
                    receipt_value,
                    mutation_kind="task_edit",
                )
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            membership_suffix = "/relationships/active-membership"
            if path.endswith(membership_suffix):
                action = "active_membership"
                suffix = membership_suffix
            elif path.endswith("/goal"):
                action = "goal"
                suffix = "/goal"
            elif path.endswith("/next-action"):
                action = "next_action"
                suffix = "/next-action"
            elif path.endswith("/progress-metric"):
                action = "progress_metric"
                suffix = "/progress-metric"
            elif path.endswith("/project"):
                action = "project"
                suffix = "/project"
            elif path.endswith("/status"):
                action = "status"
                suffix = "/status"
            else:
                action = ""
                suffix = ""
            if not path.startswith(prefix) or not action:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            encoded_slug = path[len(prefix) : -len(suffix)]
            task_slug = unquote(encoded_slug)
            if not task_slug:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Task slug is required."},
                )
                return
            payload = self._read_json()
            if payload is None:
                return
            if action == "active_membership":
                if payload:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Active membership repair takes no options.",
                            "code": "invalid_repair",
                        },
                    )
                    return
                try:
                    receipt = adapter.repair_active_membership(task_slug)
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
                except ValueError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "repair_not_eligible"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if action == "next_action":
                self._json(
                    HTTPStatus.GONE,
                    {
                        "error": (
                            "The single Next Action write endpoint is retired. "
                            "Create or update a canonical per-item TODO instead."
                        ),
                        "code": "next_action_retired",
                    },
                )
                return
            if action == "progress_metric":
                before_snapshot = None
                if set(payload) - {"progress_metric", "task_day", "progress_metric_revision"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "Progress metric updates accept only "
                                "progress_metric and task_day."
                            ),
                            "code": "invalid_progress_metric",
                        },
                    )
                    return
                raw_metric = payload.get("progress_metric")
                task_day_value = payload.get("task_day")
                if raw_metric is None:
                    task_day = clock().date()
                elif not isinstance(task_day_value, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "task_day is required when configuring a "
                                "progress metric."
                            ),
                            "code": "invalid_progress_metric",
                        },
                    )
                    return
                else:
                    try:
                        task_day = date.fromisoformat(task_day_value)
                    except ValueError:
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {
                                "error": "task_day must be an ISO calendar date.",
                                "code": "invalid_progress_metric",
                            },
                        )
                        return
                try:
                    current = adapter.get_task(task_slug)
                    before_snapshot = mutation_snapshot(current)
                    existing_verified_history = bool(
                        current.event_progress
                        and (
                            current.event_progress.evidence_slugs
                            or current.event_progress.receipt_ids
                        )
                    )
                    requested_binding = (
                        raw_metric.get("event_binding")
                        if isinstance(raw_metric, dict)
                        else None
                    )
                    if existing_verified_history and requested_binding != "job_applied":
                        raise DomainValidationError(
                            "Verified job-application evidence cannot be removed or converted "
                            "to a manual metric. The task was not changed."
                        )
                    expected_revision = progress_revision(current)
                    if expected_revision is not None and payload.get("progress_metric_revision") != expected_revision:
                        raise DomainValidationError(
                            "Verified job-application progress changed after Edit opened. "
                            "Refresh canonical data and try again."
                        )
                    progress_metric, event_progress = (
                        _progress_metric_from_request(
                            raw_metric,
                            due_day=task_day,
                            task_slug=task_slug,
                        )
                    )
                    receipt = adapter.set_task_progress_metric(
                        task_slug,
                        progress_metric,
                        event_progress,
                        clock(),
                    )
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_progress_metric",
                        },
                    )
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="derived_count",
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                receipt_value = canonical_mapping(receipt)
                after_canonical_mutation(
                    before_snapshot,
                    mutation_snapshot(receipt_value.get("task")),
                    receipt_value,
                    mutation_kind="derived_count",
                )
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if action == "project":
                project_slug = payload.get("project_slug")
                if project_slug == "":
                    project_slug = None
                if project_slug is not None and not isinstance(project_slug, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "project_slug must be a project slug or null.",
                            "code": "invalid_project_assignment",
                        },
                    )
                    return
                try:
                    receipt = adapter.set_task_project(task_slug, project_slug)
                except ValueError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_project_assignment",
                        },
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if action == "status":
                before_snapshot = None
                requested_status = payload.get("status")
                if (
                    not isinstance(requested_status, str)
                    or requested_status not in EDITABLE_TASK_STATUSES
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "status must be one of "
                                + ", ".join(sorted(EDITABLE_TASK_STATUSES))
                                + "."
                            ),
                            "code": "invalid_status",
                        },
                    )
                    return
                try:
                    if active_handoff_event_bridge is not None:
                        before_snapshot = mutation_snapshot(adapter.get_task(task_slug))
                    receipt = adapter.set_task_status(
                        task_slug,
                        requested_status,
                        clock(),
                    )
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
                except ValueError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_status"},
                    )
                    return
                except PartialMutationError as exc:
                    partial_mutation_attention(
                        before_snapshot,
                        slug=exc.slug,
                        mutation_kind="task_status",
                    )
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                receipt_value = canonical_mapping(receipt)
                after_canonical_mutation(
                    before_snapshot,
                    mutation_snapshot(receipt_value.get("task")),
                    receipt_value,
                    mutation_kind="task_status",
                )
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return

            goal_slug = payload.get("goal_slug")
            if goal_slug == "":
                goal_slug = None
            if goal_slug is not None and not isinstance(goal_slug, str):
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "goal_slug must be a goal slug or null."},
                )
                return
            try:
                receipt = adapter.set_task_goal(task_slug, goal_slug)
            except ValueError as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(exc), "code": "invalid_goal_link"},
                )
                return
            except PartialMutationError as exc:
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": str(exc),
                        "code": "partial_write",
                        "slug": exc.slug,
                    },
                )
                return
            except GBrainError as exc:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc), "code": "gbrain_unavailable"},
                )
                return
            invalidate_snapshot()
            self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})

        def do_DELETE(self) -> None:
            path = urlsplit(self.path).path
            prefix = "/api/goals/"
            if not path.startswith(prefix):
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            goal_slug = unquote(path[len(prefix) :])
            if not goal_slug:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Goal slug is required."},
                )
                return
            try:
                receipt = adapter.delete_goal(goal_slug)
            except (DomainValidationError, ValueError) as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(exc), "code": "invalid_goal"},
                )
                return
            except PartialMutationError as exc:
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": str(exc),
                        "code": "partial_write",
                        "slug": exc.slug,
                    },
                )
                return
            except GBrainError as exc:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc), "code": "gbrain_unavailable"},
                )
                return
            invalidate_snapshot()
            self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})

        def _serve_static(self, path: str) -> None:
            relative = {
                "/": "index.html",
                "/index.html": "index.html",
                "/styles.css": "styles.css",
                "/app.js": "app.js",
                "/favicon.svg": "favicon.svg",
                "/favicon.ico": "favicon.ico",
                "/assets/mission-control-command-mark.svg": "assets/mission-control-command-mark.svg",
                "/assets/inbox-check.svg": "assets/inbox-check.svg",
                "/assets/apple-touch-icon-180.png": "assets/apple-touch-icon-180.png",
                "/assets/mission-control-word-art.svg": "assets/mission-control-word-art.svg",
                "/assets/mission-control-word-art.png": "assets/mission-control-word-art.png",
            }.get(path)
            if relative is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            file_path = static_dir / relative
            try:
                body = file_path.read_bytes()
            except FileNotFoundError:
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"Static asset is unavailable: {relative}"},
                )
                return
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "application/json",
                "image/svg+xml",
            }:
                content_type = f"{content_type}; charset=utf-8"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return GTasksHandler


def build_server(
    host: str = "127.0.0.1",
    port: int = 4179,
    adapter: GBrainAdapter | None = None,
    clock: Callable[[], datetime] | None = None,
    identity_factory: Callable[[], str] | None = None,
    static_dir: Path = STATIC_DIR,
    warning_store: WarningDismissalStore | None = None,
    log_reader: OperationalLogReader | None = None,
    stargraph_url: str = "http://127.0.0.1:8788",
    ical_reader: ICalendarReader | None = None,
    calendar_preferences: CalendarPreferences | None = None,
    read_cache: ReadSurfaceCache | None = None,
    artifact_publisher_auth: ArtifactPublisherAuth | None = None,
    handoff_store: DurableHandoffStore | None = None,
    handoff_dispatcher_auth: HandoffDispatcherAuth | None = None,
    handoff_registration_validator: Callable[
        [str, str], AgentRegistration | None
    ]
    | None = None,
    handoff_waiter: Callable[[float], None] | None = None,
    handoff_event_bridge: CanonicalHandoffEventBridge | None = None,
    delegation_lock_path: Path | None = None,
) -> ThreadingHTTPServer:
    if not stargraph_url.startswith("http://127.0.0.1:"):
        raise ValueError("avatar attachment service must use a local 127.0.0.1 URL")
    active_log_reader = log_reader or OperationalLogReader()
    active_log_reader.append_gtasks(
        severity="info",
        message="GTasks runtime initialized.",
        now=(clock or (lambda: datetime.now().astimezone()))(),
    )
    handler = _handler_class(
        adapter or GBrainAdapter(),
        clock or (lambda: datetime.now().astimezone()),
        identity_factory or (lambda: uuid.uuid4().hex[:8]),
        static_dir,
        warning_store or WarningDismissalStore(),
        active_log_reader,
        stargraph_url.rstrip("/"),
        ical_reader,
        calendar_preferences,
        read_cache,
        artifact_publisher_auth,
        handoff_store,
        handoff_dispatcher_auth,
        handoff_registration_validator,
        handoff_waiter,
        handoff_event_bridge,
        delegation_lock_path,
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local GTasks interface backed by GBrain."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4179)
    parser.add_argument("--warning-state-file", type=Path)
    parser.add_argument("--operation-log-file", type=Path)
    parser.add_argument("--queue-log-file", type=Path)
    parser.add_argument("--artifact-publisher-credentials-file", type=Path)
    parser.add_argument("--handoff-store", type=Path)
    parser.add_argument("--handoff-dispatcher-credentials-file", type=Path)
    parser.add_argument("--agent-delegation-lock-file", type=Path)
    parser.add_argument("--stargraph-url", default=os.environ.get("MEMORY_STARGRAPH_URL", "http://127.0.0.1:8788"))
    args = parser.parse_args()

    adapter = GBrainAdapter()
    handoff_store = (
        DurableHandoffStore(str(args.handoff_store))
        if args.handoff_store
        else None
    )
    handoff_dispatcher_auth = load_handoff_dispatcher_auth(
        args.handoff_dispatcher_credentials_file
    )
    handoff_event_bridge = (
        build_runtime_handoff_event_bridge(
            adapter,
            handoff_store,
            handoff_dispatcher_auth,
        )
        if handoff_store is not None
        else None
    )
    server = build_server(
        host=args.host,
        port=args.port,
        warning_store=(
            WarningDismissalStore(args.warning_state_file)
            if args.warning_state_file
            else None
        ),
        log_reader=OperationalLogReader(
            gtasks_store=OperationalLogStore(args.operation_log_file),
            queue_path=args.queue_log_file,
        )
        if args.operation_log_file or args.queue_log_file
        else None,
        stargraph_url=args.stargraph_url,
        adapter=adapter,
        artifact_publisher_auth=load_artifact_publisher_auth(
            args.artifact_publisher_credentials_file
        ),
        handoff_store=handoff_store,
        handoff_dispatcher_auth=handoff_dispatcher_auth,
        handoff_event_bridge=handoff_event_bridge,
        delegation_lock_path=args.agent_delegation_lock_file,
    )
    print(f"GTasks listening on http://{args.host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if handoff_store is not None:
            handoff_store.close()


if __name__ == "__main__":
    main()
