import json
import tempfile
import unittest
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
from tests.test_event_contract import valid_event


LA_DAY_CLOCK = datetime(2026, 7, 30, 7, 30, tzinfo=timezone.utc)


def event_for(index: int):
    raw = valid_event()
    raw["event_id"] = f"evt_daily_{index}"
    raw["idempotency_key"] = f"career-path:linkedin:job-{index}:applied"
    raw["payload"]["application_identity"]["job_id"] = f"job-{index}"
    raw["payload"]["job_snapshot"]["url"] = (
        f"https://www.linkedin.com/jobs/view/job-{index}"
    )
    return parse_event(json.dumps(raw).encode(), "gtasks.events.job_applied.v1")


class DailyJobQuotaPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root / "receipts.sqlite3")
        self.adapter = InMemoryJobAppliedAdapter()
        self.handler = JobAppliedHandler(
            adapter=self.adapter,
            clock=lambda: LA_DAY_CLOCK,
        )
        self.processor = JobAppliedProcessor(
            store=self.store,
            handler=self.handler,
            clock=lambda: LA_DAY_CLOCK,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def add_current_quota(self, slug: str = EXPLICIT_JOB_APPLICATION_TASK) -> str:
        self.adapter.add_quota_task(
            slug=slug,
            task_day="2026-07-30",
            status="active",
            progress_metric={
                "kind": "count",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            },
            event_progress={"evidence_slugs": [], "receipt_ids": []},
        )
        return slug

    def test_uses_only_explicit_binding_even_when_other_day_matches_event(self) -> None:
        self.adapter.add_quota_task(
            slug="tasks/prior-day",
            task_day="2026-07-29",
            status="active",
            progress_metric={
                "kind": "count",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-29",
                "timezone": "America/Los_Angeles",
            },
            event_progress={"evidence_slugs": [], "receipt_ids": []},
        )
        task_slug = self.add_current_quota()

        result = self.processor.process(event_for(1))

        self.assertEqual(result.status, ProcessingStatus.ACCEPTED)
        self.assertEqual(self.adapter.tasks[task_slug].completed_count, 1)
        self.assertEqual(self.adapter.tasks["tasks/prior-day"].completed_count, 0)

    def test_missing_current_unfinished_quota_is_recoverable_without_writes(self) -> None:
        self.adapter.add_quota_task(
            slug="tasks/completed-prior-day",
            task_day="2026-07-29",
            status="completed",
            progress_metric={
                "kind": "count",
                "unit": "job_application",
                "target": 5,
                "current": 5,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-29",
                "timezone": "America/Los_Angeles",
            },
            event_progress={"evidence_slugs": ["applications/old"] * 5, "receipt_ids": ["old-1"] * 5},
        )
        self.adapter.add_quota_task(
            slug="tasks/cancelled-prior-day",
            task_day="2026-07-29",
            status="cancelled",
            progress_metric={
                "kind": "count",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-29",
                "timezone": "America/Los_Angeles",
            },
            event_progress={"evidence_slugs": [], "receipt_ids": []},
        )

        result = self.processor.process(event_for(2))

        self.assertEqual(result.status, ProcessingStatus.FAILED)
        self.assertTrue(result.retriable)
        self.assertEqual(result.error_code, "quota_task_missing")
        self.assertEqual(self.adapter.application_write_count, 0)

    def test_multiple_unbound_candidates_do_not_replace_missing_explicit_binding(self) -> None:
        self.add_current_quota("tasks/daily-five-a")
        self.add_current_quota("tasks/daily-five-b")

        result = self.processor.process(event_for(3))

        self.assertEqual(result.status, ProcessingStatus.FAILED)
        self.assertTrue(result.retriable)
        self.assertEqual(result.error_code, "quota_task_missing")
        self.assertEqual(self.adapter.application_write_count, 0)

    def test_fifth_distinct_event_completes_task_and_redelivery_does_not_increment(self) -> None:
        task_slug = self.add_current_quota()

        outcomes = [self.processor.process(event_for(index)) for index in range(1, 6)]
        duplicate = self.processor.process(event_for(5))
        task = self.adapter.tasks[task_slug]

        self.assertEqual([outcome.status for outcome in outcomes], [ProcessingStatus.ACCEPTED] * 5)
        self.assertEqual(duplicate.status, ProcessingStatus.DUPLICATE)
        self.assertFalse(task.active)
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.completed_count, 5)
        self.assertEqual(len(task.evidence), 5)
        self.assertEqual(len(task.receipt_ids), 5)
        self.assertEqual(self.adapter.completion_write_count, 1)


if __name__ == "__main__":
    unittest.main()
