from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Condition
from time import monotonic
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

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
    SYSTEM_TICKET_TARGETS,
    PROPOSALS_ROOT,
    ProgressMetric,
    Task,
    group_today,
    new_goal,
    new_inbox_task,
    new_project,
    new_task,
    new_system_ticket,
)
from .gbrain import (
    GBrainAdapter,
    GBrainError,
    LifecycleIntegrityError,
    PartialMutationError,
)
from .ical import CalendarPreferences, ICalendarError, ICalendarReader
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
MAX_AVATAR_BYTES = 5 * 1024 * 1024
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SNAPSHOT_CACHE_SECONDS = 30


def _lifecycle_attention_payload(exc: LifecycleIntegrityError) -> dict[str, object]:
    """Return a safe, actionable error without exposing graph internals."""
    return {
        "error": str(exc),
        "code": "lifecycle_membership_needs_attention",
        "slug": exc.task_slug,
        "lifecycle_edge_count": exc.edge_count,
        "repair_url": (
            "http://127.0.0.1:8788/?slug=" + quote(exc.task_slug, safe="")
        ),
    }


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
    with ThreadPoolExecutor(max_workers=4) as executor:
        active_future = executor.submit(adapter.list_collection_tasks, ACTIVE_ROOT)
        completed_future = executor.submit(
            adapter.list_collection_tasks,
            COMPLETED_ROOT,
        )
        goals_future = executor.submit(adapter.list_goals)
        owner_future = executor.submit(adapter.get_tony_profile)
        active_read = active_future.result()
        completed_read = completed_future.result()
        goal_read = goals_future.result()
        try:
            owner = owner_future.result()
        except (DomainValidationError, GBrainError):
            # Personal-avatar presentation must never block canonical tasks.
            owner = {
                "slug": "people/tony-guan",
                "name": "Tony",
                "avatar": {"kind": "initials", "value": "T"},
            }
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
        "owner": owner,
        "tasks": [task.to_dict() for task in all_tasks],
        "goals": goal_progress,
        "today": group_today(active, today).to_dict(),
        "views": {
            "inbox": [
                task.to_dict()
                for task in active
                if task.inbox and task.status not in {"completed", "cancelled"}
            ],
            "blocked": [
                task.to_dict()
                for task in active
                if task.status == "blocked"
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
    stargraph_url: str,
    ical_reader: ICalendarReader | None = None,
    calendar_preferences: CalendarPreferences | None = None,
) -> type[BaseHTTPRequestHandler]:
    snapshot_condition = Condition()
    snapshot_payload: dict[str, Any] | None = None
    snapshot_created_at = 0.0
    snapshot_loading = False
    active_ical_reader = ical_reader or ICalendarReader()
    active_calendar_preferences = calendar_preferences or CalendarPreferences()

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
                "img-src 'self' data: blob: http://127.0.0.1:8788; connect-src 'self'; frame-ancestors 'none'",
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

        def _read_avatar_upload(self) -> tuple[str, bytes, str] | None:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data;") or "boundary=" not in content_type:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Avatar upload must be a multipart image file."})
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid upload length."})
                return None
            if length <= 0 or length > MAX_AVATAR_BYTES + 8192:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Avatar images must be 5 MB or smaller."})
                return None
            boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
            body = self.rfile.read(length)
            marker = b"--" + boundary
            for part in body.split(marker):
                if b'name="file"' not in part:
                    continue
                try:
                    header, file_bytes = part.split(b"\r\n\r\n", 1)
                except ValueError:
                    continue
                file_bytes = file_bytes.rsplit(b"\r\n", 1)[0]
                match = re.search(br'filename="([^"\r\n]+)"', header)
                mime_match = re.search(br'Content-Type:\s*([^\r\n;]+)', header, re.I)
                filename = (match.group(1).decode("utf-8", "replace") if match else "avatar")
                mime = (mime_match.group(1).decode("ascii", "replace").lower() if mime_match else "")
                if mime not in ALLOWED_AVATAR_TYPES or not file_bytes or len(file_bytes) > MAX_AVATAR_BYTES:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Choose a PNG, JPEG, GIF, or WebP avatar up to 5 MB."})
                    return None
                magic = (
                    (mime == "image/jpeg" and file_bytes.startswith(b"\xff\xd8\xff"))
                    or (mime == "image/png" and file_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
                    or (mime == "image/gif" and file_bytes.startswith((b"GIF87a", b"GIF89a")))
                    or (mime == "image/webp" and file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP")
                )
                if not magic:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "The selected file does not match its image type."})
                    return None
                return filename, file_bytes, mime
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Choose an avatar image file."})
            return None

        def _attach_avatar(self, agent_slug: str, filename: str, data: bytes, mime: str) -> dict[str, Any]:
            boundary = f"gtasks-{uuid.uuid4().hex}"
            payload = b"".join((
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{filename.replace(chr(34), "")}"\r\n'.encode(),
                f"Content-Type: {mime}\r\n\r\n".encode(), data, b"\r\n",
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"description\"\r\n\r\nGTasks agent avatar\r\n".encode(),
                f"--{boundary}--\r\n".encode(),
            ))
            endpoint = f"{stargraph_url}/api/entity-attach-file/{quote(agent_slug, safe='')}"
            request = Request(endpoint, data=payload, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
            try:
                with urlopen(request, timeout=30) as response:
                    result = json.loads(response.read())
            except Exception as exc:
                raise GBrainError("Memory Stargraph avatar storage is unavailable") from exc
            local_media = result.get("local_media") if isinstance(result, dict) else None
            served_url = local_media.get("served_url") if isinstance(local_media, dict) else None
            if not result.get("ok") or not isinstance(served_url, str) or not served_url.startswith("/media/"):
                raise GBrainError("Memory Stargraph did not return durable avatar evidence")
            return result

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
                        "proposals_root": PROPOSALS_ROOT,
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
            if path == "/api/proposals":
                try:
                    payload = adapter.list_proposals().to_dict()
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
            if path == "/api/ical-events":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    start = date.fromisoformat(query.get("start", [""])[0])
                    end = date.fromisoformat(query.get("end", [""])[0])
                    if end <= start or (end - start).days > 45:
                        raise ValueError
                except ValueError:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Calendar range must be a valid local range of up to 45 days.", "code": "invalid_calendar_range"})
                    return
                try:
                    payload = active_ical_reader.read(
                        start,
                        end,
                        calendar_ids=active_calendar_preferences.selected_calendar_ids(),
                    )
                    payload["selected_calendar_ids"] = list(
                        active_calendar_preferences.selected_calendar_ids()
                    )
                    self._json(HTTPStatus.OK, payload)
                except ICalendarError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "calendar_unavailable"})
                return
            if path == "/api/ical-calendars":
                try:
                    payload = active_ical_reader.calendars()
                    payload["selected_calendar_ids"] = list(
                        active_calendar_preferences.selected_calendar_ids()
                    )
                    self._json(HTTPStatus.OK, payload)
                except ICalendarError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "calendar_unavailable"})
                return
            if path == "/api/system-tickets":
                try:
                    self._json(HTTPStatus.OK, decorate_issues(adapter.list_system_tickets().to_dict()))
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                return
            goal_prefix = "/api/goals/"
            goal_suffix = "/relationships"
            if path.startswith(goal_prefix) and path.endswith(goal_suffix):
                goal_slug = unquote(path[len(goal_prefix) : -len(goal_suffix)])
                try:
                    relationship_read = adapter.read_goal_relationships(goal_slug)
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
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
            if path == "/api/ical-access":
                try:
                    self._json(HTTPStatus.OK, active_ical_reader.request_full_access())
                except ICalendarError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "calendar_unavailable"})
                return
            if path == "/api/ical-preferences":
                payload = self._read_json()
                if payload is None:
                    return
                selected = payload.get("selected_calendar_ids")
                if (
                    set(payload) != {"selected_calendar_ids"}
                    or not isinstance(selected, list)
                    or not all(isinstance(item, str) and item for item in selected)
                ):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "selected_calendar_ids must be a list of calendar identifiers.", "code": "invalid_calendar_preferences"})
                    return
                try:
                    saved = active_calendar_preferences.save_selected_calendar_ids(selected)
                except (OSError, ValueError) as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Calendar selections could not be saved locally.", "code": "calendar_preferences_unavailable"})
                    return
                self._json(HTTPStatus.OK, {"selected_calendar_ids": list(saved), "verified": True})
                return
            avatar_prefix = "/api/agents/"
            avatar_suffix = "/avatar"
            default_goals_suffix = "/default-goals"
            if path.startswith(avatar_prefix) and path.endswith(default_goals_suffix):
                agent_slug = unquote(path[len(avatar_prefix) : -len(default_goals_suffix)])
                payload = self._read_json()
                if payload is None:
                    return
                goal_slug = payload.get("goal_slug")
                action = payload.get("action")
                if not isinstance(goal_slug, str) or action not in {"assign", "remove"}:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Choose a goal and an assignment action.", "code": "invalid_goal_assignment"})
                    return
                try:
                    agent = adapter.set_agent_default_goal(
                        agent_slug,
                        goal_slug,
                        assigned=action == "assign",
                    )
                except (DomainValidationError, ValueError) as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_goal_assignment"})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "goal_assignment_readback_failed"})
                    return
                self._json(HTTPStatus.OK, {"agent": agent.to_dict(), "verified": True})
                return
            if path.startswith(avatar_prefix) and path.endswith(avatar_suffix):
                agent_slug = unquote(path[len(avatar_prefix) : -len(avatar_suffix)])
                try:
                    # Validate the exact roster slug and typed agent page before
                    # sending any bytes to Memory Stargraph. This deliberately
                    # never derives a slug from the display name.
                    adapter.get_agent_profile(agent_slug)
                except (DomainValidationError, ValueError, GBrainError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "This Agent Directory profile cannot accept an avatar yet. "
                                + str(exc)
                            ),
                            "code": "invalid_agent_profile",
                        },
                    )
                    return
                upload = self._read_avatar_upload()
                if upload is None:
                    return
                filename, data, mime = upload
                try:
                    attachment = self._attach_avatar(agent_slug, filename, data, mime)
                    local_media = attachment["local_media"]
                    agent = adapter.set_agent_avatar(agent_slug, local_media["served_url"])
                except (ValueError, GBrainError) as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "avatar_upload_unavailable"})
                    return
                self._json(HTTPStatus.OK, {"agent": agent.to_dict(), "verified": True, "avatar": {"served_url": local_media["served_url"], "remote_read_verified": bool(local_media.get("remote_read_verified"))}})
                return
            proposal_prefix = "/api/proposals/"
            proposal_decision_suffix = "/decision"
            if (
                path.startswith(proposal_prefix)
                and path.endswith(proposal_decision_suffix)
            ):
                proposal_slug = unquote(
                    path[
                        len(proposal_prefix) : -len(proposal_decision_suffix)
                    ]
                )
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) - {"action", "decision_note"}:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": "proposal decision contains unsupported fields.",
                            "code": "invalid_proposal_decision",
                        },
                    )
                    return
                action = payload.get("action")
                decision_note = payload.get("decision_note", "")
                if action not in {"approve", "reject"} or not isinstance(
                    decision_note, str
                ) or len(decision_note) > 1000:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "action must be approve or reject and the "
                                "optional decision note must be text."
                            ),
                            "code": "invalid_proposal_decision",
                        },
                    )
                    return
                try:
                    receipt = adapter.decide_proposal(
                        proposal_slug,
                        action=action,
                        decision_note=decision_note,
                        now=clock(),
                    )
                except LifecycleIntegrityError as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        _lifecycle_attention_payload(exc),
                    )
                    return
                except (DomainValidationError, ValueError) as exc:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": str(exc), "code": "proposal_not_decidable"},
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
                raw_goals = payload.get("supporting_goal_slugs", [])
                if not isinstance(raw_title, str):
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": "project title must be text."},
                    )
                    return
                if raw_goals is not None and (not isinstance(raw_goals, list) or not all(isinstance(item, str) and item.startswith("goals/") for item in raw_goals)):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "supporting_goal_slugs must be a list of canonical goal slugs."})
                    return
                try:
                    valid_goals = {goal.slug for goal in adapter.list_goals().goals}
                    if not set(raw_goals).issubset(valid_goals):
                        raise DomainValidationError("Each supporting goal must be a current canonical goal.")
                    project = new_project(
                        raw_title,
                        now=clock(),
                        identity=identity_factory(),
                        supporting_goal_slugs=tuple(dict.fromkeys(raw_goals)),
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
            if path == "/api/system-tickets":
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) - {"title", "verbatim_request", "target_subsystem", "priority", "acceptance_criteria"}:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error":"System Ticket contains unsupported fields.", "code":"invalid_system_ticket"}); return
                try:
                    ticket = new_system_ticket(title=payload.get("title", ""), verbatim_request=payload.get("verbatim_request", ""), target_subsystem=payload.get("target_subsystem", "unknown"), priority=payload.get("priority", "normal"), acceptance_criteria=payload.get("acceptance_criteria", ""), now=clock(), identity=identity_factory())
                    receipt = adapter.create_system_ticket(ticket)
                except DomainValidationError as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error":str(exc), "code":"invalid_system_ticket"}); return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error":str(exc), "code":"partial_write", "slug":exc.slug}); return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error":str(exc), "code":"gbrain_unavailable"}); return
                self._json(HTTPStatus.CREATED, {"ticket": ticket.to_dict(), "receipt": receipt.to_dict()}); return
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
                    "assignee_slug",
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
                    assignee_slug = payload.get("assignee_slug", "tony")
                    available_agents = {
                        agent.slug: agent.work_root
                        for agent in adapter.list_agent_profiles().agents
                    }
                    if assignee_slug != "tony" and assignee_slug not in available_agents:
                        raise DomainValidationError("assignee is not an active GTasks agent")
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
                    if assignee_slug != "tony":
                        task = replace(
                            task,
                            lifecycle_root=available_agents[assignee_slug],
                            owner_agent=assignee_slug,
                        )
                        receipt = adapter.create_agent_task(task, assignee_slug)
                    else:
                        receipt = adapter.duplicate_task(source_slug, task)
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
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
                "assignee_slug",
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
                    assignee_slug = payload.get("assignee_slug", "tony")
                    available_agents = {
                        agent.slug: agent.work_root
                        for agent in adapter.list_agent_profiles().agents
                    }
                    if assignee_slug != "tony" and assignee_slug not in available_agents:
                        raise DomainValidationError(
                            "assignee must be Tony or an active Agent Directory profile"
                        )
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
                    if assignee_slug == "tony":
                        receipt = adapter.create_task(task)
                    else:
                        work_root = available_agents[assignee_slug]
                        task = replace(
                            task,
                            lifecycle_root=work_root,
                            owner_agent=assignee_slug,
                        )
                        receipt = adapter.create_agent_task(
                            task,
                            assignee_slug,
                        )
                else:
                    task = new_inbox_task(
                        raw_title,
                        now=now,
                        identity=identity_factory(),
                        due_day=due_day,
                    )
                    receipt = adapter.create_inbox(task)
            except LifecycleIntegrityError as exc:
                self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                return
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
            project_prefix = "/api/projects/"
            if path.startswith(project_prefix) and "/" not in path[len(project_prefix) :]:
                project_slug = unquote(path[len(project_prefix) :])
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) - {"title", "summary", "status", "supporting_goal_slugs"}:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "project edit contains unsupported fields.", "code": "invalid_project"})
                    return
                raw_goals = payload.get("supporting_goal_slugs", [])
                if not isinstance(raw_goals, list) or not all(isinstance(item, str) and item.startswith("goals/") for item in raw_goals):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "supporting_goal_slugs must be a list of canonical goal slugs.", "code": "invalid_project"})
                    return
                try:
                    existing = next(project for project in adapter.list_projects().projects if project.slug == project_slug)
                    raw_goals = existing.supporting_goal_slugs if raw_goals is None else raw_goals
                    valid_goals = {goal.slug for goal in adapter.list_goals().goals}
                    if not set(raw_goals).issubset(valid_goals):
                        raise DomainValidationError("Each supporting goal must be a current canonical goal.")
                    title = payload.get("title", existing.title)
                    summary = payload.get("summary", existing.summary)
                    status = payload.get("status", existing.status)
                    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
                        raise DomainValidationError("project title must be 1 to 160 characters")
                    if not isinstance(summary, str) or len(summary.strip()) > 500:
                        raise DomainValidationError("project summary must be 500 characters or fewer")
                    if status not in {"planned", "active", "paused", "completed", "cancelled"}:
                        raise DomainValidationError("project status is invalid")
                    project = replace(existing, title=title.strip(), summary=summary.strip() or title.strip(), status=status, supporting_goal_slugs=tuple(dict.fromkeys(raw_goals)), updated_at=clock())
                    receipt = adapter.update_project(project)
                except StopIteration:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Project was not found."})
                    return
                except DomainValidationError as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_project"})
                    return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "partial_write", "slug": exc.slug})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                    return
                self._json(HTTPStatus.OK, {"project": project.to_dict(), "receipt": receipt.to_dict()})
                return
            proposal_prefix = "/api/proposals/"
            proposal_review_suffix = "/review"
            if (
                path.startswith(proposal_prefix)
                and path.endswith(proposal_review_suffix)
            ):
                proposal_slug = unquote(
                    path[len(proposal_prefix) : -len(proposal_review_suffix)]
                )
                payload = self._read_json()
                if payload is None:
                    return
                if set(payload) != {
                    "title",
                    "rationale",
                    "proposed_next_step",
                    "due_day",
                }:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {
                            "error": (
                                "proposal review requires title, rationale, "
                                "proposed_next_step, and due_day."
                            ),
                            "code": "invalid_proposal_review",
                        },
                    )
                    return
                try:
                    raw_due = payload["due_day"]
                    if not isinstance(raw_due, str):
                        raise ValueError("proposal due_day must be YYYY-MM-DD")
                    due_day = date.fromisoformat(raw_due)
                    receipt = adapter.review_proposal(
                        proposal_slug,
                        title=payload["title"],
                        rationale=payload["rationale"],
                        proposed_next_step=payload["proposed_next_step"],
                        due_day=due_day,
                        now=clock(),
                    )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        {"error": str(exc), "code": "invalid_proposal_review"},
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
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
            goal_prefix = "/api/goals/"
            if path.startswith(goal_prefix) and "/" not in path[len(goal_prefix):]:
                goal_slug = unquote(path[len(goal_prefix):])
                payload = self._read_json()
                if payload is None:
                    return
                required = {"title", "outcome", "success_criteria", "strategy", "review_cadence", "constraints", "target_day"}
                if set(payload) != required or any(not isinstance(payload[field], str) for field in required):
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "Goal edit requires all goal fields and a target date.", "code": "invalid_goal"})
                    return
                try:
                    receipt = adapter.update_goal(
                        goal_slug, title=payload["title"], outcome=payload["outcome"],
                        success_criteria=payload["success_criteria"], strategy=payload["strategy"],
                        review_cadence=payload["review_cadence"], constraints=payload["constraints"],
                        target_day=date.fromisoformat(payload["target_day"]),
                    )
                except (DomainValidationError, ValueError) as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_goal"})
                    return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "partial_write", "slug": exc.slug})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"goal": receipt.goal.to_dict(), "receipt": receipt.to_dict()})
                return
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
            if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                task_slug = unquote(path[len(prefix) :])
                payload = self._read_json()
                if payload is None:
                    return
                allowed = {
                    "title", "detail", "priority", "due_day", "next_action",
                    "project_slug", "goal_slug", "status", "assignee_slug",
                    "progress_metric", "handoff_reason", "complete_when_target_reached",
                }
                if set(payload) - allowed:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "task edit contains unsupported fields.", "code": "invalid_task_edit"})
                    return
                try:
                    due_day = date.fromisoformat(payload.get("due_day", ""))
                    current = adapter.get_task(task_slug)
                    raw_metric = payload.get("progress_metric")
                    if isinstance(raw_metric, dict) and raw_metric.get("event_binding") == "job_applied":
                        event_progress = current.event_progress
                        current_value = raw_metric.get("current")
                        if event_progress is None or current_value != len(event_progress.receipt_ids):
                            raise DomainValidationError("Automatic job-applied progress is changed only by verified queue evidence.")
                        progress_metric = ProgressMetric(
                            kind=raw_metric.get("kind", "count"), label=raw_metric.get("label"),
                            unit="job_application", target=raw_metric.get("target"), current=current_value,
                            event_binding="job_applied", auto_complete=bool(raw_metric.get("auto_complete", True)),
                            task_day=due_day, timezone="America/Los_Angeles",
                        )
                    else:
                        progress_metric, event_progress = _progress_metric_from_request(raw_metric, due_day=due_day)
                    requested_status = payload.get("status")
                    if requested_status not in EDITABLE_TASK_STATUSES | {"proposed"}:
                        raise DomainValidationError("status must be a supported task status")
                    if current.status == "proposed":
                        if requested_status != "proposed":
                            raise DomainValidationError("Approve or reject proposed work through the explicit review action.")
                        if payload.get("assignee_slug", current.owner_agent) != current.owner_agent:
                            raise DomainValidationError("Proposed work keeps its assigned agent until approved.")
                    if progress_metric and progress_metric.current >= progress_metric.target and requested_status not in {"completed", "cancelled"}:
                        if payload.get("complete_when_target_reached") is not True:
                            raise DomainValidationError("Metric target is reached. Confirm completion or choose a completed status explicitly.")
                        requested_status = "completed"
                    receipt = adapter.edit_task(
                        task_slug,
                        title=payload.get("title", ""), detail=payload.get("detail", ""),
                        priority=payload.get("priority", "normal"), due_day=due_day,
                        next_action=payload.get("next_action", ""),
                        project_slug=payload.get("project_slug") or None,
                        goal_slug=payload.get("goal_slug") or None,
                        status=requested_status,
                        assignee_slug=payload.get("assignee_slug", "tony"),
                        progress_metric=progress_metric, event_progress=event_progress,
                        handoff_reason=payload.get("handoff_reason", ""), now=clock(),
                    )
                except (DomainValidationError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc), "code": "invalid_task_edit"})
                    return
                except PartialMutationError as exc:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc), "code": "partial_write", "slug": exc.slug})
                    return
                except GBrainError as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "gbrain_unavailable"})
                    return
                invalidate_snapshot()
                self._json(HTTPStatus.OK, {"receipt": receipt.to_dict()})
                return
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
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
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
                except LifecycleIntegrityError as exc:
                    self._json(HTTPStatus.CONFLICT, _lifecycle_attention_payload(exc))
                    return
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
                "/favicon.svg": "favicon.svg",
                "/favicon.ico": "favicon.ico",
                "/assets/mission-control-command-mark.svg": "assets/mission-control-command-mark.svg",
                "/assets/apple-touch-icon-180.png": "assets/apple-touch-icon-180.png",
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
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "application/json",
                "image/svg+xml",
            }:
                content_type = f"{content_type}; charset=utf-8"
            self.send_header("Content-Type", content_type)
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
    stargraph_url: str = "http://127.0.0.1:8788",
    ical_reader: ICalendarReader | None = None,
    calendar_preferences: CalendarPreferences | None = None,
) -> ThreadingHTTPServer:
    if not stargraph_url.startswith("http://127.0.0.1:"):
        raise ValueError("avatar attachment service must use a local 127.0.0.1 URL")
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
        stargraph_url.rstrip("/"),
        ical_reader,
        calendar_preferences,
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
    parser.add_argument("--stargraph-url", default=os.environ.get("MEMORY_STARGRAPH_URL", "http://127.0.0.1:8788"))
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
        stargraph_url=args.stargraph_url,
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
