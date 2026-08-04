import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gtasks.event_queue.store import (
    ClaimConflict,
    ClaimDisposition,
    EventStore,
    TerminalRecord,
)


NOW = datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc)


class EventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "receipts.sqlite3"
        self.store = EventStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_accepted_receipt_survives_reopen(self) -> None:
        claim = self.store.claim(
            event_id="evt-1",
            source_client_id="career-path",
            idempotency_key="apply-1",
            fingerprint="abc",
            now=NOW,
            lease_seconds=30,
        )
        self.assertEqual(claim.disposition, ClaimDisposition.PROCESS)
        self.store.accept("evt-1", handler_version="job_applied.v1", now=NOW)
        self.store.close()

        self.store = EventStore(self.path)
        duplicate = self.store.claim(
            event_id="evt-1",
            source_client_id="career-path",
            idempotency_key="apply-1",
            fingerprint="abc",
            now=NOW + timedelta(minutes=1),
            lease_seconds=30,
        )

        self.assertEqual(duplicate.disposition, ClaimDisposition.DUPLICATE)

    def test_same_logical_key_with_changed_content_conflicts(self) -> None:
        self.store.claim(
            event_id="evt-1",
            source_client_id="career-path",
            idempotency_key="apply-1",
            fingerprint="abc",
            now=NOW,
            lease_seconds=30,
        )

        with self.assertRaises(ClaimConflict):
            self.store.claim(
                event_id="evt-2",
                source_client_id="career-path",
                idempotency_key="apply-1",
                fingerprint="changed",
                now=NOW + timedelta(seconds=31),
                lease_seconds=30,
            )

    def test_unexpired_claim_is_busy_and_expired_claim_is_recovered(self) -> None:
        self.store.claim(
            event_id="evt-1",
            source_client_id="career-path",
            idempotency_key="apply-1",
            fingerprint="abc",
            now=NOW,
            lease_seconds=30,
        )

        busy = self.store.claim(
            event_id="evt-1",
            source_client_id="career-path",
            idempotency_key="apply-1",
            fingerprint="abc",
            now=NOW + timedelta(seconds=15),
            lease_seconds=30,
        )
        recovered = self.store.claim(
            event_id="evt-1",
            source_client_id="career-path",
            idempotency_key="apply-1",
            fingerprint="abc",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )

        self.assertEqual(busy.disposition, ClaimDisposition.BUSY)
        self.assertEqual(recovered.disposition, ClaimDisposition.PROCESS)

    def test_terminal_record_persists_safe_fields_without_payload(self) -> None:
        terminal = TerminalRecord(
            event_id="evt-1",
            event_type="job_applied",
            schema_version=1,
            source_client_id="career-path",
            error_code="invalid_event",
            attempts=1,
            original_stream="GTASKS_EVENTS",
            original_stream_sequence=42,
            failed_at=NOW,
        )

        self.store.record_terminal(terminal)
        serialized = json.dumps(self.store.list_terminals(), default=str)

        self.assertIn("evt-1", serialized)
        self.assertNotIn("Engineering Manager", serialized)
        self.assertNotIn("payload", serialized)

    def test_activity_receipt_preserves_progress_breakdown_without_payload(self) -> None:
        self.store.record_activity(
            event_id="evt-1",
            fingerprint="sha256:abc",
            source_client_id="career-path",
            disposition="incremented",
            task_slug="tasks/bound",
            scope_day="2026-08-05",
            timezone_name="America/Los_Angeles",
            prior_progress=8,
            resulting_progress=9,
            verified_count=1,
            baseline_count=8,
            target_value=15,
            recorded_at=NOW,
        )

        receipt = self.store.list_activity()[0]
        self.assertEqual(receipt["disposition"], "incremented")
        self.assertEqual(receipt["fingerprint"], "sha256:abc")
        self.assertEqual(receipt["source_client_id"], "career-path")
        self.assertEqual(receipt["prior_progress"], 8)
        self.assertEqual(receipt["resulting_progress"], 9)
        self.assertEqual(receipt["baseline_count"], 8)
        self.assertEqual(receipt["verified_count"], 1)
        serialized = json.dumps(receipt)
        self.assertNotIn("payload", serialized)
        self.assertNotIn("Example Co", serialized)

    def test_reopen_terminal_preserves_identity_and_allows_same_event_retry(self) -> None:
        self.store.claim(
            event_id="evt-1", source_client_id="career-path",
            idempotency_key="apply-1", fingerprint="abc", now=NOW,
            lease_seconds=30,
        )
        self.store.record_terminal(
            TerminalRecord(
                event_id="evt-1", event_type="job_applied", schema_version=1,
                source_client_id="career-path", error_code="quota_task_missing",
                attempts=1, original_stream="GTASKS_EVENTS",
                original_stream_sequence=42, failed_at=NOW,
            )
        )

        self.store.reopen_terminal(
            "evt-1", now=NOW + timedelta(minutes=1), lease_seconds=1
        )
        retry = self.store.claim(
            event_id="evt-1", source_client_id="career-path",
            idempotency_key="apply-1", fingerprint="abc",
            now=NOW + timedelta(minutes=1, seconds=1), lease_seconds=30,
        )

        self.assertEqual(retry.disposition, ClaimDisposition.PROCESS)
        self.assertEqual(self.store.list_terminals(), [])

    def test_reopen_terminal_refuses_accepted_receipt(self) -> None:
        self.store.claim(
            event_id="evt-1", source_client_id="career-path",
            idempotency_key="apply-1", fingerprint="abc", now=NOW,
            lease_seconds=30,
        )
        self.store.accept("evt-1", handler_version="job_applied.v1", now=NOW)

        with self.assertRaises(ValueError):
            self.store.reopen_terminal("evt-1", now=NOW + timedelta(minutes=1))

    def test_terminal_redelivery_stays_busy_until_operator_reopens_it(self) -> None:
        self.store.claim(
            event_id="evt-1", source_client_id="career-path",
            idempotency_key="apply-1", fingerprint="abc", now=NOW,
            lease_seconds=30,
        )
        self.store.record_terminal(
            TerminalRecord(
                event_id="evt-1", event_type="job_applied", schema_version=1,
                source_client_id="career-path", error_code="quota_task_missing",
                attempts=1, original_stream="GTASKS_EVENTS",
                original_stream_sequence=42, failed_at=NOW,
            )
        )

        claim = self.store.claim(
            event_id="evt-1", source_client_id="career-path",
            idempotency_key="apply-1", fingerprint="abc",
            now=NOW + timedelta(minutes=1), lease_seconds=30,
        )

        self.assertEqual(claim.disposition, ClaimDisposition.BUSY)
        self.assertEqual(self.store.list_terminals()[0]["event_id"], "evt-1")


if __name__ == "__main__":
    unittest.main()
