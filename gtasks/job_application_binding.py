"""Versioned explicit job-application event binding."""

from __future__ import annotations

import hashlib
import json

from .domain import Task

JOB_APPLIED_BOUND_TASK_SLUG = "tasks/562466ac-3569-4013-b105-746a64816cc6"
JOB_APPLIED_TIMEZONE = "America/Los_Angeles"
JOB_APPLIED_UNIT = "job_application"


def progress_revision(task: Task) -> str | None:
    """Return a stable edit token for the canonical verified progress state."""
    metric = task.progress_metric
    if metric is None or metric.event_binding != "job_applied":
        return None
    progress = task.event_progress
    payload = {
        "slug": task.slug,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "metric": metric.to_dict(),
        "event_progress": progress.to_dict() if progress is not None else None,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
