from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import StrEnum
from threading import RLock
from typing import Callable, Protocol

from .contract import JobAppliedV1
from .store import ClaimConflict, ClaimDisposition, EventStore
from ..job_application_binding import (
    JOB_APPLIED_BOUND_TASK_SLUG,
    JOB_APPLIED_TIMEZONE,
    JOB_APPLIED_UNIT,
)


HANDLER_VERSION = "job_applied.v1"
DAILY_JOB_APPLICATION_TIMEZONE = JOB_APPLIED_TIMEZONE
DAILY_JOB_APPLICATION_UNIT = JOB_APPLIED_UNIT
DEFAULT_JOB_APPLICATION_TARGET = 5
DAILY_JOB_APPLICATION_BINDING = "job_applied"
EXPLICIT_JOB_APPLICATION_TASK = JOB_APPLIED_BOUND_TASK_SLUG


class HandlerFailure(RuntimeError):
    def __init__(self, code: str, *, retriable: bool) -> None:
        self.code = code
        self.retriable = retriable
        super().__init__(code)


class ProcessingStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    status: ProcessingStatus
    event_id: str
    event_type: str
    schema_version: int
    handler_version: str
    error_code: str | None = None
    retriable: bool = False

    def safe_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "handler_version": self.handler_version,
            "error_code": self.error_code,
            "retriable": self.retriable,
        }


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    timezone: str
    unit: str
    event_binding: str


DAILY_JOB_APPLICATION_POLICY = QuotaPolicy(
    timezone=DAILY_JOB_APPLICATION_TIMEZONE,
    unit=DAILY_JOB_APPLICATION_UNIT,
    event_binding=DAILY_JOB_APPLICATION_BINDING,
)


@dataclass(frozen=True, slots=True)
class QuotaTaskState:
    slug: str
    active: bool
    status: str
    day: date
    unit: str
    target: int
    baseline_count: int
    evidence: frozenset[str]
    receipt_ids: frozenset[str]
    completed_count: int
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    slug: str
    event_id: str
    source_client_id: str
    job_source: str
    job_id: str
    title: str
    company: str
    location: str
    url: str
    status: str
    applied_local_date: date
    committed_at: datetime
    evidence_source: str


@dataclass(frozen=True, slots=True)
class JobAppliedEffect:
    application_slug: str
    task_slug: str
    task_day: date
    prior_progress: int
    resulting_progress: int
    verified_count: int
    baseline_count: int
    target: int
    disposition: str


class JobAppliedAdapter(Protocol):
    def get_quota_task(self, slug: str) -> QuotaTaskState: ...

    def upsert_application(self, record: ApplicationRecord) -> None: ...

    def ensure_link(self, from_slug: str, to_slug: str, link_type: str) -> None: ...

    def set_quota_progress(
        self,
        slug: str,
        *,
        day: date,
        unit: str,
        target: int,
        evidence: frozenset[str],
        receipt_ids: frozenset[str],
        occurred_at: datetime,
    ) -> QuotaTaskState: ...

    def complete_quota_task(
        self,
        slug: str,
        *,
        completed_at: datetime,
    ) -> QuotaTaskState: ...

    def verify(
        self,
        application: ApplicationRecord,
        task: QuotaTaskState,
    ) -> None: ...


def application_slug(job_source: str, job_id: str) -> str:
    source = re.sub(r"[^a-z0-9]+", "-", job_source.strip().lower()).strip("-")
    identifier = re.sub(r"[^a-z0-9]+", "-", job_id.strip().lower()).strip("-")
    source = source[:48] or "source"
    identifier = identifier[:72] or "job"
    digest = hashlib.sha256(
        f"{job_source.strip().lower()}\0{job_id.strip().lower()}".encode("utf-8")
    ).hexdigest()[:12]
    return f"applications/{source}-{identifier}-{digest}"


