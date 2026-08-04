from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable

from .gbrain_adapter import GBrainJobAppliedAdapter
from .handler import (
    JobAppliedHandler,
    JobAppliedProcessor,
    ProcessingStatus,
)
from .runtime import (
    CONSUMER_NAME,
    DLQ_STREAM,
    DLQ_SUBJECT,
    EVENTS_STREAM,
    RuntimeLayout,
)
from .store import EventStore, TerminalRecord
from .worker import EventWorker


OBSERVABILITY_SCHEMA_VERSION = 1
OBSERVABILITY_MAX_EVENTS = 100
_EVENT_DEFINITIONS = {
    "reader_initialized": (
        "queue_reader",
        "info",
        "Queue reader initialized.",
    ),
    "reader_initialization_failed": (
        "queue_reader",
        "error",
        "Queue reader initialization failed; retry is scheduled.",
    ),
    "broker_unavailable": (
        "broker",
        "warning",
        "Queue broker is unavailable; retry is scheduled.",
    ),
    "broker_disconnected": (
        "broker",
        "warning",
        "Queue reader disconnected from the broker; retry remains active.",
    ),
    "reader_connected": (
        "broker",
        "info",
        "Queue reader connected to the broker.",
    ),
    "consumer_bound": (
        "consumer",
        "info",
        "Queue reader bound to the durable consumer.",
    ),
    "reader_failure": (
        "consumer",
        "error",
        "Queue receive failed; retry is scheduled.",
    ),
    "processing_failure": (
        "handler",
        "error",
        "Queue event processing failed; queue retry remains active.",
    ),
    "quota_task_missing": (
        "handler",
        "warning",
        "The explicitly bound job-application task is unavailable; retry remains recoverable and operator action is required.",
    ),
    "quota_task_ambiguous": (
        "handler",
        "warning",
        "Daily job-application quota task is ambiguous; operator action is required.",
    ),
    "progress_applied": (
        "handler",
        "info",
        "A distinct job-application event was verified and applied once.",
    ),
    "duplicate_ignored": (
        "handler",
        "info",
        "A duplicate job-application delivery was verified with no extra progress.",
    ),
    "quota_task_contract_invalid": (
        "handler",
        "warning",
        "Daily job-application quota task contract is invalid; operator action is required.",
    ),
    "reader_recovered": (
        "queue_reader",
        "info",
        "Queue reader recovered.",
    ),
}
_SAFE_EVENT_VALUES = frozenset(_EVENT_DEFINITIONS.values())


def handler_observability_event(error_code: str | None) -> str:
    return {
        "quota_task_missing": "quota_task_missing",
        "quota_task_ambiguous": "quota_task_ambiguous",
        "quota_task_contract_invalid": "quota_task_contract_invalid",
    }.get(error_code, "processing_failure")


