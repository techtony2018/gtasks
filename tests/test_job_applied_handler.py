import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gtasks.event_queue.contract import parse_event
from gtasks.event_queue.handler import (
    EXPLICIT_JOB_APPLICATION_TASK,
    HandlerFailure,
    InMemoryJobAppliedAdapter,
    JobAppliedHandler,
    JobAppliedProcessor,
    ProcessingStatus,
    application_slug,
)
from gtasks.event_queue.store import EventStore
from tests.test_event_contract import valid_event


NOW = datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc)


def event():
    return parse_event(
        json.dumps(valid_event()).encode(),
        "gtasks.events.job_applied.v1",
    )


class JobAppliedHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = EventStore(self.root / "receipts.sqlite3")
        self.adapter = InMemoryJobAppliedAdapter()
        self.adapter.add_quota_task(
            slug=EXPLICIT_JOB_APPLICATION_TASK,
            day="2026-07-30",
            unit="job_application",
            target=5,
            active=True,
        )
        self.handler = JobAppliedHandler(
            adapter=self.adapter,
            clock=lambda: NOW,
        )
        self.processor = JobAppliedProcessor(
            store=self.store,
            handler=self.handler,
            clock=lambda: NOW,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_application_slug_uses_normalized_source_id_and_digest(self) -> None:
        first = application_slug("LinkedIn", " JOB/42 ")
        second = application_slug("linkedin", "job/42")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("applications/linkedin-job-42-"))
        self.assertNotIn("engineering-manager", first)

    def test_handler_upserts_application_links_and_once_only_progress(self) -> None:
        result = self.processor.process(event())

        slug = application_slug("linkedin", "job-42")
        task_slug = EXPLICIT_JOB_APPLICATION_TASK
        self.assertEqual(result.status, ProcessingStatus.ACCEPTED)
        self.assertEqual(self.adapter.progress_evidence(task_slug), {slug})
        self.assertIn((slug, task_slug, "evidence_for"), self.adapter.links)
        self.assertIn((task_slug, slug, "has_evidence"), self.adapter.links)
        self.assertEqual(self.adapter.application_write_count, 1)
        self.assertEqual(self.adapter.progress_write_count, 1)

    def test_duplicate_delivery_has_exactly_once_effects(self) -> None:
        first = self.processor.process(event())
        second = self.processor.process(event())

        self.assertEqual(first.status, ProcessingStatus.ACCEPTED)
        self.assertEqual(second.status, ProcessingStatus.DUPLICATE)
        self.assertEqual(self.adapter.application_write_count, 1)
        self.assertEqual(self.adapter.progress_write_count, 1)
        activities = self.store.list_activity()
        self.assertEqual(
            [item["disposition"] for item in activities],
            ["duplicate_noop", "incremented"],
        )
        self.assertTrue(all(item["fingerprint"] for item in activities))
        self.assertTrue(
            all(item["source_client_id"] == "career-path" for item in activities)
        )

    def test_redelivery_after_post_mutation_failure_converges_once(self) -> None:
        self.adapter.fail_readback_once = True

        first = self.processor.process(event())
        second = self.processor.process(event())

        slug = application_slug("linkedin", "job-42")
        task_slug = EXPLICIT_JOB_APPLICATION_TASK
        self.assertEqual(first.status, ProcessingStatus.FAILED)
        self.assertTrue(first.retriable)
        self.assertEqual(second.status, ProcessingStatus.ACCEPTED)
        self.assertEqual(self.adapter.progress_evidence(task_slug), {slug})

    def test_final_event_readback_failure_replays_after_verified_completion(self) -> None:
        self.adapter.add_quota_task(
            slug=EXPLICIT_JOB_APPLICATION_TASK,
            day="2026-07-30",
            unit="job_application",
            target=5,
            status="active",
            event_progress={
                "baseline_count": 4,
                "evidence_slugs": [],
                "receipt_ids": [],
            },
        )
        self.adapter.fail_readback_once = True

        first = self.processor.process(event())
        second = self.processor.process(event())

        task = self.adapter.tasks[EXPLICIT_JOB_APPLICATION_TASK]
        self.assertEqual(first.status, ProcessingStatus.FAILED)
        self.assertTrue(first.retriable)
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.completed_count, 5)
        self.assertEqual(second.status, ProcessingStatus.ACCEPTED)
        self.assertEqual(task.receipt_ids, frozenset({event().event_id}))

    def test_missing_current_quota_fails_closed(self) -> None:
        self.adapter.tasks.clear()
        handler = JobAppliedHandler(
            adapter=self.adapter,
            clock=lambda: NOW,
        )

        with self.assertRaisesRegex(HandlerFailure, "quota_task_missing"):
            handler.handle(event())

    def test_other_matching_task_never_replaces_explicit_binding(self) -> None:
        self.adapter.add_quota_task(
            slug="tasks/another-daily-five",
            day="2026-07-30",
            unit="job_application",
            target=5,
            active=True,
        )
        handler = JobAppliedHandler(
            adapter=self.adapter,
            clock=lambda: NOW,
        )

        handler.handle(event())
        self.assertEqual(
            self.adapter.tasks[EXPLICIT_JOB_APPLICATION_TASK].completed_count,
            1,
        )
        self.assertEqual(self.adapter.tasks["tasks/another-daily-five"].completed_count, 0)

    def test_quota_task_contract_mismatch_fails_closed(self) -> None:
        self.adapter.add_quota_task(
            slug=EXPLICIT_JOB_APPLICATION_TASK,
            day="2026-07-30",
            unit="wrong_unit",
            target=5,
            active=True,
        )

        with self.assertRaisesRegex(HandlerFailure, "quota_task_contract_invalid"):
            self.handler.handle(event())


if __name__ == "__main__":
    unittest.main()
