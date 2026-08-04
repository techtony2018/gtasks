import asyncio
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from gtasks.event_queue.producer import (
    EnqueueStatus,
    PublishRejected,
    PublishUnavailable,
    enqueue_once,
)
from tests.test_event_contract import valid_event


@dataclass
class Ack:
    stream: str
    seq: int
    duplicate: bool = False


class FakePublisher:
    def __init__(self, ack=None, error: Exception | None = None) -> None:
        self.ack = ack
        self.error = error
        self.calls = []

    async def publish(self, subject, payload, *, headers, timeout):
        self.calls.append((subject, payload, headers, timeout))
        if self.error:
            raise self.error
        return self.ack


def binding(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "broker_url": "nats://127.0.0.1:4222",
                "subject": "gtasks.events.job_applied.v1",
                "stream": "GTASKS_EVENTS",
                "credential_file": "/tmp/not-opened-by-fake.json",
                "acceptance": "jetstream_puback",
                "message_id_header": "Nats-Msg-Id",
                "publish_timeout_seconds": 2.0,
                "result_semantics": "nonblocking_enqueue_only",
            }
        ),
        encoding="utf-8",
    )
    return path


class EventProducerTests(unittest.TestCase):
    def test_puback_is_durable_enqueue_acceptance_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            publisher = FakePublisher(Ack("GTASKS_EVENTS", 12))

            result = asyncio.run(
                enqueue_once(
                    valid_event(),
                    binding_path=binding(Path(temp) / "binding.json"),
                    publisher=publisher,
                )
            )

        self.assertEqual(result.status, EnqueueStatus.ACCEPTED)
        self.assertEqual(result.stream_sequence, 12)
        self.assertFalse(result.duplicate)
        self.assertNotIn("processed", result.safe_dict())
        self.assertEqual(
            publisher.calls[0][2]["Nats-Msg-Id"],
            valid_event()["event_id"],
        )

    def test_duplicate_puback_is_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = asyncio.run(
                enqueue_once(
                    valid_event(),
                    binding_path=binding(Path(temp) / "binding.json"),
                    publisher=FakePublisher(Ack("GTASKS_EVENTS", 12, duplicate=True)),
                )
            )

        self.assertEqual(result.status, EnqueueStatus.ACCEPTED)
        self.assertTrue(result.duplicate)

    def test_timeout_returns_nonblocking_unavailable_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = asyncio.run(
                enqueue_once(
                    valid_event(),
                    binding_path=binding(Path(temp) / "binding.json"),
                    publisher=FakePublisher(error=PublishUnavailable("timeout")),
                )
            )

        self.assertEqual(result.status, EnqueueStatus.UNAVAILABLE)
        self.assertTrue(result.retry_same_ids)

    def test_auth_rejection_returns_nonblocking_rejected_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = asyncio.run(
                enqueue_once(
                    valid_event(),
                    binding_path=binding(Path(temp) / "binding.json"),
                    publisher=FakePublisher(error=PublishRejected("authorization")),
                )
            )

        self.assertEqual(result.status, EnqueueStatus.REJECTED)
        self.assertTrue(result.retry_same_ids)

    def test_invalid_envelope_is_rejected_before_publish(self) -> None:
        raw = valid_event()
        raw["payload"]["unknown"] = "unsafe"
        publisher = FakePublisher(Ack("GTASKS_EVENTS", 12))
        with tempfile.TemporaryDirectory() as temp:
            result = asyncio.run(
                enqueue_once(
                    raw,
                    binding_path=binding(Path(temp) / "binding.json"),
                    publisher=publisher,
                )
            )

        self.assertEqual(result.status, EnqueueStatus.REJECTED)
        self.assertEqual(publisher.calls, [])


if __name__ == "__main__":
    unittest.main()
