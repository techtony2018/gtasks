"""Durable, privacy-safe handoff classification and audit primitives."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import sqlite3
import stat
import threading
import time
from typing import Callable, Iterable, Iterator
from uuid import uuid4


ACTIONABLE_TRIGGERS = frozenset(
    {
        "answer_received",
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

    @property
    def reference(self) -> str:
        return _registration_reference(self.registration_id)


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
    ) -> Classification:
        if change.trigger in SUPPRESSED_TRIGGERS:
            return Classification(False, SUPPRESSED_TRIGGERS[change.trigger], None, None)
        if change.trigger not in ACTIONABLE_TRIGGERS:
            return Classification(False, "non_actionable", None, None)
        if not change.assigned_to:
            return Classification(False, "missing_assignment", None, None)
        if len(change.assigned_to) != 1:
            return Classification(False, "multiple_assignments", None, None)

        agent_slug = change.assigned_to[0]
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
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    task_slug TEXT NOT NULL,
                    canonical_event_id TEXT NOT NULL,
                    canonical_version TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    agent_slug TEXT,
                    registration_ref TEXT,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    correlation_id TEXT,
                    created_at TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    detail TEXT
                );
                CREATE TABLE IF NOT EXISTS leases (
                    handoff_id TEXT PRIMARY KEY REFERENCES handoffs(handoff_id),
                    registration_id TEXT NOT NULL,
                    registration_agent_slug TEXT NOT NULL,
                    registration_route TEXT NOT NULL,
                    lease_until TEXT,
                    lease_capability_ref TEXT,
                    lease_generation INTEGER NOT NULL DEFAULT 0
                );
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
                );
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
                    registration_ref TEXT,
                    status TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail TEXT,
                    correlation_id TEXT,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    supersedes_event_id TEXT
                );
                CREATE INDEX IF NOT EXISTS handoff_events_filters
                    ON handoff_events(task_slug, agent_slug, status, event_type, correlation_id, sequence);
                """
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
    ) -> HandoffRecord:
        _validate_change(change)
        now = _require_utc(now, "now")
        occurred_at = _require_utc(change.occurred_at, "change.occurred_at")
        idempotency_key = _reference(
            f"{change.task_slug}|{change.canonical_version}|{change.canonical_event_id}|{change.trigger}"
        )
        handoff_id = f"handoff-{idempotency_key}"
        status = "queued" if classification.actionable else "suppressed"
        if classification.actionable and registration_id is None:
            raise ValueError("actionable handoffs require a private registration id")
        with self._write_transaction():
            inserted = self._connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, idempotency_key, task_slug, canonical_event_id,
                    canonical_version, trigger, agent_slug, registration_ref, status,
                    reason, summary, correlation_id, created_at, attempt, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
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
                    classification.registration_ref,
                    status,
                    classification.reason,
                    change.summary.strip(),
                    change.correlation_id,
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
            self._append_event_from_record(
                self.get(handoff_id),
                event_type="handoff_queued" if classification.actionable else "handoff_suppressed",
                summary=change.summary.strip(),
                detail=None,
                mutation_ref=None,
                occurred_at=occurred_at,
                recorded_at=now,
            )
            return self.get(handoff_id)

    def get(self, handoff_id: str) -> HandoffRecord:
        row = self._connection.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
        ).fetchone()
        if row is None:
            raise KeyError(handoff_id)
        return self._record_from_row(row)

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
            row = self._connection.execute(
                f"""
                SELECT h.handoff_id FROM handoffs h JOIN leases l ON l.handoff_id = h.handoff_id
                WHERE l.registration_id = ? AND h.status IN ('queued', 'retrying')
                    AND (l.lease_until IS NULL OR l.lease_until <= ?)
                    {identity_clause}
                ORDER BY h.created_at, h.handoff_id LIMIT 1
                """,
                (registration_id, _timestamp(now), *identity_parameters),
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

        with self._write_transaction():
            current = self._connection.execute(
                """
                SELECT h.status, h.agent_slug, h.registration_ref,
                    l.registration_id, l.registration_agent_slug,
                    l.registration_route, l.lease_generation,
                    l.lease_capability_ref
                FROM handoffs h JOIN leases l ON l.handoff_id = h.handoff_id
                WHERE h.handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
            if current is None:
                raise KeyError(handoff_id)
            if current["status"] not in {
                "received",
                "actively_executing",
                "still_blocked",
            }:
                raise ValueError("recovery requires an in-progress handoff")
            if (
                current["registration_id"] != registration.registration_id
                or current["registration_agent_slug"] != registration.agent_slug
                or current["registration_route"] != registration.route
                or current["agent_slug"] != registration.agent_slug
                or current["registration_ref"] != registration.reference
            ):
                raise ValueError("verified registration is not the current owner")
            if current["lease_generation"] != expected_generation:
                raise ValueError("expected generation is not current")
            if current["lease_capability_ref"] is None:
                raise ValueError("current owner capability is unavailable")

            previous_capability_ref = current["lease_capability_ref"]
            lease_token = uuid4().hex
            lease_capability_ref = _reference(lease_token)
            changed = self._connection.execute(
                """
                UPDATE leases
                SET lease_capability_ref = ?, lease_generation = lease_generation + 1,
                    lease_until = NULL
                WHERE handoff_id = ? AND registration_id = ?
                    AND registration_agent_slug = ? AND registration_route = ?
                    AND lease_generation = ? AND lease_capability_ref = ?
                """,
                (
                    lease_capability_ref,
                    handoff_id,
                    registration.registration_id,
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
                    l.lease_generation
                FROM handoffs h JOIN leases l ON l.handoff_id = h.handoff_id
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
    ) -> EventPage:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if after_sequence is not None and after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
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
                agent_slug, registration_ref, status, event_type, summary, detail,
                correlation_id, occurred_at, recorded_at, supersedes_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


class HandoffDispatcher:
    def __init__(
        self,
        store: DurableHandoffStore,
        *,
        registrations: Iterable[AgentRegistration],
        classifier: HandoffClassifier | None = None,
    ) -> None:
        self.store = store
        self.registrations = tuple(registrations)
        self.classifier = classifier or HandoffClassifier()

    def record(self, change: ActionableChange, *, now: datetime) -> HandoffRecord:
        classification = self.classifier.classify(change, self.registrations)
        registration_id = None
        if classification.actionable:
            registration_id = next(
                registration.registration_id
                for registration in self.registrations
                if registration.reference == classification.registration_ref
            )
        return self.store.record(
            change,
            classification,
            now=now,
            registration_id=registration_id,
        )


class LocalAgentDispatcher:
    def __init__(
        self,
        store: DurableHandoffStore,
        *,
        registration_id: str,
        verify_route: Callable[[HandoffRecord], bool],
        wake: Callable[[HandoffRecord], bool],
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
        if not self.wake(record):
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