class ReaderObservability:
    """Bounded, fixed-message reader history with an atomic JSON snapshot."""

    def __init__(
        self,
        path: Path,
        *,
        max_events: int = OBSERVABILITY_MAX_EVENTS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self.path = path
        self.max_events = max_events
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._events: deque[dict[str, str]] = deque(maxlen=max_events)
        self._health: dict[str, Any] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._load_safe_history()

    def _load_safe_history(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        events = raw.get("events") if isinstance(raw, dict) else None
        if not isinstance(events, list):
            return
        safe_events: list[dict[str, str]] = []
        for event in events:
            if not isinstance(event, dict) or set(event) != {
                "timestamp",
                "component",
                "severity",
                "message",
            }:
                continue
            values = (
                event.get("component"),
                event.get("severity"),
                event.get("message"),
            )
            timestamp = event.get("timestamp")
            if values not in _SAFE_EVENT_VALUES or not isinstance(timestamp, str):
                continue
            try:
                parsed = datetime.fromisoformat(timestamp)
            except ValueError:
                continue
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                continue
            safe_events.append(
                {
                    "timestamp": parsed.astimezone(timezone.utc).isoformat(),
                    "component": values[0],
                    "severity": values[1],
                    "message": values[2],
                }
            )
        for event in reversed(safe_events[: self.max_events]):
            self._events.appendleft(event)

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "schema_version": OBSERVABILITY_SCHEMA_VERSION,
            "health": dict(self._health),
            "events": [dict(event) for event in self._events],
            "retention": {
                "max_events": self.max_events,
                "order": "newest_first",
                "storage": "atomic_file",
            },
        }

    def _write_locked(self) -> None:
        payload = json.dumps(
            self._snapshot_locked(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
            raise

    def record(self, event_code: str) -> None:
        try:
            component, severity, message = _EVENT_DEFINITIONS[event_code]
        except KeyError as exc:
            raise ValueError("unknown observability event") from exc
        timestamp = self.clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("observability timestamps must be timezone-aware")
        event = {
            "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
            "component": component,
            "severity": severity,
            "message": message,
        }
        with self._lock:
            if self._events and all(
                self._events[0][key] == event[key]
                for key in ("component", "severity", "message")
            ):
                return
            self._events.appendleft(event)
            self._write_locked()

    def update_health(self, health: dict[str, Any]) -> None:
        with self._lock:
            if health == self._health:
                return
            self._health = dict(health)
            self._write_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()


@dataclass
class HealthState:
    store: EventStore
    observability: ReaderObservability
    connected: bool = False
    pending: int = 0
    ack_pending: int = 0
    redelivered: int = 0
    last_error_code: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, **values: Any) -> None:
        changed = False
        with self._lock:
            for key, value in values.items():
                if getattr(self, key) != value:
                    setattr(self, key, value)
                    changed = True
        if changed:
            self.observability.update_health(self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            values = {
                "status": "ok" if self.connected else "degraded",
                "broker_connected": self.connected,
                "stream": EVENTS_STREAM,
                "durable_consumer": CONSUMER_NAME,
                "pending": self.pending,
                "ack_pending": self.ack_pending,
                "redelivered": self.redelivered,
                "last_error_code": self.last_error_code,
            }
        values.update(self.store.counts())
        return values

    def observability_snapshot(self) -> dict[str, Any]:
        self.observability.update_health(self.snapshot())
        return self.observability.snapshot()


class NatsDlqPublisher:
    def __init__(self, jetstream: Any) -> None:
        self.jetstream = jetstream

    async def publish_terminal(self, record: TerminalRecord) -> None:
        payload = json.dumps(
            record.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ack = await self.jetstream.publish(
            DLQ_SUBJECT,
            payload,
            headers={
                "Nats-Msg-Id": (
                    f"dlq:{record.event_id}:{record.attempts}:"
                    f"{record.original_stream_sequence}"
                )
            },
            timeout=3,
        )
        if ack.stream != DLQ_STREAM:
            raise RuntimeError("dead-letter PubAck stream mismatch")


def _health_server(state: HealthState, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/api/health":
                payload = state.snapshot()
            elif self.path == "/api/observability":
                payload = state.observability_snapshot()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


async def _default_connector(**options: Any) -> Any:
    import nats

    return await nats.connect(**options)


async def _wait_for_retry(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        return


def _is_fetch_timeout(error: Exception) -> bool:
    return error.__class__.__name__ in {"TimeoutError", "NatsTimeoutError"}


async def run_consumer(
    layout: RuntimeLayout | None = None,
    *,
    health_host: str = "127.0.0.1",
    health_port: int = 4181,
    connector: Callable[..., Awaitable[Any]] | None = None,
    stop_event: asyncio.Event | None = None,
    retry_delays: tuple[float, ...] = (1, 2, 5, 10, 30),
) -> None:
    if not retry_delays or any(delay <= 0 for delay in retry_delays):
        raise ValueError("retry_delays must contain positive values")
    layout = layout or RuntimeLayout()
    stop = stop_event or asyncio.Event()
    connect = connector or _default_connector
    store = EventStore(layout.receipts)
    observability = ReaderObservability(layout.observability)
    health = HealthState(store, observability)
    health.observability.update_health(health.snapshot())
    health.observability.record("reader_initialized")
    server = _health_server(health, health_host, health_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    retry_index = 0
    experienced_failure = False

    try:
        while not stop.is_set():
            nc = None
            stage = "initialization"
            try:
                credentials = json.loads(
                    layout.consumer_credentials.read_text(encoding="utf-8")
                )
                stage = "connect"

                async def disconnected() -> None:
                    health.update(
                        connected=False,
                        last_error_code="broker_disconnected",
                    )
                    health.observability.record("broker_disconnected")

                async def reconnected() -> None:
                    health.update(connected=True, last_error_code=None)
                    health.observability.record("reader_recovered")

                async def error_callback(_error: Exception) -> None:
                    health.update(
                        connected=False,
                        last_error_code="broker_client_error",
                    )
                    health.observability.record("broker_unavailable")

                nc = await connect(
                    servers=[credentials["url"]],
                    user=credentials["user"],
                    password=credentials["password"],
                    inbox_prefix=credentials["inbox_prefix"].encode("ascii"),
                    name="gtasks-event-consumer",
                    disconnected_cb=disconnected,
                    reconnected_cb=reconnected,
                    error_cb=error_callback,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=1,
                    connect_timeout=3,
                )
                health.update(connected=True, last_error_code=None)
                health.observability.record("reader_connected")
                if experienced_failure:
                    health.observability.record("reader_recovered")
                    experienced_failure = False
                stage = "bind"
                js = nc.jetstream(timeout=3)
                subscription = await js.pull_subscribe_bind(
                    consumer=CONSUMER_NAME,
                    stream=EVENTS_STREAM,
                )
                health.observability.record("consumer_bound")
                processor = JobAppliedProcessor(
                    store=store,
                    handler=JobAppliedHandler(
                        adapter=GBrainJobAppliedAdapter(),
                    ),
                )
                worker = EventWorker(
                    processor=processor,
                    store=store,
                    dlq=NatsDlqPublisher(js),
                )
                retry_index = 0
                stage = "consume"
                while not stop.is_set():
                    try:
                        messages = await subscription.fetch(1, timeout=2)
                    except Exception as exc:
                        if _is_fetch_timeout(exc):
                            messages = []
                        else:
                            raise
                    for message in messages:
                        try:
                            result = await worker.handle_message(message)
                        except Exception:
                            health.update(last_error_code="processing_failure")
                            health.observability.record("processing_failure")
                            experienced_failure = True
                            continue
                        if result is not None and result.status in {
                            ProcessingStatus.FAILED,
                            ProcessingStatus.REJECTED,
                        }:
                            health.update(last_error_code="processing_failure")
                            health.observability.record(
                                handler_observability_event(result.error_code)
                            )
                            experienced_failure = True
                        elif experienced_failure:
                            health.update(last_error_code=None)
                            health.observability.record("reader_recovered")
                            experienced_failure = False
                        elif result is not None and result.status == ProcessingStatus.ACCEPTED:
                            health.observability.record("progress_applied")
                        elif result is not None and result.status == ProcessingStatus.DUPLICATE:
                            health.observability.record("duplicate_ignored")
                    try:
                        info = await js.consumer_info(
                            EVENTS_STREAM,
                            CONSUMER_NAME,
                        )
                        health.update(
                            connected=True,
                            pending=info.num_pending,
                            ack_pending=info.num_ack_pending,
                            redelivered=info.num_redelivered,
                        )
                    except Exception:
                        health.update(last_error_code="consumer_info_unavailable")
            except asyncio.CancelledError:
                raise
            except Exception:
                experienced_failure = True
                if stage == "initialization":
                    error_code = "initialization_failure"
                    event_code = "reader_initialization_failed"
                elif stage == "connect":
                    error_code = "broker_unavailable"
                    event_code = "broker_unavailable"
                elif stage == "bind":
                    error_code = "consumer_bind_failure"
                    event_code = "reader_initialization_failed"
                else:
                    error_code = "reader_failure"
                    event_code = "reader_failure"
                health.update(connected=False, last_error_code=error_code)
                health.observability.record(event_code)
            finally:
                if nc is not None:
                    try:
                        await nc.close()
                    except Exception:
                        pass
            if not stop.is_set():
                delay = retry_delays[min(retry_index, len(retry_delays) - 1)]
                retry_index += 1
                await _wait_for_retry(stop, delay)
    finally:
        health.update(connected=False)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()
