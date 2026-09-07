"""Stable optimistic-edit revisions for canonical task data.

The revision covers these managed fields exactly: ``slug``, ``title``,
``summary``, ``detail``, ``status``, ``priority``, ``next_action``, ``due_day``,
``due_at``, ``scheduled_day``, ``project``, ``goal``, ``parent``,
``owner_agent``, ``lifecycle_root``, ``progress_metric``, ``event_progress``, and
``completed_at``, and ``updated_at``. Display markup, derived TODO enrichment, UI state, and any
existing ``revision`` property are deliberately excluded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


TASK_EDIT_REVISION_FIELDS = (
    "slug",
    "title",
    "summary",
    "detail",
    "status",
    "priority",
    "next_action",
    "due_day",
    "due_at",
    "scheduled_day",
    "project",
    "goal",
    "parent",
    "owner_agent",
    "lifecycle_root",
    "progress_metric",
    "event_progress",
    "completed_at",
    "updated_at",
)


def task_edit_revision(payload: Mapping[str, Any]) -> str:
    """Return a stable SHA256 revision for fields controlled by task editing."""
    managed = {field: payload.get(field) for field in TASK_EDIT_REVISION_FIELDS}
    canonical = json.dumps(
        managed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskEditConflict(ValueError):
    """The canonical task changed after an editor draft was opened."""

    def __init__(self) -> None:
        super().__init__(
            "This task changed since you opened it. Your draft was kept; "
            "refresh and review before saving again."
        )
