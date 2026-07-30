from __future__ import annotations

import argparse
import json
import mimetypes
import uuid
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.parse import unquote

from . import __version__
from .domain import (
    ACTIVE_ROOT,
    COMPLETED_ROOT,
    DomainValidationError,
    EDITABLE_TASK_STATUSES,
    GOALS_ROOT,
    Task,
    group_today,
    new_inbox_task,
)
from .gbrain import GBrainAdapter, GBrainError, PartialMutationError
from .releases import release_payload


MAX_REQUEST_BYTES = 16 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


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
    active_read = adapter.list_collection_tasks(ACTIVE_ROOT)
    completed_read = adapter.list_collection_tasks(COMPLETED_ROOT)
    goal_read = adapter.list_goals()
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
) -> type[BaseHTTPRequestHandler]:
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
                    },
                )
                return
            if path == "/api/releases":
                self._json(HTTPStatus.OK, release_payload())
                return
            if path == "/api/tasks":
                try:
                    payload = build_task_snapshot(adapter, clock().date())
                except GBrainError as exc:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": str(exc), "code": "gbrain_unavailable"},
                    )
                    return
                self._json(HTTPStatus.OK, payload)
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
            try:
                task = new_inbox_task(
                    raw_title,
                    now=now,
                    identity=identity_factory(),
                    due_day=due_day,
                )
                receipt = adapter.create_inbox(task)
            except DomainValidationError as exc:
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
) -> ThreadingHTTPServer:
    handler = _handler_class(
        adapter or GBrainAdapter(),
        clock or (lambda: datetime.now().astimezone()),
        identity_factory or (lambda: uuid.uuid4().hex[:8]),
        static_dir,
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local GTasks interface backed by GBrain."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4179)
    args = parser.parse_args()

    server = build_server(host=args.host, port=args.port)
    print(f"GTasks listening on http://{args.host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
