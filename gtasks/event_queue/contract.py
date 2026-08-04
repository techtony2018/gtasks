from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_EVENT_BYTES = 64 * 1024
JOB_APPLIED_SUBJECT = "gtasks.events.job_applied.v1"
AUTHORIZED_SOURCES = frozenset({"career-path"})


class ContractError(ValueError):
    """A deterministic event-contract rejection safe to terminalize."""

    code = "invalid_event"


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    return value


def _exact_fields(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    label: str,
) -> None:
    missing = required - value.keys()
    if missing:
        raise ContractError(f"missing {label} field: {sorted(missing)[0]}")
    unknown = value.keys() - required
    if unknown:
        noun = "envelope" if label == "envelope" else label
        raise ContractError(f"unknown {noun} field: {sorted(unknown)[0]}")


def _text(value: Any, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ContractError(f"{label} exceeds {maximum} characters")
    if any(ord(char) < 32 for char in normalized):
        raise ContractError(f"{label} contains control characters")
    return normalized


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be RFC 3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must include a timezone offset")
    return parsed


def _date(value: Any, label: str) -> date:
    text = _text(value, label, maximum=10)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ContractError(f"{label} must be YYYY-MM-DD") from exc


@dataclass(frozen=True, slots=True)
class EventSource:
    client_id: str
    instance_id: str


@dataclass(frozen=True, slots=True)
class ApplicationIdentity:
    job_source: str
    job_id: str


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    title: str
    company: str
    location: str
    url: str


@dataclass(frozen=True, slots=True)
class StatusEvidence:
    status: str
    committed_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class JobAppliedPayload:
    application_identity: ApplicationIdentity
    job_snapshot: JobSnapshot
    applied_local_date: date
    status_evidence: StatusEvidence


@dataclass(frozen=True, slots=True)
class JobAppliedV1:
    event_id: str
    idempotency_key: str
    event_type: str
    schema_version: int
    source: EventSource
    occurred_at: datetime
    timezone: str
    payload: JobAppliedPayload
    fingerprint: str

    @property
    def subject(self) -> str:
        return JOB_APPLIED_SUBJECT


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], object] = {}

    def register(self, event_type: str, schema_version: int, handler: object) -> None:
        key = (event_type, schema_version)
        if key in self._handlers:
            raise ValueError(f"handler already registered for {key!r}")
        self._handlers[key] = handler

    def resolve(self, event_type: str, schema_version: int) -> object:
        try:
            return self._handlers[(event_type, schema_version)]
        except KeyError as exc:
            raise ContractError(
                f"unsupported event type/version: {event_type} v{schema_version}"
            ) from exc


def _parse_source(raw: Any) -> EventSource:
    value = _object(raw, "source")
    _exact_fields(
        value,
        required=frozenset({"client_id", "instance_id"}),
        label="source",
    )
    client_id = _text(value["client_id"], "source.client_id", maximum=64)
    if client_id not in AUTHORIZED_SOURCES:
        raise ContractError(f"source client is not authorized: {client_id}")
    return EventSource(
        client_id=client_id,
        instance_id=_text(value["instance_id"], "source.instance_id", maximum=128),
    )


def _parse_payload(raw: Any, timezone_name: str) -> JobAppliedPayload:
    value = _object(raw, "payload")
    _exact_fields(
        value,
        required=frozenset(
            {
                "application_identity",
                "job_snapshot",
                "applied_local_date",
                "status_evidence",
            }
        ),
        label="payload",
    )

    identity = _object(value["application_identity"], "application_identity")
    _exact_fields(
        identity,
        required=frozenset({"job_source", "job_id"}),
        label="application_identity",
    )
    parsed_identity = ApplicationIdentity(
        job_source=_text(
            identity["job_source"], "application_identity.job_source", maximum=64
        ).lower(),
        job_id=_text(identity["job_id"], "application_identity.job_id", maximum=256),
    )

    snapshot = _object(value["job_snapshot"], "job_snapshot")
    _exact_fields(
        snapshot,
        required=frozenset({"title", "company", "location", "url"}),
        label="job_snapshot",
    )
    url = _text(snapshot["url"], "job_snapshot.url", maximum=2048)
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ContractError("job_snapshot.url must be an absolute HTTP(S) URL")
    parsed_snapshot = JobSnapshot(
        title=_text(snapshot["title"], "job_snapshot.title", maximum=256),
        company=_text(snapshot["company"], "job_snapshot.company", maximum=256),
        location=_text(snapshot["location"], "job_snapshot.location", maximum=256),
        url=url,
    )

    evidence = _object(value["status_evidence"], "status_evidence")
    _exact_fields(
        evidence,
        required=frozenset({"status", "committed_at", "source"}),
        label="status_evidence",
    )
    status = _text(evidence["status"], "status_evidence.status", maximum=32)
    if status != "applied":
        raise ContractError("status_evidence.status must be applied")
    committed_at = _timestamp(evidence["committed_at"], "status_evidence.committed_at")
    applied_local_date = _date(value["applied_local_date"], "applied_local_date")
    zone = ZoneInfo(timezone_name)
    if committed_at.astimezone(zone).date() != applied_local_date:
        raise ContractError(
            "applied_local_date must match status_evidence.committed_at in timezone"
        )

    return JobAppliedPayload(
        application_identity=parsed_identity,
        job_snapshot=parsed_snapshot,
        applied_local_date=applied_local_date,
        status_evidence=StatusEvidence(
            status=status,
            committed_at=committed_at,
            source=_text(evidence["source"], "status_evidence.source", maximum=128),
        ),
    )


def parse_event(raw: bytes, subject: str) -> JobAppliedV1:
    if len(raw) > MAX_EVENT_BYTES:
        raise ContractError(f"event exceeds {MAX_EVENT_BYTES} bytes")
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContractError("event must be valid UTF-8 JSON") from exc
    value = _object(decoded, "envelope")
    _exact_fields(
        value,
        required=frozenset(
            {
                "event_id",
                "idempotency_key",
                "event_type",
                "schema_version",
                "source",
                "occurred_at",
                "timezone",
                "payload",
            }
        ),
        label="envelope",
    )
    event_type = _text(value["event_type"], "event_type", maximum=64)
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ContractError("schema_version must be an integer")
    expected_subject = f"gtasks.events.{event_type}.v{schema_version}"
    if subject != expected_subject or subject != JOB_APPLIED_SUBJECT:
        raise ContractError(
            f"subject {subject!r} does not match supported event type/version"
        )
    if (event_type, schema_version) != ("job_applied", 1):
        raise ContractError(
            f"unsupported event type/version: {event_type} v{schema_version}"
        )
    timezone_name = _text(value["timezone"], "timezone", maximum=64)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ContractError("timezone must be a valid IANA timezone") from exc
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return JobAppliedV1(
        event_id=_text(value["event_id"], "event_id", maximum=128),
        idempotency_key=_text(
            value["idempotency_key"], "idempotency_key", maximum=256
        ),
        event_type=event_type,
        schema_version=schema_version,
        source=_parse_source(value["source"]),
        occurred_at=_timestamp(value["occurred_at"], "occurred_at"),
        timezone=timezone_name,
        payload=_parse_payload(value["payload"], timezone_name),
        fingerprint=hashlib.sha256(canonical).hexdigest(),
    )
