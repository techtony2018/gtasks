from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Mapping, Protocol

from .handler import (
    ApplicationRecord,
    DAILY_JOB_APPLICATION_BINDING,
    DAILY_JOB_APPLICATION_TIMEZONE,
    DAILY_JOB_APPLICATION_UNIT,
    HandlerFailure,
    QuotaPolicy,
    QuotaTaskState,
)
from ..gbrain import (
    GBrainAdapter as CanonicalTaskAdapter,
    GBrainCommandError,
    SubprocessCommandRunner as CanonicalCommandRunner,
)


ACTIVE_ROOT = "collections/tonys-tasks"


class GBrainProtocolError(RuntimeError):
    pass


class GBrainPageNotFound(GBrainProtocolError):
    pass


class CommandRunner(Protocol):
    def run(self, tool: str, params: dict[str, Any]) -> object: ...


class SubprocessCommandRunner:
    def __init__(self, executable: str = "gbrain", timeout_seconds: float = 30) -> None:
        self._runner = CanonicalCommandRunner(executable, timeout_seconds)

    def run(self, tool: str, params: dict[str, Any]) -> object:
        try:
            return self._runner.run(tool, params)
        except GBrainCommandError as exc:
            if tool == "get_page" and "page_not_found" in str(exc):
                raise GBrainPageNotFound("page_not_found")
            raise


