"""Durable, privacy-safe handoff classification and audit primitives."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import threading
import time
from typing import Callable, Iterable, Iterator
from uuid import uuid4

from .delegation import (
    AgentDelegationLease,
    DelegationState,
    delegated_work_is_eligible,
    lease_state_at,
)


ACTIONABLE_TRIGGERS = frozenset(
    {
        "answer_received",
        "tony_answer_received",
        "waiting_for_information_updated",
        "todo_added",
        "todo_materially_changed",
        "task_activated",
        "blocker_resolved",
        "system_dependency_recovered",
        "authorization_granted",
        "ownership_changed",
    }
)
SUPPRESSED_TRIGGERS = {
    "presentation_only": "presentation_only",
    "duplicate_save": "duplicate_save",
    "derived_count": "derived_count",
    "stale_cache_refresh": "stale_cache_refresh",
    "unchanged_blocker": "stable_blocker",
    "stable_blocker": "stable_blocker",
    "tony_owned_no_agent": "tony_owned_no_agent",
}
ACKNOWLEDGEMENT_STATES = frozenset(
    {"received", "actively_executing", "still_blocked", "completed"}
)
ACKNOWLEDGEMENT_TRANSITIONS = {
    "leased": ACKNOWLEDGEMENT_STATES,
    "received": frozenset({"actively_executing", "still_blocked", "completed"}),
    "actively_executing": frozenset({"still_blocked", "completed"}),
    "still_blocked": frozenset({"actively_executing", "completed"}),
    "completed": frozenset(),
}
DEFAULT_RECOVERY_LEASE_SECONDS = 30
DEFAULT_EXECUTION_CLAIM_SECONDS = 3600
EXECUTION_TERMINAL_STATES = frozenset(
    {"completed", "revoked", "expired", "checkpointed", "dead_letter"}
)
_STRUCTURED_ID = re.compile(r"[a-z0-9][a-z0-9._/-]{0,127}")
_CORRELATION_ID = re.compile(r"(?:corr|correlation)-[a-z0-9][a-z0-9._-]{0,47}")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,;:!?()'/_-]{0,159}")
_OPAQUE_VALUE = re.compile(r"[A-Za-z0-9_-]{24,}")
_PRIVATE_TEXT = re.compile(
    r"\b(?:bearer|secret|token|thread(?:[_ ]?id)?|private prompt|system prompt|full output|raw output)\b",
    re.IGNORECASE,
)


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _require_utc(value, "timestamp").isoformat()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _reference(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _registration_reference(registration_id: str) -> str:
    return _reference(registration_id)


def _require_structured_id(value: str, field: str) -> str:
    if not isinstance(value, str) or _STRUCTURED_ID.fullmatch(value) is None:
        raise ValueError(f"{field} must use a bounded privacy-safe identifier")
    return value


def _require_safe_text(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be privacy-safe text")
    stripped = value.strip()
    if (
        _SAFE_TEXT.fullmatch(stripped) is None
        or _PRIVATE_TEXT.search(stripped) is not None
        or _OPAQUE_VALUE.search(stripped) is not None
    ):
        raise ValueError(f"{field} must be privacy-safe text")
    return stripped


def _validate_change(change: ActionableChange) -> None:
    _require_structured_id(change.task_slug, "task_slug")
    _require_structured_id(change.canonical_event_id, "canonical_event_id")
    _require_structured_id(change.canonical_version, "canonical_version")
    _require_structured_id(change.trigger, "trigger")
    _require_structured_id(change.route, "route")
    _require_structured_id(change.task_status, "task_status")
    _require_structured_id(change.requested_operation, "requested_operation")
    for agent_slug in change.assigned_to:
        _require_structured_id(agent_slug, "assigned_to")
    _require_safe_text(change.summary, "summary")
    if change.correlation_id is not None:
        if _CORRELATION_ID.fullmatch(change.correlation_id) is None:
            raise ValueError("correlation_id must be a bounded privacy-safe correlation id")
    if change.blocker is not None:
        _require_safe_text(change.blocker, "blocker")


@dataclass(frozen=True, slots=True)
class AgentRegistration:
    registration_id: str
    agent_slug: str
    route: str
    verified: bool
    _registration_reference: str | None = None

    @classmethod
    def from_reference(
        cls,
        registration_reference: str,
        *,
        agent_slug: str,
        route: str,
        verified: bool = True,
    ) -> "AgentRegistration":
        if re.fullmatch(r"[0-9a-f]{64}", registration_reference) is None:
            raise ValueError("registration reference must be a SHA-256 digest")
        return cls(
            registration_id=registration_reference,
            agent_slug=agent_slug,
            route=route,
            verified=verified,
            _registration_reference=registration_reference,
        )

    @property
    def reference(self) -> str:
        return self._registration_reference or _registration_reference(
            self.registration_id
        )

    @property
    def lease_identity(self) -> str:
        return self._registration_reference or self.registration_id


@dataclass(frozen=True, slots=True)
class ActionableChange:
    task_slug: str
    canonical_event_id: str
    canonical_version: str
    trigger: str
    assigned_to: tuple[str, ...]
    route: str
    summary: str
    occurred_at: datetime
    correlation_id: str | None = None
    blocker: str | None = None
    task_status: str = "unknown"
    requested_operation: str = "task_status"


@dataclass(frozen=True, slots=True)
class Classification:
    actionable: bool
    reason: str
    agent_slug: str | None
    registration_ref: str | None


@dataclass(frozen=True, slots=True)
class HandoffRecord:
    handoff_id: str
    task_slug: str
    canonical_event_id: str
    canonical_version: str
    idempotency_key: str
    trigger: str
    agent_slug: str | None
    executor_agent: str | None
    permanent_owner: str | None
    delegation_slug: str | None
    registration_ref: str | None
    status: str
    reason: str
    summary: str
    correlation_id: str | None
    created_at: datetime
    attempt: int
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "handoff_id": self.handoff_id,
            "task_slug": self.task_slug,
            "canonical_event_id": self.canonical_event_id,
            "canonical_version": self.canonical_version,
            "idempotency_key": self.idempotency_key,
            "trigger": self.trigger,
            "agent_slug": self.agent_slug,
            "executor_agent": self.executor_agent,
            "permanent_owner": self.permanent_owner,
            "delegation_slug": self.delegation_slug,
            "registration_ref": self.registration_ref,
            "status": self.status,
            "reason": self.reason,
            "summary": self.summary,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(),
            "attempt": self.attempt,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    record: HandoffRecord
    lease_token: str
    lease_generation: int

    @property
    def handoff_id(self) -> str:
        return self.record.handoff_id

    @property
    def agent_slug(self) -> str | None:
        return self.record.agent_slug

    @property
    def status(self) -> str:
        return self.record.status

    @property
    def task_slug(self) -> str:
        return self.record.task_slug


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    task_slug: str
    executor_agent: str
    permanent_owner: str
    delegation_slug: str | None
    correlation_id: str
    idempotency_key: str
    claimed_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _ExecutionClaimRequest:
    permanent_owner: str
    executor_agent: str
    delegation: AgentDelegationLease | None
    task_status: str
    requested_operation: str
    owned_work_ready: bool
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class HandoffRecoveryState:
    handoff_id: str
    status: str
    lease_generation: int
    agent_slug: str
    registration_ref: str

    def to_dict(self) -> dict[str, object]:
        return {
            "handoff_id": self.handoff_id,
            "status": self.status,
            "lease_generation": self.lease_generation,
            "agent_slug": self.agent_slug,
            "registration_ref": self.registration_ref,
        }


class HandoffOwnershipError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HandoffEvent:
    event_id: str
    sequence: int
    handoff_id: str
    task_slug: str
    canonical_event_id: str
    canonical_version: str
    idempotency_key: str
    classification_reason: str
    trigger: str
    attempt: int
    lease_generation: int
    mutation_ref: str | None
    agent_slug: str | None
    executor_agent: str | None
    permanent_owner: str | None
    delegation_slug: str | None
    execution_state: str | None
    registration_ref: str | None
    status: str
    event_type: str
    summary: str
    detail: str | None
    correlation_id: str | None
    occurred_at: datetime
    recorded_at: datetime
    supersedes_event_id: str | None


@dataclass(frozen=True, slots=True)
class EventPage:
    events: tuple[HandoffEvent, ...]
    total: int
    next_sequence: int | None

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "events": [
                {
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "handoff_id": event.handoff_id,
                    "task_slug": event.task_slug,
                    "canonical_event_id": event.canonical_event_id,
                    "canonical_version": event.canonical_version,
                    "idempotency_key": event.idempotency_key,
                    "classification_reason": event.classification_reason,
                    "trigger": event.trigger,
                    "attempt": event.attempt,
                    "lease_generation": event.lease_generation,
                    "mutation_ref": event.mutation_ref,
                    "agent_slug": event.agent_slug,
                    "executor_agent": event.executor_agent,
                    "permanent_owner": event.permanent_owner,
                    "delegation_slug": event.delegation_slug,
                    "execution_state": event.execution_state,
                    "registration_ref": event.registration_ref,
                    "status": event.status,
                    "event_type": event.event_type,
                    "summary": event.summary,
                    "detail": event.detail,
                    "correlation_id": event.correlation_id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "recorded_at": event.recorded_at.isoformat(),
                    "supersedes_event_id": event.supersedes_event_id,
                }
                for event in self.events
            ],
            "total": self.total,
        }
        if self.next_sequence is not None:
            value["next_sequence"] = self.next_sequence
        return value


class HandoffClassifier:
    """Classify a canonical change without inferring identity or route."""

    def classify(
        self,
        change: ActionableChange,
        registrations: Iterable[AgentRegistration] = (),
        *,
        executor_agent: str | None = None,
    ) -> Classification:
        if change.trigger in SUPPRESSED_TRIGGERS:
            return Classification(False, SUPPRESSED_TRIGGERS[change.trigger], None, None)
        if change.trigger not in ACTIONABLE_TRIGGERS:
            return Classification(False, "non_actionable", None, None)
        if not change.assigned_to:
            return Classification(False, "missing_assignment", None, None)
        if len(change.assigned_to) != 1:
            return Classification(False, "multiple_assignments", None, None)

        agent_slug = executor_agent or change.assigned_to[0]
        _require_structured_id(agent_slug, "executor_agent")
        eligible = [
            registration
            for registration in registrations
            if registration.verified and registration.agent_slug == agent_slug
        ]
        if not eligible:
            return Classification(False, "missing_registration", agent_slug, None)
        if len(eligible) != 1:
            return Classification(False, "multiple_registrations", agent_slug, None)
        matched = eligible[0]
        if matched.route != change.route:
            return Classification(False, "route_mismatch", agent_slug, None)
        return Classification(True, change.trigger, agent_slug, matched.reference)


class DurableHandoffStore:
    """SQLite-backed idempotent outbox with an append-only audit event table."""

    def __init__(self, path: str, *, retention_days: int = 90) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self.path = path
        self.retention_days = retention_days
        if path != ":memory:":
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("handoff store must be a regular private file")
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            if os.stat(path, follow_symlinks=False).st_mode & 0o777 != 0o600:
                raise ValueError("handoff store must use mode 0600")
        self._connection = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,
            timeout=2.0,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 2000")
        self._lock = threading.RLock()
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def _create_schema(self) -> None:
        with self._write_transaction():
            statements = (
                """
                CREATE TABLE IF NOT EXISTS handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    task_slug TEXT NOT NULL,
                    canonical_event_id TEXT NOT NULL,
                    canonical_version TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    agent_slug TEXT,
                    executor_agent TEXT,
                    permanent_owner TEXT,
                    delegation_slug TEXT,
                    registration_ref TEXT,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    correlation_id TEXT,
                    created_at TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    detail TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS leases (
                    handoff_id TEXT PRIMARY KEY REFERENCES handoffs(handoff_id),
                    registration_id TEXT NOT NULL,
                    registration_agent_slug TEXT NOT NULL,
                    registration_route TEXT NOT NULL,
                    lease_until TEXT,
                    lease_capability_ref TEXT,
                    lease_generation INTEGER NOT NULL DEFAULT 0
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS mutation_receipts (
                    mutation_ref TEXT PRIMARY KEY,
                    handoff_id TEXT NOT NULL REFERENCES handoffs(handoff_id),
                    operation TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    registration_ref TEXT NOT NULL,
                    lease_generation INTEGER NOT NULL,
                    lease_capability_ref TEXT NOT NULL,
                    resulting_status TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS execution_claims (
                    claim_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    handoff_id TEXT NOT NULL UNIQUE REFERENCES handoffs(handoff_id),
                    task_slug TEXT NOT NULL,
                    executor_agent TEXT NOT NULL,
                    permanent_owner TEXT NOT NULL,
                    delegation_slug TEXT,
                    correlation_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    requested_operation TEXT NOT NULL,
                    delegation_version TEXT,
                    priority_version TEXT,
                    claimed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    terminal_state TEXT,
                    terminal_at TEXT,
                    release_mutation_ref TEXT UNIQUE,
                    release_event_id TEXT
                )
                """,
                """
                CREATE UNIQUE INDEX IF NOT EXISTS execution_claims_active_task
                    ON execution_claims(task_slug) WHERE terminal_state IS NULL
                """,
                """
                CREATE TABLE IF NOT EXISTS handoff_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    handoff_id TEXT NOT NULL REFERENCES handoffs(handoff_id),
                    task_slug TEXT NOT NULL,
                    canonical_event_id TEXT NOT NULL,
                    canonical_version TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    classification_reason TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    lease_generation INTEGER NOT NULL,
                    mutation_ref TEXT,
                    agent_slug TEXT,
                    executor_agent TEXT,
                    permanent_owner TEXT,
                    delegation_slug TEXT,
                    execution_state TEXT,
                    registration_ref TEXT,
                    status TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail TEXT,
                    correlation_id TEXT,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    supersedes_event_id TEXT
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS handoff_events_filters
                    ON handoff_events(task_slug, agent_slug, status, event_type, correlation_id, sequence)
                """,
                """
                CREATE TABLE IF NOT EXISTS delegation_authority (
                    delegation_slug TEXT PRIMARY KEY,
                    source_agent TEXT NOT NULL,
                    executor_agent TEXT NOT NULL,
                    state TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    allowed_operations TEXT NOT NULL,
                    version TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    verified INTEGER NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS executor_priority (
                    executor_agent TEXT PRIMARY KEY,
                    owned_work_ready INTEGER NOT NULL,
                    version TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS authority_control_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    control_kind TEXT NOT NULL,
                    subject_slug TEXT NOT NULL,
                    version TEXT NOT NULL,
                    state TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS wake_intents (
                    handoff_id TEXT PRIMARY KEY REFERENCES handoffs(handoff_id),
                    wake_token_ref TEXT NOT NULL UNIQUE,
                    execution_idempotency_key TEXT NOT NULL,
                    lease_generation INTEGER NOT NULL,
                    authorized_at TEXT NOT NULL
                )
                """,
            )
            for statement in statements:
                self._connection.execute(statement)
            for table, name, definition in (
                ("handoffs", "executor_agent", "TEXT"),
                ("handoffs", "permanent_owner", "TEXT"),
                ("handoffs", "delegation_slug", "TEXT"),
                ("handoff_events", "executor_agent", "TEXT"),
                ("handoff_events", "permanent_owner", "TEXT"),
                ("handoff_events", "delegation_slug", "TEXT"),
                ("handoff_events", "execution_state", "TEXT"),
                ("execution_claims", "delegation_version", "TEXT"),
                ("execution_claims", "priority_version", "TEXT"),
            ):
                self._ensure_schema_column(table, name, definition)
            self._connection.execute(
                """
                UPDATE handoffs
                SET executor_agent = COALESCE(executor_agent, agent_slug),
                    permanent_owner = COALESCE(permanent_owner, agent_slug),
                    correlation_id = COALESCE(
                        correlation_id,
                        'corr-' || substr(idempotency_key, 1, 24)
                    )
                WHERE executor_agent IS NULL OR permanent_owner IS NULL
                    OR correlation_id IS NULL
                """
            )
            self._backfill_legacy_execution_claims_in_transaction(
                now=datetime.now(timezone.utc)
            )

    def _backfill_legacy_execution_claims_in_transaction(
        self, *, now: datetime
    ) -> None:
        """Fence every deliverable pre-claim handoff or quarantine it atomically."""
        now = _require_utc(now, "now")
        rows = self._connection.execute(
            """
            SELECT h.* FROM handoffs h
            LEFT JOIN execution_claims e ON e.handoff_id = h.handoff_id
            WHERE h.status IN (
                'queued', 'retrying', 'leased', 'received',
                'actively_executing', 'still_blocked'
            ) AND e.claim_sequence IS NULL
            ORDER BY h.created_at, h.handoff_id
            """
        ).fetchall()
        for row in rows:
            conflict = self._connection.execute(
                """
                SELECT 1 FROM execution_claims
                WHERE task_slug = ? AND terminal_state IS NULL
                LIMIT 1
                """,
                (row["task_slug"],),
            ).fetchone()
            if (
                conflict is not None
                or row["executor_agent"] is None
                or row["permanent_owner"] is None
                or row["delegation_slug"] is not None
                or row["executor_agent"] != row["permanent_owner"]
            ):
                self._quarantine_unfenced_handoff_in_transaction(
                    row["handoff_id"], now=now
                )
                continue
            try:
                self._connection.execute(
                    """
                    INSERT INTO execution_claims (
                        handoff_id, task_slug, executor_agent, permanent_owner,
                        delegation_slug, correlation_id, idempotency_key,
                        requested_operation, delegation_version, priority_version,
                        claimed_at, expires_at, terminal_state, terminal_at,
                        release_mutation_ref, release_event_id
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, 'legacy_handoff', NULL, NULL,
                        ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        row["handoff_id"],
                        row["task_slug"],
                        row["executor_agent"],
                        row["permanent_owner"],
                        row["correlation_id"],
                        row["idempotency_key"],
                        _timestamp(now),
                        _timestamp(
                            now
                            + timedelta(seconds=DEFAULT_EXECUTION_CLAIM_SECONDS)
                        ),
                    ),
                )
            except sqlite3.IntegrityError:
                self._quarantine_unfenced_handoff_in_transaction(
                    row["handoff_id"], now=now
                )

    def _quarantine_unfenced_handoff_in_transaction(
        self, handoff_id: str, *, now: datetime
    ) -> None:
        changed = self._connection.execute(
            """
            UPDATE handoffs
            SET status = 'dead_letter', reason = 'legacy_execution_claim_conflict',
                detail = 'Legacy handoff could not acquire a unique task fence.'
            WHERE handoff_id = ? AND status IN (
                'queued', 'retrying', 'leased', 'received',
                'actively_executing', 'still_blocked'
            )
            """,
            (handoff_id,),
        ).rowcount
        if changed != 1:
            return
        self._connection.execute(
            """
            UPDATE leases SET lease_until = NULL, lease_capability_ref = NULL
            WHERE handoff_id = ?
            """,
            (handoff_id,),
        )
        self._append_event_from_record(
            self.get(handoff_id),
            event_type="delivery_terminal",
            summary="Legacy handoff quarantined because its task fence conflicted.",
            detail=None,
            mutation_ref=None,
            occurred_at=now,
            recorded_at=now,
            execution_state="dead_letter",
        )

    def _ensure_schema_column(
        self, table: str, name: str, definition: str
    ) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        }
        if name not in columns:
            self._connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )

    def observe_delegation_authority(
        self, lease: AgentDelegationLease, *, observed_at: datetime
    ) -> None:
        """Persist one verified canonical lease snapshot without stale overwrite."""
        if not isinstance(lease, AgentDelegationLease):
            raise TypeError("lease must be an AgentDelegationLease")
        observed_at = _require_utc(observed_at, "observed_at")
        version = _timestamp(lease.updated_at)
        allowed_operations = json.dumps(
            list(lease.allowed_operations), separators=(",", ":")
        )
        values = (
            lease.source_agent,
            lease.executor_agent,
            lease.state.value,
            _timestamp(lease.starts_at),
            _timestamp(lease.ends_at),
            allowed_operations,
        )
        with self._write_transaction():
            current = self._connection.execute(
                "SELECT * FROM delegation_authority WHERE delegation_slug = ?",
                (lease.slug,),
            ).fetchone()
            if current is not None:
                if current["version"] > version:
                    return
                current_values = (
                    current["source_agent"],
                    current["executor_agent"],
                    current["state"],
                    current["starts_at"],
                    current["ends_at"],
                    current["allowed_operations"],
                )
                if current["version"] == version:
                    if current_values != values or current["verified"] != 1:
                        raise ValueError(
                            "delegation version conflicts with verified authority"
                        )
                    if _parse_timestamp(current["observed_at"]) < observed_at:
                        self._connection.execute(
                            """
                            UPDATE delegation_authority SET observed_at = ?
                            WHERE delegation_slug = ? AND version = ?
                            """,
                            (_timestamp(observed_at), lease.slug, version),
                        )
                    return
            self._connection.execute(
                """
                INSERT INTO delegation_authority (
                    delegation_slug, source_agent, executor_agent, state,
                    starts_at, ends_at, allowed_operations, version,
                    observed_at, verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(delegation_slug) DO UPDATE SET
                    source_agent = excluded.source_agent,
                    executor_agent = excluded.executor_agent,
                    state = excluded.state,
                    starts_at = excluded.starts_at,
                    ends_at = excluded.ends_at,
                    allowed_operations = excluded.allowed_operations,
                    version = excluded.version,
                    observed_at = excluded.observed_at,
                    verified = 1
                """,
                (lease.slug, *values, version, _timestamp(observed_at)),
            )
            self._append_authority_event(
                control_kind="delegation",
                subject_slug=lease.slug,
                version=version,
                state=lease.state.value,
                observed_at=observed_at,
            )

    def observe_executor_priority(
        self,
        executor_agent: str,
        *,
        owned_work_ready: bool,
        version: str,
        observed_at: datetime,
    ) -> None:
        """Persist a versioned owned-work priority snapshot for atomic claims."""
        _require_structured_id(executor_agent, "executor_agent")
        _require_structured_id(version, "priority version")
        if not isinstance(owned_work_ready, bool):
            raise ValueError("owned_work_ready must be a boolean")
        observed_at = _require_utc(observed_at, "observed_at")
        with self._write_transaction():
            current = self._connection.execute(
                "SELECT * FROM executor_priority WHERE executor_agent = ?",
                (executor_agent,),
            ).fetchone()
            if current is not None:
                current_observed = _parse_timestamp(current["observed_at"])
                if current_observed > observed_at:
                    return
                if current_observed == observed_at:
                    if (
                        bool(current["owned_work_ready"]) != owned_work_ready
                        or current["version"] != version
                    ):
                        raise ValueError(
                            "priority observation conflicts at the same version instant"
                        )
                    return
                if (
                    bool(current["owned_work_ready"]) == owned_work_ready
                    and current["version"] == version
                ):
                    self._connection.execute(
                        """
                        UPDATE executor_priority SET observed_at = ?
                        WHERE executor_agent = ?
                        """,
                        (_timestamp(observed_at), executor_agent),
                    )
                    return
            self._connection.execute(
                """
                INSERT INTO executor_priority (
                    executor_agent, owned_work_ready, version, observed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(executor_agent) DO UPDATE SET
                    owned_work_ready = excluded.owned_work_ready,
                    version = excluded.version,
                    observed_at = excluded.observed_at
                """,
                (
                    executor_agent,
                    1 if owned_work_ready else 0,
                    version,
                    _timestamp(observed_at),
                ),
            )
            self._append_authority_event(
                control_kind="priority",
                subject_slug=executor_agent,
                version=version,
                state="owned_ready" if owned_work_ready else "delegation_eligible",
                observed_at=observed_at,
            )

    def observe_delegation_absence(
        self, delegation_slug: str, *, observed_at: datetime
    ) -> None:
        """Record canonical lease removal so an older active snapshot cannot wake."""
        _require_structured_id(delegation_slug, "delegation_slug")
        observed_at = _require_utc(observed_at, "observed_at")
        version = _timestamp(observed_at)
        with self._write_transaction():
            current = self._connection.execute(
                "SELECT * FROM delegation_authority WHERE delegation_slug = ?",
                (delegation_slug,),
            ).fetchone()
            if current is None or current["version"] > version:
                return
            self._connection.execute(
                """
                UPDATE delegation_authority
                SET state = ?, version = ?, observed_at = ?, verified = 1
                WHERE delegation_slug = ?
                """,
                (
                    DelegationState.REVOKED.value,
                    version,
                    version,
                    delegation_slug,
                ),
            )
            self._append_authority_event(
                control_kind="delegation",
                subject_slug=delegation_slug,
                version=version,
                state="absent",
                observed_at=observed_at,
            )

    def _append_authority_event(
        self,
        *,
        control_kind: str,
        subject_slug: str,
        version: str,
        state: str,
        observed_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO authority_control_events (
                event_id, control_kind, subject_slug, version, state, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"authority-event-{uuid4()}",
                control_kind,
                subject_slug,
                version,
                state,
                _timestamp(observed_at),
            ),
        )

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        with self._lock:
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    self._connection.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error).lower() or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
            try:
                yield
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def record(
        self,
        change: ActionableChange,
        classification: Classification,
        *,
        now: datetime,
        registration_id: str | None = None,
        execution_request: _ExecutionClaimRequest | None = None,
    ) -> HandoffRecord:
        _validate_change(change)
        now = _require_utc(now, "now")
        occurred_at = _require_utc(change.occurred_at, "change.occurred_at")
        idempotency_key = _reference(
            f"{change.task_slug}|{change.canonical_version}|{change.canonical_event_id}|{change.trigger}"
        )
        handoff_id = f"handoff-{idempotency_key}"
        correlation_id = change.correlation_id or f"corr-{idempotency_key[:24]}"
        terminal_rejection = classification.reason == "delegation_identity_mismatch"
        status = (
            "queued"
            if classification.actionable
            else "dead_letter" if terminal_rejection else "suppressed"
        )
        if classification.actionable and registration_id is None:
            raise ValueError("actionable handoffs require a private registration id")
        permanent_owner = (
            execution_request.permanent_owner
            if execution_request is not None
            else change.assigned_to[0] if len(change.assigned_to) == 1 else None
        )
        executor_agent = (
            execution_request.executor_agent
            if execution_request is not None
            else classification.agent_slug
        )
        delegation_slug = (
            execution_request.delegation.slug
            if execution_request is not None
            and execution_request.delegation is not None
            else None
        )
        with self._write_transaction():
            inserted = self._connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, idempotency_key, task_slug, canonical_event_id,
                    canonical_version, trigger, agent_slug, executor_agent,
                    permanent_owner, delegation_slug, registration_ref, status,
                    reason, summary, correlation_id, created_at, attempt, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    handoff_id,
                    idempotency_key,
                    change.task_slug,
                    change.canonical_event_id,
                    change.canonical_version,
                    change.trigger,
                    classification.agent_slug,
                    executor_agent,
                    permanent_owner,
                    delegation_slug,
                    classification.registration_ref,
                    status,
                    classification.reason,
                    change.summary.strip(),
                    correlation_id,
                    _timestamp(now),
                ),
            ).rowcount
            if inserted == 0:
                return self.get(handoff_id)
            if classification.actionable:
                self._connection.execute(
                    """
                    INSERT INTO leases (
                        handoff_id, registration_id, registration_agent_slug,
                        registration_route, lease_until,
                        lease_capability_ref, lease_generation
                    ) VALUES (?, ?, ?, ?, NULL, NULL, 0)
                    """,
                    (
                        handoff_id,
                        registration_id,
                        classification.agent_slug,
                        change.route,
                    ),
                )
            execution_claim: ExecutionClaim | None = None
            execution_created = False
            if classification.actionable and execution_request is not None:
                execution_claim, execution_created = (
                    self._claim_execution_in_transaction(
                        self.get(handoff_id),
                        request=execution_request,
                        now=now,
                    )
                )
                if execution_claim is None:
                    self._connection.execute(
                        """
                        UPDATE handoffs
                        SET status = 'suppressed', reason = 'execution_claim_unavailable'
                        WHERE handoff_id = ? AND status = 'queued'
                        """,
                        (handoff_id,),
                    )
                    self._connection.execute(
                        "DELETE FROM leases WHERE handoff_id = ?", (handoff_id,)
                    )
                    classification = Classification(
                        False,
                        "execution_claim_unavailable",
                        classification.agent_slug,
                        classification.registration_ref,
                    )
            record = self.get(handoff_id)
            self._append_event_from_record(
                record,
                event_type=(
                    "handoff_queued"
                    if classification.actionable
                    else "delivery_terminal"
                    if terminal_rejection
                    else "handoff_suppressed"
                ),
                summary=change.summary.strip(),
                detail=None,
                mutation_ref=None,
                occurred_at=occurred_at,
                recorded_at=now,
            )
            if execution_created and execution_claim is not None:
                self._append_event_from_record(
                    record,
                    event_type="execution_claimed",
                    summary="Task execution claim created for the verified route.",
                    detail=None,
                    mutation_ref=None,
                    occurred_at=now,
                    recorded_at=now,
                    execution_state="active",
                )
            return record

    def get(self, handoff_id: str) -> HandoffRecord:
        row = self._connection.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
        ).fetchone()
        if row is None:
            raise KeyError(handoff_id)
        return self._record_from_row(row)

    def get_execution_claim(
        self, task_slug: str, *, include_terminal: bool = False
    ) -> ExecutionClaim | None:
        _require_structured_id(task_slug, "task_slug")
        terminal_clause = "" if include_terminal else "AND terminal_state IS NULL"
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT * FROM execution_claims
                WHERE task_slug = ? {terminal_clause}
                ORDER BY claim_sequence DESC LIMIT 1
                """,
                (task_slug,),
            ).fetchone()
        return self._execution_claim_from_row(row) if row is not None else None

    def claim_execution(
        self,
        handoff_id: str,
        *,
        permanent_owner: str,
        executor_agent: str,
        delegation: AgentDelegationLease | None,
        task_status: str,
        requested_operation: str,
        owned_work_ready: bool,
        now: datetime,
        expires_at: datetime | None = None,
    ) -> ExecutionClaim | None:
        """Create one atomic task-scoped execution fence or replay it exactly."""
        now = _require_utc(now, "now")
        if expires_at is None:
            expires_at = (
                delegation.ends_at
                if delegation is not None
                else now + timedelta(seconds=DEFAULT_EXECUTION_CLAIM_SECONDS)
            )
        request = _ExecutionClaimRequest(
            permanent_owner=permanent_owner,
            executor_agent=executor_agent,
            delegation=delegation,
            task_status=task_status,
            requested_operation=requested_operation,
            owned_work_ready=owned_work_ready,
            expires_at=_require_utc(expires_at, "expires_at"),
        )
        with self._write_transaction():
            record = self.get(handoff_id)
            claim, created = self._claim_execution_in_transaction(
                record, request=request, now=now
            )
            if created and claim is not None:
                self._append_event_from_record(
                    record,
                    event_type="execution_claimed",
                    summary="Task execution claim created for the verified route.",
                    detail=None,
                    mutation_ref=None,
                    occurred_at=now,
                    recorded_at=now,
                    execution_state="active",
                )
            return claim

    def _claim_execution_in_transaction(
        self,
        record: HandoffRecord,
        *,
        request: _ExecutionClaimRequest,
        now: datetime,
    ) -> tuple[ExecutionClaim | None, bool]:
        _require_structured_id(request.permanent_owner, "permanent_owner")
        _require_structured_id(request.executor_agent, "executor_agent")
        _require_structured_id(request.task_status, "task_status")
        _require_structured_id(request.requested_operation, "requested_operation")
        if not isinstance(request.owned_work_ready, bool):
            raise ValueError("owned_work_ready must be a boolean")
        expires_at = _require_utc(request.expires_at, "expires_at")
        now = _require_utc(now, "now")
        delegation_slug = request.delegation.slug if request.delegation else None
        if (
            record.executor_agent != request.executor_agent
            or record.permanent_owner != request.permanent_owner
            or record.delegation_slug != delegation_slug
        ):
            raise ValueError("execution claim does not match the durable route")

        replay = self._connection.execute(
            "SELECT * FROM execution_claims WHERE idempotency_key = ?",
            (record.idempotency_key,),
        ).fetchone()
        if replay is not None:
            if (
                replay["task_slug"] != record.task_slug
                or replay["executor_agent"] != request.executor_agent
                or replay["permanent_owner"] != request.permanent_owner
                or replay["delegation_slug"] != delegation_slug
                or replay["correlation_id"] != record.correlation_id
                or replay["requested_operation"] != request.requested_operation
            ):
                raise ValueError("idempotency key belongs to another execution claim")
            if replay["terminal_state"] is not None:
                return None, False
            return self._execution_claim_from_row(replay), False

        if record.status not in {"queued", "retrying"}:
            return None, False

        if request.delegation is None:
            delegation_version = None
            priority_version = None
            if request.executor_agent != request.permanent_owner:
                raise ValueError("owned execution must preserve permanent ownership")
        else:
            authority = self._connection.execute(
                """
                SELECT * FROM delegation_authority WHERE delegation_slug = ?
                """,
                (delegation_slug,),
            ).fetchone()
            priority = self._connection.execute(
                """
                SELECT * FROM executor_priority WHERE executor_agent = ?
                """,
                (request.executor_agent,),
            ).fetchone()
            owned_claim = self._connection.execute(
                """
                SELECT 1 FROM execution_claims
                WHERE executor_agent = ? AND permanent_owner = ?
                    AND delegation_slug IS NULL AND terminal_state IS NULL
                LIMIT 1
                """,
                (request.executor_agent, request.executor_agent),
            ).fetchone()
            try:
                allowed_operations = (
                    json.loads(authority["allowed_operations"])
                    if authority is not None
                    else None
                )
            except (TypeError, json.JSONDecodeError):
                allowed_operations = None
            if (
                authority is None
                or authority["verified"] != 1
                or authority["source_agent"] != request.permanent_owner
                or authority["executor_agent"] != request.executor_agent
                or authority["state"]
                in {
                    DelegationState.COMPLETED.value,
                    DelegationState.EXPIRED.value,
                    DelegationState.REVOKED.value,
                }
                or now < _parse_timestamp(authority["starts_at"])
                or now >= _parse_timestamp(authority["ends_at"])
                or not isinstance(allowed_operations, list)
                or request.requested_operation not in allowed_operations
                or request.task_status != "planned"
                or priority is None
                or bool(priority["owned_work_ready"])
                or owned_claim is not None
            ):
                return None, False
            delegation_version = authority["version"]
            priority_version = priority["version"]
            expires_at = min(expires_at, _parse_timestamp(authority["ends_at"]))

        if expires_at <= now:
            return None, False
        active = self._connection.execute(
            """
            SELECT 1 FROM execution_claims
            WHERE task_slug = ? AND terminal_state IS NULL
            LIMIT 1
            """,
            (record.task_slug,),
        ).fetchone()
        if active is not None:
            return None, False
        try:
            self._connection.execute(
                """
                INSERT INTO execution_claims (
                    handoff_id, task_slug, executor_agent, permanent_owner,
                    delegation_slug, correlation_id, idempotency_key,
                    requested_operation, delegation_version, priority_version,
                    claimed_at, expires_at, terminal_state, terminal_at,
                    release_mutation_ref, release_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
                """,
                (
                    record.handoff_id,
                    record.task_slug,
                    request.executor_agent,
                    request.permanent_owner,
                    delegation_slug,
                    record.correlation_id,
                    record.idempotency_key,
                    request.requested_operation,
                    delegation_version,
                    priority_version,
                    _timestamp(now),
                    _timestamp(expires_at),
                ),
            )
        except sqlite3.IntegrityError:
            return None, False
        row = self._connection.execute(
            "SELECT * FROM execution_claims WHERE idempotency_key = ?",
            (record.idempotency_key,),
        ).fetchone()
        return self._execution_claim_from_row(row), True

    def release_execution_claim(
        self,
        task_slug: str,
        *,
        executor_agent: str,
        idempotency_key: str,
        terminal_state: str,
        mutation_id: str,
        now: datetime,
    ) -> HandoffEvent:
        """Fence and terminalize one claim, returning its immutable audit event."""
        _require_structured_id(task_slug, "task_slug")
        _require_structured_id(executor_agent, "executor_agent")
        _require_structured_id(mutation_id, "mutation_id")
        if re.fullmatch(r"[0-9a-f]{64}", idempotency_key) is None:
            raise ValueError("idempotency_key must be a SHA-256 digest")
        if terminal_state not in EXECUTION_TERMINAL_STATES:
            raise ValueError("execution terminal state is invalid")
        now = _require_utc(now, "now")
        mutation_ref = _reference(mutation_id)
        with self._write_transaction():
            row = self._connection.execute(
                """
                SELECT * FROM execution_claims
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if (
                row is None
                or row["task_slug"] != task_slug
                or row["executor_agent"] != executor_agent
            ):
                raise ValueError("release requires the active execution claim fence")
            if (
                row["terminal_state"] is None
                and row["delegation_slug"] is not None
                and now >= _parse_timestamp(row["expires_at"])
                and terminal_state not in {"checkpointed", "revoked", "expired"}
            ):
                raise ValueError("expired execution may only checkpoint or hand back")
            if row["terminal_state"] is None:
                handoff = self.get(row["handoff_id"])
                if handoff.status in {
                    "queued",
                    "retrying",
                    "leased",
                    "received",
                    "actively_executing",
                    "still_blocked",
                }:
                    self._connection.execute(
                        """
                        UPDATE handoffs
                        SET status = 'suppressed', reason = 'execution_claim_released',
                            detail = ?
                        WHERE handoff_id = ? AND status IN (
                            'queued', 'retrying', 'leased', 'received',
                            'actively_executing', 'still_blocked'
                        )
                        """,
                        (
                            "Delegated execution returned to its permanent owner."
                            if row["delegation_slug"] is not None
                            else "Owned execution claim ended before delivery completed.",
                            row["handoff_id"],
                        ),
                    )
                    self._connection.execute(
                        """
                        UPDATE leases
                        SET lease_until = NULL, lease_capability_ref = NULL
                        WHERE handoff_id = ?
                        """,
                        (row["handoff_id"],),
                    )
            return self._terminalize_execution_claim_in_transaction(
                row,
                terminal_state=terminal_state,
                mutation_ref=mutation_ref,
                now=now,
            )

    def _terminalize_execution_claim_in_transaction(
        self,
        row: sqlite3.Row,
        *,
        terminal_state: str,
        mutation_ref: str,
        now: datetime,
    ) -> HandoffEvent:
        reused = self._connection.execute(
            """
            SELECT claim_sequence FROM execution_claims
            WHERE release_mutation_ref = ?
            """,
            (mutation_ref,),
        ).fetchone()
        if reused is not None and reused["claim_sequence"] != row["claim_sequence"]:
            raise ValueError("mutation_id was already used for another execution claim")
        if row["terminal_state"] is not None:
            if (
                row["terminal_state"] == terminal_state
                and row["release_mutation_ref"] == mutation_ref
                and row["release_event_id"] is not None
            ):
                return self._event_from_row(
                    self._connection.execute(
                        "SELECT * FROM handoff_events WHERE event_id = ?",
                        (row["release_event_id"],),
                    ).fetchone()
                )
            raise ValueError("execution claim is already terminal")
        changed = self._connection.execute(
            """
            UPDATE execution_claims
            SET terminal_state = ?, terminal_at = ?, release_mutation_ref = ?
            WHERE claim_sequence = ? AND terminal_state IS NULL
                AND executor_agent = ? AND idempotency_key = ?
            """,
            (
                terminal_state,
                _timestamp(now),
                mutation_ref,
                row["claim_sequence"],
                row["executor_agent"],
                row["idempotency_key"],
            ),
        ).rowcount
        if changed != 1:
            raise ValueError("release requires the active execution claim fence")
        record = self.get(row["handoff_id"])
        event = self._append_event_from_record(
            record,
            event_type=(
                "delegated_execution_handed_back"
                if row["delegation_slug"] is not None
                else "execution_claim_released"
            ),
            summary=(
                "Delegated execution returned to its permanent owner."
                if row["delegation_slug"] is not None
                else "Owned execution claim released."
            ),
            detail=None,
            mutation_ref=mutation_ref,
            occurred_at=now,
            recorded_at=now,
            execution_state=terminal_state,
        )
        self._connection.execute(
            """
            UPDATE execution_claims SET release_event_id = ?
            WHERE claim_sequence = ? AND release_mutation_ref = ?
            """,
            (event.event_id, row["claim_sequence"], mutation_ref),
        )
        return event

    def claim(
        self,
        registration_id: str,
        *,
        now: datetime,
        lease_seconds: int,
        expected_agent_slug: str | None = None,
        expected_registration_ref: str | None = None,
        expected_route: str | None = None,
    ) -> LeaseClaim | None:
        now = _require_utc(now, "now")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        identity_values = (
            expected_agent_slug,
            expected_registration_ref,
            expected_route,
        )
        if any(value is not None for value in identity_values):
            if not all(value is not None for value in identity_values):
                raise ValueError("atomic claim identity requires agent, reference, and route")
            _require_structured_id(expected_agent_slug, "expected_agent_slug")
            _require_structured_id(expected_route, "expected_route")
            if (
                not isinstance(expected_registration_ref, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_registration_ref) is None
            ):
                raise ValueError("expected_registration_ref must be a SHA-256 digest")
        lease_until = now + timedelta(seconds=lease_seconds)
        identity_clause = ""
        identity_parameters: tuple[object, ...] = ()
        if expected_agent_slug is not None:
            identity_clause = (
                " AND h.agent_slug = ? AND h.registration_ref = ?"
                " AND l.registration_agent_slug = ? AND l.registration_route = ?"
            )
            identity_parameters = (
                expected_agent_slug,
                expected_registration_ref,
                expected_agent_slug,
                expected_route,
            )
        with self._write_transaction():
            self._backfill_legacy_execution_claims_in_transaction(now=now)
            row = self._connection.execute(
                f"""
                SELECT h.handoff_id
                FROM handoffs h
                JOIN leases l ON l.handoff_id = h.handoff_id
                JOIN execution_claims e ON e.handoff_id = h.handoff_id
                WHERE l.registration_id = ? AND h.status IN ('queued', 'retrying')
                    AND (l.lease_until IS NULL OR l.lease_until <= ?)
                    AND e.terminal_state IS NULL AND e.expires_at > ?
                    {identity_clause}
                ORDER BY h.created_at, h.handoff_id LIMIT 1
                """,
                (
                    registration_id,
                    _timestamp(now),
                    _timestamp(now),
                    *identity_parameters,
                ),
            ).fetchone()
            if row is None:
                return None
            lease_token = uuid4().hex
            lease_capability_ref = _reference(lease_token)
            changed = self._connection.execute(
                """
                UPDATE handoffs SET status = 'leased', detail = NULL, attempt = attempt + 1
                WHERE handoff_id = ? AND status IN ('queued', 'retrying')
                """,
                (row["handoff_id"],),
            ).rowcount
            if changed != 1:
                return None
            self._connection.execute(
                """
                UPDATE leases
                SET lease_until = ?, lease_capability_ref = ?,
                    lease_generation = lease_generation + 1
                WHERE handoff_id = ? AND registration_id = ?
                """,
                (
                    _timestamp(lease_until),
                    lease_capability_ref,
                    row["handoff_id"],
                    registration_id,
                ),
            )
            lease_row = self._connection.execute(
                "SELECT lease_generation FROM leases WHERE handoff_id = ?",
                (row["handoff_id"],),
            ).fetchone()
            record = self.get(row["handoff_id"])
            self._append_event_from_record(
                record,
                event_type="handoff_leased",
                summary="Handoff leased to its registered local dispatcher.",
                detail=None,
                mutation_ref=None,
                occurred_at=now,
                recorded_at=now,
            )
            return LeaseClaim(record, lease_token, lease_row["lease_generation"])

    def recover_in_progress(
        self,
        handoff_id: str,
        *,
        registration: AgentRegistration,
        expected_generation: int,
        now: datetime,
        lease_seconds: int = DEFAULT_RECOVERY_LEASE_SECONDS,
    ) -> LeaseClaim:
        """Authenticate the durable owner and rotate a lost runtime capability."""
        now = _require_utc(now, "now")
        if registration.verified is not True:
            raise ValueError("recovery requires a verified registration")
        _require_structured_id(registration.agent_slug, "registration.agent_slug")
        _require_structured_id(registration.route, "registration.route")
        if not isinstance(registration.registration_id, str) or not registration.registration_id:
            raise ValueError("recovery requires a verified registration")
        if not isinstance(expected_generation, int) or expected_generation < 1:
            raise ValueError("expected generation must be a positive integer")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds < 5
            or lease_seconds > 120
        ):
            raise ValueError("recovery lease must be 5 to 120 seconds")

        with self._write_transaction():
            current = self._connection.execute(
                """
                SELECT h.status, h.agent_slug, h.registration_ref,
                    l.registration_id, l.registration_agent_slug,
                    l.registration_route, l.lease_generation,
                    l.lease_capability_ref, e.*
                FROM handoffs h
                JOIN leases l ON l.handoff_id = h.handoff_id
                JOIN execution_claims e ON e.handoff_id = h.handoff_id
                WHERE h.handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
            if current is None:
                raise KeyError(handoff_id)
            if current["status"] not in {
                "leased",
                "received",
                "actively_executing",
                "still_blocked",
            }:
                raise ValueError("recovery requires an in-progress handoff")
            if (
                current["registration_id"] != registration.lease_identity
                or current["registration_agent_slug"] != registration.agent_slug
                or current["registration_route"] != registration.route
                or current["agent_slug"] != registration.agent_slug
                or current["registration_ref"] != registration.reference
            ):
                raise HandoffOwnershipError(
                    "verified registration is not the current owner"
                )
            if current["lease_generation"] != expected_generation:
                raise ValueError("expected generation is not current")
            if current["lease_capability_ref"] is None:
                raise ValueError("current owner capability is unavailable")

            authority_failure = self._execution_authority_failure_in_transaction(
                current, now=now
            )
            if authority_failure is not None:
                terminal_state, reason, detail = authority_failure
                self._suppress_execution_authority_in_transaction(
                    current,
                    terminal_state=terminal_state,
                    reason=reason,
                    detail=detail,
                    now=now,
                )
            else:
                previous_capability_ref = current["lease_capability_ref"]
                lease_token = uuid4().hex
                lease_capability_ref = _reference(lease_token)
                lease_until = (
                    _timestamp(now + timedelta(seconds=lease_seconds))
                    if current["status"] == "leased"
                    else None
                )
                changed = self._connection.execute(
                    """
                    UPDATE leases
                    SET lease_capability_ref = ?, lease_generation = lease_generation + 1,
                        lease_until = ?
                    WHERE handoff_id = ? AND registration_id = ?
                        AND registration_agent_slug = ? AND registration_route = ?
                        AND lease_generation = ? AND lease_capability_ref = ?
                    """,
                    (
                        lease_capability_ref,
                        lease_until,
                        handoff_id,
                        registration.lease_identity,
                        registration.agent_slug,
                        registration.route,
                        expected_generation,
                        previous_capability_ref,
                    ),
                ).rowcount
                if changed != 1:
                    raise ValueError("recovery owner or generation changed")
                lease_generation = expected_generation + 1
                record = self.get(handoff_id)
                self._append_event_from_record(
                    record,
                    event_type="capability_rotated",
                    summary="Dispatcher capability rotated after authenticated recovery.",
                    detail=None,
                    mutation_ref=None,
                    occurred_at=now,
                    recorded_at=now,
                    lease_generation=lease_generation,
                )
                return LeaseClaim(record, lease_token, lease_generation)

        raise ValueError("execution authority ended; checkpoint and hand back")

    def _execution_authority_failure_in_transaction(
        self,
        execution_row: sqlite3.Row,
        *,
        now: datetime,
    ) -> tuple[str, str, str] | None:
        """Return the terminal reconciliation required by current control state."""
        terminal_state = execution_row["terminal_state"]
        if terminal_state is not None:
            return (
                terminal_state,
                "execution_claim_terminal",
                "Execution authority already ended; checkpoint and hand back.",
            )
        if now >= _parse_timestamp(execution_row["expires_at"]):
            return (
                "expired",
                "execution_claim_expired",
                "Expired execution must checkpoint and hand back.",
            )
        delegation_slug = execution_row["delegation_slug"]
        if delegation_slug is None:
            return None

        authority = self._connection.execute(
            "SELECT * FROM delegation_authority WHERE delegation_slug = ?",
            (delegation_slug,),
        ).fetchone()
        priority = self._connection.execute(
            "SELECT * FROM executor_priority WHERE executor_agent = ?",
            (execution_row["executor_agent"],),
        ).fetchone()
        try:
            allowed_operations = (
                json.loads(authority["allowed_operations"])
                if authority is not None
                else None
            )
        except (TypeError, json.JSONDecodeError):
            allowed_operations = None
        if (
            authority is None
            or authority["verified"] != 1
            or authority["version"] != execution_row["delegation_version"]
            or authority["source_agent"] != execution_row["permanent_owner"]
            or authority["executor_agent"] != execution_row["executor_agent"]
            or authority["state"]
            in {
                DelegationState.COMPLETED.value,
                DelegationState.EXPIRED.value,
                DelegationState.REVOKED.value,
            }
            or now < _parse_timestamp(authority["starts_at"])
            or now >= _parse_timestamp(authority["ends_at"])
            or not isinstance(allowed_operations, list)
            or execution_row["requested_operation"] not in allowed_operations
        ):
            return (
                "revoked",
                "delegation_authority_changed",
                "Delegated execution authority changed; checkpoint and hand back.",
            )
        if (
            priority is None
            or priority["version"] != execution_row["priority_version"]
            or bool(priority["owned_work_ready"])
        ):
            return (
                "checkpointed",
                "owned_work_priority_changed",
                "Owned work now has priority; delegated execution must checkpoint and hand back.",
            )
        return None

    def authorize_wake(
        self,
        handoff_id: str,
        *,
        registration_id: str,
        lease_token: str,
        lease_generation: int,
        wake_token: str,
        now: datetime,
    ) -> HandoffRecord:
        """Persist one stable wake intent after the final execution-authority check."""
        _require_structured_id(handoff_id, "handoff_id")
        _require_structured_id(wake_token, "wake_token")
        if not isinstance(lease_generation, int) or lease_generation < 1:
            raise ValueError("lease_generation must be a positive integer")
        now = _require_utc(now, "now")
        lease_capability_ref = _reference(lease_token)
        wake_token_ref = _reference(wake_token)
        with self._write_transaction():
            current = self._connection.execute(
                """
                SELECT h.status, l.registration_id, l.lease_generation,
                    l.lease_capability_ref, e.*
                FROM handoffs h
                JOIN leases l ON l.handoff_id = h.handoff_id
                JOIN execution_claims e ON e.handoff_id = h.handoff_id
                WHERE h.handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
            if current is None:
                raise KeyError(handoff_id)
            if (
                current["status"] != "leased"
                or current["registration_id"] != registration_id
                or current["lease_generation"] != lease_generation
                or current["lease_capability_ref"] is None
                or not hmac.compare_digest(
                    current["lease_capability_ref"], lease_capability_ref
                )
            ):
                raise ValueError("wake requires the active leased owner and capability")

            authority_failure = self._execution_authority_failure_in_transaction(
                current, now=now
            )
            if authority_failure is not None:
                terminal_state, reason, detail = authority_failure
                self._suppress_execution_authority_in_transaction(
                    current,
                    terminal_state=terminal_state,
                    reason=reason,
                    detail=detail,
                    now=now,
                )
                return self.get(handoff_id)

            existing = self._connection.execute(
                "SELECT * FROM wake_intents WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if existing is not None:
                if (
                    not hmac.compare_digest(existing["wake_token_ref"], wake_token_ref)
                    or existing["execution_idempotency_key"]
                    != current["idempotency_key"]
                ):
                    raise ValueError("wake intent token does not match its durable replay")
                self._connection.execute(
                    """
                    UPDATE wake_intents SET lease_generation = ?, authorized_at = ?
                    WHERE handoff_id = ? AND wake_token_ref = ?
                    """,
                    (
                        lease_generation,
                        _timestamp(now),
                        handoff_id,
                        wake_token_ref,
                    ),
                )
                return self.get(handoff_id)

            self._connection.execute(
                """
                INSERT INTO wake_intents (
                    handoff_id, wake_token_ref, execution_idempotency_key,
                    lease_generation, authorized_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    wake_token_ref,
                    current["idempotency_key"],
                    lease_generation,
                    _timestamp(now),
                ),
            )
            record = self.get(handoff_id)
            self._append_event_from_record(
                record,
                event_type="wake_authorized",
                summary="Durable wake intent authorized after execution revalidation.",
                detail=None,
                mutation_ref=wake_token_ref,
                occurred_at=now,
                recorded_at=now,
                execution_state="active",
            )
            return record

    def _suppress_execution_authority_in_transaction(
        self,
        execution_row: sqlite3.Row,
        *,
        terminal_state: str,
        reason: str,
        detail: str,
        now: datetime,
    ) -> None:
        self._connection.execute(
            """
            UPDATE handoffs SET status = 'suppressed', reason = ?, detail = ?
            WHERE handoff_id = ? AND status IN (
                'queued', 'retrying', 'leased', 'received',
                'actively_executing', 'still_blocked'
            )
            """,
            (reason, detail, execution_row["handoff_id"]),
        )
        self._connection.execute(
            """
            UPDATE leases SET lease_until = NULL, lease_capability_ref = NULL
            WHERE handoff_id = ?
            """,
            (execution_row["handoff_id"],),
        )
        if execution_row["terminal_state"] is None:
            self._terminalize_execution_claim_in_transaction(
                execution_row,
                terminal_state=terminal_state,
                mutation_ref=_reference(
                    f"authority|{terminal_state}|{execution_row['idempotency_key']}"
                ),
                now=now,
            )

    def read_recovery_state(
        self,
        handoff_id: str,
        *,
        registration: AgentRegistration,
        now: datetime | None = None,
    ) -> HandoffRecoveryState:
        """Return safe authoritative state only to the exact durable owner."""
        if registration.verified is not True:
            raise ValueError("recovery state requires a verified registration")
        _require_structured_id(registration.agent_slug, "registration.agent_slug")
        _require_structured_id(registration.route, "registration.route")
        if not isinstance(registration.registration_id, str) or not registration.registration_id:
            raise ValueError("recovery state requires a verified registration")
        if now is not None:
            now = _require_utc(now, "now")
        with self._write_transaction():
            current = self._connection.execute(
                """
                SELECT h.status, h.agent_slug, h.registration_ref,
                    l.registration_id, l.registration_agent_slug,
                    l.registration_route, l.lease_generation, e.*
                FROM handoffs h
                JOIN leases l ON l.handoff_id = h.handoff_id
                JOIN execution_claims e ON e.handoff_id = h.handoff_id
                WHERE h.handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
            if current is None:
                raise KeyError(handoff_id)
            if (
                current["registration_id"] != registration.lease_identity
                or current["registration_agent_slug"] != registration.agent_slug
                or current["registration_route"] != registration.route
                or current["agent_slug"] != registration.agent_slug
                or current["registration_ref"] != registration.reference
            ):
                raise HandoffOwnershipError(
                    "verified registration is not the current owner"
                )
            if now is not None:
                authority_failure = self._execution_authority_failure_in_transaction(
                    current, now=now
                )
                if authority_failure is not None and current["status"] in {
                    "queued",
                    "retrying",
                    "leased",
                    "received",
                    "actively_executing",
                    "still_blocked",
                }:
                    terminal_state, reason, detail = authority_failure
                    self._suppress_execution_authority_in_transaction(
                        current,
                        terminal_state=terminal_state,
                        reason=reason,
                        detail=detail,
                        now=now,
                    )
                    current = self._connection.execute(
                        """
                        SELECT h.status, h.agent_slug, h.registration_ref,
                            l.registration_id, l.registration_agent_slug,
                            l.registration_route, l.lease_generation, e.*
                        FROM handoffs h
                        JOIN leases l ON l.handoff_id = h.handoff_id
                        JOIN execution_claims e ON e.handoff_id = h.handoff_id
                        WHERE h.handoff_id = ?
                        """,
                        (handoff_id,),
                    ).fetchone()
            return HandoffRecoveryState(
                handoff_id=handoff_id,
                status=current["status"],
                lease_generation=current["lease_generation"],
                agent_slug=current["agent_slug"],
                registration_ref=current["registration_ref"],
            )

    def acknowledge(
        self,
        handoff_id: str,
        status: str,
        *,
        registration_id: str,
        lease_token: str,
        lease_generation: int,
        mutation_id: str,
        now: datetime,
        detail: str | None = None,
    ) -> HandoffRecord:
        """Advance a fenced acknowledgement lifecycle.

        A lease may acknowledge any current state directly. Normal progression is
        received -> actively_executing -> still_blocked or completed; blocked work
        may resume actively_executing or complete. Completed work never regresses.
        """
        now = _require_utc(now, "now")
        if status not in ACKNOWLEDGEMENT_STATES:
            raise ValueError("acknowledgement status is invalid")
        if status == "still_blocked":
            if not isinstance(detail, str) or not detail.strip():
                raise ValueError("still_blocked acknowledgement requires detail")
            detail = _require_safe_text(detail, "acknowledgement detail")
        elif detail is not None:
            detail = _require_safe_text(detail, "acknowledgement detail")
        return self._finish_lease(
            handoff_id,
            registration_id=registration_id,
            lease_token=lease_token,
            lease_generation=lease_generation,
            mutation_id=mutation_id,
            operation="acknowledge",
            fingerprint=_reference(f"acknowledge|{status}|{detail or ''}"),
            status=status,
            event_type="acknowledgement",
            summary=f"Agent acknowledged handoff as {status}.",
            detail=detail,
            now=now,
            acknowledgement=True,
        )

    def record_failure(
        self,
        handoff_id: str,
        *,
        registration_id: str,
        lease_token: str,
        lease_generation: int,
        mutation_id: str,
        retryable: bool,
        summary: str,
        now: datetime,
    ) -> HandoffRecord:
        now = _require_utc(now, "now")
        summary = _require_safe_text(summary, "failure summary")
        status = "retrying" if retryable else "dead_letter"
        event_type = "delivery_retry" if retryable else "delivery_terminal"
        return self._finish_lease(
            handoff_id,
            registration_id=registration_id,
            lease_token=lease_token,
            lease_generation=lease_generation,
            mutation_id=mutation_id,
            operation="record_failure",
            fingerprint=_reference(f"record_failure|{retryable}|{summary}"),
            status=status,
            event_type=event_type,
            summary=summary,
            detail=summary,
            now=now,
            acknowledgement=False,
        )

    def _finish_lease(
        self,
        handoff_id: str,
        *,
        registration_id: str,
        lease_token: str,
        lease_generation: int,
        mutation_id: str,
        operation: str,
        fingerprint: str,
        status: str,
        event_type: str,
        summary: str,
        detail: str | None,
        now: datetime,
        acknowledgement: bool,
    ) -> HandoffRecord:
        _require_structured_id(mutation_id, "mutation_id")
        if not isinstance(lease_generation, int) or lease_generation < 1:
            raise ValueError("lease_generation must be a positive integer")
        mutation_ref = _reference(mutation_id)
        registration_ref = _registration_reference(registration_id)
        lease_capability_ref = _reference(lease_token)
        with self._write_transaction():
            receipt = self._connection.execute(
                "SELECT * FROM mutation_receipts WHERE mutation_ref = ?", (mutation_ref,)
            ).fetchone()
            if receipt is not None:
                if (
                    receipt["handoff_id"] != handoff_id
                    or receipt["operation"] != operation
                    or receipt["fingerprint"] != fingerprint
                ):
                    raise ValueError("mutation_id was already used for another mutation")
                if (
                    not hmac.compare_digest(receipt["registration_ref"], registration_ref)
                    or receipt["lease_generation"] != lease_generation
                    or not hmac.compare_digest(
                        receipt["lease_capability_ref"], lease_capability_ref
                    )
                ):
                    raise ValueError("mutation receipt does not match the original lease")
                return self.get(handoff_id)
            active = self._connection.execute(
                """
                SELECT h.status, l.registration_id, l.lease_capability_ref,
                    l.lease_generation,
                    e.delegation_slug AS execution_delegation_slug,
                    e.expires_at AS execution_expires_at,
                    e.terminal_state AS execution_terminal_state
                FROM handoffs h
                JOIN leases l ON l.handoff_id = h.handoff_id
                LEFT JOIN execution_claims e ON e.handoff_id = h.handoff_id
                WHERE h.handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
            if (
                active is None
                or active["registration_id"] != registration_id
                or active["lease_generation"] != lease_generation
                or active["lease_capability_ref"] is None
                or not hmac.compare_digest(
                    active["lease_capability_ref"], lease_capability_ref
                )
            ):
                raise ValueError("mutation requires the active lease owner and token")
            if (
                active["execution_delegation_slug"] is not None
                and (
                    active["execution_terminal_state"] is not None
                    or now >= _parse_timestamp(active["execution_expires_at"])
                )
            ):
                raise ValueError(
                    "expired delegated execution must checkpoint and hand back"
                )
            current_status = active["status"]
            if acknowledgement:
                if status not in ACKNOWLEDGEMENT_TRANSITIONS.get(
                    current_status, frozenset()
                ):
                    raise ValueError(
                        f"invalid acknowledgement transition: {current_status} -> {status}"
                    )
            elif current_status not in {
                "leased",
                "received",
                "actively_executing",
                "still_blocked",
            }:
                raise ValueError(
                    "delivery failure requires an active recoverable handoff"
                )
            changed = self._connection.execute(
                """
                UPDATE handoffs SET status = ?, detail = ?
                WHERE handoff_id = ? AND status = ?
                    AND EXISTS (
                        SELECT 1 FROM leases
                        WHERE handoff_id = ? AND registration_id = ?
                            AND lease_generation = ? AND lease_capability_ref = ?
                    )
                """,
                (
                    status,
                    detail,
                    handoff_id,
                    current_status,
                    handoff_id,
                    registration_id,
                    lease_generation,
                    lease_capability_ref,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("mutation requires the active lease owner and token")
            if not acknowledgement or status == "completed":
                self._connection.execute(
                    """
                    UPDATE leases SET lease_until = NULL, lease_capability_ref = NULL
                    WHERE handoff_id = ? AND registration_id = ?
                        AND lease_generation = ? AND lease_capability_ref = ?
                    """,
                    (
                        handoff_id,
                        registration_id,
                        lease_generation,
                        lease_capability_ref,
                    ),
                )
            event = self._append_event_from_record(
                self.get(handoff_id),
                event_type=event_type,
                summary=summary,
                detail=detail if event_type == "acknowledgement" else None,
                mutation_ref=mutation_ref,
                occurred_at=now,
                recorded_at=now,
            )
            self._connection.execute(
                """
                INSERT INTO mutation_receipts (
                    mutation_ref, handoff_id, operation, fingerprint,
                    registration_ref, lease_generation, lease_capability_ref,
                    resulting_status, event_id, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mutation_ref,
                    handoff_id,
                    operation,
                    fingerprint,
                    registration_ref,
                    lease_generation,
                    lease_capability_ref,
                    status,
                    event.event_id,
                    _timestamp(now),
                ),
            )
            execution_terminal = (
                "completed"
                if acknowledgement and status == "completed"
                else "dead_letter"
                if not acknowledgement and status == "dead_letter"
                else None
            )
            if execution_terminal is not None:
                execution_row = self._connection.execute(
                    """
                    SELECT * FROM execution_claims
                    WHERE handoff_id = ? AND terminal_state IS NULL
                    """,
                    (handoff_id,),
                ).fetchone()
                if execution_row is not None:
                    self._terminalize_execution_claim_in_transaction(
                        execution_row,
                        terminal_state=execution_terminal,
                        mutation_ref=_reference(f"execution|{mutation_id}"),
                        now=now,
                    )
            return self.get(handoff_id)

    def reconcile_expired_leases(self, *, now: datetime) -> int:
        now = _require_utc(now, "now")
        with self._write_transaction():
            rows = self._connection.execute(
                """
                SELECT h.handoff_id FROM handoffs h JOIN leases l ON l.handoff_id = h.handoff_id
                WHERE h.status = 'leased' AND l.lease_until IS NOT NULL AND l.lease_until <= ?
                """,
                (_timestamp(now),),
            ).fetchall()
            for row in rows:
                handoff_id = row["handoff_id"]
                changed = self._connection.execute(
                    """
                    UPDATE handoffs SET status = 'retrying', detail = ?
                    WHERE handoff_id = ? AND status = 'leased'
                    """,
                    ("Local dispatcher lease expired.", handoff_id),
                ).rowcount
                if changed != 1:
                    continue
                self._connection.execute(
                    """
                    UPDATE leases
                    SET lease_until = NULL, lease_capability_ref = NULL
                    WHERE handoff_id = ?
                    """,
                    (handoff_id,),
                )
                self._append_event_from_record(
                    self.get(handoff_id),
                    event_type="lease_expired",
                    summary="Local dispatcher lease expired; handoff returned for retry.",
                    detail=None,
                    mutation_ref=None,
                    occurred_at=now,
                    recorded_at=now,
                )
            return len(rows)

    def append_correction(
        self,
        handoff_id: str,
        *,
        supersedes_event_id: str,
        summary: str,
        now: datetime,
    ) -> HandoffEvent:
        now = _require_utc(now, "now")
        summary = _require_safe_text(summary, "correction summary")
        with self._write_transaction():
            record = self.get(handoff_id)
            original = self._connection.execute(
                "SELECT 1 FROM handoff_events WHERE event_id = ? AND handoff_id = ?",
                (supersedes_event_id, handoff_id),
            ).fetchone()
            if original is None:
                raise ValueError("supersedes_event_id must belong to the handoff")
            return self._append_event_from_record(
                record,
                event_type="correction",
                summary=summary,
                detail=None,
                mutation_ref=None,
                occurred_at=now,
                recorded_at=now,
                supersedes_event_id=supersedes_event_id,
            )

    def query_events(
        self,
        *,
        limit: int,
        after_sequence: int | None,
        task_slug: str | None = None,
        agent_slug: str | None = None,
        status: str | None = None,
        event_type: str | None = None,
        correlation_id: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
    ) -> EventPage:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if after_sequence is not None and after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if occurred_after is not None:
            occurred_after = _require_utc(occurred_after, "occurred_after")
        if occurred_before is not None:
            occurred_before = _require_utc(occurred_before, "occurred_before")
        if (
            occurred_after is not None
            and occurred_before is not None
            and occurred_after > occurred_before
        ):
            raise ValueError("occurred_after must not exceed occurred_before")
        clauses = ["sequence > ?"]
        parameters: list[object] = [after_sequence or 0]
        for column, value in (
            ("task_slug", task_slug),
            ("agent_slug", agent_slug),
            ("status", status),
            ("event_type", event_type),
            ("correlation_id", correlation_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if occurred_after is not None:
            clauses.append("occurred_at >= ?")
            parameters.append(_timestamp(occurred_after))
        if occurred_before is not None:
            clauses.append("occurred_at <= ?")
            parameters.append(_timestamp(occurred_before))
        where = " AND ".join(clauses)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM handoff_events WHERE {where} ORDER BY sequence LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
            total = self._connection.execute(
                f"SELECT COUNT(*) FROM handoff_events WHERE {where}", parameters
            ).fetchone()[0]
        has_more = len(rows) > limit
        events = tuple(self._event_from_row(row) for row in rows[:limit])
        next_sequence = events[-1].sequence if has_more and events else None
        return EventPage(events=events, total=total, next_sequence=next_sequence)

    def export_events(self, **query: object) -> dict[str, object]:
        page = self.query_events(**query)  # type: ignore[arg-type]
        return {
            "metadata": {
                "format": "handoff-audit-v1",
                "retention_days": self.retention_days,
                "exported_at": datetime.now(timezone.utc).isoformat(),
            },
            **page.to_dict(),
        }

    def _append_event_from_record(
        self,
        record: HandoffRecord,
        *,
        event_type: str,
        summary: str,
        detail: str | None,
        mutation_ref: str | None,
        occurred_at: datetime,
        recorded_at: datetime,
        supersedes_event_id: str | None = None,
        lease_generation: int | None = None,
        execution_state: str | None = None,
    ) -> HandoffEvent:
        if lease_generation is None:
            lease_row = self._connection.execute(
                "SELECT lease_generation FROM leases WHERE handoff_id = ?",
                (record.handoff_id,),
            ).fetchone()
            lease_generation = lease_row["lease_generation"] if lease_row else 0
        return self._append_event(
            handoff_id=record.handoff_id,
            task_slug=record.task_slug,
            canonical_event_id=record.canonical_event_id,
            canonical_version=record.canonical_version,
            idempotency_key=record.idempotency_key,
            classification_reason=record.reason,
            trigger=record.trigger,
            attempt=record.attempt,
            lease_generation=lease_generation,
            mutation_ref=mutation_ref,
            agent_slug=record.agent_slug,
            executor_agent=record.executor_agent,
            permanent_owner=record.permanent_owner,
            delegation_slug=record.delegation_slug,
            execution_state=execution_state,
            registration_ref=record.registration_ref,
            status=record.status,
            event_type=event_type,
            summary=summary,
            detail=detail,
            correlation_id=record.correlation_id,
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            supersedes_event_id=supersedes_event_id,
        )

    def _append_event(self, **values: object) -> HandoffEvent:
        event_id = f"handoff-event-{uuid4()}"
        self._connection.execute(
            """
            INSERT INTO handoff_events (
                event_id, handoff_id, task_slug, canonical_event_id, canonical_version,
                idempotency_key, classification_reason, trigger, attempt,
                lease_generation, mutation_ref,
                agent_slug, executor_agent, permanent_owner, delegation_slug,
                execution_state, registration_ref, status, event_type, summary, detail,
                correlation_id, occurred_at, recorded_at, supersedes_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                values["handoff_id"],
                values["task_slug"],
                values["canonical_event_id"],
                values["canonical_version"],
                values["idempotency_key"],
                values["classification_reason"],
                values["trigger"],
                values["attempt"],
                values["lease_generation"],
                values["mutation_ref"],
                values["agent_slug"],
                values["executor_agent"],
                values["permanent_owner"],
                values["delegation_slug"],
                values["execution_state"],
                values["registration_ref"],
                values["status"],
                values["event_type"],
                values["summary"],
                values["detail"],
                values["correlation_id"],
                _timestamp(values["occurred_at"]),  # type: ignore[arg-type]
                _timestamp(values["recorded_at"]),  # type: ignore[arg-type]
                values["supersedes_event_id"],
            ),
        )
        return self._event_from_row(
            self._connection.execute(
                "SELECT * FROM handoff_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=row["handoff_id"],
            task_slug=row["task_slug"],
            canonical_event_id=row["canonical_event_id"],
            canonical_version=row["canonical_version"],
            idempotency_key=row["idempotency_key"],
            trigger=row["trigger"],
            agent_slug=row["agent_slug"],
            executor_agent=row["executor_agent"],
            permanent_owner=row["permanent_owner"],
            delegation_slug=row["delegation_slug"],
            registration_ref=row["registration_ref"],
            status=row["status"],
            reason=row["reason"],
            summary=row["summary"],
            correlation_id=row["correlation_id"],
            created_at=_parse_timestamp(row["created_at"]),
            attempt=row["attempt"],
            detail=row["detail"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> HandoffEvent:
        return HandoffEvent(
            event_id=row["event_id"],
            sequence=row["sequence"],
            handoff_id=row["handoff_id"],
            task_slug=row["task_slug"],
            canonical_event_id=row["canonical_event_id"],
            canonical_version=row["canonical_version"],
            idempotency_key=row["idempotency_key"],
            classification_reason=row["classification_reason"],
            trigger=row["trigger"],
            attempt=row["attempt"],
            lease_generation=row["lease_generation"],
            mutation_ref=row["mutation_ref"],
            agent_slug=row["agent_slug"],
            executor_agent=row["executor_agent"],
            permanent_owner=row["permanent_owner"],
            delegation_slug=row["delegation_slug"],
            execution_state=row["execution_state"],
            registration_ref=row["registration_ref"],
            status=row["status"],
            event_type=row["event_type"],
            summary=row["summary"],
            detail=row["detail"],
            correlation_id=row["correlation_id"],
            occurred_at=_parse_timestamp(row["occurred_at"]),
            recorded_at=_parse_timestamp(row["recorded_at"]),
            supersedes_event_id=row["supersedes_event_id"],
        )

    @staticmethod
    def _execution_claim_from_row(row: sqlite3.Row) -> ExecutionClaim:
        return ExecutionClaim(
            task_slug=row["task_slug"],
            executor_agent=row["executor_agent"],
            permanent_owner=row["permanent_owner"],
            delegation_slug=row["delegation_slug"],
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            claimed_at=_parse_timestamp(row["claimed_at"]),
            expires_at=_parse_timestamp(row["expires_at"]),
        )


class HandoffDispatcher:
    def __init__(
        self,
        store: DurableHandoffStore,
        *,
        registrations: Iterable[AgentRegistration],
        classifier: HandoffClassifier | None = None,
        delegations: (
            Iterable[AgentDelegationLease]
            | Callable[[], Iterable[AgentDelegationLease]]
            | None
        ) = None,
        owned_work_ready: Callable[[str], bool] | None = None,
        owned_work_snapshot: Callable[[str], tuple[bool, str]] | None = None,
        execution_claim_seconds: int = DEFAULT_EXECUTION_CLAIM_SECONDS,
    ) -> None:
        if (
            isinstance(execution_claim_seconds, bool)
            or not isinstance(execution_claim_seconds, int)
            or execution_claim_seconds < 1
        ):
            raise ValueError("execution_claim_seconds must be positive")
        self.store = store
        self.registrations = tuple(registrations)
        self.classifier = classifier or HandoffClassifier()
        self._lease_aware = delegations is not None
        self._delegations = delegations
        self._owned_work_ready = owned_work_ready or (lambda _executor: False)
        self._owned_work_snapshot = owned_work_snapshot
        self.execution_claim_seconds = execution_claim_seconds

    def _read_delegations(self) -> tuple[AgentDelegationLease, ...]:
        source = self._delegations
        values = source() if callable(source) else source
        leases = tuple(values or ())
        if any(not isinstance(lease, AgentDelegationLease) for lease in leases):
            raise ValueError("delegation reader returned an unverified lease")
        return leases

    def record(self, change: ActionableChange, *, now: datetime) -> HandoffRecord:
        now = _require_utc(now, "now")
        executor_agent = change.assigned_to[0] if len(change.assigned_to) == 1 else None
        selected_lease: AgentDelegationLease | None = None
        owned_work_ready = False
        identity_mismatch = False
        if (
            self._lease_aware
            and change.trigger in ACTIONABLE_TRIGGERS
            and executor_agent is not None
        ):
            leases = self._read_delegations()
            for lease in leases:
                self.store.observe_delegation_authority(lease, observed_at=now)
            active = tuple(
                lease
                for lease in leases
                if lease.source_agent == executor_agent
                and lease_state_at(lease, now) == DelegationState.ACTIVE
            )
            if len(active) > 1:
                identity_mismatch = True
                selected_lease = active[0]
                executor_agent = selected_lease.executor_agent
            elif len(active) == 1:
                candidate = active[0]
                if self._owned_work_snapshot is not None:
                    owned_work_ready, priority_version = self._owned_work_snapshot(
                        candidate.executor_agent
                    )
                else:
                    owned_work_ready = self._owned_work_ready(candidate.executor_agent)
                    priority_version = (
                        "priority-owned-ready"
                        if owned_work_ready
                        else "priority-delegation-eligible"
                    )
                if not isinstance(owned_work_ready, bool):
                    raise ValueError("owned work readiness must be a boolean")
                self.store.observe_executor_priority(
                    candidate.executor_agent,
                    owned_work_ready=owned_work_ready,
                    version=priority_version,
                    observed_at=now,
                )
                if (
                    change.requested_operation in candidate.allowed_operations
                    and delegated_work_is_eligible(
                        owned_work_ready=owned_work_ready,
                        task_status=change.task_status,
                        task_owner=change.assigned_to[0],
                        lease=candidate,
                        now=now,
                    )
                ):
                    selected_lease = candidate
                    executor_agent = candidate.executor_agent

        classification = self.classifier.classify(
            change,
            self.registrations,
            executor_agent=executor_agent,
        )
        if selected_lease is not None and (
            identity_mismatch
            or not classification.actionable
            and classification.reason
            in {"missing_registration", "multiple_registrations", "route_mismatch"}
        ):
            classification = Classification(
                False,
                "delegation_identity_mismatch",
                executor_agent,
                classification.registration_ref,
            )
        registration_id = None
        if classification.actionable:
            registration_id = next(
                registration.lease_identity
                for registration in self.registrations
                if registration.reference == classification.registration_ref
            )
        execution_request = None
        if self._lease_aware and len(change.assigned_to) == 1:
            execution_request = _ExecutionClaimRequest(
                permanent_owner=change.assigned_to[0],
                executor_agent=executor_agent or change.assigned_to[0],
                delegation=selected_lease,
                task_status=change.task_status,
                requested_operation=change.requested_operation,
                owned_work_ready=owned_work_ready,
                expires_at=(
                    selected_lease.ends_at
                    if selected_lease is not None
                    else now + timedelta(seconds=self.execution_claim_seconds)
                ),
            )
        return self.store.record(
            change,
            classification,
            now=now,
            registration_id=registration_id,
            execution_request=execution_request,
        )


HandoffStore = DurableHandoffStore


class LocalAgentDispatcher:
    def __init__(
        self,
        store: DurableHandoffStore,
        *,
        registration_id: str,
        verify_route: Callable[[HandoffRecord], bool],
        wake: Callable[[HandoffRecord, str], bool],
        lease_seconds: int = 30,
    ) -> None:
        self.store = store
        self.registration_id = registration_id
        self.verify_route = verify_route
        self.wake = wake
        self.lease_seconds = lease_seconds

    def run_once(self, *, now: datetime) -> LeaseClaim | None:
        claim = self.store.claim(
            self.registration_id, now=now, lease_seconds=self.lease_seconds
        )
        if claim is None:
            return None
        record = claim.record
        mutation_id = f"mutation-local-{uuid4().hex}"
        if not self.verify_route(record):
            self.store.record_failure(
                record.handoff_id,
                registration_id=self.registration_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                mutation_id=mutation_id,
                retryable=False,
                summary="Registered route verification failed.",
                now=now,
            )
            return LeaseClaim(
                self.store.get(record.handoff_id),
                claim.lease_token,
                claim.lease_generation,
            )
        wake_token = f"wake/{record.idempotency_key}"
        authorized = self.store.authorize_wake(
            record.handoff_id,
            registration_id=self.registration_id,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            wake_token=wake_token,
            now=now,
        )
        if authorized.status != "leased":
            return LeaseClaim(
                authorized,
                claim.lease_token,
                claim.lease_generation,
            )
        if not self.wake(record, wake_token):
            self.store.record_failure(
                record.handoff_id,
                registration_id=self.registration_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                mutation_id=mutation_id,
                retryable=True,
                summary="Local dispatcher wake attempt failed.",
                now=now,
            )
            return LeaseClaim(
                self.store.get(record.handoff_id),
                claim.lease_token,
                claim.lease_generation,
            )
        self.store.acknowledge(
            record.handoff_id,
            "received",
            registration_id=self.registration_id,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id=mutation_id,
            now=now,
        )
        return LeaseClaim(
            self.store.get(record.handoff_id),
            claim.lease_token,
            claim.lease_generation,
        )


class HandoffGuardian:
    def __init__(self, store: DurableHandoffStore) -> None:
        self.store = store

    def reconcile(self, *, now: datetime) -> int:
        return self.store.reconcile_expired_leases(now=now)
