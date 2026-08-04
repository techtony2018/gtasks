from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


EVENTS_STREAM = "GTASKS_EVENTS"
DLQ_STREAM = "GTASKS_EVENTS_DLQ"
CONSUMER_NAME = "GTASKS_JOB_EVENTS"
EVENT_SUBJECT = "gtasks.events.job_applied.v1"
DLQ_SUBJECT = "gtasks.deadletter.job_applied.v1"
DEFAULT_RUNTIME_ROOT = Path(
    "/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-events"
)


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    root: Path = DEFAULT_RUNTIME_ROOT
    client_port: int = 4222
    monitor_port: int = 8222

    @property
    def server_config(self) -> Path:
        return self.root / "nats-server.conf"

    @property
    def jetstream_store(self) -> Path:
        return self.root / "jetstream"

    @property
    def admin_credentials(self) -> Path:
        return self.root / "admin.credentials.json"

    @property
    def producer_credentials(self) -> Path:
        return self.root / "career-path.credentials.json"

    @property
    def consumer_credentials(self) -> Path:
        return self.root / "gtasks-consumer.credentials.json"

    @property
    def producer_binding(self) -> Path:
        return self.root / "career-path-producer-binding.json"

    @property
    def resources(self) -> Path:
        return self.root / "jetstream-resources.json"

    @property
    def receipts(self) -> Path:
        return self.root / "consumer-receipts.sqlite3"

    @property
    def observability(self) -> Path:
        return self.root / "reader-observability.json"

    @property
    def secret_files(self) -> tuple[Path, ...]:
        return (
            self.server_config,
            self.admin_credentials,
            self.producer_credentials,
            self.consumer_credentials,
        )


def _write_json(path: Path, value: object, *, mode: int) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, mode)


def _credential(
    path: Path,
    *,
    user: str,
    inbox_prefix: str,
    broker_url: str,
) -> dict[str, str]:
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(raw, dict)
            and raw.get("url") == broker_url
            and raw.get("user") == user
            and isinstance(raw.get("password"), str)
            and len(raw["password"]) >= 32
        ):
            if raw.get("inbox_prefix") != inbox_prefix:
                raw["inbox_prefix"] = inbox_prefix
                _write_json(path, raw, mode=0o600)
            os.chmod(path, 0o600)
            return raw
        raise RuntimeError(f"existing credential has an invalid contract: {path}")
    value = {
        "url": broker_url,
        "user": user,
        "password": secrets.token_urlsafe(36),
        "inbox_prefix": inbox_prefix,
    }
    _write_json(path, value, mode=0o600)
    return value


def _server_config(
    layout: RuntimeLayout,
    *,
    admin: dict[str, str],
    producer: dict[str, str],
    consumer: dict[str, str],
) -> str:
    return f"""server_name: gtasks-events-local
host: 127.0.0.1
port: {layout.client_port}
http: 127.0.0.1:{layout.monitor_port}

jetstream {{
  store_dir: {json.dumps(str(layout.jetstream_store))}
  max_mem_store: 64MB
  max_file_store: 1GB
}}

authorization {{
  users: [
    {{
      user: {json.dumps(admin["user"])}
      password: {json.dumps(admin["password"])}
      permissions: {{ publish: ">", subscribe: ">" }}
    }}
    {{
      user: {json.dumps(producer["user"])}
      password: {json.dumps(producer["password"])}
      permissions: {{
        publish: "{EVENT_SUBJECT}"
        subscribe: "_INBOX.GTASKS_CAREER_PATH.>"
      }}
    }}
    {{
      user: {json.dumps(consumer["user"])}
      password: {json.dumps(consumer["password"])}
      permissions: {{
        publish: [
          "$JS.API.INFO"
          "$JS.API.STREAM.INFO.{EVENTS_STREAM}"
          "$JS.API.CONSUMER.INFO.{EVENTS_STREAM}.{CONSUMER_NAME}"
          "$JS.API.CONSUMER.MSG.NEXT.{EVENTS_STREAM}.{CONSUMER_NAME}"
          "$JS.ACK.{EVENTS_STREAM}.{CONSUMER_NAME}.>"
          "{DLQ_SUBJECT}"
        ]
        subscribe: "_INBOX.GTASKS_CONSUMER.>"
      }}
    }}
  ]
  timeout: 2
}}
"""


def initialize_runtime(layout: RuntimeLayout | None = None) -> RuntimeLayout:
    layout = layout or RuntimeLayout()
    layout.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(layout.root, 0o700)
    layout.jetstream_store.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(layout.jetstream_store, 0o700)

    broker_url = f"nats://127.0.0.1:{layout.client_port}"
    admin = _credential(
        layout.admin_credentials,
        user="gtasks-admin",
        inbox_prefix="_INBOX.GTASKS_ADMIN",
        broker_url=broker_url,
    )
    producer = _credential(
        layout.producer_credentials,
        user="career-path-producer",
        inbox_prefix="_INBOX.GTASKS_CAREER_PATH",
        broker_url=broker_url,
    )
    consumer = _credential(
        layout.consumer_credentials,
        user="gtasks-event-consumer",
        inbox_prefix="_INBOX.GTASKS_CONSUMER",
        broker_url=broker_url,
    )
    layout.server_config.write_text(
        _server_config(
            layout,
            admin=admin,
            producer=producer,
            consumer=consumer,
        ),
        encoding="utf-8",
    )
    os.chmod(layout.server_config, 0o600)

    _write_json(
        layout.producer_binding,
        {
            "version": 1,
            "broker_url": broker_url,
            "subject": EVENT_SUBJECT,
            "stream": EVENTS_STREAM,
            "credential_file": str(layout.producer_credentials),
            "acceptance": "jetstream_puback",
            "message_id_header": "Nats-Msg-Id",
            "publish_timeout_seconds": 2.0,
            "result_semantics": "nonblocking_enqueue_only",
        },
        mode=0o644,
    )
    _write_json(
        layout.resources,
        {
            "version": 1,
            "events_stream": {
                "name": EVENTS_STREAM,
                "subjects": ["gtasks.events.>"],
                "storage": "file",
                "retention": "limits",
                "max_age_seconds": 30 * 24 * 60 * 60,
                "max_bytes": 512 * 1024 * 1024,
                "max_message_bytes": 64 * 1024,
                "duplicate_window_seconds": 2 * 60 * 60,
            },
            "consumer": {
                "durable_name": CONSUMER_NAME,
                "filter_subject": EVENT_SUBJECT,
                "ack_policy": "explicit",
                "ack_wait_seconds": 30,
                "max_deliver": 5,
                "max_ack_pending": 1,
                "backoff_seconds": [30, 60, 300, 900, 3600],
            },
            "dead_letter_stream": {
                "name": DLQ_STREAM,
                "subjects": ["gtasks.deadletter.>"],
                "storage": "file",
                "retention": "limits",
                "max_age_seconds": 365 * 24 * 60 * 60,
                "max_bytes": 128 * 1024 * 1024,
                "max_message_bytes": 8 * 1024,
            },
        },
        mode=0o644,
    )
    return layout
