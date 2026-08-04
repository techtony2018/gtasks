from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol

from .contract import ContractError, HandlerRegistry, parse_event
from .handler import (
    EXPLICIT_JOB_APPLICATION_TASK,
    JobAppliedProcessor,
    ProcessingResult,
    ProcessingStatus,
)
from .store import EventStore, TerminalRecord


BACKOFF_SECONDS = (30, 60, 300, 900, 3600)
MAX_DELIVER = len(BACKOFF_SECONDS)


class DlqPublisher(Protocol):
    async def publish_terminal(self, record: TerminalRecord) -> None: ...


class EventWorker:
    def __init__(
        self,
        *,
        processor: JobAppliedProcessor,
        store: EventStore,
        dlq: DlqPublisher,
        heartbeat_seconds: float = 10,
    ) -> None:
        self.processor = processor
        self.store = store
        self.dlq = dlq
        self.heartbeat_seconds = heartbeat_seconds
        self.registry = HandlerRegistry()
        self.registry.register("job_applied", 1, processor)

    async def _heartbeat(self, message: Any, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
            except TimeoutError:
                await message.in_progress()

    @staticmethod
    def _safe_identity(raw: bytes) -> tuple[str, str, int, str]:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ("unknown", "unknown", 0, "unknown")
        if not isinstance(value, dict):
            return ("unknown", "unknown", 0, "unknown")
        source = value.get("source")
        source_client = (
            source.get("client_id")
            if isinstance(source, dict) and isinstance(source.get("client_id"), str)
            else "unknown"
        )
        return (
            value.get("event_id")
            if isinstance(value.get("event_id"), str)
            else "unknown",
            value.get("event_type")
            if isinstance(value.get("event_type"), str)
            else "unknown",
            value.get("schema_version")
            if isinstance(value.get("schema_version"), int)
            else 0,
            source_client,
        )

    def _terminal(
        self,
        message: Any,
        *,
        error_code: str,
        attempts: int,
    ) -> TerminalRecord:
        event_id, event_type, schema_version, source = self._safe_identity(
            message.data
        )
        return TerminalRecord(
            event_id=event_id,
            event_type=event_type,
            schema_version=schema_version,
            source_client_id=source,
            error_code=error_code,
            attempts=attempts,
            original_stream="GTASKS_EVENTS",
            original_stream_sequence=int(message.metadata.sequence.stream),
            failed_at=datetime.now(timezone.utc),
        )

    async def _terminalize(
        self,
        message: Any,
        *,
        error_code: str,
        attempts: int,
        fingerprint: str | None = None,
    ) -> None:
        record = self._terminal(
            message,
            error_code=error_code,
            attempts=attempts,
        )
        try:
            await self.dlq.publish_terminal(record)
            self.store.record_terminal(record)
            self.store.record_activity(
                event_id=record.event_id,
                fingerprint=fingerprint or hashlib.sha256(message.data).hexdigest(),
                source_client_id=record.source_client_id,
                disposition="terminal_failure",
                error_code=error_code,
                task_slug=EXPLICIT_JOB_APPLICATION_TASK,
                recorded_at=record.failed_at,
            )
            await message.ack()
        except Exception:
            await message.nak(delay=BACKOFF_SECONDS[-1])

    async def handle_message(
        self,
        message: Any,
    ) -> ProcessingResult | None:
        attempts = int(message.metadata.num_delivered)
        event_id, event_type, schema_version, _source = self._safe_identity(
            message.data
        )
        try:
            self.registry.resolve(event_type, schema_version)
        except ContractError:
            await self._terminalize(
                message,
                error_code="rejected_unknown_type",
                attempts=attempts,
            )
            return
        try:
            event = parse_event(message.data, message.subject)
        except ContractError:
            await self._terminalize(
                message,
                error_code="invalid_event",
                attempts=attempts,
            )
            return

        await message.in_progress()
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(message, stop))
        try:
            result: ProcessingResult = await asyncio.to_thread(
                self.processor.process, event
            )
        finally:
            stop.set()
            await heartbeat

        if result.status in {ProcessingStatus.ACCEPTED, ProcessingStatus.DUPLICATE}:
            await message.ack()
            return result
        if result.retriable and attempts < MAX_DELIVER:
            self.store.record_activity(
                event_id=event.event_id,
                fingerprint=event.fingerprint,
                source_client_id=event.source.client_id,
                disposition="retrying",
                error_code=result.error_code,
                task_slug=EXPLICIT_JOB_APPLICATION_TASK,
                recorded_at=datetime.now(timezone.utc),
            )
            await message.nak(delay=BACKOFF_SECONDS[max(0, attempts - 1)])
            return result
        await self._terminalize(
            message,
            error_code=result.error_code or "handler_failed",
            attempts=attempts,
            fingerprint=event.fingerprint,
        )
        return result
