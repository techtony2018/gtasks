import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from gtasks.event_queue.contract import parse_event
from gtasks.event_queue.handler import (
    EXPLICIT_JOB_APPLICATION_TASK,
    InMemoryJobAppliedAdapter,
    JobAppliedHandler,
    JobAppliedProcessor,
    ProcessingStatus,
)
from gtasks.event_queue.store import EventStore


NOW = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)


def event(event_id: str = "evt-explicit-1", job_id: str = "job-42"):
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": "job_applied",
        "idempotency_key": f"career-path:{event_id}",
        "source": {"client_id": "career-path", "instance_id": "qa-fixture"},
        "occurred_at": "2026-08-01T23:55:00-07:00",
        "timezone": "America/Los_Angeles",
        "payload": {
            "application_identity": {"job_source": "linkedin", "job_id": job_id},
            "job_snapshot": {
                "title": "Engineering Manager",
                "company": "Example Co",
                "location": "California",
                "url": "https://example.invalid/jobs/42",
            },
            "applied_local_date": "2026-08-01",
            "status_evidence": {
                "status": "applied",
                "committed_at": "2026-08-01T23:54:00-07:00",
                "source": "career_path_local_commit",
            },
        },
    }
    return parse_event(json.dumps(payload).encode(), "gtasks.events.job_applied.v1")


class ExplicitJobAppliedBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp_dir.name) / "receipts.sqlite3")
        self.adapter = InMemoryJobAppliedAdapter()
        self.adapter.add_quota_task(
            slug=EXPLICIT_JOB_APPLICATION_TASK,
            task_day="2026-08-05",
            status="active",
            target=15,
            progress_metric={
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 15,
                "current": 8,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-08-05",
                "timezone": "America/Los_Angeles",
            },
            event_progress={
                "baseline_count": 8,
                "evidence_slugs": [],
                "receipt_ids": [],
            },
        )
        self.processor = JobAppliedProcessor(
            store=self.store,
            handler=JobAppliedHandler(adapter=self.adapter, clock=lambda: NOW),
            clock=lambda: NOW,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_event_uses_explicit_bound_task_not_event_or_processing_day(self) -> None:
        result = self.processor.process(event())

        self.assertEqual(result.status, ProcessingStatus.ACCEPTED)
        task = self.adapter.tasks[EXPLICIT_JOB_APPLICATION_TASK]
        self.assertEqual(task.day.isoformat(), "2026-08-05")
        self.assertEqual(task.baseline_count, 8)
        self.assertEqual(task.completed_count, 9)

    def test_duplicate_and_restart_preserve_manual_baseline_and_increment_once(self) -> None:
        first = self.processor.process(event())
        restarted = JobAppliedProcessor(
            store=self.store,
            handler=JobAppliedHandler(adapter=self.adapter, clock=lambda: NOW),
            clock=lambda: NOW,
        )
        duplicate = restarted.process(event())

        self.assertEqual(first.status, ProcessingStatus.ACCEPTED)
        self.assertEqual(duplicate.status, ProcessingStatus.DUPLICATE)
        task = self.adapter.tasks[EXPLICIT_JOB_APPLICATION_TASK]
        self.assertEqual(task.baseline_count, 8)
        self.assertEqual(task.completed_count, 9)
        self.assertEqual(len(task.receipt_ids), 1)

    def test_missing_explicit_binding_is_recoverable(self) -> None:
        self.adapter.tasks.clear()

        result = self.processor.process(event())

        self.assertEqual(result.status, ProcessingStatus.FAILED)
        self.assertTrue(result.retriable)
        self.assertEqual(result.error_code, "quota_task_missing")

        activity = self.store.list_activity(limit=10)
        self.assertEqual(activity[0]["event_id"], "evt-explicit-1")
        self.assertEqual(activity[0]["disposition"], "quota_task_missing")
        self.assertEqual(activity[0]["task_slug"], EXPLICIT_JOB_APPLICATION_TASK)

    def test_concurrent_distinct_events_each_increment_exactly_once(self) -> None:
        events = (
            event("evt-concurrent-a", "job-a"),
            event("evt-concurrent-b", "job-b"),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.processor.process, events))

        self.assertEqual(
            [result.status for result in results],
            [ProcessingStatus.ACCEPTED, ProcessingStatus.ACCEPTED],
        )
        task = self.adapter.tasks[EXPLICIT_JOB_APPLICATION_TASK]
        self.assertEqual(task.baseline_count, 8)
        self.assertEqual(task.completed_count, 10)
        self.assertEqual(task.receipt_ids, frozenset({"evt-concurrent-a", "evt-concurrent-b"}))

        with ThreadPoolExecutor(max_workers=2) as executor:
            replayed = list(executor.map(self.processor.process, events))

        self.assertEqual(
            [result.status for result in replayed],
            [ProcessingStatus.DUPLICATE, ProcessingStatus.DUPLICATE],
        )
        self.assertEqual(self.adapter.tasks[EXPLICIT_JOB_APPLICATION_TASK].completed_count, 10)


if __name__ == "__main__":
    unittest.main()
