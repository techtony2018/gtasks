"""Private request identities, not a task database or a source of task state."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Any


class TaskOperationConflict(ValueError):
    """An operation identity was reused for a different request."""


@dataclass(frozen=True, slots=True)
class TaskOperation:
    identity: str
    created_at: datetime
    is_new: bool
    verified: bool = False

    @property
    def slug(self) -> str:
        return f"tasks/{uuid.UUID(hex=self.identity)}"


def default_task_operation_path() -> Path:
    configured = os.environ.get("GTASKS_TASK_OPERATION_FILE")
    return Path(configured).expanduser() if configured else (
        Path.home() / "Library" / "Application Support" / "GTasks" / "task-operations.sqlite3"
    )


class TaskOperationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create owner-only before SQLite opens it, not after the first commit.
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        path.chmod(0o600)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS task_operations (
                    operation_key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0
                )
            """)
        path.chmod(0o600)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def reserve(
        self, key: str, payload: Mapping[str, Any], now: datetime,
    ) -> TaskOperation:
        if (not isinstance(key, str) or not key.strip() or len(key) > 200
                or "\n" in key or "\r" in key):
            raise ValueError("idempotency_key must be one line of 1 to 200 characters")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("operation time must include a timezone")
        digest = hashlib.sha256(json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        # Serialize reservation across threads/processes and commit the identity
        # before a caller attempts a remote write. Persist no user-authored text.
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_hash, identity, created_at, verified FROM task_operations WHERE operation_key=?",
                (key,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise TaskOperationConflict("This request ID already belongs to different task values.")
                return TaskOperation(row[1], datetime.fromisoformat(row[2]), False, bool(row[3]))
            identity = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO task_operations (operation_key, request_hash, identity, created_at) VALUES (?, ?, ?, ?)",
                (key, digest, identity, now.isoformat()),
            )
            return TaskOperation(identity, now, True)

    def mark_verified(self, operation: TaskOperation) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE task_operations SET verified=1 WHERE identity=?",
                (operation.identity,),
            )