def _render_page(frontmatter: Mapping[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(
            f"{json.dumps(str(key), ensure_ascii=False)}: "
            f"{json.dumps(value, ensure_ascii=False)}"
        )
    lines.extend(["---", "", body.rstrip(), ""])
    return "\n".join(lines)


def _link_records(raw: object) -> list[dict[str, str | None]]:
    if not isinstance(raw, list):
        raise GBrainProtocolError("get_links did not return a list")
    result: list[dict[str, str | None]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        source = item.get("from_slug")
        target = item.get("to_slug")
        link_type = item.get("link_type")
        if all(isinstance(value, str) for value in (source, target, link_type)):
            link_source = item.get("link_source")
            result.append(
                {
                    "from_slug": source,
                    "to_slug": target,
                    "link_type": link_type,
                    "link_source": link_source if isinstance(link_source, str) else None,
                }
            )
    return result


def _links(raw: object) -> set[tuple[str, str, str]]:
    return {
        (str(item["from_slug"]), str(item["to_slug"]), str(item["link_type"]))
        for item in _link_records(raw)
    }


def _application_frontmatter(record: ApplicationRecord) -> dict[str, Any]:
    return {
        "type": "job_application",
        "title": f"{record.title} at {record.company}",
        "status": record.status,
        "event_id": record.event_id,
        "source_client_id": record.source_client_id,
        "job_source": record.job_source,
        "job_id": record.job_id,
        "job_title": record.title,
        "company": record.company,
        "location": record.location,
        "url": record.url,
        "applied_local_date": record.applied_local_date.isoformat(),
        "committed_at": record.committed_at.isoformat(),
        "evidence_source": record.evidence_source,
    }


def _application_page_matches(
    page: Mapping[str, Any],
    record: ApplicationRecord,
) -> bool:
    expected = _application_frontmatter(record)
    frontmatter = page.get("frontmatter")
    if not isinstance(frontmatter, Mapping):
        return False
    actual = dict(frontmatter)
    actual.setdefault("type", page.get("type"))
    actual.setdefault("title", page.get("title"))
    return all(actual.get(key) == value for key, value in expected.items())


def _application_body(record: ApplicationRecord) -> str:
    return "\n".join(
        [
            f"# {record.title} at {record.company}",
            "",
            "Canonical job-application evidence ingested by GTasks.",
        ]
    )


class GBrainJobAppliedAdapter:
    """Bounded, handler-specific GBrain mutations with exact readback."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or SubprocessCommandRunner()

    def _quota_task_from_page(
        self,
        page: Mapping[str, Any],
        links: set[tuple[str, str, str]],
    ) -> QuotaTaskState:
        slug = page.get("slug")
        frontmatter = page.get("frontmatter")
        if not isinstance(slug, str) or not isinstance(frontmatter, Mapping):
            raise HandlerFailure("quota_task_contract_invalid", retriable=True)
        metric = frontmatter.get("progress_metric")
        required_metric_keys = {
            "kind",
            "unit",
            "target",
            "current",
            "event_binding",
            "auto_complete",
            "task_day",
            "timezone",
        }
        if (
            not isinstance(metric, Mapping)
            or not required_metric_keys.issubset(metric)
            or set(metric) - (required_metric_keys | {"label"})
        ):
            raise HandlerFailure("quota_task_contract_invalid", retriable=True)
        label = metric.get("label")
        if label is not None and (
            not isinstance(label, str) or not label.strip() or len(label) > 160
        ):
            raise HandlerFailure("quota_task_contract_invalid", retriable=True)
        try:
            task_day = date.fromisoformat(str(metric["task_day"]))
        except (TypeError, ValueError) as exc:
            raise HandlerFailure("quota_task_contract_invalid", retriable=True) from exc
        target = metric.get("target")
        current = metric.get("current")
        if (
            metric.get("kind") != "count"
            or metric.get("unit") != DAILY_JOB_APPLICATION_UNIT
            or metric.get("event_binding") != DAILY_JOB_APPLICATION_BINDING
            or metric.get("auto_complete") is not True
            or metric.get("timezone") != DAILY_JOB_APPLICATION_TIMEZONE
            or isinstance(target, bool)
            or not isinstance(target, int)
            or target <= 0
            or isinstance(current, bool)
            or not isinstance(current, int)
            or current < 0
            or current > target
        ):
            raise HandlerFailure("quota_task_contract_invalid", retriable=True)
        progress = frontmatter.get("event_progress")
        if (
            not isinstance(progress, Mapping)
            or not {"evidence_slugs", "receipt_ids"}.issubset(progress)
            or set(progress) - {"baseline_count", "evidence_slugs", "receipt_ids"}
        ):
            raise HandlerFailure("quota_progress_invalid", retriable=True)
        raw_evidence = progress.get("evidence_slugs")
        raw_receipts = progress.get("receipt_ids")
        baseline_count = progress.get("baseline_count", 0)
        if (
            not isinstance(raw_evidence, list)
            or not isinstance(raw_receipts, list)
            or isinstance(baseline_count, bool)
            or not isinstance(baseline_count, int)
            or baseline_count < 0
            or any(not isinstance(item, str) or not item for item in raw_evidence)
            or any(not isinstance(item, str) or not item for item in raw_receipts)
        ):
            raise HandlerFailure("quota_progress_invalid", retriable=True)
        evidence = frozenset(raw_evidence)
        receipt_ids = frozenset(raw_receipts)
        if (
            len(evidence) != len(raw_evidence)
            or len(receipt_ids) != len(raw_receipts)
            or len(evidence) != len(receipt_ids)
            or current != baseline_count + len(evidence)
        ):
            raise HandlerFailure("quota_progress_invalid", retriable=True)
        status = frontmatter.get("status")
        if status not in {"planned", "active", "blocked", "completed", "cancelled"}:
            raise HandlerFailure("quota_task_contract_invalid", retriable=True)
        completed_at = frontmatter.get("completed_at")
        if completed_at in {None, "", "none"}:
            parsed_completed_at = None
        elif isinstance(completed_at, str):
            try:
                parsed_completed_at = datetime.fromisoformat(
                    completed_at.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise HandlerFailure("quota_task_contract_invalid", retriable=True) from exc
        else:
            raise HandlerFailure("quota_task_contract_invalid", retriable=True)
        active = (
            page.get("type") == "task"
            and status in {"planned", "active", "blocked"}
            and (slug, ACTIVE_ROOT, "member_of") in links
        )
        return QuotaTaskState(
            slug=slug,
            active=active,
            status=status,
            day=task_day,
            unit=metric["unit"],
            target=target,
            baseline_count=baseline_count,
            evidence=evidence,
            receipt_ids=receipt_ids,
            completed_count=current,
            completed_at=parsed_completed_at,
        )

    def get_quota_task(self, slug: str) -> QuotaTaskState:
        page = self.runner.run("get_page", {"slug": slug})
        if not isinstance(page, Mapping):
            raise HandlerFailure("quota_task_missing", retriable=True)
        return self._quota_task_from_page(
            page,
            _links(self.runner.run("get_links", {"slug": slug})),
        )

    def upsert_application(self, record: ApplicationRecord) -> None:
        expected = _application_frontmatter(record)
        try:
            existing = self.runner.run("get_page", {"slug": record.slug})
        except GBrainPageNotFound:
            existing = None
        body = _application_body(record)
        frontmatter = dict(expected)
        if isinstance(existing, Mapping):
            raw_frontmatter = existing.get("frontmatter")
            if not isinstance(raw_frontmatter, Mapping):
                raise HandlerFailure("application_identity_conflict", retriable=False)
            existing_frontmatter = dict(raw_frontmatter)
            existing_frontmatter.setdefault("type", existing.get("type"))
            existing_frontmatter.setdefault("title", existing.get("title"))
            if (
                existing_frontmatter.get("type") != "job_application"
                or existing_frontmatter.get("job_source") != record.job_source
                or existing_frontmatter.get("job_id") != record.job_id
            ):
                raise HandlerFailure("application_identity_conflict", retriable=False)
            existing_body = existing.get("compiled_truth")
            if not isinstance(existing_body, str):
                raise HandlerFailure("application_identity_conflict", retriable=False)
            if _application_page_matches(existing, record):
                return
            frontmatter = {**existing_frontmatter, **expected}
            body = existing_body
        self.runner.run(
            "put_page",
            {
                "slug": record.slug,
                "content": _render_page(frontmatter, body),
            },
        )
        readback = self.runner.run("get_page", {"slug": record.slug})
        if not isinstance(readback, Mapping) or not _application_page_matches(
            readback, record
        ):
            raise HandlerFailure("gbrain_application_readback_mismatch", retriable=True)
        if (
            isinstance(existing, Mapping)
            and (
                not isinstance(readback.get("compiled_truth"), str)
                or str(readback.get("compiled_truth")).rstrip("\n")
                != body.rstrip("\n")
            )
        ):
            raise HandlerFailure("gbrain_application_readback_mismatch", retriable=True)

    def ensure_link(self, from_slug: str, to_slug: str, link_type: str) -> None:
        records = _link_records(self.runner.run("get_links", {"slug": from_slug}))
        matching = [
            item
            for item in records
            if (
                item["from_slug"], item["to_slug"], item["link_type"]
            ) == (from_slug, to_slug, link_type)
        ]
        if len(matching) > 1:
            raise HandlerFailure("gbrain_relationship_ambiguous", retriable=False)
        if matching:
            if matching[0]["link_source"] not in {None, "", "gtasks"}:
                raise HandlerFailure("gbrain_relationship_provenance_invalid", retriable=False)
            return
        self.runner.run(
            "add_link",
            {
                "from": from_slug,
                "to": to_slug,
                "link_type": link_type,
                "context": "Verified GTasks job_applied v1 evidence.",
                "link_source": "gtasks",
            },
        )
        readback = _link_records(self.runner.run("get_links", {"slug": from_slug}))
        matching = [
            item
            for item in readback
            if (
                item["from_slug"], item["to_slug"], item["link_type"]
            ) == (from_slug, to_slug, link_type)
        ]
        if len(matching) != 1 or matching[0]["link_source"] != "gtasks":
            raise HandlerFailure("gbrain_relationship_readback_mismatch", retriable=True)

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
        current = self.get_quota_task(slug)
        if (
            current.day != day
            or current.unit != unit
            or current.target != target
            or not current.active
            or len(evidence) != len(receipt_ids)
        ):
            raise HandlerFailure("quota_task_contract_mismatch", retriable=True)
        merged = frozenset((*current.evidence, *evidence))
        merged_receipts = frozenset((*current.receipt_ids, *receipt_ids))
        if (
            len(merged) != len(merged_receipts)
            or current.baseline_count + len(merged) > target
        ):
            raise HandlerFailure("quota_progress_invalid", retriable=True)
        added_evidence = merged - current.evidence
        added_receipts = merged_receipts - current.receipt_ids
        if not added_evidence and not added_receipts:
            return current
        if len(added_evidence) != 1 or len(added_receipts) != 1:
            raise HandlerFailure("quota_progress_invalid", retriable=True)
        try:
            CanonicalTaskAdapter(self.runner).apply_task_progress_event(
                slug,
                event_binding=DAILY_JOB_APPLICATION_BINDING,
                evidence_slug=next(iter(added_evidence)),
                receipt_id=next(iter(added_receipts)),
                now=occurred_at,
            )
        except Exception as exc:
            raise HandlerFailure("quota_progress_write_failed", retriable=True) from exc
        return self.get_quota_task(slug)

    def complete_quota_task(
        self,
        slug: str,
        *,
        completed_at: datetime,
    ) -> QuotaTaskState:
        current = self.get_quota_task(slug)
        if current.status == "completed":
            return current
        if not current.active or current.completed_count != current.target:
            raise HandlerFailure("quota_completion_invalid", retriable=True)
        try:
            CanonicalTaskAdapter(self.runner).set_task_status(
                slug,
                "completed",
                completed_at,
            )
        except Exception as exc:
            raise HandlerFailure("quota_completion_failed", retriable=True) from exc
        completed = self.get_quota_task(slug)
        if (
            completed.status != "completed"
            or completed.active
            or completed.completed_at != completed_at
            or completed.evidence != current.evidence
            or completed.receipt_ids != current.receipt_ids
        ):
            raise HandlerFailure("gbrain_completion_readback_mismatch", retriable=True)
        return completed

    def verify(
        self,
        application: ApplicationRecord,
        task: QuotaTaskState,
    ) -> None:
        page = self.runner.run("get_page", {"slug": application.slug})
        if (
            not isinstance(page, Mapping)
            or not _application_page_matches(page, application)
        ):
            raise HandlerFailure(
                "gbrain_application_readback_mismatch", retriable=True
            )
        application_links = _link_records(
            self.runner.run("get_links", {"slug": application.slug})
        )
        task_links = _link_records(self.runner.run("get_links", {"slug": task.slug}))
        forward = [item for item in application_links if (
            item["from_slug"], item["to_slug"], item["link_type"]
        ) == (application.slug, task.slug, "evidence_for")]
        reverse = [item for item in task_links if (
            item["from_slug"], item["to_slug"], item["link_type"]
        ) == (task.slug, application.slug, "has_evidence")]
        if len(forward) != 1 or forward[0]["link_source"] not in {None, "", "gtasks"}:
            raise HandlerFailure("gbrain_forward_link_missing", retriable=True)
        if len(reverse) != 1 or reverse[0]["link_source"] not in {None, "", "gtasks"}:
            raise HandlerFailure("gbrain_reverse_link_missing", retriable=True)
        readback = self.get_quota_task(task.slug)
        if readback != task:
            raise HandlerFailure("gbrain_progress_readback_mismatch", retriable=True)
