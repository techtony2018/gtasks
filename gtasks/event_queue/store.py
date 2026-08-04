from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class ClaimConflict(RuntimeError):
    """A logical operation or event identifier was reused with changed content."""


class ClaimDisposition(StrEnum):
    PROCESS = "process"
    DUPLICATE = "duplicate"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class ClaimResult:
    disposition: ClaimDisposition


@dataclass(frozen=True, slots=True)
class TerminalRecord:
    event_id: str
    event_type: str
    schema_version: int
    source_client_id: str
    error_code: str
    attempts: int
    original_stream: str
    original_stream_sequence: int
    failed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["failed_at"] = self.failed_at.astimezone(timezone.utc).isoformat()
        return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class EventStore:
    """Durable operational receipts; never a task or application data store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                event_id TEXT PRIMARY KEY,
                source_client_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('processing','accepted','terminal')),
                lease_expires_at TEXT,
                handler_version TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_client_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS terminals (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                source_client_id TEXT NOT NULL,
                error_code TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                original_stream TEXT NOT NULL,
                original_stream_sequence INTEGER NOT NULL,
                failed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                source_client_id TEXT NOT NULL,
                disposition TEXT NOT NULL,
                error_code TEXT,
                task_slug TEXT NOT NULL,
                scope_day TEXT,
                timezone TEXT,
                prior_progress INTEGER,
                resulting_progress INTEGER,
                verified_count INTEGER,
                baseline_count INTEGER,
                target_value INTEGER,
                recorded_at TEXT NOT NULL
            );
            """
        )
        os.chmod(self.path, 0o600)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _con(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("event store is closed")
        return self._connection

    def claim(
        self,
        *,
        event_id: str,
        source_client_id: str,
        idempotency_key: str,
        fingerprint: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimResult:
        current = _utc(now)
        lease_expires = current + timedelta(seconds=lease_seconds)
        with self._lock:
            con = self._con()
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute(
                    """
                    SELECT * FROM receipts
                    WHERE event_id = ?
                       OR (source_client_id = ? AND idempotency_key = ?)
                    """,
                    (event_id, source_client_id, idempotency_key),
                ).fetchone()
                if row is None:
                    con.execute(
                        """
                        INSERT INTO receipts(
                            event_id, source_client_id, idempotency_key, fingerprint,
                            status, lease_expires_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'processing', ?, ?, ?)
                        """,
                        (
                            event_id,
                            source_client_id,
                            idempotency_key,
                            fingerprint,
                            lease_expires.isoformat(),
                            current.isoformat(),
                            current.isoformat(),
                        ),
                    )
                    con.commit()
                    return ClaimResult(ClaimDisposition.PROCESS)

                if (
                    row["event_id"] != event_id
                    or row["source_client_id"] != source_client_id
                    or row["idempotency_key"] != idempotency_key
                    or row["fingerprint"] != fingerprint
                ):
                    raise ClaimConflict(
                        "event or idempotency key conflicts with prior content"
                    )
                if row["status"] == "accepted":
                    con.commit()
                    return ClaimResult(ClaimDisposition.DUPLICATE)
                if row["status"] == "terminal":
                    con.commit()
                    return ClaimResult(ClaimDisposition.BUSY)
                lease = datetime.fromisoformat(row["lease_expires_at"])
                if lease > current:
                    con.commit()
                    return ClaimResult(ClaimDisposition.BUSY)
                con.execute(
                    """
                    UPDATE receipts
                    SET lease_expires_at = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (lease_expires.isoformat(), current.isoformat(), event_id),
                )
                con.commit()
                return ClaimResult(ClaimDisposition.PROCESS)
            except Exception:
                if con.in_transaction:
                    con.rollback()
                raise

    def release(self, event_id: str, *, now: datetime) -> None:
        current = _utc(now)
        with self._lock:
            self._con().execute(
                """
                UPDATE receipts
                SET lease_expires_at = ?, updated_at = ?
                WHERE event_id = ? AND status = 'processing'
                """,
                (current.isoformat(), current.isoformat(), event_id),
            )

    def accept(
        self,
        event_id: str,
        *,
        handler_version: str,
        now: datetime,
    ) -> None:
        current = _utc(now)
        with self._lock:
            cursor = self._con().execute(
                """
                UPDATE receipts
                SET status = 'accepted', lease_expires_at = NULL,
                    handler_version = ?, updated_at = ?
                WHERE event_id = ? AND status IN ('processing','accepted')
                """,
                (handler_version, current.isoformat(), event_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("cannot accept an unclaimed event")

    def record_terminal(self, record: TerminalRecord) -> None:
        values = record.to_dict()
        with self._lock:
            con = self._con()
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute(
                    """
                    INSERT INTO terminals(
                        event_id, event_type, schema_version, source_client_id,
                        error_code, attempts, original_stream,
                        original_stream_sequence, failed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        error_code = excluded.error_code,
                        attempts = excluded.attempts,
                        failed_at = excluded.failed_at
                    """,
                    (
                        values["event_id"],
                        values["event_type"],
                        values["schema_version"],
                        values["source_client_id"],
                        values["error_code"],
                        values["attempts"],
                        values["original_stream"],
                        values["original_stream_sequence"],
                        values["failed_at"],
                    ),
                )
                con.execute(
                    """
                    UPDATE receipts
                    SET status = 'terminal', lease_expires_at = NULL, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (values["failed_at"], values["event_id"]),
                )
                con.commit()
            except Exception:
                if con.in_transaction:
                    con.rollback()
                raise

    def reopen_terminal(
        self,
        event_id: str,
        *,
        now: datetime,
        lease_seconds: int = 60,
    ) -> None:
        """Reopen only the same terminal receipt for a verified operator redrive."""
        current = _utc(now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        lease_expires = current + timedelta(seconds=lease_seconds)
        with self._lock:
            con = self._con()
            con.execute("BEGIN IMMEDIATE")
            try:
                receipt = con.execute(
                    "SELECT status FROM receipts WHERE event_id = ?", (event_id,)
                ).fetchone()
                terminal = con.execute(
                    "SELECT event_id FROM terminals WHERE event_id = ?", (event_id,)
                ).fetchone()
                if (
                    receipt is None
                    or receipt["status"] != "terminal"
                    or terminal is None
                ):
                    raise ValueError("event is not a terminal receipt")
                con.execute("DELETE FROM terminals WHERE event_id = ?", (event_id,))
                con.execute(
                    """
                    UPDATE receipts
                    SET status = 'processing', lease_expires_at = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (lease_expires.isoformat(), current.isoformat(), event_id),
                )
                con.commit()
            except Exception:
                if con.in_transaction:
                    con.rollback()
                raise

    def list_terminals(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con().execute(
                "SELECT * FROM terminals ORDER BY failed_at, event_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_activity(
        self,
        *,
        event_id: str,
        fingerprint: str,
        source_client_id: str,
        disposition: str,
        task_slug: str,
        recorded_at: datetime,
        error_code: str | None = None,
        scope_day: str | None = None,
        timezone_name: str | None = None,
        prior_progress: int | None = None,
        resulting_progress: int | None = None,
        verified_count: int | None = None,
        baseline_count: int | None = None,
        target_value: int | None = None,
    ) -> None:
        current = _utc(recorded_at)
        with self._lock:
            self._con().execute(
                """
                INSERT INTO activity_receipts(
                    event_id, fingerprint, source_client_id, disposition,
                    error_code, task_slug, scope_day, timezone,
                    prior_progress, resulting_progress, verified_count,
                    baseline_count, target_value, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, fingerprint, source_client_id, disposition,
                    error_code, task_slug, scope_day, timezone_name,
                    prior_progress, resulting_progress, verified_count,
                    baseline_count, target_value, current.isoformat(),
                ),
            )

    def list_activity(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("activity limit must be between 1 and 1000")
        with self._lock:
            rows = self._con().execute(
                "SELECT * FROM activity_receipts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_terminal(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._con().execute(
                "SELECT * FROM terminals WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def counts(self) -> dict[str, int]:
        with self._lock:
            receipt_rows = self._con().execute(
                "SELECT status, count(*) AS total FROM receipts GROUP BY status"
            ).fetchall()
            terminal_count = self._con().execute(
                "SELECT count(*) FROM terminals"
            ).fetchone()[0]
        counts = {"processing": 0, "accepted": 0, "terminal": 0}
        counts.update({row["status"]: row["total"] for row in receipt_rows})
        counts["dead_letter"] = terminal_count
        return counts
