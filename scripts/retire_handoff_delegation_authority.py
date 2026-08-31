#!/usr/bin/env python3
"""Retire legacy OpenClaw delegation authority while preserving its audit rows."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import stat


RETIRED_EXECUTORS = (
    "agents/tammy-oc",
    "agents/timmy-oc",
    "agents/toddy-oc",
)


def retire(store: Path, *, now: datetime | None = None) -> dict[str, object]:
    metadata = store.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Handoff store must be a regular non-symbolic file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("Handoff store must use mode 0600")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed_at.utcoffset() is None:
        raise ValueError("Retirement time must be timezone-aware")
    connection = sqlite3.connect(store)
    try:
        connection.execute("BEGIN IMMEDIATE")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("delegation_authority",),
        ).fetchone()
        if table is None:
            raise ValueError("Handoff store has no delegation_authority history")
        placeholders = ",".join("?" for _ in RETIRED_EXECUTORS)
        active = connection.execute(
            f"SELECT delegation_slug FROM delegation_authority "
            f"WHERE state = 'active' AND executor_agent IN ({placeholders}) "
            "ORDER BY delegation_slug",
            RETIRED_EXECUTORS,
        ).fetchall()
        connection.execute(
            f"UPDATE delegation_authority SET state = 'revoked', observed_at = ? "
            f"WHERE state = 'active' AND executor_agent IN ({placeholders})",
            (observed_at.isoformat(), *RETIRED_EXECUTORS),
        )
        remaining = connection.execute(
            f"SELECT COUNT(*) FROM delegation_authority "
            f"WHERE state = 'active' AND executor_agent IN ({placeholders})",
            RETIRED_EXECUTORS,
        ).fetchone()[0]
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "ok": remaining == 0,
        "status": "retired" if active else "already_retired",
        "retired_count": len(active),
        "remaining_active_retired": remaining,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retire legacy OpenClaw delegation authority without deleting history."
    )
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(retire(args.store.expanduser().resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