class JobAppliedHandler:
    def __init__(
        self,
        *,
        adapter: JobAppliedAdapter,
        clock: Callable[[], datetime] | None = None,
        task_slug: str = EXPLICIT_JOB_APPLICATION_TASK,
    ) -> None:
        self.adapter = adapter
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.task_slug = task_slug
        self._lock = RLock()

    def handle(self, event: JobAppliedV1) -> JobAppliedEffect:
        with self._lock:
            return self._handle(event)

    def _handle(self, event: JobAppliedV1) -> JobAppliedEffect:
        policy = DAILY_JOB_APPLICATION_POLICY
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise HandlerFailure("quota_clock_invalid", retriable=True)
        task = self.adapter.get_quota_task(self.task_slug)
        if task.unit != policy.unit or task.target <= 0:
            raise HandlerFailure("quota_task_contract_invalid", retriable=True)

        identity = event.payload.application_identity
        snapshot = event.payload.job_snapshot
        evidence = event.payload.status_evidence
        record = ApplicationRecord(
            slug=application_slug(identity.job_source, identity.job_id),
            event_id=event.event_id,
            source_client_id=event.source.client_id,
            job_source=identity.job_source,
            job_id=identity.job_id,
            title=snapshot.title,
            company=snapshot.company,
            location=snapshot.location,
            url=snapshot.url,
            status=evidence.status,
            applied_local_date=event.payload.applied_local_date,
            committed_at=evidence.committed_at,
            evidence_source=evidence.source,
        )
        if not task.active:
            exact_completed_replay = (
                task.status == "completed"
                and record.slug in task.evidence
                and event.event_id in task.receipt_ids
            )
            if not exact_completed_replay:
                raise HandlerFailure("quota_task_not_active", retriable=True)
            self.adapter.verify(record, task)
            return JobAppliedEffect(
                application_slug=record.slug,
                task_slug=task.slug,
                task_day=task.day,
                prior_progress=task.completed_count,
                resulting_progress=task.completed_count,
                verified_count=len(task.receipt_ids),
                baseline_count=task.baseline_count,
                target=task.target,
                disposition="duplicate_noop",
            )
        if task.completed_count > task.target:
            raise HandlerFailure("quota_progress_invalid", retriable=True)
        if task.completed_count >= task.target and record.slug not in task.evidence:
            self.adapter.complete_quota_task(task.slug, completed_at=now)
            raise HandlerFailure("quota_task_full", retriable=True)
        self.adapter.upsert_application(record)
        self.adapter.ensure_link(record.slug, task.slug, "evidence_for")
        self.adapter.ensure_link(task.slug, record.slug, "has_evidence")
        intended_evidence = frozenset((*task.evidence, record.slug))
        intended_receipts = (
            frozenset((*task.receipt_ids, event.event_id))
            if record.slug not in task.evidence
            else task.receipt_ids
        )
        intended_task = self.adapter.set_quota_progress(
            task.slug,
            day=task.day,
            unit=policy.unit,
            target=task.target,
            evidence=intended_evidence,
            receipt_ids=intended_receipts,
            occurred_at=now,
        )
        if intended_task.completed_count == intended_task.target:
            intended_task = self.adapter.complete_quota_task(
                intended_task.slug,
                completed_at=now,
            )
        self.adapter.verify(record, intended_task)
        return JobAppliedEffect(
            application_slug=record.slug,
            task_slug=intended_task.slug,
            task_day=intended_task.day,
            prior_progress=task.completed_count,
            resulting_progress=intended_task.completed_count,
            verified_count=len(intended_task.receipt_ids),
            baseline_count=intended_task.baseline_count,
            target=intended_task.target,
            disposition=(
                "incremented"
                if intended_task.completed_count > task.completed_count
                else "duplicate_noop"
            ),
        )


