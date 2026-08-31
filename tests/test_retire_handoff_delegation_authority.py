from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "retire_handoff_delegation_authority.py"


def load_migrator():
    spec = importlib.util.spec_from_file_location("retire_delegation_authority", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("delegation authority migrator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RetireDelegationAuthorityTests(unittest.TestCase):
    def test_retires_only_active_openclaw_rows_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary) / "handoffs.sqlite3"
            connection = sqlite3.connect(store)
            connection.execute(
                """
                CREATE TABLE delegation_authority (
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
                """
            )
            rows = (
                ("agent-delegations/one", "agents/tammy", "agents/tammy-oc", "active"),
                ("agent-delegations/two", "agents/tammy", "agents/tammy-oc", "revoked"),
                ("agent-delegations/three", "agents/timmy", "agents/timmy", "active"),
            )
            for slug, source, executor, state in rows:
                connection.execute(
                    "INSERT INTO delegation_authority VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (slug, source, executor, state, "start", "end", "[]", "v1", "old", 1),
                )
            connection.commit()
            connection.close()
            store.chmod(0o600)

            result = load_migrator().retire(
                store, now=datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc)
            )

            self.assertEqual(result["retired_count"], 1)
            self.assertEqual(result["remaining_active_retired"], 0)
            connection = sqlite3.connect(store)
            actual = connection.execute(
                "SELECT delegation_slug, state, observed_at FROM delegation_authority "
                "ORDER BY delegation_slug"
            ).fetchall()
            connection.close()
            self.assertEqual(
                actual,
                [
                    ("agent-delegations/one", "revoked", "2026-08-30T23:00:00+00:00"),
                    ("agent-delegations/three", "active", "old"),
                    ("agent-delegations/two", "revoked", "old"),
                ],
            )

            replay = load_migrator().retire(store)
            self.assertEqual(replay["status"], "already_retired")


if __name__ == "__main__":
    unittest.main()
