"""Durable, privacy-safe handoff classification and audit primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import threading
from typing import Callable, Iterable
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


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _require_utc(value, "timestamp").isoformat()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _registration_reference(registration_id: str) -> str:
    return hashlib.sha256(registration_id.encode("utf-8")).hexdigest()


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
    agent_slug: str | None
    registration_ref: str | None
    status: str
    reason: str
    summary: str
    correlation_id: str | None
    created_at: datetime
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "handoff_id": self.handoff_id,
            "task_slug": self.task_slug,
            "agent_slug": self.agent_slug,
            "registration_ref": self.registration_ref,
            "status": self.status,
            "reason": self.reason,
            "summary": self.summary,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HandoffEvent:
    event_id: str
    sequence: int
    handoff_id: str
    task_slug: str
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
        matching_route = [
            registration for registration in eligible if registration.route == change.route
        ]
        if not matching_route:
            reason = "route_mismatch" if eligible else "missing_registration"
            return Classification(False, reason, agent_slug, None)
        if len(matching_route) != 1:
            return Classification(False, "multiple_registrations", agent_slug, None)
        matched = matching_route[0]
        return Classification(
            True,
            change.trigger,
            agent_slug,
            matched.reference,
        )


class DurableHandoffStore:
    """SQLite-backed idempotent outbox with an append-only audit event table."""

    def __init__(self, path: str, *, retention_days: int = 90) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self.path = path
        self.retention_days = retention_days
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    task_slug TEXT NOT NULL,
                    agent_slug TEXT,
                    registration_ref TEXT,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    correlation_id TEXT,
                    created_at TEXT NOT NULL,
                    detail TEXT
                );
                CREATE TABLE IF NOT EXISTS leases (
                    handoff_id TEXT PRIMARY KEY REFERENCES handoffs(handoff_id),
                    registration_id TEXT NOT NULL,
                    lease_until TEXT
                );
                CREATE TABLE IF NOT EXISTS handoff_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    handoff_id TEXT NOT NULL REFERENCES handoffs(handoff_id),
                    task_slug TEXT NOT NULL,
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

    def record(
        self,
        change: ActionableChange,
        classification: Classification,
        *,
        now: datetime,
        registration_id: str | None = None,
    ) -> HandoffRecord:
        now = _require_utc(now, "now")
        occurred_at = _require_utc(change.occurred_at, "change.occurred_at")
        idempotency_key = hashlib.sha256(
            f"{change.task_slug}|{change.canonical_version}|{change.canonical_event_id}|{change.trigger}".encode("utf-8")
        ).hexdigest()
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM handoffs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return self._record_from_row(existing)
            handoff_id = f"handoff-{idempotency_key}"
            status = "queued" if classification.actionable else "suppressed"
            self._connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, idempotency_key, task_slug, agent_slug, registration_ref,
                    status, reason, summary, correlation_id, created_at, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    handoff_id,
                    idempotency_key,
                    change.task_slug,
                    classification.agent_slug,
                    classification.registration_ref,
                    status,
                    classification.reason,
                    change.summary,
                    change.correlation_id,
                    _timestamp(now),
                ),
            )
            if classification.actionable:
                if registration_id is None:
                    raise ValueError("actionable handoffs require a private registration id")
                self._connection.execute(
                    "INSERT INTO leases (handoff_id, registration_id, lease_until) VALUES (?, ?, NULL)",
                    (handoff_id, registration_id),
                )
            self._append_event(
                handoff_id=handoff_id,
                task_slug=change.task_slug,
                agent_slug=classification.agent_slug,
                registration_ref=classification.registration_ref,
                status=status,
                event_type="handoff_queued" if classification.actionable else "handoff_suppressed",
                summary=change.summary,
                detail=None,
                correlation_id=change.correlation_id,
                occurred_at=occurred_at,
                recorded_at=now,
                supersedes_event_id=None,
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
        self, registration_id: str, *, now: datetime, lease_seconds: int
    ) -> HandoffRecord | None:
        now = _require_utc(now, "now")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT h.* FROM handoffs h JOIN leases l ON l.handoff_id = h.handoff_id
                WHERE l.registration_id = ? AND h.status IN ('queued', 'retrying')
                    AND (l.lease_until IS NULL OR l.lease_until <= ?)
                ORDER BY h.created_at, h.handoff_id LIMIT 1
                """,
                (registration_id, _timestamp(now)),
            ).fetchone()
            if row is None:
                return None
            changed = self._connection.execute(
                """
                UPDATE handoffs SET status = 'leased', detail = NULL
                WHERE handoff_id = ? AND status IN ('queued', 'retrying')
                """,
                (row["handoff_id"],),
            ).rowcount
            if changed != 1:
                return None
            self._connection.execute(
                "UPDATE leases SET lease_until = ? WHERE handoff_id = ?",
                (_timestamp(lease_until), row["handoff_id"]),
            )
            self._append_event_from_record(
                self.get(row["handoff_id"]),
                event_type="handoff_leased",
                summary="Handoff leased to its registered local dispatcher.",
                detail=None,
                occurred_at=now,
                recorded_at=now,
            )
            return self.get(row["handoff_id"])

    def acknowledge(
        self,
        handoff_id: str,
        status: str,
        *,
        now: datetime,
        detail: str | None = None,
    ) -> HandoffRecord:
        now = _require_utc(now, "now")
        if status not in ACKNOWLEDGEMENT_STATES:
            raise ValueError("acknowledgement status is invalid")
        if status == "still_blocked":
            if not isinstance(detail, str) or not detail.strip():
                raise ValueError("still_blocked acknowledgement requires detail")
            if not self._privacy_safe(detail):
                raise ValueError("acknowledgement detail must be privacy-safe")
            detail = detail.strip()
        elif detail is not None and not self._privacy_safe(detail):
            raise ValueError("acknowledgement detail must be privacy-safe")
        with self._lock, self._connection:
            record = self.get(handoff_id)
            self._connection.execute(
                "UPDATE handoffs SET status = ?, detail = ? WHERE handoff_id = ?",
                (status, detail, handoff_id),
            )
            self._connection.execute(
                "UPDATE leases SET lease_until = NULL WHERE handoff_id = ?", (handoff_id,)
            )
            self._append_event_from_record(
                self.get(handoff_id),
                event_type="acknowledgement",
                summary=f"Agent acknowledged handoff as {status}.",
                detail=detail,
                occurred_at=now,
                recorded_at=now,
            )
            return self.get(record.handoff_id)

    def record_failure(
        self, handoff_id: str, *, retryable: bool, summary: str, now: datetime
    ) -> HandoffRecord:
        now = _require_utc(now, "now")
        if not self._privacy_safe(summary):
            raise ValueError("failure summary must be privacy-safe")
        status = "retrying" if retryable else "dead_letter"
        event_type = "delivery_retry" if retryable else "delivery_terminal"
        with self._lock, self._connection:
            self.get(handoff_id)
            self._connection.execute(
                "UPDATE handoffs SET status = ?, detail = ? WHERE handoff_id = ?",
                (status, summary.strip(), handoff_id),
            )
            self._connection.execute(
                "UPDATE leases SET lease_until = NULL WHERE handoff_id = ?", (handoff_id,)
            )
            self._append_event_from_record(
                self.get(handoff_id),
                event_type=event_type,
                summary=summary.strip(),
                detail=None,
                occurred_at=now,
                recorded_at=now,
            )
            return self.get(handoff_id)

    def reconcile_expired_leases(self, *, now: datetime) -> int:
        now = _require_utc(now, "now")
        with self._lock, self._connection:
            rows = self._connection.execute(
                """
                SELECT h.handoff_id FROM handoffs h JOIN leases l ON l.handoff_id = h.handoff_id
                WHERE h.status = 'leased' AND l.lease_until IS NOT NULL AND l.lease_until <= ?
                """,
                (_timestamp(now),),
            ).fetchall()
            for row in rows:
                handoff_id = row["handoff_id"]
                self._connection.execute(
                    "UPDATE handoffs SET status = 'retrying', detail = ? WHERE handoff_id = ?",
                    ("Local dispatcher lease expired.", handoff_id),
                )
                self._connection.execute(
                    "UPDATE leases SET lease_until = NULL WHERE handoff_id = ?", (handoff_id,)
                )
                self._append_event_from_record(
                    self.get(handoff_id),
                    event_type="lease_expired",
                    summary="Local dispatcher lease expired; handoff returned for retry.",
                    detail=None,
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
        if not self._privacy_safe(summary):
            raise ValueError("correction summary must be privacy-safe")
        with self._lock, self._connection:
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
                summary=summary.strip(),
                detail=None,
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
        occurred_at: datetime,
        recorded_at: datetime,
        supersedes_event_id: str | None = None,
    ) -> HandoffEvent:
        return self._append_event(
            handoff_id=record.handoff_id,
            task_slug=record.task_slug,
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
                event_id, handoff_id, task_slug, agent_slug, registration_ref, status,
                event_type, summary, detail, correlation_id, occurred_at, recorded_at,
                supersedes_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                values["handoff_id"],
                values["task_slug"],
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
    def _privacy_safe(value: str) -> bool:
        stripped = value.strip()
        forbidden = ("token", "thread", "secret", "bearer", "\n", "\r")
        return bool(stripped) and len(stripped) <= 240 and not any(
            term in stripped.lower() for term in forbidden
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=row["handoff_id"],
            task_slug=row["task_slug"],
            agent_slug=row["agent_slug"],
            registration_ref=row["registration_ref"],
            status=row["status"],
            reason=row["reason"],
            summary=row["summary"],
            correlation_id=row["correlation_id"],
            created_at=_parse_timestamp(row["created_at"]),
            detail=row["detail"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> HandoffEvent:
        return HandoffEvent(
            event_id=row["event_id"],
            sequence=row["sequence"],
            handoff_id=row["handoff_id"],
            task_slug=row["task_slug"],
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

    def run_once(self, *, now: datetime) -> HandoffRecord | None:
        record = self.store.claim(
            self.registration_id, now=now, lease_seconds=self.lease_seconds
        )
        if record is None:
            return None
        if not self.verify_route(record):
            self.store.record_failure(
                record.handoff_id,
                retryable=False,
                summary="Registered route verification failed.",
                now=now,
            )
            return record
        if not self.wake(record):
            self.store.record_failure(
                record.handoff_id,
                retryable=True,
                summary="Local dispatcher wake attempt failed.",
                now=now,
            )
            return record
        self.store.acknowledge(record.handoff_id, "received", now=now)
        return record


class HandoffGuardian:
    def __init__(self, store: DurableHandoffStore) -> None:
        self.store = store

    def reconcile(self, *, now: datetime) -> int:
        return self.store.reconcile_expired_leases(now=now)
