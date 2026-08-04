import asyncio
import http.client
import json
import socket
import tempfile
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from gtasks.event_queue.runtime import RuntimeLayout, initialize_runtime
from gtasks.event_queue.service import (
    ReaderObservability,
    handler_observability_event,
    run_consumer,
)
from tests.test_server import FakeAdapter, ServerHarness


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def read_health(port: int) -> dict:
    url = f"http://127.0.0.1:{port}/api/health"
    for _ in range(100):
        try:
            with urllib.request.urlopen(url, timeout=0.2) as response:
                return json.loads(response.read())
        except OSError:
            await asyncio.sleep(0.01)
    raise AssertionError("consumer health server did not become available")


class FailingSubscription:
    def __init__(self, failed: asyncio.Event) -> None:
        self.failed = failed

    async def fetch(self, _batch: int, *, timeout: float):
        self.failed.set()
        raise RuntimeError("sensitive payload must not escape")


class FakeJetStream:
    def __init__(self, subscription: FailingSubscription) -> None:
        self.subscription = subscription

    async def pull_subscribe_bind(self, *, consumer: str, stream: str):
        return self.subscription

    async def consumer_info(self, stream: str, consumer: str):
        return SimpleNamespace(
            num_pending=0,
            num_ack_pending=0,
            num_redelivered=0,
        )


class FakeConnection:
    def __init__(self, jetstream: FakeJetStream) -> None:
        self._jetstream = jetstream
        self.closed = False

    def jetstream(self, *, timeout: float):
        return self._jetstream

    async def close(self) -> None:
        self.closed = True


class ConsumerIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.layout = RuntimeLayout(
            Path(self.temp.name) / "runtime",
            client_port=free_port(),
            monitor_port=free_port(),
        )
        initialize_runtime(self.layout)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_core_gtasks_available(self) -> None:
        harness = ServerHarness(self, FakeAdapter())
        status, payload, _ = harness.request("GET", "/api/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(payload["tasks"], [])

        status, payload, _ = harness.request(
            "POST",
            "/api/tasks",
            {"title": "Core operation remains available"},
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["receipt"]["verified"])

    def test_broker_unavailable_at_startup_is_degraded_not_fatal(self) -> None:
        async def exercise() -> None:
            attempts = 0
            stop = asyncio.Event()
            health_port = free_port()

            async def unavailable_connector(**_options):
                nonlocal attempts
                attempts += 1
                raise OSError("credential-bearing broker detail")

            task = asyncio.create_task(
                run_consumer(
                    self.layout,
                    health_port=health_port,
                    connector=unavailable_connector,
                    stop_event=stop,
                    retry_delays=(0.01, 0.02),
                )
            )
            health = await read_health(health_port)
            for _ in range(100):
                if attempts >= 2:
                    break
                await asyncio.sleep(0.01)

            self.assertGreaterEqual(attempts, 2)
            self.assertEqual(health["status"], "degraded")
            self.assertFalse(health["broker_connected"])
            self.assertEqual(health["last_error_code"], "broker_unavailable")
            self.assertNotIn("credential", json.dumps(health).lower())
            with urllib.request.urlopen(
                f"http://127.0.0.1:{health_port}/api/observability",
                timeout=0.2,
            ) as response:
                observability = json.loads(response.read())
            self.assertEqual(observability["schema_version"], 1)
            self.assertEqual(observability["health"], health)
            self.assertTrue(observability["events"])
            self.assertEqual(
                set(observability["events"][0]),
                {"timestamp", "component", "severity", "message"},
            )
            self.assertNotIn("credential-bearing", json.dumps(observability))
            persisted = json.loads(
                self.layout.observability.read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, observability)
            self.assert_core_gtasks_available()

            stop.set()
            await asyncio.wait_for(task, timeout=1)

        asyncio.run(exercise())

    def test_observability_journal_is_bounded_newest_first_and_atomic(self) -> None:
        timestamps = iter(
            datetime(2026, 7, 30, 19, minute, tzinfo=timezone.utc)
            for minute in range(4)
        )
        journal = ReaderObservability(
            self.layout.observability,
            max_events=3,
            clock=lambda: next(timestamps),
        )

        journal.record("reader_initialized")
        journal.record("broker_unavailable")
        journal.record("reader_connected")
        journal.record("reader_recovered")

        snapshot = journal.snapshot()
        events = snapshot["events"]
        self.assertEqual(len(events), 3)
        self.assertEqual(
            [event["message"] for event in events],
            [
                "Queue reader recovered.",
                "Queue reader connected to the broker.",
                "Queue broker is unavailable; retry is scheduled.",
            ],
        )
        self.assertEqual(
            [event["timestamp"] for event in events],
            sorted(
                [event["timestamp"] for event in events],
                reverse=True,
            ),
        )
        self.assertEqual(
            set(events[0]),
            {"timestamp", "component", "severity", "message"},
        )
        self.assertEqual(
            self.layout.observability.stat().st_mode & 0o777,
            0o600,
        )
        self.assertFalse(
            list(self.layout.root.glob("reader-observability.json.*.tmp"))
        )

    def test_quota_selection_failures_have_actionable_fixed_log_events(self) -> None:
        self.assertEqual(
            handler_observability_event("quota_task_missing"),
            "quota_task_missing",
        )
        self.assertEqual(
            handler_observability_event("quota_task_ambiguous"),
            "quota_task_ambiguous",
        )
        self.assertEqual(
            handler_observability_event("quota_task_contract_invalid"),
            "quota_task_contract_invalid",
        )
        self.assertEqual(
            handler_observability_event("gbrain_unavailable"),
            "processing_failure",
        )

    def test_reader_failure_after_startup_keeps_core_gtasks_available(self) -> None:
        async def exercise() -> None:
            failed = asyncio.Event()
            stop = asyncio.Event()
            health_port = free_port()
            connection = FakeConnection(
                FakeJetStream(FailingSubscription(failed))
            )

            async def connected_connector(**_options):
                return connection

            task = asyncio.create_task(
                run_consumer(
                    self.layout,
                    health_port=health_port,
                    connector=connected_connector,
                    stop_event=stop,
                    retry_delays=(1,),
                )
            )
            await read_health(health_port)
            await asyncio.wait_for(failed.wait(), timeout=1)
            health = await read_health(health_port)

            self.assertEqual(health["status"], "degraded")
            self.assertFalse(health["broker_connected"])
            self.assertEqual(health["last_error_code"], "reader_failure")
            self.assertNotIn("sensitive payload", json.dumps(health))
            self.assert_core_gtasks_available()

            stop.set()
            await asyncio.wait_for(task, timeout=1)
            self.assertTrue(connection.closed)

        asyncio.run(exercise())

    def test_connect_callback_records_safe_warning_before_connect_returns(self) -> None:
        async def exercise() -> None:
            callback_seen = asyncio.Event()
            allow_return = asyncio.Event()
            stop = asyncio.Event()
            health_port = free_port()

            async def retrying_connector(**options):
                await options["error_cb"](
                    RuntimeError("password and raw event must stay private")
                )
                callback_seen.set()
                await allow_return.wait()
                raise OSError("broker still unavailable")

            task = asyncio.create_task(
                run_consumer(
                    self.layout,
                    health_port=health_port,
                    connector=retrying_connector,
                    stop_event=stop,
                    retry_delays=(1,),
                )
            )
            await read_health(health_port)
            await asyncio.wait_for(callback_seen.wait(), timeout=1)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{health_port}/api/observability",
                timeout=0.2,
            ) as response:
                observability = json.loads(response.read())

            self.assertEqual(
                observability["events"][0]["message"],
                "Queue broker is unavailable; retry is scheduled.",
            )
            self.assertNotIn("raw event", json.dumps(observability))
            self.assertNotIn("password", json.dumps(observability))

            allow_return.set()
            stop.set()
            await asyncio.wait_for(task, timeout=1)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
