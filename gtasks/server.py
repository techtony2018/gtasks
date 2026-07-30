from __future__ import annotations

import argparse
import json
import mimetypes
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Condition
from time import monotonic
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
from urllib.parse import unquote

from . import __version__
from .domain import (
    ACTIVE_ROOT,
    AGENT_SCOPES,
    COMPLETED_ROOT,
    DomainValidationError,
    EDITABLE_TASK_STATUSES,
    EventProgress,
    GOALS_ROOT,
    PROJECTS_ROOT,
    ProgressMetric,
    Task,
    group_today,
    new_goal,
    new_inbox_task,
    new_project,
    new_task,
)
from .gbrain import GBrainAdapter, GBrainError, PartialMutationError
from .operational_logs import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SEVERITIES,
    COMPONENT_PATTERN,
    OperationalLogReader,
    OperationalLogStore,
)
from .releases import release_payload
from .warnings import WarningDismissalStore


MAX_REQUEST_BYTES = 16 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SNAPSHOT_CACHE_SECONDS = 30


def _manual_metric_unit(label: str) -> str:
    unit = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return unit[:64] or "count"


def _progress_metric_from_request(
    raw: object,
    *,
    due_day: date,
) -> tuple[ProgressMetric | None, EventProgress | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise DomainValidationError("progress_metric must be an object")
    allowed = {
        "kind",
        "label",
        "target",
        "current",
        "event_binding",
        "auto_complete",
    }
    if set(raw) - allowed:
        raise DomainValidationError(
            "progress_metric contains unsupported fields"
        )
    kind = raw.get("kind", "count")
    label = raw.get("label")
    target = raw.get("target")
    current = raw.get("current", 0)
    binding = raw.get("event_binding")
    auto_complete = raw.get("auto_complete", binding == "job_applied")
    if not isinstance(label, str):
        raise DomainValidationError("progress metric label is required")
    if binding not in (None, "", "job_applied"):
        raise DomainValidationError(
            "job_applied is the only supported automatic event binding"
        )
    binding = binding or None
    if binding == "job_applied" and current != 0:
        raise DomainValidationError(
            "job_applied progress must start at 0 without verified event evidence"
        )
    value = {
        "kind": kind,
        "label": label,
        "unit": (
            "job_application"
            if binding == "job_applied"
            else _manual_metric_unit(label)
        ),
        "target": target,
        "current": current,
        "event_binding": binding,
        "auto_complete": auto_complete if binding else False,
        "task_day": due_day.isoformat() if binding else None,
        "timezone": "America/Los_Angeles" if binding else None,
    }
    metric = ProgressMetric.from_value(value)
    return metric, EventProgress() if binding else None


def _dedupe_tasks(tasks: list[Task]) -> list[Task]:
    seen: set[str] = set()
    result: list[Task] = []
    for task in tasks:
        if task.slug in seen:
            continue
        seen.add(task.slug)
        result.append(task)
    return result


def build_task_snapshot(adapter: GBrainAdapter, today: date) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=3) as executor:
        active_future = executor.submit(adapter.list_collection_tasks, ACTIVE_ROOT)
        completed_future = executor.submit(
            adapter.list_collection_tasks,
            COMPLETED_ROOT,
        )
        goals_future = executor.submit(adapter.list_goals)
        active_read = active_future.result()
        completed_read = completed_future.result()
        goal_read = goals_future.result()
    active = _dedupe_tasks(list(active_read.tasks))
    archived = _dedupe_tasks(list(completed_read.tasks))
    all_tasks = _dedupe_tasks(active + archived)
    completed = [
        task
        for task in all_tasks
        if task.status == "completed" or task.lifecycle_root == COMPLETED_ROOT
    ]
    goal_progress: list[dict[str, Any]] = []
    for goal in goal_read.goals:
        linked = [task for task in all_tasks if task.goal == goal.slug]
        active_goal_tasks = [
            task
            for task in linked
            if task.status not in {"completed", "cancelled"}
        ]
        completed_goal_tasks = [
            task for task in linked if task.status == "completed"
        ]
        linked_count = len(linked)
        completed_count = len(completed_goal_tasks)
        progress_percent = (
            round(completed_count / linked_count * 100) if linked_count else 0
        )
        goal_progress.append(
            {
                **goal.to_dict(),
                "legacy_one_way_tasks": [],
                "relationship_warning": False,
                "active_tasks": [task.to_dict() for task in active_goal_tasks],
                "completed_tasks": [
                    task.to_dict() for task in completed_goal_tasks
                ],
                "progress": {
                    "active": len(active_goal_tasks),
                    "completed": completed_count,
                    "linked": linked_count,
                    "percent": progress_percent,
                },
            }
        )

    return {
        "as_of": today.isoformat(),
        "default_due_day": "task_creation_day",
        "default_goal_target_day": "end_of_creation_quarter",
        "roots": {
            "active": ACTIVE_ROOT,
            "completed": COMPLETED_ROOT,
            "goals": GOALS_ROOT,
        },
        "tasks": [task.to_dict() for task in all_tasks],
        "goals": goal_progress,
        "today": group_today(active, today).to_dict(),
        "views": {
            "inbox": [
                task.to_dict()
                for task in active
                if task.inbox and task.status not in {"completed", "cancelled"}
            ],
            "upcoming": [
                task.to_dict()
                for task in active
                if task.due_day
                and task.due_day > today
                and task.status not in {"completed", "cancelled"}
            ],
            "blocked": [
                task.to_dict()
                for task in active
                if task.status in {"waiting", "blocked"}
            ],
            "projects": [
                task.to_dict() for task in active if task.project is not None
            ],
            "completed": [task.to_dict() for task in completed],
        },
        "issues": [
            issue.to_dict()
            for issue in active_read.issues + completed_read.issues + goal_read.issues
        ],
    }