class JobAppliedProcessor:
    def __init__(
        self,
        *,
        store: EventStore,
        handler: JobAppliedHandler,
        clock: Callable[[], datetime] | None = None,
        claim_lease_seconds: int = 60,
    ) -> None:
        self.store = store
        self.handler = handler
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.claim_lease_seconds = claim_lease_seconds

    def process(self, event: JobAppliedV1) -> ProcessingResult:
        common = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "handler_version": HANDLER_VERSION,
        }
        try:
            claim = self.store.claim(
                event_id=event.event_id,
                source_client_id=event.source.client_id,
                idempotency_key=event.idempotency_key,
                fingerprint=event.fingerprint,
                now=self.clock(),
                lease_seconds=self.claim_lease_seconds,
            )
        except ClaimConflict:
            self.store.record_activity(
                event_id=event.event_id,
                fingerprint=event.fingerprint,
                source_client_id=event.source.client_id,
                disposition="validation_failed",
                error_code="idempotency_conflict",
                task_slug=self.handler.task_slug,
                recorded_at=self.clock(),
            )
            return ProcessingResult(
                status=ProcessingStatus.REJECTED,
                error_code="idempotency_conflict",
                retriable=False,
                **common,
            )
        if claim.disposition == ClaimDisposition.DUPLICATE:
            self.store.record_activity(
                event_id=event.event_id,
                fingerprint=event.fingerprint,
                source_client_id=event.source.client_id,
                disposition="duplicate_noop",
                task_slug=self.handler.task_slug,
                recorded_at=self.clock(),
            )
            return ProcessingResult(status=ProcessingStatus.DUPLICATE, **common)
        if claim.disposition == ClaimDisposition.BUSY:
            self.store.record_activity(
                event_id=event.event_id,
                fingerprint=event.fingerprint,
                source_client_id=event.source.client_id,
                disposition="retrying",
                error_code="processing_lease_busy",
                task_slug=self.handler.task_slug,
                recorded_at=self.clock(),
            )
            return ProcessingResult(
                status=ProcessingStatus.FAILED,
                error_code="processing_lease_busy",
                retriable=True,
                **common,
            )
        try:
            effect = self.handler.handle(event)
        except HandlerFailure as exc:
            self.store.record_activity(
                event_id=event.event_id,
                fingerprint=event.fingerprint,
                source_client_id=event.source.client_id,
                disposition=exc.code,
                error_code=exc.code,
                task_slug=self.handler.task_slug,
                scope_day=event.payload.applied_local_date.isoformat(),
                timezone_name=event.timezone,
                recorded_at=self.clock(),
            )
            self.store.release(event.event_id, now=self.clock())
            return ProcessingResult(
                status=(
                    ProcessingStatus.FAILED
                    if exc.retriable
                    else ProcessingStatus.REJECTED
                ),
                error_code=exc.code,
                retriable=exc.retriable,
                **common,
            )
        except Exception:
            self.store.record_activity(
                event_id=event.event_id,
                fingerprint=event.fingerprint,
                source_client_id=event.source.client_id,
                disposition="retrying",
                error_code="handler_dependency_failure",
                task_slug=self.handler.task_slug,
                recorded_at=self.clock(),
            )
            self.store.release(event.event_id, now=self.clock())
            return ProcessingResult(
                status=ProcessingStatus.FAILED,
                error_code="handler_dependency_failure",
                retriable=True,
                **common,
            )
        self.store.record_activity(
            event_id=event.event_id,
            fingerprint=event.fingerprint,
            source_client_id=event.source.client_id,
            disposition=effect.disposition,
            task_slug=effect.task_slug,
            scope_day=effect.task_day.isoformat(),
            timezone_name=DAILY_JOB_APPLICATION_TIMEZONE,
            prior_progress=effect.prior_progress,
            resulting_progress=effect.resulting_progress,
            verified_count=effect.verified_count,
            baseline_count=effect.baseline_count,
            target_value=effect.target,
            recorded_at=self.clock(),
        )
        self.store.accept(
            event.event_id,
            handler_version=HANDLER_VERSION,
            now=self.clock(),
        )
        return ProcessingResult(status=ProcessingStatus.ACCEPTED, **common)


