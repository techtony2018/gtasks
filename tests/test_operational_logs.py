import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from gtasks.operational_logs import (
    OperationalEvent,
    OperationalLogReader,
    OperationalLogStore,
    redact_message,
)


class OperationalEventTests(unittest.TestCase):
    def test_redacts_credentials_tokens_addresses_and_opaque_values(self) -> None:
        message = (
            "password=hunter2 token: abcdefghijklmnopqrstuvwxyz123456 "
            "Bearer eyJhbGciOiJIUzI1NiJ9.private.signature "
            "nats://reader:private@127.0.0.1 user@example.com"
        )

        redacted = redact_message(message)

        for sensitive in (
            "hunter2",
            "abcdefghijklmnopqrstuvwxyz123456",
            "eyJhbGciOiJIUzI1NiJ9",
            "reader:private",
            "user@example.com",
        ):
            self.assertNotIn(sensitive, redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_queue_event_accepts_only_the_fixed_safe_message_contract(self) -> None:
        event = OperationalEvent.from_mapping(
            {
                "timestamp": "2026-07-30T16:00:00Z",
                "component": "broker",
                "severity": "warning",
                "message": "Queue broker is unavailable; retry is scheduled.",
            },
            queue_source=True,
        )

        self.assertEqual(
            event.message,
            "Queue broker is unavailable; retry is scheduled.",
        )

    def test_unknown_queue_message_is_not_accepted(self) -> None:
        with self.assertRaisesRegex(ValueError, "not approved"):
            OperationalEvent.from_mapping(
                {
                    "timestamp": "2026-07-30T16:00:00Z",
                    "component": "handler",
                    "severity": "info",
                    "message": "Applied to Sensitive Company.",
                },
                queue_source=True,
            )


class OperationalLogStoreTests(unittest.TestCase):
    def test_retention_is_bounded_and_newest_first_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            store = OperationalLogStore(path, retention=2)
            for minute in range(3):
                store.append(
                    component="gtasks",
                    severity="info",
                    message=f"Safe event {minute}",
                    now=datetime.fromisoformat(
                        f"2026-07-30T09:0{minute}:00-07:00"
                    ),
                )

            restarted = OperationalLogStore(path, retention=2)
            events = restarted.read()

            self.assertEqual(
                [event.message for event in events],
                ["Safe event 2", "Safe event 1"],
            )
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_filtering_and_pagination_do_not_mutate_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = OperationalLogStore(root / "gtasks.jsonl")
            for minute, severity in enumerate(("info", "warning", "error")):
                store.append(
                    component="gtasks",
                    severity=severity,
                    message=f"Safe {severity} event",
                    now=datetime.fromisoformat(
                        f"2026-07-30T09:0{minute}:00-07:00"
                    ),
                )
            before = store.path.read_bytes()
            reader = OperationalLogReader(
                gtasks_store=store,
                queue_path=root / "missing.jsonl",
                queue_health=lambda: {"status": "connected"},
            )

            first = reader.page(
                severity=None,
                component="gtasks",
                cursor=0,
                limit=2,
            )
            second = reader.page(
                severity=None,
                component="gtasks",
                cursor=first["next_cursor"],
                limit=2,
            )

            self.assertEqual([event["severity"] for event in first["events"]], ["error", "warning"])
            self.assertEqual([event["severity"] for event in second["events"]], ["info"])
            self.assertEqual(store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
