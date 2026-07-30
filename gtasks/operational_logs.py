from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable, Mapping
from urllib.error import URLError
from urllib.request import urlopen


LOG_RETENTION = 500
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 50
MAX_SOURCE_BYTES = 2 * 1024 * 1024
SEVERITIES = ("info", "warning", "error")
COMPONENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FINGERPRINT_PATTERN = re.compile(r"^[a-f0-9]{64}$")

QUEUE_SAFE_EVENT_VALUES = frozenset(
    {
        ("queue_reader", "info", "Queue reader initialized."),
        (
            "queue_reader",
            "error",
            "Queue reader initialization failed; retry is scheduled.",
        ),
        (
            "broker",
            "warning",
            "Queue broker is unavailable; retry is scheduled.",
        ),
        (
            "broker",
            "warning",
            "Queue reader disconnected from the broker; retry remains active.",
        ),
        ("broker", "info", "Queue reader connected to the broker."),
        ("consumer", "info", "Queue reader bound to the durable consumer."),
        (
            "consumer",
            "error",
            "Queue receive failed; retry is scheduled.",
        ),
        (
            "handler",
            "error",
            "Queue event processing failed; queue retry remains active.",
        ),
        ("queue_reader", "info", "Queue reader recovered."),
    }
)
QUEUE_SAFE_ERROR_CODES = frozenset(
    {
        "initialization_failure",
        "broker_unavailable",
        "broker_disconnected",
        "broker_client_error",
        "consumer_bind_failure",
        "consumer_info_unavailable",
        "processing_failure",
        "reader_failure",
    }
)

SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|token|secret|credential|authorization|api[_-]?key)"
    r"\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
LONG_OPAQUE_VALUE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
EMAIL_ADDRESS = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")


def default_gtasks_log_path() -> Path:
    configured = os.environ.get("GTASKS_OPERATION_LOG_FILE")
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "GTasks"
        / "operational-events.jsonl"
    )


def default_queue_log_path() -> Path:
    configured = os.environ.get("GTASKS_QUEUE_LOG_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path(
        "/Users/tony/.codex/services/all-things-codex-dashboard/"
        "state/gtasks-events/reader-observability.json"
    )