def _handler_class(
    adapter: GBrainAdapter,
    clock: Callable[[], datetime],
    identity_factory: Callable[[], str],
    static_dir: Path,
    warning_store: WarningDismissalStore,
    log_reader: OperationalLogReader,
) -> type[BaseHTTPRequestHandler]:
    snapshot_condition = Condition()
    snapshot_payload: dict[str, Any] | None = None
    snapshot_created_at = 0.0
    snapshot_loading = False

    def decorate_issues(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            decorated = warning_store.decorate(payload.get("issues", []))
            error = None
        except RuntimeError as exc:
            decorated = [
                {
                    **dict(issue),
                    "fingerprint": None,
                    "dismissed": False,
                }
                for issue in payload.get("issues", [])
            ]
            error = str(exc)
        return {
            **payload,
            "issues": decorated,
            "warning_state_error": error,
        }

    def invalidate_snapshot() -> None:
        nonlocal snapshot_payload, snapshot_created_at
        with snapshot_condition:
            snapshot_payload = None
            snapshot_created_at = 0.0

    def read_snapshot(force: bool = False) -> dict[str, Any]:
        nonlocal snapshot_payload, snapshot_created_at, snapshot_loading
        with snapshot_condition:
            while snapshot_loading:
                snapshot_condition.wait()
                if snapshot_payload is not None:
                    return snapshot_payload
            age = monotonic() - snapshot_created_at
            if (
                not force
                and snapshot_payload is not None
                and age <= SNAPSHOT_CACHE_SECONDS
            ):
                return snapshot_payload
            snapshot_loading = True
        try:
            payload = build_task_snapshot(adapter, clock().date())
        except Exception:
            with snapshot_condition:
                snapshot_loading = False
                snapshot_condition.notify_all()
            raise
        with snapshot_condition:
            snapshot_payload = payload
            snapshot_created_at = monotonic()
            snapshot_loading = False
            snapshot_condition.notify_all()
            return payload

    class GTasksHandler(BaseHTTPRequestHandler):
        server_version = f"GTasks/{__version__}"

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            )

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            operational_messages = {
                "gbrain_unavailable": "A GBrain operation was unavailable.",
                "partial_write": (
                    "A GBrain mutation needs verification before it is retried."
                ),
                "warning_state_unavailable": (
                    "Inbox warning preference storage is unavailable."
                ),
            }
            code = payload.get("code")
            if status >= 500 and code in operational_messages:
                log_reader.append_gtasks(
                    severity="error" if status >= 502 else "warning",
                    message=operational_messages[code],
                    now=clock(),
                )
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any] | None:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length."})
                return None
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    if length > MAX_REQUEST_BYTES
                    else HTTPStatus.BAD_REQUEST,
                    {"error": "Request body size is invalid."},
                )
                return None
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Request body must be valid JSON."},
                )
                return None
            if not isinstance(payload, dict):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Request body must be a JSON object."},
                )
                return None
            return payload

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": __version__,
                        "canonical_store": "gbrain",
                        "default_due_day": "task_creation_day",
                        "default_goal_target_day": "end_of_creation_quarter",
                        "mutations": "explicit_user_actions_only",
                        "active_root": ACTIVE_ROOT,
                        "completed_root": COMPLETED_ROOT,
                        "goals_root": GOALS_ROOT,
                        "projects_root": PROJECTS_ROOT,
                        "warning_dismissals": "user_scoped_local_state",
                        "operational_logs": "privacy_safe_read_only",
                        "queue_reader_dependency": "optional",
                        "agent_work_roots": [
                            root for _agent, root in AGENT_SCOPES
                        ],
                    },
                )
                return
            if path == "/api/releases":
                self._json(HTTPStatus.OK, release_payload())
                return
            if path == "/api/agents":
                try:
                    payload = adapter.list_agent_profiles().to_dict()
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            if path == "/api/agent-work":
                try:
                    payload = adapter.list_agent_work().to_dict()
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            if path == "/api/logs":
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if any(
                    key not in {"severity", "component", "cursor", "limit"}
                    or len(values) != 1
                    for key, values in query.items()
                ):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Unsupported or repeated log filter."},
                    )
                    return
                severity = query.get("severity", [None])[0] or None
                component = query.get("component", [None])[0] or None
                if severity is not None and severity not in SEVERITIES:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Unknown log severity filter."},
                    )
                    return
                if (
                    component is not None
                    and not COMPONENT_PATTERN.fullmatch(component)
                ):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Invalid log component filter."},
                    )
                    return
                try:
                    cursor = int(query.get("cursor", ["0"])[0])
                    limit = int(
                        query.get("limit", [str(DEFAULT_PAGE_SIZE)])[0]
                    )
                except ValueError:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Log cursor and limit must be integers."},
                    )
                    return
                if cursor < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                f"Log cursor must be nonnegative and limit must "
                                f"be between 1 and {MAX_PAGE_SIZE}."
                            )
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    log_reader.page(
                        severity=severity,
                        component=component,
                        cursor=cursor,
                        limit=limit,
                    ),
                )
                return
            if path == "/api/tasks":
                try:
                    force = (
                        urlsplit(self.path).query == "refresh=1"
                    )
                    payload = read_snapshot(force=force)
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            if path == "/api/projects":
                try:
                    payload = adapter.list_projects().to_dict()
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, decorate_issues(payload))
                return
            goal_prefix = "/api/goals/"
            goal_suffix = "/relationships"
            if path.startswith(goal_prefix) and path.endswith(goal_suffix):
                goal_slug = unquote(path[len(goal_prefix) : -len(goal_suffix)])
                try:
                    relationship_read = adapter.read_goal_relationships(goal_slug)
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_goal"},
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, relationship_read.to_dict())
                return
            self._serve_static(path)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path in {"/api/warnings/dismiss", "/api/warnings/restore"}:
                payload = self._read_json()
                if payload is None:
                    return
                fingerprint = payload.get("fingerprint")
                if not isinstance(fingerprint, str) or len(payload) != 1:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "fingerprint must be the only field.",
                            "code": "invalid_warning_fingerprint",
                        },
                    )
                    return
                try:
                    dismissed = path.endswith("/dismiss")
                    verified = (
                        warning_store.dismiss(fingerprint)
                        if dismissed
                        else warning_store.restore(fingerprint)
                    )
                except ValueError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_warning_fingerprint",
                        },
                    )
                    return
                except (OSError, RuntimeError) as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": str(exc),
                            "code": "warning_state_unavailable",
                        },
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "fingerprint": fingerprint,
                        "dismissed": dismissed,
                        "verified": verified,
                    },
                )
                return
            if path == "/api/goals":
                payload = self._read_json()
                if payload is None:
                    return
                required_fields = (
                    "title",
                    "outcome",
                    "success_criteria",
                    "strategy",
                    "review_cadence",
                    "constraints",
                )
                if any(
                    not isinstance(payload.get(field), str)
                    for field in required_fields
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "all goal text fields must be text.",
                            "code": "invalid_goal",
                        },
                    )
                    return
                target_day = None
                raw_target_day = payload.get("target_day")
                if raw_target_day not in (None, ""):
                    if not isinstance(raw_target_day, str):
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {
                                "error": "target_day must be YYYY-MM-DD or omitted.",
                                "code": "invalid_goal",
                            },
                        )
                        return
                    try:
                        target_day = date.fromisoformat(raw_target_day)
                    except ValueError:
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {
                                "error": "target_day must be YYYY-MM-DD or omitted.",
                                "code": "invalid_goal",
                            },
                        )
                        return
                try:
                    goal = new_goal(
                        title=payload["title"],
                        outcome=payload["outcome"],
                        success_criteria=payload["success_criteria"],
                        strategy=payload["strategy"],
                        review_cadence=payload["review_cadence"],
                        constraints=payload["constraints"],
                        target_day=target_day,
                        now=clock(),
                        identity=identity_factory(),
                    )
                    receipt = adapter.create_goal(goal)
                except DomainValidationError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_goal"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(
                    HTTPStatus.CREATED,
                    {"goal": goal.to_dict(), "receipt": receipt.to_dict()},
                )
                return
            if path == "/api/projects":
                payload = self._read_json()
                if payload is None:
                    return
                raw_title = payload.get("title", "")
                if not isinstance(raw_title, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "project title must be text."},
                    )
                    return
                try:
                    project = new_project(
                        raw_title,
                        now=clock(),
                        identity=identity_factory(),
                    )
                    receipt = adapter.create_project(project)
                except DomainValidationError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_project"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(
                    HTTPStatus.CREATED,
                    {
                        "project": project.to_dict(),
                        "receipt": receipt.to_dict(),
                    },
                )
                return
            duplicate_prefix = "/api/tasks/"
            duplicate_suffix = "/duplicate"
            if (
                path.startswith(duplicate_prefix)
                and path.endswith(duplicate_suffix)
            ):
                source_slug = unquote(
                    path[len(duplicate_prefix) : -len(duplicate_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                allowed_fields = {
                    "title",
                    "due_day",
                    "detail",
                    "priority",
                    "next_action",
                    "project_slug",
                    "goal_slug",
                    "progress_metric",
                }
                if set(payload) - allowed_fields:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "duplicate task request contains unsupported fields.",
                            "code": "invalid_task",
                        },
                    )
                    return
                now = clock()
                raw_due_day = payload.get("due_day")
                due_day = now.date()
                due_source = "task_creation_day"
                if raw_due_day not in (None, ""):
                    if not isinstance(raw_due_day, str):
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {"error": "due_day must be YYYY-MM-DD."},
                        )
                        return
                    try:
                        due_day = date.fromisoformat(raw_due_day)
                    except ValueError:
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {"error": "due_day must be YYYY-MM-DD."},
                        )
                        return
                    due_source = "explicit"
                try:
                    progress_metric, event_progress = (
                        _progress_metric_from_request(
                            payload.get("progress_metric"),
                            due_day=due_day,
                        )
                    )
                    task = new_task(
                        title=payload.get("title", ""),
                        detail=payload.get("detail", ""),
                        priority=payload.get("priority", "normal"),
                        next_action=payload.get("next_action", ""),
                        due_day=due_day,
                        project=payload.get("project_slug") or None,
                        goal=payload.get("goal_slug") or None,
                        progress_metric=progress_metric,
                        event_progress=event_progress,
                        now=now,
                        identity=identity_factory(),
                    )
                    receipt = adapter.duplicate_task(source_slug, task)
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_task"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(
                    HTTPStatus.CREATED,
                    {
                        "source_slug": source_slug,
                        "task": task.to_dict(),
                        "receipt": receipt.to_dict(),
                        "due_day_source": due_source,
                    },
                )
                return
            if path != "/api/tasks":
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            payload = self._read_json()
            if payload is None:
                return

            now = clock()
            raw_due_day = payload.get("due_day")
            due_source = "task_creation_day"
            due_day = now.date()
            if raw_due_day not in (None, ""):
                if not isinstance(raw_due_day, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "due_day must be YYYY-MM-DD."},
                    )
                    return
                try:
                    due_day = date.fromisoformat(raw_due_day)
                except ValueError:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "due_day must be YYYY-MM-DD."},
                    )
                    return
                due_source = "explicit"

            raw_title = payload.get("title", "")
            if not isinstance(raw_title, str):
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "title must be text."},
                )
                return
            quick_add_fields = {"title", "due_day"}
            full_task_fields = {
                *quick_add_fields,
                "detail",
                "priority",
                "next_action",
                "project_slug",
                "goal_slug",
                "progress_metric",
            }
            unsupported = set(payload) - full_task_fields
            if unsupported:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {
                        "error": "task request contains unsupported fields.",
                        "code": "invalid_task",
                    },
                )
                return
            is_full_creation = bool(set(payload) - quick_add_fields)
            try:
                if is_full_creation:
                    progress_metric, event_progress = (
                        _progress_metric_from_request(
                            payload.get("progress_metric"),
                            due_day=due_day,
                        )
                    )
                    project_slug = payload.get("project_slug") or None
                    goal_slug = payload.get("goal_slug") or None
                    task = new_task(
                        title=raw_title,
                        detail=payload.get("detail", ""),
                        priority=payload.get("priority", "normal"),
                        next_action=payload.get("next_action", ""),
                        due_day=due_day,
                        project=project_slug,
                        goal=goal_slug,
                        progress_metric=progress_metric,
                        event_progress=event_progress,
                        now=now,
                        identity=identity_factory(),
                    )
                    receipt = adapter.create_task(task)
                else:
                    task = new_inbox_task(
                        raw_title,
                        now=now,
                        identity=identity_factory(),
                        due_day=due_day,
                    )
                    receipt = adapter.create_inbox(task)
            except (DomainValidationError, ValueError) as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(exc), "code": "invalid_task"},
                )
                return
            except PartialMutationError as exc:
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": str(exc),
                        "code": "partial_write",
                        "slug": exc.slug,
                    },
                )
                return
            except GBrainError as exc:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc), "code": "gbrain_unavailable"},
                )
                return

            invalidate_snapshot()
            self._json(
                HTTPStatus.CREATED,
                {
                    "task": task.to_dict(),
                    "receipt": receipt.to_dict(),
                    "due_day_source": due_source,
                },
            )

        def do_PATCH(self) -> None:
            path = urlsplit(self.path).path
            goal_prefix = "/api/goals/"
            goal_status_suffix = "/status"
            if path.startswith(goal_prefix) and path.endswith(goal_status_suffix):
                goal_slug = unquote(
                    path[len(goal_prefix) : -len(goal_status_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                if payload.get("status") != "paused" or len(payload) != 1:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "goal status action supports only paused.",
                            "code": "invalid_goal_status",
                        },
                    )
                    return
                try:
                    receipt = adapter.set_goal_paused(goal_slug)
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_goal"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            prefix = "/api/tasks/"
            membership_suffix = "/relationships/active-membership"
            if path.endswith(membership_suffix):
                action = "active_membership"
                suffix = membership_suffix
            elif path.endswith("/goal"):
                action = "goal"
                suffix = "/goal"
            elif path.endswith("/next-action"):
                action = "next_action"
                suffix = "/next-action"
            elif path.endswith("/progress-metric"):
                action = "progress_metric"
                suffix = "/progress-metric"
            elif path.endswith("/project"):
                action = "project"
                suffix = "/project"
            elif path.endswith("/status"):
                action = "status"
                suffix = "/status"
            else:
                action = ""
                suffix = ""
            if not path.startswith(prefix) or not action:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            encoded_slug = path[len(prefix) : -len(suffix)]
            task_slug = unquote(encoded_slug)
            if not task_slug:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Task slug is required."},
                )
                return
            payload = self._read_json()
            if payload is None:
                return
            if action == "active_membership":
                if payload:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "Active membership repair takes no options.",
                            "code": "invalid_repair",
                        },
                    )
                    return
                try:
                    receipt = adapter.repair_active_membership(task_slug)
                except ValueError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "repair_not_eligible"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if action == "next_action":
                requested_next_action = payload.get("next_action")
                if (
                    not isinstance(requested_next_action, str)
                    or len(requested_next_action.strip()) > 240
                    or "\n" in requested_next_action
                    or "\r" in requested_next_action
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "next_action must be one concise line of "
                                "240 characters or fewer."
                            ),
                            "code": "invalid_next_action",
                        },
                    )
                    return
                try:
                    receipt = adapter.set_task_next_action(
                        task_slug,
                        requested_next_action,
                        clock(),
                    )
                except ValueError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_next_action"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if action == "progress_metric":
                if set(payload) - {"progress_metric", "task_day"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "Progress metric updates accept only "
                                "progress_metric and task_day."
                            ),
                            "code": "invalid_progress_metric",
                        },
                    )
                    return
                raw_metric = payload.get("progress_metric")
                task_day_value = payload.get("task_day")
                if raw_metric is None:
                    task_day = clock().date()
                elif not isinstance(task_day_value, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "task_day is required when configuring a "
                                "progress metric."
                            ),
                            "code": "invalid_progress_metric",
                        },
                    )
                    return
                else:
                    try:
                        task_day = date.fromisoformat(task_day_value)
                    except ValueError:
                        self._json(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            {
                                "error": "task_day must be an ISO calendar date.",
                                "code": "invalid_progress_metric",
                            },
                        )
                        return
                try:
                    progress_metric, event_progress = (
                        _progress_metric_from_request(
                            raw_metric,
                            due_day=task_day,
                        )
                    )
                    receipt = adapter.set_task_progress_metric(
                        task_slug,
                        progress_metric,
                        event_progress,
                        clock(),
                    )
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_progress_metric",
                        },
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if action == "project":
                project_slug = payload.get("project_slug")
                if project_slug == "":
                    project_slug = None
                if project_slug is not None and not isinstance(project_slug, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "project_slug must be a project slug or null.",
                            "code": "invalid_project_assignment",
                        },
                    )
                    return
                try:
                    receipt = adapter.set_task_project(task_slug, project_slug)
                except ValueError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": str(exc),
                            "code": "invalid_project_assignment",
                        },
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            if action == "status":
                requested_status = payload.get("status")
                if (
                    not isinstance(requested_status, str)
                    or requested_status not in EDITABLE_TASK_STATUSES
                ):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "status must be one of "
                                + ", ".join(sorted(EDITABLE_TASK_STATUSES))
                                + "."
                            ),
                            "code": "invalid_status",
                        },
                    )
                    return
                try:
                    receipt = adapter.set_task_status(
                        task_slug,
                        requested_status,
                        clock(),
                    )
                except ValueError as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_status"},
                    )
                    return
                except PartialMutationError as exc:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {
                            "error": str(exc),
                            "code": "partial_write",
                            "slug": exc.slug,
                        },
                    )
                    return
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return

            goal_slug = payload.get("goal_slug")
            if goal_slug == "":
                goal_slug = None
            if goal_slug is not None and not isinstance(goal_slug, str):
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "goal_slug must be a goal slug or null."},
                )
                return
            try:
                receipt = adapter.set_task_goal(task_slug, goal_slug)
            except ValueError as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(exc), "code": "invalid_goal_link"},
                )
                return
            except PartialMutationError as exc:
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": str(exc),
                        "code": "partial_write",
                        "slug": exc.slug,
                    },
                )
                return
            except GBrainError as exc:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc), "code": "gbrain_unavailable"},
                )
                return
            invalidate_snapshot()
            self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})

        def do_DELETE(self) -> None:
            path = urlsplit(self.path).path
            prefix = "/api/goals/"
            if not path.startswith(prefix):
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            goal_slug = unquote(path[len(prefix) :])
            if not goal_slug:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Goal slug is required."},
                )
                return
            try:
                receipt = adapter.delete_goal(goal_slug)
            except (DomainValidationError, ValueError) as exc:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(exc), "code": "invalid_goal"},
                )
                return
            except PartialMutationError as exc:
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": str(exc),
                        "code": "partial_write",
                        "slug": exc.slug,
                    },
                )
                return
            except GBrainError as exc:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": str(exc), "code": "gbrain_unavailable"},
                )
                return
            invalidate_snapshot()
            self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})

        def _serve_static(self, path: str) -> None:
            relative = {
                "/": "index.html",
                "/index.html": "index.html",
                "/styles.css": "styles.css",
                "/app.js": "app.js",
            }.get(path)
            if relative is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
                return
            file_path = static_dir / relative
            try:
                body = file_path.read_bytes()
            except FileNotFoundError:
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"Static asset is unavailable: {relative}"},
                )
                return
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return GTasksHandler


