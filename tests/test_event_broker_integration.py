import asyncio
import json
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import nats
except ImportError:  # pragma: no cover - exercised by the event-queue venv
    nats = None

from gtasks.event_queue.broker import provision
from gtasks.event_queue.handler import (
    InMemoryJobAppliedAdapter,
    JobAppliedHandler,
    JobAppliedProcessor,
)
from gtasks.event_queue.ops import redrive
from gtasks.event_queue.producer import EnqueueStatus, enqueue_once
from gtasks.event_queue.runtime import RuntimeLayout, initialize_runtime
from gtasks.event_queue.service import NatsDlqPublisher
from gtasks.event_queue.store import EventStore, TerminalRecord
from gtasks.event_queue.worker import EventWorker
from tests.test_event_contract import valid_event


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipIf(nats is None, "nats-py is installed in the event-queue venv")
class BrokerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.layout = RuntimeLayout(
            root / "runtime",
            client_port=free_port(),
            monitor_port=free_port(),
        )
        initialize_runtime(self.layout)
        resources = json.loads(self.layout.resources.read_text())
        resources["consumer"]["ack_wait_seconds"] = 0.3
        resources["consumer"]["backoff_seconds"] = [0.1, 0.2, 0.3, 0.4, 0.5]
        self.layout.resources.write_text(json.dumps(resources), encoding="utf-8")
        self.process = self._start()
        asyncio.run(provision(self.layout))

    def tearDown(self) -> None:
        self._stop()
        self.temp.cleanup()

    def _start(self):
        process = subprocess.Popen(
            ["nats-server", "-c", str(self.layout.server_config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health = (
            f"http://127.0.0.1:{self.layout.monitor_port}"
            "/healthz?js-enabled-only=true"
        )
        for _ in range(60):
            if process.poll() is not None:
                self.fail("isolated NATS server exited during startup")
            try:
                with urllib.request.urlopen(health, timeout=0.2) as response:
                    if response.status == 200:
                        return process
            except OSError:
                time.sleep(0.05)
        self.fail("isolated NATS server did not become healthy")

    def _stop(self):
        if getattr(self, "process", None) and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    async def _connect(self, credential_path: Path):
        credentials = json.loads(credential_path.read_text())
        return await nats.connect(
            credentials["url"],
            user=credentials["user"],
            password=credentials["password"],
            inbox_prefix=credentials["inbox_prefix"].encode(),
            max_reconnect_attempts=0,
        )

    def test_puback_dedup_auth_and_restart_durability(self) -> None:
        first = asyncio.run(
            enqueue_once(
                valid_event(),
                binding_path=self.layout.producer_binding,
            )
        )
        second = asyncio.run(
            enqueue_once(
                valid_event(),
                binding_path=self.layout.producer_binding,
            )
        )

        self.assertEqual(first.status, EnqueueStatus.ACCEPTED)
        self.assertEqual(second.status, EnqueueStatus.ACCEPTED)
        self.assertTrue(second.duplicate)

        unauthorized = subprocess.run(
            [
                "nats",
                "--server",
                f"nats://127.0.0.1:{self.layout.client_port}",
                "--timeout",
                "500ms",
                "server",
                "check",
                "connection",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(unauthorized.returncode, 0)
        self.assertIn(
            "Authorization Violation",
            unauthorized.stderr + unauthorized.stdout,
        )

        self._stop()
        self.process = self._start()

        async def inspect():
            nc = await self._connect(self.layout.admin_credentials)
            try:
                stream = await nc.jetstream().stream_info("GTASKS_EVENTS")
                consumer = await nc.jetstream().consumer_info(
                    "GTASKS_EVENTS", "GTASKS_JOB_EVENTS"
                )
                return stream.state.messages, consumer.num_pending
            finally:
                await nc.close()

        messages, pending = asyncio.run(inspect())
        self.assertEqual(messages, 1)
        self.assertEqual(pending, 1)

    def test_bad_producer_password_returns_rejected_without_throwing(self) -> None:
        credentials = json.loads(self.layout.producer_credentials.read_text())
        credentials["password"] = "definitely-wrong"
        bad_credentials = self.layout.root / "bad-producer.credentials.json"
        bad_credentials.write_text(json.dumps(credentials), encoding="utf-8")
        binding = json.loads(self.layout.producer_binding.read_text())
        binding["credential_file"] = str(bad_credentials)
        bad_binding = self.layout.root / "bad-producer-binding.json"
        bad_binding.write_text(json.dumps(binding), encoding="utf-8")

        result = asyncio.run(
            enqueue_once(valid_event(), binding_path=bad_binding)
        )

        self.assertEqual(result.status, EnqueueStatus.REJECTED)

    def test_consumer_can_publish_safe_dlq_and_operator_can_redrive_original(self) -> None:
        accepted = asyncio.run(
            enqueue_once(
                valid_event(),
                binding_path=self.layout.producer_binding,
            )
        )
        self.assertEqual(accepted.status, EnqueueStatus.ACCEPTED)
        terminal = TerminalRecord(
            event_id=valid_event()["event_id"],
            event_type="job_applied",
            schema_version=1,
            source_client_id="career-path",
            error_code="fixture_terminal",
            attempts=5,
            original_stream="GTASKS_EVENTS",
            original_stream_sequence=accepted.stream_sequence,
            failed_at=datetime.now(timezone.utc),
        )

        async def publish_dlq():
            nc = await self._connect(self.layout.consumer_credentials)
            try:
                await NatsDlqPublisher(nc.jetstream()).publish_terminal(terminal)
            finally:
                await nc.close()

        asyncio.run(publish_dlq())
        store = EventStore(self.layout.receipts)
        store.record_terminal(terminal)
        store.close()

        redriven = asyncio.run(
            redrive(
                valid_event()["event_id"],
                layout=self.layout,
                confirmed=True,
            )
        )

        self.assertEqual(redriven["event_id"], valid_event()["event_id"])
        self.assertEqual(
            redriven["idempotency_key"], valid_event()["idempotency_key"]
        )

        async def counts():
            nc = await self._connect(self.layout.admin_credentials)
            try:
                js = nc.jetstream()
                events = await js.stream_info("GTASKS_EVENTS")
                dlq = await js.stream_info("GTASKS_EVENTS_DLQ")
                return events.state.messages, dlq.state.messages
            finally:
                await nc.close()

        self.assertEqual(asyncio.run(counts()), (2, 1))

    def test_unacked_lease_is_redelivered_after_consumer_restart(self) -> None:
        accepted = asyncio.run(
            enqueue_once(valid_event(), binding_path=self.layout.producer_binding)
        )
        self.assertEqual(accepted.status, EnqueueStatus.ACCEPTED)

        async def lease_without_ack():
            nc = await self._connect(self.layout.consumer_credentials)
            sub = await nc.jetstream().pull_subscribe_bind(
                consumer="GTASKS_JOB_EVENTS",
                stream="GTASKS_EVENTS",
            )
            messages = await sub.fetch(1, timeout=1)
            delivered = messages[0].metadata.num_delivered
            await nc.close()
            return delivered

        self.assertEqual(asyncio.run(lease_without_ack()), 1)
        time.sleep(0.45)

        async def lease_after_restart():
            nc = await self._connect(self.layout.consumer_credentials)
            sub = await nc.jetstream().pull_subscribe_bind(
                consumer="GTASKS_JOB_EVENTS",
                stream="GTASKS_EVENTS",
            )
            messages = await sub.fetch(1, timeout=1)
            delivered = messages[0].metadata.num_delivered
            await messages[0].ack()
            await nc.close()
            return delivered

        self.assertEqual(asyncio.run(lease_after_restart()), 2)

    def test_broker_duplicate_delivery_has_once_only_fake_gbrain_effect(self) -> None:
        adapter = InMemoryJobAppliedAdapter()
        adapter.add_quota_task(
            slug="tasks/quota",
            day="2026-07-30",
            unit="job_application",
            target=5,
            active=True,
        )
        store = EventStore(self.layout.receipts)

        async def exercise():
            producer = await self._connect(self.layout.producer_credentials)
            producer_js = producer.jetstream()
            payload = json.dumps(
                valid_event(), sort_keys=True, separators=(",", ":")
            ).encode()
            await producer_js.publish(
                "gtasks.events.job_applied.v1",
                payload,
                headers={"Nats-Msg-Id": "fixture-delivery-1"},
            )
            await producer_js.publish(
                "gtasks.events.job_applied.v1",
                payload,
                headers={"Nats-Msg-Id": "fixture-delivery-2"},
            )
            await producer.close()

            consumer = await self._connect(self.layout.consumer_credentials)
            js = consumer.jetstream()
            worker = EventWorker(
                processor=JobAppliedProcessor(
                    store=store,
                    handler=JobAppliedHandler(
                        adapter=adapter,
                        clock=lambda: datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc),
                    ),
                ),
                store=store,
                dlq=NatsDlqPublisher(js),
            )
            sub = await js.pull_subscribe_bind(
                consumer="GTASKS_JOB_EVENTS",
                stream="GTASKS_EVENTS",
            )
            for _ in range(2):
                messages = await sub.fetch(1, timeout=1)
                await worker.handle_message(messages[0])
            await consumer.close()

        try:
            asyncio.run(exercise())
        finally:
            store.close()

        self.assertEqual(adapter.application_write_count, 1)
        self.assertEqual(adapter.progress_write_count, 1)


if __name__ == "__main__":
    unittest.main()