def _normalize_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("log timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_message(message: str) -> str:
    value = " ".join(message.split())
    value = SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = BEARER_VALUE.sub("Bearer [REDACTED]", value)
    value = URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    value = EMAIL_ADDRESS.sub("[REDACTED EMAIL]", value)
    value = LONG_OPAQUE_VALUE.sub(
        lambda match: (
            "[REDACTED]"
            if any(character.isalpha() for character in match.group())
            and any(character.isdigit() for character in match.group())
            else match.group()
        ),
        value,
    )
    return value[:240]


@dataclass(frozen=True)
class OperationalEvent:
    timestamp: str
    component: str
    severity: str
    message: str
    event_id: str

    @classmethod
    def create(
        cls,
        *,
        timestamp: str,
        component: str,
        severity: str,
        message: str,
        event_id: str | None = None,
    ) -> "OperationalEvent":
        normalized_timestamp = _normalize_timestamp(timestamp)
        if not COMPONENT_PATTERN.fullmatch(component):
            raise ValueError("invalid operational log component")
        if severity not in SEVERITIES:
            raise ValueError("invalid operational log severity")
        safe_message = redact_message(message)
        if not safe_message:
            raise ValueError("operational log message is required")
        identity = event_id
        if identity is None:
            encoded = json.dumps(
                {
                    "timestamp": normalized_timestamp,
                    "component": component,
                    "severity": severity,
                    "message": safe_message,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            identity = hashlib.sha256(encoded).hexdigest()
        if not FINGERPRINT_PATTERN.fullmatch(identity):
            raise ValueError("invalid operational log event id")
        return cls(
            timestamp=normalized_timestamp,
            component=component,
            severity=severity,
            message=safe_message,
            event_id=identity,
        )

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        queue_source: bool = False,
    ) -> "OperationalEvent":
        required = ("timestamp", "component", "severity")
        if any(not isinstance(raw.get(field), str) for field in required):
            raise ValueError("operational log event fields are invalid")
        if queue_source:
            message = raw.get("message")
            safe_values = (raw.get("component"), raw.get("severity"), message)
            if safe_values not in QUEUE_SAFE_EVENT_VALUES:
                raise ValueError("queue operational log event is not approved")
        else:
            message = raw.get("message")
            if not isinstance(message, str):
                raise ValueError("operational log message is invalid")
        event_id = raw.get("id")
        if event_id is not None and not isinstance(event_id, str):
            raise ValueError("operational log id is invalid")
        return cls.create(
            timestamp=raw["timestamp"],
            component=raw["component"],
            severity=raw["severity"],
            message=message,
            event_id=event_id,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.event_id,
            "timestamp": self.timestamp,
            "component": self.component,
            "severity": self.severity,
            "message": self.message,
        }


class OperationalLogStore:
    def __init__(self, path: Path | None = None, *, retention: int = LOG_RETENTION):
        self.path = path or default_gtasks_log_path()
        self.retention = retention
        self._lock = Lock()

    def _read_unlocked(self) -> list[OperationalEvent]:
        return read_event_file(self.path, retention=self.retention)

    def read(self) -> list[OperationalEvent]:
        with self._lock:
            return self._read_unlocked()

    def append(
        self,
        *,
        component: str,
        severity: str,
        message: str,
        now: datetime | None = None,
    ) -> OperationalEvent:
        timestamp = (now or datetime.now().astimezone()).isoformat()
        event = OperationalEvent.create(
            timestamp=timestamp,
            component=component,
            severity=severity,
            message=message,
        )
        with self._lock:
            events = self._read_unlocked()
            events.append(event)
            events = sorted(
                {item.event_id: item for item in events}.values(),
                key=lambda item: item.timestamp,
                reverse=True,
            )[: self.retention]
            self._write_unlocked(events)
            verified = {item.event_id for item in self._read_unlocked()}
            if event.event_id not in verified:
                raise RuntimeError("operational log write could not be verified")
        return event

    def _write_unlocked(self, events: Iterable[OperationalEvent]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                for event in events:
                    stream.write(
                        json.dumps(
                            event.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


def read_event_file(
    path: Path,
    *,
    retention: int = LOG_RETENTION,
    queue_source: bool = False,
) -> list[OperationalEvent]:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise RuntimeError("operational log source exceeds its safe size limit")
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RuntimeError("operational log source could not be read") from exc
    result: list[OperationalEvent] = []
    for line in lines[-retention * 2 :]:
        try:
            raw = json.loads(line)
            if not isinstance(raw, Mapping):
                continue
            result.append(
                OperationalEvent.from_mapping(raw, queue_source=queue_source)
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return sorted(
        {event.event_id: event for event in result}.values(),
        key=lambda event: event.timestamp,
        reverse=True,
    )[:retention]


def read_queue_observability(
    path: Path,
    *,
    retention: int = LOG_RETENTION,
) -> tuple[list[OperationalEvent], dict[str, Any]]:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise RuntimeError("queue observability source exceeds its safe size limit")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("queue observability source could not be read") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise RuntimeError("queue observability source has an unsupported schema")
    retention_contract = raw.get("retention")
    if (
        not isinstance(retention_contract, Mapping)
        or retention_contract.get("order") != "newest_first"
        or retention_contract.get("storage") != "atomic_file"
        or not isinstance(retention_contract.get("max_events"), int)
        or not 1 <= retention_contract["max_events"] <= 100
    ):
        raise RuntimeError("queue observability retention contract is invalid")
    raw_events = raw.get("events")
    if not isinstance(raw_events, list):
        raise RuntimeError("queue observability events are invalid")
    events: list[OperationalEvent] = []
    for raw_event in raw_events[: min(retention, 100)]:
        try:
            if not isinstance(raw_event, Mapping) or set(raw_event) != {
                "timestamp",
                "component",
                "severity",
                "message",
            }:
                continue
            events.append(
                OperationalEvent.from_mapping(raw_event, queue_source=True)
            )
        except (TypeError, ValueError):
            continue
    events = sorted(
        {event.event_id: event for event in events}.values(),
        key=lambda event: event.timestamp,
        reverse=True,
    )
    health = raw.get("health")
    return events, _safe_queue_health(health)


def read_queue_health(
    url: str = "http://127.0.0.1:4181/api/observability",
    *,
    timeout: float = 0.35,
) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout) as response:
            raw = json.loads(response.read(MAX_REQUEST_BYTES))
    except (OSError, URLError, json.JSONDecodeError, ValueError):
        return {
            "status": "unavailable",
            "broker_connected": False,
            "message": "Event Queue Reader status is unavailable. GTasks remains available.",
        }
    if (
        not isinstance(raw, Mapping)
        or raw.get("schema_version") != 1
        or not isinstance(raw.get("health"), Mapping)
    ):
        return {
            "status": "unavailable",
            "broker_connected": False,
            "message": "Event Queue Reader status is unavailable. GTasks remains available.",
        }
    return _safe_queue_health(raw["health"])


def _safe_queue_health(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    connected = raw.get("broker_connected") is True
    last_error = raw.get("last_error_code")
    safe_error = last_error if last_error in QUEUE_SAFE_ERROR_CODES else None
    return {
        "status": "connected" if connected else "degraded",
        "broker_connected": connected,
        "pending": _safe_count(raw.get("pending")),
        "ack_pending": _safe_count(raw.get("ack_pending")),
        "redelivered": _safe_count(raw.get("redelivered")),
        "last_error_code": safe_error,
        "message": (
            "Event Queue Reader is connected."
            if connected
            else "Event Queue Reader is degraded. GTasks remains available."
        ),
    }


MAX_REQUEST_BYTES = 64 * 1024


def _safe_count(value: Any) -> int:
    return value if isinstance(value, int) and 0 <= value <= 1_000_000_000 else 0


class OperationalLogReader:
    def __init__(
        self,
        *,
        gtasks_store: OperationalLogStore | None = None,
        queue_path: Path | None = None,
        queue_health: Callable[[], dict[str, Any]] | None = None,
        retention: int = LOG_RETENTION,
    ) -> None:
        self.gtasks_store = gtasks_store or OperationalLogStore(retention=retention)
        self.queue_path = queue_path or default_queue_log_path()
        self.queue_health = queue_health or read_queue_health
        self.retention = retention

    def append_gtasks(
        self,
        *,
        severity: str,
        message: str,
        now: datetime | None = None,
    ) -> None:
        try:
            self.gtasks_store.append(
                component="gtasks",
                severity=severity,
                message=message,
                now=now,
            )
        except (OSError, RuntimeError, ValueError):
            return

    def page(
        self,
        *,
        severity: str | None,
        component: str | None,
        cursor: int,
        limit: int,
    ) -> dict[str, Any]:
        source_errors: list[dict[str, str]] = []
        try:
            events = self.gtasks_store.read()
        except RuntimeError:
            events = []
            source_errors.append(
                {
                    "component": "gtasks",
                    "message": "GTasks operational history is temporarily unavailable.",
                }
            )
        persisted_queue_status: dict[str, Any] = {}
        try:
            queue_events, persisted_queue_status = read_queue_observability(
                self.queue_path,
                retention=self.retention,
            )
            events.extend(queue_events)
        except RuntimeError:
            source_errors.append(
                {
                    "component": "queue_reader",
                    "message": (
                        "Event Queue Reader history is temporarily unavailable. "
                        "GTasks remains available."
                    ),
                }
            )
        events = sorted(
            {event.event_id: event for event in events}.values(),
            key=lambda event: event.timestamp,
            reverse=True,
        )[: self.retention]
        components = sorted({event.component for event in events})
        filtered = [
            event
            for event in events
            if (severity is None or event.severity == severity)
            and (component is None or event.component == component)
        ]
        page = filtered[cursor : cursor + limit]
        next_cursor = cursor + len(page)
        try:
            queue_status = self.queue_health()
        except Exception:
            queue_status = {
                "status": "unavailable",
                "broker_connected": False,
                "message": (
                    "Event Queue Reader status is unavailable. "
                    "GTasks remains available."
                ),
            }
        if (
            persisted_queue_status
            and (
                not queue_status
                or queue_status.get("status") == "unavailable"
            )
        ):
            queue_status = {
                **persisted_queue_status,
                "status": "last_known",
                "message": (
                    "Showing the Event Queue Reader’s last known status. "
                    "GTasks remains available."
                ),
            }
        return {
            "events": [event.to_dict() for event in page],
            "total": len(filtered),
            "next_cursor": next_cursor if next_cursor < len(filtered) else None,
            "retention_limit": self.retention,
            "components": components,
            "severities": list(SEVERITIES),
            "queue_reader": queue_status,
            "source_errors": source_errors,
            "read_only": True,
        }