def build_server(
    host: str = "127.0.0.1",
    port: int = 4179,
    adapter: GBrainAdapter | None = None,
    clock: Callable[[], datetime] | None = None,
    identity_factory: Callable[[], str] | None = None,
    static_dir: Path = STATIC_DIR,
    warning_store: WarningDismissalStore | None = None,
    log_reader: OperationalLogReader | None = None,
) -> ThreadingHTTPServer:
    active_log_reader = log_reader or OperationalLogReader()
    active_log_reader.append_gtasks(
        severity="info",
        message="GTasks runtime initialized.",
        now=(clock or (lambda: datetime.now().astimezone()))(),
    )
    handler = _handler_class(
        adapter or GBrainAdapter(),
        clock or (lambda: datetime.now().astimezone()),
        identity_factory or (lambda: uuid.uuid4().hex[:8]),
        static_dir,
        warning_store or WarningDismissalStore(),
        active_log_reader,
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local GTasks interface backed by GBrain."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4179)
    parser.add_argument("--warning-state-file", type=Path)
    parser.add_argument("--operation-log-file", type=Path)
    parser.add_argument("--queue-log-file", type=Path)
    args = parser.parse_args()

    server = build_server(
        host=args.host,
        port=args.port,
        warning_store=(
            WarningDismissalStore(args.warning_state_file)
            if args.warning_state_file
            else None
        ),
        log_reader=OperationalLogReader(
            gtasks_store=OperationalLogStore(args.operation_log_file),
            queue_path=args.queue_log_file,
        )
        if args.operation_log_file or args.queue_log_file
        else None,
    )
    print(f"GTasks listening on http://{args.host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
