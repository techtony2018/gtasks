import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gtasks.event_queue.runtime import (
    CONSUMER_NAME,
    DLQ_STREAM,
    EVENTS_STREAM,
    RuntimeLayout,
    initialize_runtime,
)


class EventRuntimeTests(unittest.TestCase):
    def test_generated_server_config_passes_nats_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = RuntimeLayout(Path(temp) / "gtasks-events")
            initialize_runtime(layout)

            result = subprocess.run(
                ["nats-server", "-t", "-c", str(layout.server_config)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_isolated_runtime_uses_requested_loopback_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = RuntimeLayout(
                Path(temp) / "gtasks-events",
                client_port=14222,
                monitor_port=18222,
            )

            initialize_runtime(layout)

            config = layout.server_config.read_text(encoding="utf-8")
            credentials = json.loads(layout.admin_credentials.read_text())
            self.assertIn("port: 14222", config)
            self.assertIn("http: 127.0.0.1:18222", config)
            self.assertEqual(credentials["url"], "nats://127.0.0.1:14222")

    def test_setup_generates_loopback_authenticated_owner_only_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = RuntimeLayout(Path(temp) / "gtasks-events")

            initialize_runtime(layout)

            self.assertEqual(os.stat(layout.root).st_mode & 0o777, 0o700)
            for path in layout.secret_files:
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            config = layout.server_config.read_text(encoding="utf-8")
            self.assertIn("host: 127.0.0.1", config)
            self.assertIn("port: 4222", config)
            self.assertIn("http: 127.0.0.1:8222", config)
            self.assertIn(str(layout.jetstream_store), config)
            self.assertNotIn("0.0.0.0", config)

    def test_setup_generates_distinct_least_privilege_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = RuntimeLayout(Path(temp) / "gtasks-events")
            initialize_runtime(layout)

            admin = json.loads(layout.admin_credentials.read_text())
            producer = json.loads(layout.producer_credentials.read_text())
            consumer = json.loads(layout.consumer_credentials.read_text())
            config = layout.server_config.read_text(encoding="utf-8")

            self.assertEqual(len({admin["password"], producer["password"], consumer["password"]}), 3)
            self.assertFalse(producer["inbox_prefix"].endswith("."))
            self.assertFalse(consumer["inbox_prefix"].endswith("."))
            self.assertIn('publish: "gtasks.events.job_applied.v1"', config)
            self.assertIn('subscribe: "_INBOX.GTASKS_CAREER_PATH.>"', config)
            self.assertNotIn('subscribe: "gtasks.events.>"', config)
            self.assertIn(
                '"$JS.API.CONSUMER.MSG.NEXT.GTASKS_EVENTS.GTASKS_JOB_EVENTS"',
                config,
            )

    def test_producer_binding_contains_location_not_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = RuntimeLayout(Path(temp) / "gtasks-events")
            initialize_runtime(layout)

            binding = json.loads(layout.producer_binding.read_text())
            serialized = json.dumps(binding)

            self.assertEqual(binding["subject"], "gtasks.events.job_applied.v1")
            self.assertEqual(binding["acceptance"], "jetstream_puback")
            self.assertEqual(binding["stream"], EVENTS_STREAM)
            self.assertEqual(binding["credential_file"], str(layout.producer_credentials))
            self.assertNotIn("password", serialized)

    def test_resource_contract_records_stream_consumer_and_dlq_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = RuntimeLayout(Path(temp) / "gtasks-events")
            initialize_runtime(layout)

            resources = json.loads(layout.resources.read_text())

            self.assertEqual(resources["events_stream"]["name"], EVENTS_STREAM)
            self.assertEqual(resources["consumer"]["durable_name"], CONSUMER_NAME)
            self.assertEqual(resources["consumer"]["ack_policy"], "explicit")
            self.assertEqual(resources["consumer"]["max_deliver"], 5)
            self.assertEqual(
                resources["consumer"]["backoff_seconds"],
                [30, 60, 300, 900, 3600],
            )
            self.assertEqual(resources["dead_letter_stream"]["name"], DLQ_STREAM)

    def test_setup_has_no_static_quota_mapping_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = RuntimeLayout(Path(temp) / "gtasks-events")
            initialize_runtime(layout)

        self.assertFalse((layout.root / "quota-task-map.json").exists())


if __name__ == "__main__":
    unittest.main()