class InMemoryJobAppliedAdapter:
    """Fixture adapter for tests; it cannot connect to or mutate live GBrain."""

    def __init__(self) -> None:
        self.applications: dict[str, ApplicationRecord] = {}
        self.tasks: dict[str, QuotaTaskState] = {}
        self.links: set[tuple[str, str, str]] = set()
        self.application_write_count = 0
        self.progress_write_count = 0
        self.completion_write_count = 0
        self.fail_readback_once = False

    def add_quota_task(
        self,
        *,
        slug: str,
        task_day: str | None = None,
        status: str | None = None,
        progress_metric: dict | None = None,
        event_progress: dict | None = None,
        day: str | None = None,
        unit: str = DAILY_JOB_APPLICATION_UNIT,
        target: int = DEFAULT_JOB_APPLICATION_TARGET,
        active: bool | None = None,
    ) -> None:
        selected_day = task_day or day
        if selected_day is None:
            raise ValueError("task_day is required")
        selected_status = status or ("active" if active is not False else "completed")
        raw_evidence = (event_progress or {}).get("evidence_slugs", [])
        raw_receipts = (event_progress or {}).get("receipt_ids", [])
        baseline_count = (event_progress or {}).get("baseline_count", 0)
        self.tasks[slug] = QuotaTaskState(
            slug=slug,
            active=selected_status in {"planned", "active", "blocked"},
            status=selected_status,
            day=date.fromisoformat(selected_day),
            unit=unit,
            target=target,
            baseline_count=baseline_count,
            evidence=frozenset(raw_evidence),
            receipt_ids=frozenset(raw_receipts),
            completed_count=baseline_count + len(raw_evidence),
            completed_at=None,
        )

    def get_quota_task(self, slug: str) -> QuotaTaskState:
        try:
            return self.tasks[slug]
        except KeyError as exc:
            raise HandlerFailure("quota_task_missing", retriable=True) from exc

    def upsert_application(self, record: ApplicationRecord) -> None:
        if self.applications.get(record.slug) != record:
            self.applications[record.slug] = record
            self.application_write_count += 1

    def ensure_link(self, from_slug: str, to_slug: str, link_type: str) -> None:
        self.links.add((from_slug, to_slug, link_type))

    def set_quota_progress(
        self,
        slug: str,
        *,
        day: date,
        unit: str,
        target: int,
        evidence: frozenset[str],
        receipt_ids: frozenset[str],
        occurred_at: datetime,
    ) -> QuotaTaskState:
        current = self.tasks[slug]
        updated = replace(
            current,
            day=day,
            unit=unit,
            target=target,
            evidence=evidence,
            receipt_ids=receipt_ids,
            completed_count=current.baseline_count + len(evidence),
        )
        if updated != current:
            self.tasks[slug] = updated
            self.progress_write_count += 1
        return updated

    def complete_quota_task(
        self,
        slug: str,
        *,
        completed_at: datetime,
    ) -> QuotaTaskState:
        current = self.tasks[slug]
        if current.status == "completed":
            return current
        updated = replace(
            current,
            active=False,
            status="completed",
            completed_at=completed_at,
        )
        self.tasks[slug] = updated
        self.completion_write_count += 1
        return updated

    def verify(
        self,
        application: ApplicationRecord,
        task: QuotaTaskState,
    ) -> None:
        if self.fail_readback_once:
            self.fail_readback_once = False
            raise HandlerFailure("gbrain_readback_failed", retriable=True)
        if self.applications.get(application.slug) != application:
            raise HandlerFailure("gbrain_application_readback_mismatch", retriable=True)
        if (application.slug, task.slug, "evidence_for") not in self.links:
            raise HandlerFailure("gbrain_forward_link_missing", retriable=True)
        if (task.slug, application.slug, "has_evidence") not in self.links:
            raise HandlerFailure("gbrain_reverse_link_missing", retriable=True)
        if self.tasks.get(task.slug) != task:
            raise HandlerFailure("gbrain_progress_readback_mismatch", retriable=True)
        if task.completed_count == task.target and task.status != "completed":
            raise HandlerFailure("gbrain_completion_readback_mismatch", retriable=True)

    def progress_evidence(self, slug: str) -> set[str]:
        return set(self.tasks[slug].evidence)
