import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from gtasks.event_queue.handler import ProcessingResult, ProcessingStatus
from gtasks.event_queue.store import EventStore
from gtasks.event_queue.worker import EventWorker
from tests.test_event_contract import valid_event


class FakeMessage:
    def __init__(self, payload: dict, *, deliveries: int = 1, sequence: int = 7) -> None:
        self.data = json.dumps(payload).encode()
        self.subject = "gtasks.events.job_applied.v1"
        self.metadata = SimpleNamespace(
            num_delivered=deliveries,
            sequence=SimpleNamespace(stream=sequence),
        )
        self.acked = 0
        self.naks = []
        self.in_progress_count = 0

    async def ack(self):
        self.acked += 1

    async def nak(self, delay=None):
        self.naks.append(delay)

    async def in_progress(self):
        self.in_progress_count += 1


class FakeProcessor:
    def __init__(self, result: ProcessingResult) -> None:
        self.result = result
        self.calls = 0

    def process(self, event):
        self.calls += 1
        return self.result


class FakeDlq:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.records = []

    async def publish_terminal(self, record):
        if self.fail:
            raise RuntimeError("dlq unavailable")
        self.records.append(record)


def result(status, *, error_code=None, retriable=False):
    return ProcessingResult(
        status=status,
        event_id=valid_event()["event_id"],
        event_type="job_applied",
        schema_version=1,
        handler_version="job_applied.v1",
        error_code=error_code,
        retriable=retriable,
    )


class EventWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name) / "receipts.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_ack_occurs_after_accepted_processing(self) -> None:
        message = FakeMessage(valid_event())
        worker = EventWorker(
            processor=FakeProcessor(result(ProcessingStatus.ACCEPTED)),
            store=self.store,
            dlq=FakeDlq(),
        )

        outcome = asyncio.run(worker.handle_message(message))

        self.assertEqual(message.acked, 1)
        self.assertEqual(message.naks, [])
        self.assertGreaterEqual(message.in_progress_count, 1)
        self.assertEqual(outcome.status, ProcessingStatus.ACCEPTED)

    def test_retriable_failure_uses_broker_backoff_before_last_attempt(self) -> None:
        message = FakeMessage(valid_event(), deliveries=2)
        worker = EventWorker(
            processor=FakeProcessor(
                result(
                    ProcessingStatus.FAILED,
                    error_code="gbrain_unavailable",
                    retriable=True,
                )
            ),
            store=self.store,
            dlq=FakeDlq(),
        )

        asyncio.run(worker.handle_message(message))

        self.assertEqual(message.acked, 0)
        self.assertEqual(message.naks, [60])

    def test_retry_exhaustion_publishes_safe_dlq_then_acks(self) -> None:
        message = FakeMessage(valid_event(), deliveries=5, sequence=44)
        dlq = FakeDlq()
        worker = EventWorker(
            processor=FakeProcessor(
                result(
                    ProcessingStatus.FAILED,
                    error_code="gbrain_unavailable",
                    retriable=True,
                )
            ),
            store=self.store,
            dlq=dlq,
        )

        asyncio.run(worker.handle_message(message))

        serialized = json.dumps(dlq.records[0].to_dict())
        self.assertEqual(message.acked, 1)
        self.assertEqual(message.naks, [])
        self.assertIn("gbrain_unavailable", serialized)
        self.assertNotIn("Engineering Manager", serialized)
        self.assertNotIn("payload", serialized)
        terminal_activity = self.store.list_activity()[0]
        self.assertEqual(terminal_activity["disposition"], "terminal_failure")
        self.assertEqual(terminal_activity["source_client_id"], "career-path")
        self.assertTrue(terminal_activity["fingerprint"])

    def test_unknown_type_is_terminal_without_processor_fallback(self) -> None:
        raw = valid_event()
        raw["event_type"] = "unknown"
        message = FakeMessage(raw)
        message.subject = "gtasks.events.unknown.v1"
        processor = FakeProcessor(result(ProcessingStatus.ACCEPTED))
        dlq = FakeDlq()
        worker = EventWorker(
            processor=processor,
            store=self.store,
            dlq=dlq,
        )

        asyncio.run(worker.handle_message(message))

        self.assertEqual(processor.calls, 0)
        self.assertEqual(message.acked, 1)
        self.assertEqual(dlq.records[0].error_code, "rejected_unknown_type")

    def test_original_is_not_acked_when_dlq_acceptance_fails(self) -> None:
        message = FakeMessage(valid_event(), deliveries=5)
        worker = EventWorker(
            processor=FakeProcessor(
                result(
                    ProcessingStatus.FAILED,
                    error_code="gbrain_unavailable",
                    retriable=True,
                )
            ),
            store=self.store,
            dlq=FakeDlq(fail=True),
        )

        asyncio.run(worker.handle_message(message))

        self.assertEqual(message.acked, 0)
        self.assertEqual(message.naks, [3600])


if __name__ == "__main__":
    unittest.main()
