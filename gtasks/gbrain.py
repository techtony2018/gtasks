from __future__ import annotations

import json
import os
import subprocess
import hashlib
import ipaddress
import hmac
import re
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Condition, Lock, current_thread
from time import monotonic, sleep, time
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .domain import (
    ACTIVE_ROOT,
    ARTIFACTS_ROOT,
    ARTIFACT_AGENT_SCOPES,
    ARTIFACT_BY_AGENT,
    ARTIFACT_BY_COLLECTION,
    AGENT_RUNTIME_BY_SLUG,
    EXISTING_CODEX_ARTIFACT_AGENT_SCOPES,
    EXISTING_CODEX_AGENT_SLUGS,
    AGENT_SCOPES,
    EXISTING_CODEX_AGENT_SCOPES,
    AGENT_WORK_ROOTS,
    AGENT_BY_WORK_ROOT,
    AgentProfile,
    AgentArtifact,
    ArtifactExecutionClaim,
    COMPLETED_ROOT,
    DomainValidationError,
    EDITABLE_TASK_STATUSES,
    EventProgress,
    GOALS_ROOT,
    Goal,
    GoalDerivationReceipt,
    LIFECYCLE_ROOTS,
    NextActionHistoryEntry,
    PROJECTS_ROOT,
    ProposalDecisionEvent,
    ProgressMetric,
    Project,
    PROPOSALS_ROOT,
    QA_FIXTURES_ROOT,
    SYSTEM_TICKETS_ROOT,
    SYSTEM_TICKET_STATUSES,
    SystemTicket,
    TASK_SCOPE_ROOTS,
    Task,
    TaskProposal,
    TodoComment,
    TodoEvent,
    TodoItem,
    new_task,
)
from .handoff import TaskHandoff
from .delegation import AgentDelegationLease, DelegationState, lease_state_at
from .handoff_dispatcher import (
    ActionableChange,
    AgentRegistration,
    HandoffDispatcher,
)
from .markdown_policy import (
    MARKDOWN_CONTRACT,
    MarkdownContractError,
    SystemTicketReference,
    extract_system_ticket_slugs,
    reference_is_explicitly_labeled_system_ticket,
    render_system_ticket_body,
    render_task_body,
)

if TYPE_CHECKING:
    from .goal_execution import GoalExecutionCandidate, GoalExecutionSnapshot


APPROVED_ROOTS = frozenset({ACTIVE_ROOT, COMPLETED_ROOT, QA_FIXTURES_ROOT})
# Memory Stargraph budgets are 6.5s for provision, 4.5s for recovery request,
# and 2.5s for status/active. Caller deadlines retain transport margin.
OPENCLAW_SUBMIT_CALLER_TIMEOUT_SECONDS = 8.0
OPENCLAW_STATUS_CALLER_TIMEOUT_SECONDS = 4.0
TONY_PROFILE_SLUG = "people/tony-guan"
AGENT_DELEGATIONS_ROOT = "collections/mission-control-agent-delegations"
_MARKDOWN_ATTACHMENT = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
APPROVED_OPENCLAW_DECLARATIONS: dict[str, dict[str, str]] = {
    "agents/tammy-oc": {
        "slug": "agents/tammy-oc",
        "name": "Tammy-OC",
        "runtime": "openclaw",
        "route": "hosts/tammy",
        "task_collection": "collections/tammy-oc-tasks",
        "artifact_collection": "collections/tammy-oc-artifacts",
    },
    "agents/timmy-oc": {
        "slug": "agents/timmy-oc",
        "name": "Timmy-OC",
        "runtime": "openclaw",
        "route": "hosts/timmy",
        "task_collection": "collections/timmy-oc-tasks",
        "artifact_collection": "collections/timmy-oc-artifacts",
    },
    "agents/toddy-oc": {
        "slug": "agents/toddy-oc",
        "name": "Toddy-OC",
        "runtime": "openclaw",
        "route": "hosts/toddy",
        "task_collection": "collections/toddy-oc-tasks",
        "artifact_collection": "collections/toddy-oc-artifacts",
    },
}
HANDOFF_ROUTE_BY_AGENT: dict[str, str] = {
    "agents/tammy": "hosts/tammy",
    "agents/tammy-oc": "hosts/tammy",
    "agents/timmy": "hosts/timmy",
    "agents/timmy-oc": "hosts/timmy",
    "agents/toddy": "hosts/toddy",
    "agents/toddy-oc": "hosts/toddy",
}


def _openclaw_active_manifest_identity_is_valid(
    generation: Any, manifest_slug: Any, manifest_digest: Any
) -> bool:
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
    ):
        return False
    if generation == 0:
        return manifest_slug is None and manifest_digest is None
    if (
        not isinstance(manifest_slug, str)
        or not isinstance(manifest_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_digest) is None
    ):
        return False
    prefix = (
        "system/openclaw-profile-manifests/"
        f"g{generation:06d}-"
    )
    if not manifest_slug.startswith(prefix):
        return False
    operation_id = manifest_slug[len(prefix) :]
    return (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", operation_id)
        is not None
    )


def _canonical_json_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class GBrainError(RuntimeError):
    """Base error for GBrain command, protocol, and verification failures."""


class GBrainCommandError(GBrainError):
    pass


def is_page_not_found_error(exc: GBrainCommandError) -> bool:
    message = str(exc).casefold()
    return "page_not_found" in message or "page not found" in message


class GBrainProtocolError(GBrainError):
    pass


class CanonicalRootError(GBrainError):
    """Required canonical Goal execution authority could not be verified."""

    def __init__(self, roots: Sequence[str]) -> None:
        self.roots = tuple(sorted(dict.fromkeys(str(root) for root in roots)))
        super().__init__(
            "Goal execution canonical authority is unavailable for: "
            + ", ".join(self.roots)
        )


class ArtifactIdempotencyConflict(ValueError):
    """A publication key already identifies different canonical content."""

    pass


class LifecycleIntegrityError(ValueError):
    """A task has no single canonical lifecycle membership to mutate safely."""

    def __init__(self, task_slug: str, edges: list[Mapping[str, Any]]) -> None:
        self.task_slug = task_slug
        self.edge_count = len(edges)
        self.roots = tuple(
            sorted(
                {
                    str(edge.get("to_slug"))
                    for edge in edges
                    if isinstance(edge.get("to_slug"), str)
                }
            )
        )
        root_copy = ", ".join(self.roots) if self.roots else "none"
        super().__init__(
            f"Task {task_slug} has {self.edge_count} verified lifecycle memberships "
            f"({root_copy}). No change was made. Inspect and repair its lifecycle "
            "membership before retrying."
        )


class ConcurrentTodoUpdateError(ValueError):
    """The canonical TODO changed after the caller rendered it."""

    def __init__(self, todo_slug: str) -> None:
        self.todo_slug = todo_slug
        super().__init__(
            f"TODO {todo_slug} changed since it was read. Refresh the task and retry the same item."
        )


class ConcurrentAgentDelegationUpdateError(ValueError):
    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"Agent delegation {slug} changed; refresh before retrying.")


class PartialMutationError(GBrainError):
    """A page may exist in GBrain, but the complete mutation was not verified."""

    def __init__(self, slug: str, message: str) -> None:
        self.slug = slug
        super().__init__(f"{message} Page slug: {slug}")


class CommandRunner(Protocol):
    def run(self, tool: str, params: dict[str, Any]) -> object: ...


class OpenClawProfileActivationClient(Protocol):
    """Read/write boundary for Memory Stargraph's activation authority."""

    def submit(
        self,
        declarations: Sequence[Mapping[str, str]],
        *,
        owner: str,
        operation_id: str,
    ) -> Mapping[str, Any]: ...

    def status(
        self, operation_id: str, *, timeout_seconds: float | None = None
    ) -> Mapping[str, Any]: ...

    def recover(
        self, operation_id: str, *, timeout_seconds: float | None = None
    ) -> Mapping[str, Any]: ...

    def wait(
        self,
        operation_id: str,
        *,
        initial: Mapping[str, Any] | None = None,
        on_status: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]: ...

    def active_projection(self) -> Mapping[str, Any]: ...


class MemoryStargraphOpenClawProfileClient:
    """Authenticated client for the fail-closed Stargraph activation endpoint."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float | None = None,
        submit_timeout_seconds: float = OPENCLAW_SUBMIT_CALLER_TIMEOUT_SECONDS,
        status_timeout_seconds: float = OPENCLAW_STATUS_CALLER_TIMEOUT_SECONDS,
        poll_timeout_seconds: float = 180,
        poll_interval_seconds: float = 0.5,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        try:
            parsed_base_url = urlsplit(base_url)
            port = parsed_base_url.port
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Memory Stargraph OpenClaw activation client is not configured"
            ) from error
        hostname = (parsed_base_url.hostname or "").lower()
        loopback_host = hostname == "localhost"
        if hostname and not loopback_host:
            try:
                loopback_host = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                loopback_host = False
        if (
            parsed_base_url.scheme != "http"
            or not loopback_host
            or port is None
            or port < 1
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.path not in {"", "/"}
            or parsed_base_url.query
            or parsed_base_url.fragment
            or not token
        ):
            raise ValueError("Memory Stargraph OpenClaw activation client is not configured")
        if timeout_seconds is not None:
            submit_timeout_seconds = timeout_seconds
            status_timeout_seconds = timeout_seconds
        if (
            submit_timeout_seconds <= 0
            or submit_timeout_seconds > 30
            or status_timeout_seconds <= 0
            or status_timeout_seconds > 30
            or poll_timeout_seconds < max(
                submit_timeout_seconds, status_timeout_seconds
            )
            or poll_timeout_seconds > 3600
            or poll_interval_seconds <= 0
            or poll_interval_seconds > 10
        ):
            raise ValueError("Memory Stargraph activation timeouts are not aligned")
        rendered_host = f"[{hostname}]" if ":" in hostname else hostname
        self.base_url = f"http://{rendered_host}:{port}"
        self.token = token
        self.submit_timeout_seconds = submit_timeout_seconds
        self.status_timeout_seconds = status_timeout_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.sleeper = sleeper
        self.clock = clock

    @classmethod
    def from_environment(cls) -> "MemoryStargraphOpenClawProfileClient":
        return cls(
            os.environ.get("MEMORY_STARGRAPH_URL", ""),
            os.environ.get("MEMORY_STARGRAPH_OC_PROVISION_TOKEN", ""),
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        body = None if payload is None else json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise GBrainCommandError("Memory Stargraph OpenClaw activation request failed") from exc
        if not isinstance(parsed, Mapping) or parsed.get("ok") is not True:
            raise GBrainProtocolError("Memory Stargraph OpenClaw activation response was invalid")
        return dict(parsed)

    @staticmethod
    def _operation_response(
        payload: Mapping[str, Any], operation_id: str
    ) -> dict[str, Any]:
        item = {key: value for key, value in payload.items() if key != "ok"}
        if (
            set(item)
            != {
                "operation_id",
                "status",
                "fence_generation",
                "receipt",
                "error",
                "recovery_request_generation",
                "recovery_processed_generation",
            }
            or item.get("operation_id") != operation_id
            or not isinstance(operation_id, str)
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", operation_id
            )
            is None
            or item.get("status")
            not in {"accepted", "running", "completed", "failed", "recovery_required"}
            or (
                item.get("fence_generation") is not None
                and (
                    isinstance(item["fence_generation"], bool)
                    or not isinstance(item["fence_generation"], int)
                    or item["fence_generation"] < 1
                )
            )
            or (item.get("receipt") is not None and not isinstance(item["receipt"], Mapping))
            or (item.get("error") is not None and not isinstance(item["error"], str))
            or isinstance(item.get("recovery_request_generation"), bool)
            or not isinstance(item.get("recovery_request_generation"), int)
            or item["recovery_request_generation"] < 0
            or isinstance(item.get("recovery_processed_generation"), bool)
            or not isinstance(item.get("recovery_processed_generation"), int)
            or item["recovery_processed_generation"] < 0
            or item["recovery_processed_generation"]
            > item["recovery_request_generation"]
        ):
            raise GBrainProtocolError(
                f"Memory Stargraph activation status was invalid for {operation_id}"
            )
        if (
            item["status"] == "completed"
            and (
                not isinstance(item["receipt"], Mapping)
                or item.get("error") is not None
            )
        ):
            raise GBrainProtocolError(
                f"Memory Stargraph completed without a receipt for {operation_id}"
            )
        if item["status"] == "failed" and (
            item.get("receipt") is not None
            or not isinstance(item.get("error"), str)
            or not item["error"]
        ):
            raise GBrainProtocolError(
                f"Memory Stargraph failed terminal state was invalid for {operation_id}"
            )
        if item["status"] == "completed":
            receipt = item["receipt"]
            if (
                set(receipt)
                != {
                    "generation",
                    "manifest_slug",
                    "manifest_digest",
                    "default_goal_link_count",
                }
                or isinstance(receipt.get("generation"), bool)
                or not isinstance(receipt.get("generation"), int)
                or receipt["generation"] < 1
                or receipt["generation"] != item["fence_generation"]
                or receipt.get("manifest_slug")
                != "system/openclaw-profile-manifests/"
                f"g{receipt['generation']:06d}-{operation_id}"
                or not isinstance(receipt.get("manifest_digest"), str)
                or re.fullmatch(r"[0-9a-f]{64}", receipt["manifest_digest"]) is None
                or isinstance(receipt.get("default_goal_link_count"), bool)
                or not isinstance(receipt.get("default_goal_link_count"), int)
                or receipt.get("default_goal_link_count") != 0
            ):
                raise GBrainProtocolError(
                    f"Memory Stargraph terminal receipt was invalid for {operation_id}"
                )
        return dict(item)

    def submit(
        self,
        declarations: Sequence[Mapping[str, str]],
        *,
        owner: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        response = self._request(
            "POST", "/api/internal/openclaw-profiles/provision",
            {"declarations": list(declarations), "owner": owner, "operation_id": operation_id},
            timeout_seconds=self.submit_timeout_seconds,
        )
        return self._operation_response(response, operation_id)

    def status(
        self, operation_id: str, *, timeout_seconds: float | None = None
    ) -> Mapping[str, Any]:
        response = self._request(
            "GET",
            f"/api/internal/openclaw-profiles/operations/{quote(operation_id, safe='')}",
            timeout_seconds=min(
                self.status_timeout_seconds,
                timeout_seconds
                if timeout_seconds is not None
                else self.status_timeout_seconds,
            ),
        )
        return self._operation_response(response, operation_id)

    def recover(
        self, operation_id: str, *, timeout_seconds: float | None = None
    ) -> Mapping[str, Any]:
        response = self._request(
            "POST",
            f"/api/internal/openclaw-profiles/operations/{quote(operation_id, safe='')}/recover",
            timeout_seconds=min(
                self.submit_timeout_seconds,
                timeout_seconds
                if timeout_seconds is not None
                else self.submit_timeout_seconds,
            ),
        )
        return self._operation_response(response, operation_id)

    def wait(
        self,
        operation_id: str,
        *,
        initial: Mapping[str, Any] | None = None,
        on_status: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Mapping[str, Any]:
        deadline = self.clock() + self.poll_timeout_seconds
        current = (
            self._operation_response(initial, operation_id)
            if initial is not None
            else dict(
                self.status(
                    operation_id,
                    timeout_seconds=min(
                        self.status_timeout_seconds,
                        max(0.001, deadline - self.clock()),
                    ),
                )
            )
        )
        recovery_generation: int | None = None
        while True:
            if on_status is not None:
                on_status(current)
            status = current["status"]
            if recovery_generation is not None:
                if (
                    current["recovery_processed_generation"]
                    < recovery_generation
                ):
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise GBrainCommandError(
                            "Memory Stargraph activation polling timed out for "
                            f"{operation_id}"
                        )
                    self.sleeper(
                        min(self.poll_interval_seconds, remaining)
                    )
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        continue
                    current = self._operation_response(
                        self.status(
                            operation_id,
                            timeout_seconds=min(
                                self.status_timeout_seconds, remaining
                            ),
                        ),
                        operation_id,
                    )
                    continue
                recovery_generation = None
            if status == "completed":
                return current
            if status == "failed":
                raise GBrainCommandError(
                    f"Memory Stargraph activation failed for {operation_id}: "
                    f"{current.get('error') or 'unknown failure'}"
                )
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise GBrainCommandError(
                    f"Memory Stargraph activation polling timed out for {operation_id}"
                )
            if status == "recovery_required":
                current = self._operation_response(
                    self.recover(
                        operation_id,
                        timeout_seconds=min(
                            self.submit_timeout_seconds, remaining
                        ),
                    ),
                    operation_id,
                )
                recovery_generation = current[
                    "recovery_request_generation"
                ]
                if (
                    current["status"] == "recovery_required"
                    and recovery_generation
                    <= current["recovery_processed_generation"]
                ):
                    raise GBrainProtocolError(
                        "Memory Stargraph recovery response did not queue a "
                        f"generation for {operation_id}"
                    )
                continue
            self.sleeper(min(self.poll_interval_seconds, remaining))
            remaining = deadline - self.clock()
            if remaining <= 0:
                continue
            current = self._operation_response(
                self.status(
                    operation_id,
                    timeout_seconds=min(self.status_timeout_seconds, remaining),
                ),
                operation_id,
            )

    def provision(
        self,
        declarations: Sequence[Mapping[str, str]],
        *,
        owner: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        accepted = self.submit(
            declarations, owner=owner, operation_id=operation_id
        )
        return self.wait(operation_id, initial=accepted)

    def active_projection(self) -> Mapping[str, Any]:
        projection = self._request(
            "GET",
            "/api/internal/openclaw-profiles/active",
            timeout_seconds=self.status_timeout_seconds,
        )
        status = projection.get("status")
        if status == "validation_pending":
            raise GBrainCommandError(
                "Memory Stargraph OpenClaw active projection validation is pending"
            )
        if (
            status != "ready"
            or isinstance(projection.get("control_revision"), bool)
            or not isinstance(projection.get("control_revision"), int)
            or projection["control_revision"] < 0
            or isinstance(projection.get("validated_at"), bool)
            or not isinstance(projection.get("validated_at"), (int, float))
            or isinstance(projection.get("generation"), bool)
            or not isinstance(projection.get("generation"), int)
            or projection["generation"] < 0
            or not isinstance(projection.get("profiles"), list)
            or not _openclaw_active_manifest_identity_is_valid(
                projection.get("generation"),
                projection.get("active_manifest"),
                projection.get("manifest_digest"),
            )
        ):
            raise GBrainProtocolError(
                "Memory Stargraph OpenClaw active projection was invalid"
            )
        return projection


class SubprocessCommandRunner:
    def __init__(self, executable: str = "gbrain", timeout_seconds: float = 30) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        # Every gbrain CLI subprocess negotiates OAuth independently. A single
        # canonical lane prevents concurrent token mints from invalidating
        # otherwise safe reads. Higher-level adapters may still prepare work
        # concurrently, but the remote CLI boundary remains serialized.
        self._lane_condition = Condition()
        self._lane_active = False
        self._foreground_operations = 0
        self._auth_recovery = Lock()

    @contextmanager
    def foreground_operation(self):
        with self._lane_condition:
            self._foreground_operations += 1
            self._lane_condition.notify_all()
        try:
            yield
        finally:
            with self._lane_condition:
                self._foreground_operations -= 1
                self._lane_condition.notify_all()

    @contextmanager
    def _lane(self):
        is_background_refresh = (
            current_thread().name.startswith("gtasks-")
            and current_thread().name.endswith("-refresh")
        )
        with self._lane_condition:
            while self._lane_active or (
                is_background_refresh and self._foreground_operations
            ):
                self._lane_condition.wait()
            self._lane_active = True
        try:
            yield
        finally:
            with self._lane_condition:
                self._lane_active = False
                self._lane_condition.notify_all()

    def run(self, tool: str, params: dict[str, Any]) -> object:
        payload = json.dumps(params, separators=(",", ":"))
        command = [self.executable, "call", tool, payload]

        def invoke() -> subprocess.CompletedProcess[str]:
            try:
                return subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise GBrainCommandError(
                    f"GBrain executable not found: {self.executable}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise GBrainCommandError(
                    f"GBrain tool {tool} timed out after {self.timeout_seconds:g}s"
                ) from exc

        with self._lane():
            result = invoke()

        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        safe_read = tool in {"get_page", "get_links", "get_backlinks", "list_pages"}
        if (
            result.returncode != 0
            and safe_read
            and "auth failed after token refresh" in detail.casefold()
        ):
            # The CLI authenticates per process. If two safe reads race a token
            # refresh, perform a bounded serialized recovery with one short
            # cooldown. Never retry a write because its remote outcome may be
            # ambiguous.
            with self._auth_recovery:
                with self._lane():
                    for recovery_attempt in range(2):
                        if recovery_attempt:
                            sleep(0.5)
                        result = invoke()
                        detail = (
                            result.stderr.strip()
                            or result.stdout.strip()
                            or "unknown error"
                        )
                        if (
                            result.returncode == 0
                            or "auth failed after token refresh"
                            not in detail.casefold()
                        ):
                            break

        if result.returncode != 0:
            raise GBrainCommandError(f"GBrain tool {tool} failed: {detail}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GBrainProtocolError(
                f"GBrain tool {tool} returned invalid JSON"
            ) from exc


class RemoteHttpCommandRunner(SubprocessCommandRunner):
    """Persistent-token thin-client runner for the remote stateless MCP."""

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self._config_path = config_path or self._default_config_path()
        self._allow_dashboard_fallback = config_path is None
        self._credentials_path: Path | None = None
        self._token_lock = Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_endpoint: str | None = None

    @staticmethod
    def _default_config_path() -> Path:
        configured_path = os.environ.get("GBRAIN_CONFIG_FILE")
        if configured_path:
            return Path(configured_path).expanduser()
        configured_home = os.environ.get("GBRAIN_HOME")
        base = Path(configured_home).expanduser() if configured_home else Path.home()
        return base / ".gbrain" / "config.json"

    @staticmethod
    def _dashboard_integration_paths() -> tuple[Path, ...]:
        paths: list[Path] = []
        for candidate_root in (Path.cwd(), *Path.cwd().parents):
            paths.append(candidate_root / "dashboard-integration.json")
        try:
            module_path = Path(__file__).resolve()
        except OSError:
            module_path = Path(__file__)
        for candidate_root in module_path.parents:
            paths.append(candidate_root / "dashboard-integration.json")

        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return tuple(unique)

    @staticmethod
    def _dashboard_remote_runtime_paths() -> tuple[Path, Path | None] | None:
        for contract_path in RemoteHttpCommandRunner._dashboard_integration_paths():
            try:
                contract = json.loads(contract_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                continue
            remote = contract.get("remote_mcp") if isinstance(contract, Mapping) else None
            if not isinstance(remote, Mapping):
                continue
            config = remote.get("config")
            credentials = remote.get("credentials")
            if not isinstance(config, str) or not config.strip():
                continue
            credentials_path = (
                Path(credentials).expanduser()
                if isinstance(credentials, str) and credentials.strip()
                else None
            )
            return Path(config).expanduser(), credentials_path
        return None

    @staticmethod
    def _read_remote_secret_from_file(path: Path) -> str | None:
        try:
            mode = path.stat().st_mode & 0o777
            if mode != 0o600:
                raise GBrainCommandError(
                    "GBrain remote client secret permissions are invalid"
                )
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.startswith("GBRAIN_REMOTE_CLIENT_SECRET="):
                    continue
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
        except GBrainCommandError:
            raise
        except OSError as exc:
            raise GBrainCommandError(
                "GBrain remote client secret is unavailable"
            ) from exc
        return None

    def _remote_config(self) -> dict[str, str]:
        config_path = self._config_path
        credentials_path = self._credentials_path
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            raise GBrainCommandError("GBrain remote config is unavailable") from exc
        remote = raw.get("remote_mcp") if isinstance(raw, Mapping) else None
        if not isinstance(remote, Mapping):
            fallback = (
                self._dashboard_remote_runtime_paths()
                if self._allow_dashboard_fallback
                else None
            )
            if fallback is None:
                raise GBrainCommandError("GBrain remote_mcp config is unavailable")
            config_path, credentials_path = fallback
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
                raise GBrainCommandError("GBrain remote config is unavailable") from exc
            remote = raw.get("remote_mcp") if isinstance(raw, Mapping) else None
            if not isinstance(remote, Mapping):
                raise GBrainCommandError("GBrain remote_mcp config is unavailable")
            self._config_path = config_path
            self._credentials_path = credentials_path
        result: dict[str, str] = {}
        for field in ("issuer_url", "mcp_url", "oauth_client_id"):
            value = remote.get(field)
            if not isinstance(value, str) or not value.strip():
                raise GBrainCommandError(f"GBrain remote_mcp {field} is unavailable")
            result[field] = value.strip()
        secret = os.environ.get("GBRAIN_REMOTE_CLIENT_SECRET") or remote.get(
            "oauth_client_secret"
        )
        if (not isinstance(secret, str) or not secret) and credentials_path is not None:
            secret = self._read_remote_secret_from_file(credentials_path)
        if not isinstance(secret, str) or not secret:
            raise GBrainCommandError("GBrain remote client secret is unavailable")
        result["oauth_client_secret"] = secret
        return result

    def _read_json_response(self, request: Request) -> object:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as exc:
            raise GBrainCommandError(
                f"GBrain remote request failed with HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GBrainCommandError("GBrain remote request failed") from exc
        try:
            if "text/event-stream" in content_type:
                data_lines = [
                    line[5:].strip()
                    for line in raw.splitlines()
                    if line.startswith("data:")
                ]
                if not data_lines:
                    raise ValueError("missing SSE data")
                return json.loads(data_lines[-1])
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise GBrainProtocolError("GBrain remote response was invalid") from exc

    def _access_token(self, remote: Mapping[str, str]) -> str:
        with self._token_lock:
            if self._token is not None and self._token_expires_at > time() + 30:
                return self._token
            if self._token_endpoint is None:
                discovery_url = (
                    remote["issuer_url"].rstrip("/")
                    + "/.well-known/oauth-authorization-server"
                )
                metadata = self._read_json_response(Request(discovery_url))
                endpoint = (
                    metadata.get("token_endpoint")
                    if isinstance(metadata, Mapping)
                    else None
                )
                if not isinstance(endpoint, str) or not endpoint:
                    raise GBrainProtocolError(
                        "GBrain OAuth discovery omitted token_endpoint"
                    )
                self._token_endpoint = endpoint
            body = urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": remote["oauth_client_id"],
                    "client_secret": remote["oauth_client_secret"],
                }
            ).encode("utf-8")
            token_payload = self._read_json_response(
                Request(
                    self._token_endpoint,
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            )
            token = (
                token_payload.get("access_token")
                if isinstance(token_payload, Mapping)
                else None
            )
            if not isinstance(token, str) or not token:
                raise GBrainProtocolError("GBrain OAuth response omitted access_token")
            expires_in = token_payload.get("expires_in", 3600)
            ttl = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0
            self._token = token
            self._token_expires_at = time() + max(60.0, ttl)
            return token

    def _call(self, remote: Mapping[str, str], token: str, tool: str, params: dict[str, Any]) -> object:
        envelope = self._read_json_response(
            Request(
                remote["mcp_url"],
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": uuid.uuid4().hex,
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": params},
                    },
                    separators=(",", ":"),
                ).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        )
        result = envelope.get("result") if isinstance(envelope, Mapping) else None
        if not isinstance(result, Mapping):
            raise GBrainProtocolError("GBrain remote response omitted result")
        content = result.get("content")
        text_blocks = [
            item.get("text", "")
            for item in content
            if isinstance(item, Mapping) and item.get("type") == "text"
        ] if isinstance(content, list) else []
        if result.get("isError"):
            detail = "\n".join(str(value) for value in text_blocks if value).strip()
            raise GBrainCommandError(
                f"GBrain tool {tool} failed: {detail or 'unknown remote error'}"
            )
        if not text_blocks:
            structured = result.get("structuredContent")
            if structured is not None:
                return structured
            raise GBrainProtocolError("GBrain remote result omitted text content")
        try:
            return json.loads(str(text_blocks[-1]))
        except json.JSONDecodeError as exc:
            raise GBrainProtocolError(
                f"GBrain tool {tool} returned invalid JSON"
            ) from exc

    def run(self, tool: str, params: dict[str, Any]) -> object:
        remote = self._remote_config()
        with self._lane():
            token = self._access_token(remote)
            return self._call(remote, token, tool, params)


@dataclass(frozen=True, slots=True)
class CollectionIssue:
    slug: str
    message: str
    severity: str = "error"
    task_visible: bool = False
    category: str = "core_data"
    impact: str = "This task could not be shown until its core data is corrected."
    repair_action: str | None = None
    owner_agent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "message": self.message,
            "severity": self.severity,
            "task_visible": self.task_visible,
            "category": self.category,
            "impact": self.impact,
            "repair_action": self.repair_action,
        }


@dataclass(frozen=True, slots=True)
class CollectionRead:
    root_slug: str
    tasks: tuple[Task, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_slug": self.root_slug,
            "tasks": [task.to_dict() for task in self.tasks],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class AgentRead:
    agents: tuple[AgentProfile, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents": [agent.to_dict() for agent in self.agents],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class AgentProvisioningReceipt:
    """Readback evidence for one explicit OpenClaw profile provision."""

    agent_slug: str
    collection_slugs: tuple[str, str]
    default_goal_slugs: tuple[str, ...]
    operations: tuple[str, ...]
    verified: bool
    mutated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_slug": self.agent_slug,
            "collection_slugs": list(self.collection_slugs),
            "default_goal_slugs": list(self.default_goal_slugs),
            "operations": list(self.operations),
            "verified": self.verified,
            "mutated": self.mutated,
        }


@dataclass(frozen=True, slots=True)
class AgentWorkRead:
    tasks: tuple[dict[str, Any], ...]
    issues: tuple[CollectionIssue, ...] = ()
    roots: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [dict(task) for task in self.tasks],
            "issues": [issue.to_dict() for issue in self.issues],
            "roots": list(self.roots),
        }


@dataclass(frozen=True, slots=True)
class ArtifactRead:
    artifacts: tuple[AgentArtifact, ...]
    issues: tuple[CollectionIssue, ...] = ()
    next_cursor: int | None = None
    relation_context: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        artifacts = []
        for artifact in self.artifacts:
            value = artifact.to_dict()
            contexts = self.relation_context.get(artifact.slug)
            if contexts:
                value["relation_context"] = list(contexts)
            artifacts.append(value)
        return {
            "artifacts": artifacts,
            "issues": [issue.to_dict() for issue in self.issues],
            "next_cursor": self.next_cursor,
        }


@dataclass(frozen=True, slots=True)
class ArtifactMutationReceipt:
    artifact: AgentArtifact
    verified: bool
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "verified": self.verified,
            "idempotent": self.idempotent,
        }


@dataclass(frozen=True, slots=True)
class ArtifactReviewReferenceReceipt:
    task_slug: str
    artifact_slug: str
    verified: bool
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "artifact_slug": self.artifact_slug,
            "verified": self.verified,
            "idempotent": self.idempotent,
        }


@dataclass(frozen=True, slots=True)
class ProposalRead:
    proposals: tuple[TaskProposal, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_slug": PROPOSALS_ROOT,
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class ProposalMutationReceipt:
    proposal_slug: str
    status: str
    proposal: TaskProposal
    created_task: Task | None
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_slug": self.proposal_slug,
            "status": self.status,
            "proposal": self.proposal.to_dict(),
            "created_task": (
                self.created_task.to_dict() if self.created_task else None
            ),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    slug: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "verified": self.verified}


@dataclass(frozen=True, slots=True)
class MembershipRepairReceipt:
    task_slug: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"task_slug": self.task_slug, "verified": self.verified}


@dataclass(frozen=True, slots=True)
class CompletedArchiveReceipt:
    archived_slugs: tuple[str, ...]
    skipped_slugs: tuple[str, ...]
    issues: tuple[CollectionIssue, ...] = ()
    verified: bool = True

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "archived_slugs": list(self.archived_slugs),
            "skipped_slugs": list(self.skipped_slugs),
            "issues": [issue.to_dict() for issue in self.issues],
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class GoalRead:
    goals: tuple[Goal, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_slug": GOALS_ROOT,
            "goals": [goal.to_dict() for goal in self.goals],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class IdentityMigrationReceipt:
    mapping: Mapping[str, str]
    migrated: tuple[str, ...]
    excluded: tuple[str, ...]
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping": dict(self.mapping),
            "migrated": list(self.migrated),
            "excluded": list(self.excluded),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class ProjectRead:
    projects: tuple[Project, ...]
    issues: tuple[CollectionIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_slug": PROJECTS_ROOT,
            "projects": [project.to_dict() for project in self.projects],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class SystemTicketRead:
    tickets: tuple[SystemTicket, ...]
    issues: tuple[CollectionIssue, ...] = ()
    display_markdown: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        projections = dict(self.display_markdown)
        tickets = []
        for ticket in self.tickets:
            payload = ticket.to_dict()
            if ticket.slug in projections:
                payload["display_markdown"] = projections[ticket.slug]
            tickets.append(payload)
        return {"root_slug": SYSTEM_TICKETS_ROOT, "tickets": tickets, "issues": [issue.to_dict() for issue in self.issues]}


@dataclass(frozen=True, slots=True)
class GoalRelationshipRead:
    goal_slug: str
    task_slugs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_slug": self.goal_slug,
            "task_slugs": list(self.task_slugs),
        }


@dataclass(frozen=True, slots=True)
class GoalLinkReceipt:
    task_slug: str
    goal_slug: str | None
    verified: bool
    reciprocal_verified: bool = True
    reconciled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "goal_slug": self.goal_slug,
            "verified": self.verified,
            "reciprocal_verified": self.reciprocal_verified,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True, slots=True)
class StatusMutationReceipt:
    task_slug: str
    status: str
    lifecycle_root: str
    completed_at: datetime | None
    task: Task
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "status": self.status,
            "lifecycle_root": self.lifecycle_root,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "task": self.task.to_dict(),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class NextActionMutationReceipt:
    task_slug: str
    next_action: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "next_action": self.next_action,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class TodoRead:
    todos: tuple[TodoItem, ...]
    issues: tuple[CollectionIssue, ...] = ()
    next_cursor: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "todos": [todo.to_dict() for todo in self.todos],
            "issues": [issue.to_dict() for issue in self.issues],
            "next_cursor": self.next_cursor,
        }


@dataclass(frozen=True, slots=True)
class TodoMutationReceipt:
    todo: TodoItem
    verified: bool
    idempotent: bool = False
    parent_relationship_verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "todo": self.todo.to_dict(),
            "verified": self.verified,
            "idempotent": self.idempotent,
            "parent_relationship_verified": self.parent_relationship_verified,
        }


@dataclass(frozen=True, slots=True)
class HandoffMutationReceipt:
    task: Task
    todo: TodoItem
    event: TodoEvent | None
    next_owner: str | None
    verified: bool
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "todo": self.todo.to_dict(),
            "event": self.event.to_dict() if self.event else None,
            "next_owner": self.next_owner,
            "verified": self.verified,
            "idempotent": self.idempotent,
        }


@dataclass(frozen=True, slots=True)
class TaskProgressMetricReceipt:
    task_slug: str
    task: Task
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "task": self.task.to_dict(),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class TaskProgressEventReceipt:
    task_slug: str
    task: Task
    duplicate: bool
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "task": self.task.to_dict(),
            "duplicate": self.duplicate,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class ProjectMutationReceipt:
    project_slug: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"project_slug": self.project_slug, "verified": self.verified}


@dataclass(frozen=True, slots=True)
class GoalMutationReceipt:
    goal_slug: str
    goal: Goal
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_slug": self.goal_slug,
            "goal": self.goal.to_dict(),
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class GoalDeletionReceipt:
    goal_slug: str
    removed_task_links: tuple[str, ...]
    recoverable_until_hours: int
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_slug": self.goal_slug,
            "removed_task_links": list(self.removed_task_links),
            "recoverable_until_hours": self.recoverable_until_hours,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class ProjectAssignmentReceipt:
    task_slug: str
    project_slug: str | None
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "project_slug": self.project_slug,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class TaskEditReceipt:
    task_slug: str
    task: Task
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "task": self.task.to_dict(),
            "verified": self.verified,
        }


class CanonicalHandoffEventBridge:
    """Normalize verified canonical readback into one durable handoff record."""

    _PRESENTATION_FIELDS = frozenset(
        {"title", "summary", "detail", "priority", "due_day", "due_at", "scheduled_day"}
    )
    _DERIVED_FIELDS = frozenset({"progress_metric", "event_progress"})
    _TONY_ANSWER_KINDS = frozenset(
        {
            "answer_agent_question",
            "task_answer_field_saved",
            "task_body_answer_saved",
            "todo_answer_detail_saved",
            "todo_answer_comment_saved",
            "waiting_for_information_answered",
            "waiting_for_information_updated",
        }
    )

    def __init__(self, dispatcher: HandoffDispatcher) -> None:
        self.dispatcher = dispatcher

    def latest_task_handoff_status(self, task_slug: str) -> str | None:
        return self.dispatcher.store.latest_task_handoff_status(task_slug)

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            rendered = to_dict()
            if isinstance(rendered, Mapping):
                return dict(rendered)
        return {}

    @classmethod
    def _snapshot(
        cls, value: object
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        outer = cls._mapping(value)
        task = cls._mapping(outer.get("task"))
        if not task and isinstance(outer.get("slug"), str):
            task = outer
        todo = cls._mapping(outer.get("todo"))
        return outer, task, todo

    @staticmethod
    def _safe_identifier(value: object, *, namespace: str, seed: str) -> str:
        if isinstance(value, str) and re.fullmatch(
            r"[a-z0-9][a-z0-9._/-]{0,127}", value
        ):
            return value
        return f"{namespace}/{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _assigned_to(outer: Mapping[str, Any], task: Mapping[str, Any]) -> tuple[str, ...]:
        raw = outer.get("assigned_to", task.get("assigned_to"))
        if raw is None:
            owner = task.get("owner_agent")
            if not isinstance(owner, str):
                handoff = task.get("handoff")
                owner = handoff.get("resume_owner") if isinstance(handoff, Mapping) else None
            raw = [owner] if isinstance(owner, str) else []
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(value for value in raw if isinstance(value, str))

    @staticmethod
    def _changed_fields(
        before: Mapping[str, Any], after: Mapping[str, Any]
    ) -> frozenset[str]:
        ignored = {"updated_at", "created_at", "events", "event_slugs"}
        return frozenset(
            key
            for key in set(before) | set(after)
            if key not in ignored and before.get(key) != after.get(key)
        )

    @staticmethod
    def _comment_body(comment: object) -> str | None:
        if not isinstance(comment, Mapping):
            return None
        author = comment.get("author") or comment.get("actor")
        if author != TONY_PROFILE_SLUG:
            return None
        body = comment.get("body") or comment.get("text") or comment.get("detail")
        if not isinstance(body, str) or not body.strip():
            return None
        return body.strip()

    @classmethod
    def _semantic_answer_text(
        cls,
        task: Mapping[str, Any],
        todo: Mapping[str, Any],
        *,
        kind: object,
    ) -> str | None:
        kind_text = str(kind or "")
        answerish = "answer" in kind_text or "waiting_for_information" in kind_text
        candidates: list[str] = []
        for field in (
            "answer",
            "tony_answer",
            "answer_text",
            "answer_detail",
            "decision_note",
        ):
            value = task.get(field)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
        if answerish:
            for value in (task.get("detail"), task.get("body"), todo.get("detail")):
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
        raw_comments = todo.get("comments")
        if isinstance(raw_comments, (list, tuple)):
            for comment in raw_comments:
                body = cls._comment_body(comment)
                if body is not None:
                    candidates.append(body)
        if not candidates:
            return None
        return "\n---\n".join(candidates)

    @classmethod
    def _semantic_answer_digest(
        cls,
        before_task: Mapping[str, Any],
        after_task: Mapping[str, Any],
        before_todo: Mapping[str, Any],
        after_todo: Mapping[str, Any],
        *,
        kind: object,
    ) -> str | None:
        before_answer = cls._semantic_answer_text(
            before_task, before_todo, kind=kind
        )
        after_answer = cls._semantic_answer_text(after_task, after_todo, kind=kind)
        if after_answer is None or after_answer == before_answer:
            return None
        return hashlib.sha256(after_answer.encode("utf-8")).hexdigest()[:24]

    def normalize(
        self,
        before: object,
        after: object,
        receipt: object,
        now: datetime,
    ) -> ActionableChange:
        before_outer, before_task, before_todo = self._snapshot(before)
        after_outer, after_task, after_todo = self._snapshot(after)
        receipt_value = self._mapping(receipt)
        task_slug = after_outer.get("task_slug") or after_task.get("slug")
        before_task_slug = before_outer.get("task_slug") or before_task.get("slug")
        todo_slug = after_todo.get("slug") or before_todo.get("slug")
        todo_parent = after_todo.get("parent_task") or before_todo.get("parent_task")
        assigned_to = self._assigned_to(after_outer, after_task)
        route = after_outer.get("route")
        registrations = tuple(getattr(self.dispatcher, "registrations", ()))
        eligible_registrations = tuple(
            registration
            for registration in registrations
            if len(assigned_to) == 1
            and registration.verified
            and registration.agent_slug == assigned_to[0]
        )
        if route is None and len(assigned_to) == 1:
            matching_routes = {
                registration.route
                for registration in eligible_registrations
            }
            if len(matching_routes) == 1:
                route = next(iter(matching_routes))

        identity_error = (
            not isinstance(task_slug, str)
            or (isinstance(before_task_slug, str) and before_task_slug != task_slug)
            or (isinstance(todo_parent, str) and todo_parent != task_slug)
            or len(assigned_to) != 1
            or not isinstance(route, str)
            or (
                bool(registrations)
                and (
                    len(eligible_registrations) != 1
                    or eligible_registrations[0].route != route
                )
            )
        )
        verified = receipt_value.get("verified") is True
        kind = receipt_value.get("mutation_kind")
        before_status = before_task.get("status")
        after_status = after_task.get("status")
        task_status = (
            after_status
            if isinstance(after_status, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,127}", after_status)
            else "unknown"
        )
        if kind == "todo_comment":
            requested_operation = "comment"
        elif isinstance(kind, str) and kind.startswith("todo_"):
            requested_operation = "todo"
        elif isinstance(kind, str) and "artifact" in kind:
            requested_operation = "artifact"
        else:
            requested_operation = "task_status"
        before_blockers = tuple(before_task.get("blockers") or ())
        after_blockers = tuple(after_task.get("blockers") or ())
        removed_blockers = set(before_blockers) - set(after_blockers)
        before_handoff = self._mapping(before_task.get("handoff"))
        after_handoff = self._mapping(after_task.get("handoff"))
        task_changes = self._changed_fields(before_task, after_task)
        todo_changes = self._changed_fields(before_todo, after_todo)
        answer_digest = self._semantic_answer_digest(
            before_task,
            after_task,
            before_todo,
            after_todo,
            kind=kind,
        )
        waiting_answer_transition = (
            before_handoff.get("state") == "waiting_for_input"
            and after_handoff.get("state") == "ready_for_agent"
        )
        tony_owned_answer = (
            verified
            and answer_digest is not None
            and len(assigned_to) == 0
            and (after_task.get("owner_agent") in {None, "", "tony", TONY_PROFILE_SLUG})
        )

        if tony_owned_answer:
            trigger = "tony_owned_no_agent"
            summary = "No Agent handoff required - assigned to Tony."
        elif not verified or identity_error:
            trigger = "system_attention"
            summary = "Canonical handoff data needs system attention."
        elif receipt_value.get("idempotent") is True:
            trigger = "duplicate_save"
            summary = "A duplicate canonical save was verified."
        elif kind == "stale_cache_refresh":
            trigger = "stale_cache_refresh"
            summary = "A stale canonical cache refresh changed no work."
        elif kind == "derived_count" or (
            task_changes and task_changes <= self._DERIVED_FIELDS and not todo_changes
        ):
            trigger = "derived_count"
            summary = "A derived count changed without new work."
        elif answer_digest is not None and (
            kind in self._TONY_ANSWER_KINDS or waiting_answer_transition
        ):
            trigger = (
                "waiting_for_information_updated"
                if waiting_answer_transition
                else "tony_answer_received"
            )
            summary = "Tony's verified answer is ready for the assigned Agent."
        elif kind == "answer_agent_question" or waiting_answer_transition:
            trigger = "answer_received"
            summary = "A verified answer is ready."
        elif (not before_todo and after_todo) or kind == "todo_created":
            trigger = "todo_added"
            summary = "A verified To Do was added."
        elif todo_changes or kind in {"todo_edited", "todo_status", "todo_comment"}:
            trigger = "todo_materially_changed"
            summary = "A verified To Do materially changed."
        elif before_status in {"planned", "proposed"} and after_status == "active":
            trigger = "task_activated"
            summary = "The verified Task became active."
        elif self._assigned_to(before_outer, before_task) != assigned_to:
            trigger = "ownership_changed"
            summary = "Verified Task ownership changed."
        elif before_blockers and before_blockers == after_blockers:
            trigger = "unchanged_blocker"
            summary = "The canonical blocker is unchanged."
        elif before_status == "blocked" and after_status == "active" and any(
            str(value).startswith("systems/") for value in removed_blockers
        ):
            trigger = "system_dependency_recovered"
            summary = "A verified system dependency recovered."
        elif before_status == "blocked" and after_status == "active" and removed_blockers:
            trigger = "blocker_resolved"
            summary = "The verified blocker was resolved."
        elif (
            before_task.get("proposal_decision") != "approve"
            and after_task.get("proposal_decision") == "approve"
        ):
            trigger = "authorization_granted"
            summary = "Verified authorization was granted."
        elif task_changes and task_changes <= self._PRESENTATION_FIELDS:
            trigger = "presentation_only"
            summary = "Presentation-only canonical fields changed."
        else:
            trigger = "duplicate_save"
            summary = "A duplicate canonical save was verified."

        safe_task_slug = self._safe_identifier(
            task_slug, namespace="tasks", seed=repr((before_task_slug, task_slug))
        )
        event_value = (
            receipt_value.get("canonical_event_id")
            or self._mapping(receipt_value.get("event")).get("slug")
            or todo_slug
        )
        if answer_digest is not None and trigger in {
            "tony_answer_received",
            "waiting_for_information_updated",
        }:
            base_event = (
                event_value
                if isinstance(event_value, str) and event_value.strip()
                else "events/tony-answer"
            )
            event_value = f"{base_event}/answer-{answer_digest}"
        canonical_event_id = self._safe_identifier(
            event_value,
            namespace="events",
            seed=repr((safe_task_slug, todo_slug, kind, receipt_value)),
        )
        canonical_version = self._safe_identifier(
            receipt_value.get("canonical_version"),
            namespace="versions",
            seed=json.dumps(after_outer, sort_keys=True, default=str),
        )
        route_value = self._safe_identifier(
            route, namespace="routes", seed=repr((safe_task_slug, assigned_to))
        )
        correlation = receipt_value.get("correlation_id")
        if not isinstance(correlation, str) or re.fullmatch(
            r"(?:corr|correlation)-[a-z0-9][a-z0-9._-]{0,47}", correlation
        ) is None:
            correlation = "corr-" + hashlib.sha256(
                f"{safe_task_slug}|{canonical_event_id}".encode("utf-8")
            ).hexdigest()[:24]
        return ActionableChange(
            task_slug=safe_task_slug,
            canonical_event_id=canonical_event_id,
            canonical_version=canonical_version,
            trigger=trigger,
            assigned_to=assigned_to if not identity_error else (),
            route=route_value,
            summary=summary,
            occurred_at=now.astimezone(timezone.utc),
            correlation_id=correlation,
            blocker=None,
            task_status=task_status,
            requested_operation=requested_operation,
        )

    def after_verified_mutation(
        self,
        before: object,
        after: object,
        receipt: object,
        now: datetime,
    ):
        change = self.normalize(before, after, receipt, now)
        return self.dispatcher.record(change, now=now.astimezone(timezone.utc))


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value), ensure_ascii=False)


def render_artifact_collection_page(
    *, slug: str, title: str, agent: str | None
) -> str:
    if slug != ARTIFACTS_ROOT and slug not in ARTIFACT_BY_COLLECTION:
        raise ValueError("artifact collection slug is not reserved")
    if agent is None and slug != ARTIFACTS_ROOT:
        raise ValueError("only the Artifact root may omit an Agent")
    if agent is not None and ARTIFACT_BY_AGENT.get(agent) != slug:
        raise ValueError("artifact collection Agent does not match its slug")
    lines = [
        "---",
        "type: collection",
        f"title: {_yaml_scalar(title)}",
        "collection_kind: mission_control_artifacts",
        f"agent: {_yaml_scalar(agent)}",
        "links:",
    ]
    if agent is not None:
        lines.extend(
            [
                f"  - to: {_yaml_scalar(ARTIFACTS_ROOT)}",
                "    type: part_of",
                "    context: This Agent Artifact collection belongs to Mission Control Artifacts.",
                f"  - to: {_yaml_scalar(agent)}",
                "    type: for_agent",
                "    context: This collection stores durable output from this Agent.",
            ]
        )
    lines.extend(["---", "", f"# {title}", "", "Mission Control Agent artifacts.", ""])
    return "\n".join(lines)


def render_qa_fixtures_collection_page() -> str:
    return "\n".join(
        [
            "---",
            "type: collection",
            "title: Mission Control QA Fixtures",
            "collection_kind: mission_control_qa_fixtures",
            "member_type: task",
            "---",
            "",
            "Isolated Mission Control release-verification fixtures.",
            "",
        ]
    )


def render_agent_artifact_page(
    artifact: AgentArtifact,
    *,
    idempotency_key: str | None = None,
) -> str:
    links = [
        (artifact.agent_collection, "member_of", "Producing Agent Artifact collection."),
        (artifact.created_by, "created_by", "Canonical producing Agent."),
        (artifact.produced_for, "produced_for", "Authorized canonical Task."),
    ]
    if artifact.project:
        links.append((artifact.project, "supports_project", "Supported canonical Project."))
    if artifact.goal:
        links.append((artifact.goal, "supports_goal", "Supported canonical Goal."))
    if artifact.supersedes:
        links.append((artifact.supersedes, "supersedes", "Earlier Artifact replaced by this output."))
    lines = [
        "---",
        "type: artifact",
        f"title: {_yaml_scalar(artifact.title)}",
        f"artifact_kind: {_yaml_scalar(artifact.artifact_kind)}",
        f"created_by: {_yaml_scalar(artifact.created_by)}",
        f"produced_for: {_yaml_scalar(artifact.produced_for)}",
        "attachments: " + json.dumps(list(artifact.attachments), ensure_ascii=False),
        f"git_url: {_yaml_scalar(artifact.git_url)}",
        f"delegation_ref: {_yaml_scalar(artifact.delegation_ref)}",
        f"created_at: {_yaml_scalar(artifact.created_at.isoformat())}",
    ]
    if artifact.updated_at is not None:
        lines.append(f"updated_at: {_yaml_scalar(artifact.updated_at.isoformat())}")
    if idempotency_key is not None:
        lines.append(f"idempotency_key: {_yaml_scalar(idempotency_key)}")
    lines.append("links:")
    for target, link_type, context in links:
        lines.extend(
            [
                f"  - to: {_yaml_scalar(target)}",
                f"    type: {link_type}",
                f"    context: {_yaml_scalar(context)}",
            ]
        )
    lines.extend(["---", "", artifact.markdown, ""])
    return "\n".join(lines)


def render_task_page(task: Task, *, body: str | None = None) -> str:
    links = [
        {
            "to": task.lifecycle_root,
            "type": "member_of",
            "context": "GTasks lifecycle membership.",
        }
    ]
    if task.owner_agent:
        links.append(
            {
                "to": task.owner_agent,
                "type": "assigned_to",
                "context": "Tony assigned this work to the canonical agent.",
            }
        )
    if task.project:
        links.append(
            {
                "to": task.project,
                "type": "member_of",
                "context": "GTasks project membership.",
            }
        )
    if task.parent:
        links.append(
            {
                "to": task.parent,
                "type": "child_of",
                "context": "GTasks parent task.",
            }
        )
    links.extend(
        {
            "to": dependency,
            "type": "depends_on",
            "context": "GTasks task dependency.",
        }
        for dependency in task.dependencies
    )
    links.extend(
        {
            "to": blocker,
            "type": "blocked_by",
            "context": "GTasks task blocker.",
        }
        for blocker in task.blockers
    )

    lines = ["---", "type: task"]
    if body is not None:
        lines.append(f"markdown_contract: {MARKDOWN_CONTRACT}")
    lines.extend([
        f"title: {_yaml_scalar(task.title)}",
        f"status: {_yaml_scalar(task.status)}",
        f"summary: {_yaml_scalar(task.summary)}",
        f"detail: {_yaml_scalar(task.detail)}",
        f"priority: {_yaml_scalar(task.priority)}",
        f"next_action: {_yaml_scalar(task.next_action)}",
        (
            "next_action_history: "
            + json.dumps(
                [entry.to_dict() for entry in task.next_action_history],
                ensure_ascii=False,
            )
        ),
        f"due_day: {_yaml_scalar(task.due_day.isoformat() if task.due_day else None)}",
        f"due_at: {_yaml_scalar(task.due_at.isoformat() if task.due_at else None)}",
        (
            "scheduled_day: "
            + _yaml_scalar(task.scheduled_day.isoformat() if task.scheduled_day else None)
        ),
        f"inbox: {_yaml_scalar(task.inbox)}",
        f"qa_fixture: {_yaml_scalar(task.qa_fixture)}",
        f"qa_owner: {_yaml_scalar(task.qa_owner)}",
        f"qa_release: {_yaml_scalar(task.qa_release)}",
        (
            "completed_at: "
            + _yaml_scalar(task.completed_at.isoformat() if task.completed_at else None)
        ),
        f"created_at: {_yaml_scalar(task.created_at.isoformat() if task.created_at else None)}",
        f"updated_at: {_yaml_scalar(task.updated_at.isoformat() if task.updated_at else None)}",
        (
            "handoff: "
            + json.dumps(
                task.handoff.to_dict() if task.handoff else None,
                ensure_ascii=False,
            )
        ),
        f"proposal_recipient: {_yaml_scalar(task.proposal_recipient)}",
        (
            "proposal_submitted_at: "
            + _yaml_scalar(task.proposal_submitted_at.isoformat() if task.proposal_submitted_at else None)
        ),
        f"proposal_decision: {_yaml_scalar(task.proposal_decision)}",
        (
            "proposal_decided_at: "
            + _yaml_scalar(task.proposal_decided_at.isoformat() if task.proposal_decided_at else None)
        ),
        f"proposal_decision_note: {_yaml_scalar(task.proposal_decision_note)}",
        (
            "proposal_decision_events: "
            + json.dumps(
                [event.to_dict() for event in task.proposal_decision_events],
                ensure_ascii=False,
            )
        ),
        (
            "goal_derivation: "
            + json.dumps(
                task.goal_derivation.to_dict() if task.goal_derivation else None,
                ensure_ascii=False,
            )
        ),
        (
            "progress_metric: "
            + json.dumps(
                task.progress_metric.to_dict() if task.progress_metric else None,
                ensure_ascii=False,
            )
        ),
        (
            "event_progress: "
            + json.dumps(
                task.event_progress.to_dict() if task.event_progress else None,
                ensure_ascii=False,
            )
        ),
        "links:",
    ])
    for link in links:
        lines.extend(
            [
                f"  - to: {_yaml_scalar(link['to'])}",
                f"    type: {_yaml_scalar(link['type'])}",
                f"    context: {_yaml_scalar(link['context'])}",
            ]
        )
    if body is None:
        lines.extend(["---", "", f"# {task.title}", ""])
        if task.detail:
            lines.extend([task.detail, ""])
    else:
        lines.extend(["---", "", body, ""])
    return "\n".join(lines)


def render_todo_page(todo: TodoItem) -> str:
    lines = [
        "---",
        "type: todo",
        f"title: {_yaml_scalar(todo.text)}",
        f"text: {_yaml_scalar(todo.text)}",
        f"detail: {_yaml_scalar(todo.detail)}",
        f"status: {_yaml_scalar(todo.status)}",
        f"kind: {_yaml_scalar(todo.kind)}",
        f"parent_task: {_yaml_scalar(todo.parent_task)}",
        f"created_at: {_yaml_scalar(todo.created_at.isoformat())}",
        f"updated_at: {_yaml_scalar(todo.updated_at.isoformat())}",
        f"creator: {_yaml_scalar(todo.creator)}",
        f"source: {_yaml_scalar(todo.source)}",
        "comment_slugs: " + json.dumps(list(todo.comment_slugs), ensure_ascii=False),
        "event_slugs: " + json.dumps(list(todo.event_slugs), ensure_ascii=False),
        "legacy_provenance: "
        + json.dumps(
            dict(todo.legacy_provenance) if todo.legacy_provenance is not None else None,
            ensure_ascii=False,
        ),
        "links:",
        f"  - to: {_yaml_scalar(todo.parent_task)}",
        "    type: todo_for",
        "    context: Canonical parent task for this TODO.",
        "---",
        "",
        f"# {todo.text}",
        "",
    ]
    if todo.detail:
        lines.extend([todo.detail, ""])
    return "\n".join(lines)


def render_todo_comment_page(comment: TodoComment) -> str:
    return "\n".join(
        [
            "---",
            "type: todo_comment",
            f"title: {_yaml_scalar('Comment on ' + comment.todo_slug)}",
            f"todo_slug: {_yaml_scalar(comment.todo_slug)}",
            f"body: {_yaml_scalar(comment.body)}",
            f"author: {_yaml_scalar(comment.author)}",
            f"source: {_yaml_scalar(comment.source)}",
            f"created_at: {_yaml_scalar(comment.created_at.isoformat())}",
            f"idempotency_key: {_yaml_scalar(comment.idempotency_key)}",
            "links:",
            f"  - to: {_yaml_scalar(comment.todo_slug)}",
            "    type: comment_on",
            "    context: Append-only comment on this TODO.",
            "---",
            "",
            comment.body,
            "",
        ]
    )


def render_todo_event_page(event: TodoEvent) -> str:
    return "\n".join(
        [
            "---",
            "type: todo_event",
            f"title: {_yaml_scalar(event.event_type + ' · ' + event.todo_slug)}",
            f"todo_slug: {_yaml_scalar(event.todo_slug)}",
            f"event_type: {_yaml_scalar(event.event_type)}",
            f"actor: {_yaml_scalar(event.actor)}",
            f"source: {_yaml_scalar(event.source)}",
            f"occurred_at: {_yaml_scalar(event.occurred_at.isoformat())}",
            f"idempotency_key: {_yaml_scalar(event.idempotency_key)}",
            "before: "
            + json.dumps(dict(event.before) if event.before is not None else None, ensure_ascii=False),
            "after: "
            + json.dumps(dict(event.after) if event.after is not None else None, ensure_ascii=False),
            f"comment_slug: {_yaml_scalar(event.comment_slug)}",
            "links:",
            f"  - to: {_yaml_scalar(event.todo_slug)}",
            "    type: event_for",
            "    context: Durable audit history for this TODO.",
            "---",
            "",
            f"# {event.event_type}",
            "",
        ]
    )


def _history_after_next_action_change(
    task: Task,
    next_action: str,
    now: datetime,
) -> tuple[NextActionHistoryEntry, ...]:
    history = list(task.next_action_history)
    if task.next_action and task.next_action != next_action:
        history.append(
            NextActionHistoryEntry(
                action=task.next_action,
                completed_at=now,
            )
        )
    return tuple(history[-100:])


def render_proposal_page(proposal: TaskProposal) -> str:
    links = [
        {
            "to": PROPOSALS_ROOT,
            "type": "member_of",
            "context": "GTasks proposal review scope.",
        },
        {
            "to": proposal.proposing_agent,
            "type": "proposed_by",
            "context": "Canonical proposing agent.",
        },
    ]
    if proposal.linked_goal:
        links.append(
            {
                "to": proposal.linked_goal,
                "type": "serves_goal",
                "context": "Goal this proposal serves.",
            }
        )
    if proposal.linked_task:
        links.append(
            {
                "to": proposal.linked_task,
                "type": "proposes_for_task",
                "context": "Tony task this proposal supports.",
            }
        )
    if proposal.approved_task:
        links.append(
            {
                "to": proposal.approved_task,
                "type": "approved_as",
                "context": "Canonical task created by explicit Tony approval.",
            }
        )
    lines = [
        "---",
        "type: task_proposal",
        f"title: {_yaml_scalar(proposal.title)}",
        f"status: {_yaml_scalar(proposal.status)}",
        f"recipient: {_yaml_scalar(proposal.recipient)}",
        f"proposing_agent: {_yaml_scalar(proposal.proposing_agent)}",
        f"rationale: {_yaml_scalar(proposal.rationale)}",
        f"proposed_next_step: {_yaml_scalar(proposal.proposed_next_step)}",
        f"due_day: {_yaml_scalar(proposal.due_day.isoformat())}",
        f"submitted_at: {_yaml_scalar(proposal.submitted_at.isoformat())}",
        f"updated_at: {_yaml_scalar(proposal.updated_at.isoformat())}",
        (
            "reviewed_at: "
            + _yaml_scalar(
                proposal.reviewed_at.isoformat()
                if proposal.reviewed_at
                else None
            )
        ),
        f"decision_note: {_yaml_scalar(proposal.decision_note)}",
        "links:",
    ]
    for link in links:
        lines.extend(
            [
                f"  - to: {_yaml_scalar(link['to'])}",
                f"    type: {_yaml_scalar(link['type'])}",
                f"    context: {_yaml_scalar(link['context'])}",
            ]
        )
    lines.extend(["---", "", f"# {proposal.title}", "", proposal.rationale, ""])
    return "\n".join(lines)


def render_project_page(project: Project) -> str:
    return "\n".join(
        [
            "---",
            "type: project",
            f"title: {_yaml_scalar(project.title)}",
            f"status: {_yaml_scalar(project.status)}",
            f"summary: {_yaml_scalar(project.summary)}",
            (
                "created_at: "
                + _yaml_scalar(
                    project.created_at.isoformat() if project.created_at else None
                )
            ),
            (
                "updated_at: "
                + _yaml_scalar(
                    project.updated_at.isoformat() if project.updated_at else None
                )
            ),
            "links:",
            f"  - to: {_yaml_scalar(PROJECTS_ROOT)}",
            "    type: member_of",
            "    context: This project is explicitly owned by GTasks.",
            "---",
            "",
            f"# {project.title}",
            "",
        ]
    )


def render_system_ticket_page(
    ticket: SystemTicket, *, body: str | None = None
) -> str:
    """Render the dedicated ticket projection while retaining canonical task type."""
    lines = ["---", "type: task"]
    if body is not None:
        lines.append(f"markdown_contract: {MARKDOWN_CONTRACT}")
    lines.extend([
        f"title: {_yaml_scalar(ticket.title)}",
        f"status: {_yaml_scalar(ticket.status)}", f"priority: {_yaml_scalar(ticket.priority)}",
        f"verbatim_request: {_yaml_scalar(ticket.verbatim_request)}",
        f"target_subsystem: {_yaml_scalar(ticket.target_subsystem)}",
        f"acceptance_criteria: {_yaml_scalar(ticket.acceptance_criteria)}",
        "linked_evidence:", *[f"  - {_yaml_scalar(value)}" for value in ticket.linked_evidence],
        "implementation_receipts:", *[f"  - {_yaml_scalar(value)}" for value in ticket.implementation_receipts],
        "qa_receipts:", *[f"  - {_yaml_scalar(value)}" for value in ticket.qa_receipts],
        f"created_at: {_yaml_scalar(ticket.created_at.isoformat() if ticket.created_at else None)}",
        f"updated_at: {_yaml_scalar(ticket.updated_at.isoformat() if ticket.updated_at else None)}",
        "links:", f"  - to: {_yaml_scalar(SYSTEM_TICKETS_ROOT)}", "    type: member_of",
        "    context: This task is a Mission Control System Ticket.", "---", "",
    ])
    if body is None:
        lines.extend([f"# {ticket.title}", "", ticket.verbatim_request, ""])
    else:
        lines.extend([body, ""])
    return "\n".join(lines)


def render_goal_page(goal: Goal) -> str:
    return "\n".join(
        [
            "---",
            "type: goal",
            f"title: {_yaml_scalar(goal.title)}",
            f"status: {_yaml_scalar(goal.status)}",
            f"outcome: {_yaml_scalar(goal.outcome)}",
            f"success_criteria: {_yaml_scalar(goal.success_criteria)}",
            f"target_day: {_yaml_scalar(goal.target_day.isoformat())}",
            f"strategy: {_yaml_scalar(goal.strategy)}",
            f"review_cadence: {_yaml_scalar(goal.review_cadence)}",
            f"constraints: {_yaml_scalar(goal.constraints)}",
            f"collection: {_yaml_scalar(GOALS_ROOT)}",
            "links:",
            f"  - to: {_yaml_scalar(GOALS_ROOT)}",
            "    type: member_of",
            "    context: This goal belongs to Tony's Goals.",
            "---",
            "",
            f"# {goal.title}",
            "",
            goal.outcome,
            "",
        ]
    )


def render_projects_collection_page() -> str:
    return "\n".join(
        [
            "---",
            "type: collection",
            "title: Tony's Projects",
            "owner: people/tony-guan",
            "status: active",
            "visibility: private",
            "required_project_fields:",
            "  - status",
            "  - summary",
            "---",
            "",
            "# Tony's Projects",
            "",
            "Canonical scope collection for projects explicitly created in GTasks.",
            "",
            "A project is visible in GTasks only when it has a typed "
            "`member_of` relationship to this collection.",
            "",
        ]
    )


def _agent_delegation_dict(lease: AgentDelegationLease) -> dict[str, Any]:
    return {
        "slug": lease.slug,
        "source_agent": lease.source_agent,
        "executor_agent": lease.executor_agent,
        "authorized_by": lease.authorized_by,
        "starts_at": lease.starts_at.isoformat(),
        "ends_at": lease.ends_at.isoformat(),
        "display_timezone": lease.display_timezone,
        "allowed_operations": list(lease.allowed_operations),
        "state": lease.state.value,
        "created_at": lease.created_at.isoformat(),
        "updated_at": lease.updated_at.isoformat(),
    }


def _agent_delegation_from_page(
    page: Mapping[str, Any],
    links: list[object],
) -> tuple[AgentDelegationLease, tuple[Mapping[str, Any], ...]]:
    slug = page.get("slug")
    frontmatter = page.get("frontmatter")
    if not isinstance(slug, str) or not isinstance(frontmatter, Mapping):
        raise GBrainProtocolError("agent delegation readback was not structured")
    expected_fields = {
        "type",
        "title",
        "source_agent",
        "executor_agent",
        "authorized_by",
        "starts_at",
        "ends_at",
        "display_timezone",
        "allowed_operations",
        "state",
        "created_at",
        "updated_at",
        "version",
        "receipts",
        "links",
    }
    stored_fields = frozenset(frontmatter)
    # Stargraph's canonical page projection may add transport provenance
    # fields when a page has been ingested through MCP. They are not part of
    # the delegation contract and must not make an otherwise valid lease
    # unreadable. Keep the allow-list narrow so unknown semantic fields still
    # fail closed.
    ingestion_metadata_fields = frozenset(
        {"source_kind", "ingested_via", "ingested_at", "created"}
    )
    contract_fields = stored_fields - ingestion_metadata_fields
    projected_fields = frozenset(expected_fields - {"type", "title"})
    if page.get("type") != "agent_delegation_lease" or contract_fields not in {
        frozenset(expected_fields),
        projected_fields,
    }:
        raise GBrainProtocolError("agent delegation has an invalid canonical schema")
    if stored_fields == expected_fields and (
        frontmatter.get("type") != page.get("type")
        or frontmatter.get("title") != page.get("title")
    ):
        raise GBrainProtocolError("agent delegation has conflicting canonical metadata")
    if len(links) != 1 or not isinstance(links[0], Mapping) or not (
        links[0].get("from_slug") == slug
        and links[0].get("to_slug") == AGENT_DELEGATIONS_ROOT
        and links[0].get("link_type") == "member_of"
    ):
        raise GBrainProtocolError(
            "agent delegation must have exactly one outgoing relationship: its canonical member_of link"
        )
    declared_links = frontmatter.get("links")
    if (
        not isinstance(declared_links, list)
        or len(declared_links) != 1
        or not isinstance(declared_links[0], Mapping)
        or declared_links[0].get("to") != AGENT_DELEGATIONS_ROOT
        or declared_links[0].get("type") != "member_of"
    ):
        raise GBrainProtocolError("agent delegation declared links were not canonical")
    operations = frontmatter.get("allowed_operations")
    receipts = frontmatter.get("receipts")
    if not isinstance(operations, list) or not isinstance(receipts, list) or not receipts:
        raise GBrainProtocolError("agent delegation operations or receipts were malformed")
    try:
        lease = AgentDelegationLease(
            slug=slug,
            source_agent=str(frontmatter["source_agent"]),
            executor_agent=str(frontmatter["executor_agent"]),
            authorized_by=str(frontmatter["authorized_by"]),
            starts_at=datetime.fromisoformat(str(frontmatter["starts_at"]).replace("Z", "+00:00")),
            ends_at=datetime.fromisoformat(str(frontmatter["ends_at"]).replace("Z", "+00:00")),
            display_timezone=str(frontmatter["display_timezone"]),
            allowed_operations=tuple(operations),
            state=DelegationState(str(frontmatter["state"])),
            created_at=datetime.fromisoformat(str(frontmatter["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(frontmatter["updated_at"]).replace("Z", "+00:00")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GBrainProtocolError(f"agent delegation fields were invalid: {exc}") from exc
    expected_title = f"{lease.source_agent.rsplit('/', 1)[-1].title()} temporary delegation"
    if page.get("title") != expected_title or (
        "title" in frontmatter and frontmatter.get("title") != expected_title
    ):
        raise GBrainProtocolError("agent delegation title was not canonical")
    receipt_fields = {
        "action",
        "authorized_by",
        "occurred_at",
        "version",
        "source_agent",
        "executor_agent",
        "starts_at",
        "previous_ends_at",
        "ends_at",
        "display_timezone",
        "allowed_operations",
        "previous_state",
        "state",
    }
    normalized_receipts: list[Mapping[str, Any]] = []
    prior_snapshot: AgentDelegationLease | None = None
    for index, raw_receipt in enumerate(receipts):
        if not isinstance(raw_receipt, Mapping) or set(raw_receipt) != receipt_fields:
            raise GBrainProtocolError("agent delegation receipt was malformed")
        receipt = deepcopy(dict(raw_receipt))
        action = receipt.get("action")
        if action not in {"created", "extended", "completed", "revoked"}:
            raise GBrainProtocolError("agent delegation receipt action was malformed")
        if receipt.get("authorized_by") != TONY_PROFILE_SLUG:
            raise GBrainProtocolError("agent delegation receipt was not authorized by Tony")
        if (
            receipt.get("source_agent") != lease.source_agent
            or receipt.get("executor_agent") != lease.executor_agent
            or receipt.get("starts_at") != lease.starts_at.isoformat()
            or receipt.get("display_timezone") != lease.display_timezone
            or receipt.get("allowed_operations") != list(lease.allowed_operations)
        ):
            raise GBrainProtocolError("agent delegation receipt identity was altered")
        try:
            receipt_instants = []
            for field in ("occurred_at", "version", "ends_at"):
                instant = datetime.fromisoformat(
                    str(receipt[field]).replace("Z", "+00:00")
                )
                if instant.tzinfo is None or instant.utcoffset() is None:
                    raise ValueError(f"{field} must be an aware UTC instant")
                receipt_instants.append(instant.astimezone(timezone.utc))
            occurred_at, version_at, receipt_ends_at = receipt_instants
            receipt_state = DelegationState(str(receipt["state"]))
            snapshot = AgentDelegationLease(
                slug=lease.slug,
                source_agent=lease.source_agent,
                executor_agent=lease.executor_agent,
                authorized_by=lease.authorized_by,
                starts_at=lease.starts_at,
                ends_at=receipt_ends_at,
                display_timezone=lease.display_timezone,
                allowed_operations=lease.allowed_operations,
                state=receipt_state,
                created_at=lease.created_at,
                updated_at=version_at,
            )
        except (TypeError, ValueError) as exc:
            raise GBrainProtocolError(f"agent delegation receipt was malformed: {exc}") from exc
        if occurred_at != version_at:
            raise GBrainProtocolError("agent delegation receipt occurrence and version differ")
        if index == 0:
            if (
                action != "created"
                or receipt.get("previous_ends_at") is not None
                or receipt.get("previous_state") is not None
                or version_at != lease.created_at
                or receipt_state
                not in {DelegationState.SCHEDULED, DelegationState.ACTIVE}
                or receipt_state != lease_state_at(snapshot, occurred_at)
            ):
                raise GBrainProtocolError("agent delegation creation receipt is malformed")
        else:
            assert prior_snapshot is not None
            effective_prior_state = lease_state_at(prior_snapshot, occurred_at)
            if effective_prior_state in {
                DelegationState.COMPLETED,
                DelegationState.EXPIRED,
                DelegationState.REVOKED,
            }:
                raise GBrainProtocolError("agent delegation receipt follows a terminal lease")
            if (
                version_at <= prior_snapshot.updated_at
                or receipt.get("previous_ends_at") != prior_snapshot.ends_at.isoformat()
                or receipt.get("previous_state") != effective_prior_state.value
            ):
                raise GBrainProtocolError("agent delegation receipt chain was altered")
            if action == "extended":
                expected_state = lease_state_at(snapshot, occurred_at)
                if receipt_ends_at <= prior_snapshot.ends_at or receipt_state != expected_state:
                    raise GBrainProtocolError("agent delegation extension receipt was malformed")
            elif action in {"completed", "revoked"}:
                if (
                    receipt_ends_at != prior_snapshot.ends_at
                    or receipt_state.value != action
                ):
                    raise GBrainProtocolError("agent delegation terminal receipt was malformed")
            else:
                raise GBrainProtocolError("agent delegation receipt chain repeated creation")
        normalized_receipts.append(receipt)
        prior_snapshot = snapshot
    version = frontmatter.get("version")
    if (
        version != lease.updated_at.isoformat()
        or normalized_receipts[-1].get("version") != version
        or prior_snapshot != lease
    ):
        raise GBrainProtocolError("agent delegation version did not match its immutable receipt")
    return lease, tuple(normalized_receipts)


def render_agent_delegation_page(
    lease: AgentDelegationLease,
    receipts: Sequence[Mapping[str, Any]],
) -> str:
    values = _agent_delegation_dict(lease)
    title = f"{lease.source_agent.rsplit('/', 1)[-1].title()} temporary delegation"
    lines = [
        "---",
        "type: agent_delegation_lease",
        f"title: {_yaml_scalar(title)}",
        f"source_agent: {_yaml_scalar(lease.source_agent)}",
        f"executor_agent: {_yaml_scalar(lease.executor_agent)}",
        f"authorized_by: {_yaml_scalar(lease.authorized_by)}",
        f"starts_at: {_yaml_scalar(values['starts_at'])}",
        f"ends_at: {_yaml_scalar(values['ends_at'])}",
        f"display_timezone: {_yaml_scalar(lease.display_timezone)}",
        "allowed_operations: " + json.dumps(list(lease.allowed_operations), ensure_ascii=False),
        f"state: {_yaml_scalar(lease.state.value)}",
        f"created_at: {_yaml_scalar(values['created_at'])}",
        f"updated_at: {_yaml_scalar(values['updated_at'])}",
        f"version: {_yaml_scalar(values['updated_at'])}",
        "receipts: " + json.dumps([dict(item) for item in receipts], ensure_ascii=False),
        "links:",
        f"  - to: {_yaml_scalar(AGENT_DELEGATIONS_ROOT)}",
        "    type: member_of",
        "    context: Tony-authorized temporary Agent delegation.",
        "---",
        "",
        f"# {title}",
        "",
        "Time-bounded delegation authority. Permanent task ownership is unchanged.",
        "",
    ]
    return "\n".join(lines)


def _render_preserved_page(
    page: Mapping[str, Any],
    frontmatter: Mapping[str, Any],
    *,
    body: str | None = None,
) -> str:
    preserved_body = page.get("compiled_truth") if body is None else body
    if not isinstance(preserved_body, str):
        raise GBrainProtocolError("page has no preserved body content")
    # Preserve the exact canonical type that was read. GBrain intentionally
    # normalizes Markdown-backed Goals to raw `concept` rows, so their verified
    # compiled/frontmatter `type: goal` is canonical for this one storage
    # shape. Other entity types remain strict raw-type invariants.
    raw_type = page.get("type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise GBrainProtocolError("page has no canonical entity type to preserve")
    preserved = dict(frontmatter)
    entity_type = (
        "goal"
        if raw_type == "concept" and preserved.get("type") == "goal"
        else raw_type
    )
    requested_type = preserved.get("type")
    if requested_type not in (None, entity_type):
        raise GBrainProtocolError(
            "refusing to change canonical page type through a preserved update"
        )
    preserved["type"] = entity_type
    title = page.get("title")
    if "title" not in preserved and isinstance(title, str) and title.strip():
        preserved["title"] = title.strip()
    lines = ["---"]
    for key, value in preserved.items():
        key_text = str(key)
        # GBrain's Markdown frontmatter compiler accepts YAML values (including
        # JSON flow values) but requires ordinary bare field names. Quoted YAML
        # keys are syntactically legal YAML yet are ignored by that compiler,
        # which would silently strip task/project fields on a rewrite.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key_text):
            raise GBrainProtocolError(
                f"cannot safely preserve unsupported frontmatter key: {key_text}"
            )
        if value is None:
            rendered = "null"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            # JSON flow collections are valid YAML and preserve nested links,
            # metrics, receipts, and history without inventing a second parser.
            rendered = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key_text}: {rendered}")
    lines.extend(["---", "", preserved_body.rstrip(), ""])
    return "\n".join(lines)


def _render_preserved_task_page(
    page: Mapping[str, Any],
    frontmatter: Mapping[str, Any],
    *,
    body: str | None = None,
) -> str:
    """Serialize an existing task only after fail-closed type validation."""
    if page.get("type") != "task":
        raise ValueError(
            "task has unexpected page type "
            f"{page.get('type') or 'missing'}; repair the task type before writing"
        )
    return _render_preserved_page(page, frontmatter, body=body)


def _lifecycle_edges(
    task_slug: str,
    links: list[object],
) -> list[Mapping[str, Any]]:
    return [
        link
        for link in links
        if isinstance(link, Mapping)
        and link.get("from_slug") == task_slug
        and link.get("to_slug") in TASK_SCOPE_ROOTS
        and link.get("link_type") == "member_of"
    ]


def _require_single_lifecycle_edge(
    task_slug: str,
    links: list[object],
) -> Mapping[str, Any]:
    edges = _lifecycle_edges(task_slug, links)
    if len(edges) != 1:
        raise LifecycleIntegrityError(task_slug, edges)
    return edges[0]


def _parse_required_archive_completed_at(frontmatter: Mapping[str, Any]) -> datetime:
    raw_completed_at = frontmatter.get("completed_at")
    if not isinstance(raw_completed_at, str) or not raw_completed_at.strip():
        raise ValueError("completed task is missing completed_at")
    try:
        completed_at = datetime.fromisoformat(raw_completed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("completed_at is not a valid timestamp") from exc
    if completed_at.tzinfo is None:
        raise ValueError("completed_at must include a timezone")
    return completed_at


def _completed_archive_boundary_reached(
    completed_at: datetime,
    now: datetime,
) -> bool:
    local_tz = ZoneInfo("America/Los_Angeles")
    completed_day = completed_at.astimezone(local_tz).date()
    days_until_next_monday = (7 - completed_day.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    archive_boundary = completed_day + timedelta(days=days_until_next_monday)
    return now.astimezone(local_tz).date() >= archive_boundary


def _visible_warning(
    slug: str,
    message: str,
    *,
    category: str,
    impact: str,
    repair_action: str | None = None,
) -> CollectionIssue:
    return CollectionIssue(
        slug=slug,
        message=message,
        severity="warning",
        task_visible=True,
        category=category,
        impact=impact,
        repair_action=repair_action,
    )


def _normalize_collection_task(
    page: Mapping[str, Any],
    edges: list[object],
    root_slug: str,
    *,
    legacy_untyped_backlink: bool,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], list[CollectionIssue]]:
    slug = page.get("slug")
    if not isinstance(slug, str):
        return page, [], []
    raw_frontmatter = page.get("frontmatter")
    if not isinstance(raw_frontmatter, Mapping):
        return page, [], []

    normalized_page = deepcopy(dict(page))
    frontmatter = deepcopy(dict(raw_frontmatter))
    normalized_page["frontmatter"] = frontmatter
    warnings: list[CollectionIssue] = []

    raw_links = frontmatter.get("links", [])
    valid_links: list[dict[str, Any]] = []
    if isinstance(raw_links, list):
        for link in raw_links:
            if (
                isinstance(link, Mapping)
                and isinstance(link.get("to"), str)
                and str(link.get("to")).strip()
                and isinstance(link.get("type"), str)
                and str(link.get("type")).strip()
            ):
                valid_links.append(deepcopy(dict(link)))
            else:
                warnings.append(
                    _visible_warning(
                        slug,
                        "One malformed optional frontmatter relationship was ignored.",
                        category="optional_relationship",
                        impact="The task is shown, but the malformed relationship is unavailable.",
                    )
                )
    elif raw_links is not None:
        warnings.append(
            _visible_warning(
                slug,
                "The optional frontmatter relationship list is invalid and was ignored.",
                category="optional_relationship",
                impact="The task is shown using its valid core fields.",
            )
        )

    lifecycle_links = [
        link
        for link in valid_links
        if link.get("type") == "member_of"
        and link.get("to") in TASK_SCOPE_ROOTS
    ]
    graph_has_typed_membership = any(
        isinstance(edge, Mapping)
        and edge.get("from_slug") == slug
        and edge.get("to_slug") == root_slug
        and edge.get("link_type") == "member_of"
        for edge in edges
    )
    collection_matches = frontmatter.get("collection") == root_slug
    if not lifecycle_links and (
        graph_has_typed_membership
        or (legacy_untyped_backlink and collection_matches)
    ):
        valid_links.append({"to": root_slug, "type": "member_of"})
        if legacy_untyped_backlink:
            warnings.append(
                _visible_warning(
                    slug,
                    (
                        f"Legacy untyped collection membership is being treated as "
                        f"{root_slug} because the page collection matches exactly."
                    ),
                    category="lifecycle_relationship",
                    impact=(
                        "The task is shown normally; repairing makes its active "
                        "membership explicit and typed."
                    ),
                    repair_action=(
                        "repair_active_membership"
                        if root_slug == ACTIVE_ROOT
                        else None
                    ),
                )
            )
        else:
            warnings.append(
                _visible_warning(
                    slug,
                    "The typed collection edge is missing from task frontmatter.",
                    category="lifecycle_relationship",
                    impact="The task is shown from its verified graph membership.",
                    repair_action=(
                        "repair_active_membership"
                        if root_slug == ACTIVE_ROOT
                        else None
                    ),
                )
            )

    project_links = [
        link
        for link in valid_links
        if link.get("type") == "member_of"
        and link.get("to") not in TASK_SCOPE_ROOTS
    ]
    if len({str(link.get("to")) for link in project_links}) > 1:
        valid_links = [
            link
            for link in valid_links
            if not (
                link.get("type") == "member_of"
                and link.get("to") not in TASK_SCOPE_ROOTS
            )
        ]
        warnings.append(
            _visible_warning(
                slug,
                "Multiple project relationships are ambiguous and were not selected.",
                category="optional_relationship",
                impact="The task is shown without a project until you choose one.",
            )
        )
    elif project_links:
        project_slug = str(project_links[0]["to"])
        graph_project_verified = any(
            isinstance(edge, Mapping)
            and edge.get("from_slug") == slug
            and edge.get("to_slug") == project_slug
            and edge.get("link_type") == "member_of"
            for edge in edges
        )
        if not graph_project_verified:
            valid_links = [
                link
                for link in valid_links
                if not (
                    link.get("type") == "member_of"
                    and link.get("to") not in TASK_SCOPE_ROOTS
                )
            ]
            frontmatter["project"] = None
            warnings.append(
                _visible_warning(
                    slug,
                    "The task project link is not verified in the GBrain graph.",
                    category="optional_relationship",
                    impact=(
                        "The task is shown without a project; choose a durable "
                        "project in task details to repair the assignment."
                    ),
                )
            )
    frontmatter["links"] = valid_links

    has_verified_task_shape = (
        slug.startswith("tasks/")
        and any(
            link.get("type") == "member_of" and link.get("to") == root_slug
            for link in valid_links
        )
        and all(
            field in frontmatter
            for field in ("summary", "detail", "status", "due_day")
        )
    )
    original_type = page.get("type")
    if original_type != "task" and has_verified_task_shape:
        normalized_page["type"] = "task"
        warnings.append(
            _visible_warning(
                slug,
                (
                    f"The page type is {original_type or 'missing'}, but its task slug, "
                    "collection membership, and required task fields are valid."
                ),
                category="core_metadata",
                impact=(
                    "The task is shown using the task contract; repair the page type "
                    "before relying on broader type-based queries."
                ),
            )
        )

    normalized_edges = [
        edge for edge in edges if isinstance(edge, Mapping)
    ]
    goal_edges = [
        edge
        for edge in normalized_edges
        if edge.get("from_slug") == slug
        and edge.get("link_type") == "advances_goal"
        and isinstance(edge.get("to_slug"), str)
        and str(edge.get("to_slug")).startswith("goals/")
    ]
    if len({str(edge.get("to_slug")) for edge in goal_edges}) > 1:
        normalized_edges = [
            edge
            for edge in normalized_edges
            if not (
                edge.get("from_slug") == slug
                and edge.get("link_type") == "advances_goal"
            )
        ]
        warnings.append(
            _visible_warning(
                slug,
                "Multiple goal relationships are ambiguous and were not selected.",
                category="optional_relationship",
                impact="The task is shown without a goal until you choose one.",
            )
        )

    return normalized_page, normalized_edges, warnings


class GBrainAdapter:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        openclaw_profiles: OpenClawProfileActivationClient | None = None,
    ) -> None:
        self.runner = runner or RemoteHttpCommandRunner()
        if openclaw_profiles is None and (
            os.environ.get("MEMORY_STARGRAPH_URL")
            and os.environ.get("MEMORY_STARGRAPH_OC_PROVISION_TOKEN")
        ):
            openclaw_profiles = MemoryStargraphOpenClawProfileClient.from_environment()
        self.openclaw_profiles = openclaw_profiles
        # Comments and events are append-only canonical records. Cache only
        # fully validated immutable children so repeated Todo hydration does
        # not renegotiate OAuth through two CLI subprocesses per history item.
        self._todo_comment_cache: dict[str, TodoComment] = {}
        self._todo_event_cache: dict[str, TodoEvent] = {}
        self._todo_child_cache_lock = Lock()
        self._artifact_create_lock = Lock()
        self._artifact_review_reference_lock = Lock()
        self._delegation_mutation_lock = Lock()
        self._goal_execution_locks_guard = Lock()
        self._goal_execution_locks: dict[str, Lock] = {}
        self._last_verified_openclaw_profiles: tuple[AgentProfile, ...] = ()

    def _verified_system_ticket_references(
        self, values: Sequence[str]
    ) -> dict[str, SystemTicketReference | None]:
        """Resolve only exact canonical System Ticket page/link readbacks."""
        result: dict[str, SystemTicketReference | None] = {}
        for slug in extract_system_ticket_slugs("\n".join(values)):
            try:
                page = self.runner.run("get_page", {"slug": slug})
                links = self.runner.run("get_links", {"slug": slug})
                if not isinstance(page, Mapping) or not isinstance(links, list):
                    raise GBrainProtocolError(
                        "System Ticket reference readback was not structured"
                    )
                ticket = SystemTicket.from_page(page, links)
                if not any(
                    isinstance(edge, Mapping)
                    and edge.get("from_slug") == slug
                    and edge.get("to_slug") == SYSTEM_TICKETS_ROOT
                    and edge.get("link_type") == "member_of"
                    for edge in links
                ):
                    raise GBrainProtocolError(
                        "System Ticket reference has no live membership edge"
                    )
            except (DomainValidationError, GBrainError):
                if reference_is_explicitly_labeled_system_ticket(slug, values):
                    result[slug] = None
            else:
                result[slug] = SystemTicketReference(ticket.slug, ticket.title)
        return result

    @staticmethod
    def _has_unified_markdown_contract(page: Mapping[str, Any]) -> bool:
        frontmatter = page.get("frontmatter")
        return (
            isinstance(frontmatter, Mapping)
            and frontmatter.get("markdown_contract") == MARKDOWN_CONTRACT
        )

    def _validated_task_display_markdown(
        self, task: Task, page: Mapping[str, Any]
    ) -> str | None:
        if not self._has_unified_markdown_contract(page):
            return None
        try:
            references = self._verified_system_ticket_references((task.detail,))
            expected = render_task_body(task.title, task.detail, references)
        except MarkdownContractError:
            return None
        compiled = page.get("compiled_markdown")
        if not isinstance(compiled, str) or compiled.strip() != expected.strip():
            return None
        return expected

    def _validated_system_ticket_display_markdown(
        self, ticket: SystemTicket, page: Mapping[str, Any]
    ) -> str | None:
        if not self._has_unified_markdown_contract(page):
            return None
        try:
            references = self._verified_system_ticket_references(
                (
                    ticket.verbatim_request,
                    ticket.acceptance_criteria,
                    *ticket.linked_evidence,
                    *ticket.implementation_receipts,
                    *ticket.qa_receipts,
                )
            )
            expected = render_system_ticket_body(
                ticket.title,
                ticket.verbatim_request,
                acceptance_criteria=ticket.acceptance_criteria,
                linked_evidence=ticket.linked_evidence,
                implementation_receipts=ticket.implementation_receipts,
                qa_receipts=ticket.qa_receipts,
                references=references,
            )
        except MarkdownContractError:
            return None
        compiled = page.get("compiled_markdown")
        if not isinstance(compiled, str) or compiled.strip() != expected.strip():
            return None
        return expected

    @staticmethod
    def _verify_compiled_markdown_body(
        page: Mapping[str, Any], expected_body: str, *, label: str
    ) -> None:
        if not GBrainAdapter._has_unified_markdown_contract(page):
            raise GBrainProtocolError(
                f"{label} unified Markdown contract marker did not match the write"
            )
        body = page.get("compiled_markdown")
        if body is None:
            body = page.get("compiled_truth")
        if not isinstance(body, str) or body.strip() != expected_body.strip():
            raise GBrainProtocolError(
                f"{label} compiled Markdown body did not match the write"
            )

    @staticmethod
    def _artifact_collection_title(slug: str) -> str:
        if slug == ARTIFACTS_ROOT:
            return "Mission Control Artifacts"
        agent = ARTIFACT_BY_COLLECTION.get(slug)
        if agent is None:
            raise ValueError("artifact collection slug is not reserved")
        return f"{agent.rsplit('/', 1)[-1].title()} Artifacts"

    def _verify_artifact_collection(
        self, slug: str, page: object, links: object
    ) -> None:
        if not isinstance(page, Mapping):
            raise GBrainProtocolError(f"{slug} collection readback was not an object")
        if page.get("slug") != slug or page.get("type") != "collection":
            raise GBrainProtocolError(f"{slug} is not a canonical Artifact collection")
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping) or frontmatter.get(
            "collection_kind"
        ) != "mission_control_artifacts":
            raise GBrainProtocolError(f"{slug} has the wrong Artifact collection contract")
        if slug == ARTIFACTS_ROOT:
            return
        agent = ARTIFACT_BY_COLLECTION[slug]
        if frontmatter.get("agent") != agent or not isinstance(links, list):
            raise GBrainProtocolError(f"{slug} has the wrong Agent scope")
        part_of = [
            edge.get("to_slug")
            for edge in links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == slug
            and edge.get("link_type") == "part_of"
        ]
        for_agent = [
            edge.get("to_slug")
            for edge in links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == slug
            and edge.get("link_type") == "for_agent"
        ]
        if part_of != [ARTIFACTS_ROOT]:
            raise GBrainProtocolError(
                f"{slug} must have exactly one part_of relationship to {ARTIFACTS_ROOT}"
            )
        if for_agent != [agent]:
            raise GBrainProtocolError(
                f"{slug} must have exactly one for_agent relationship to {agent}"
            )

    def ensure_artifact_collections(self) -> None:
        scopes: tuple[tuple[str, str | None], ...] = (
            (ARTIFACTS_ROOT, None),
            *(
                (collection, agent)
                for agent, collection in EXISTING_CODEX_ARTIFACT_AGENT_SCOPES
            ),
        )
        for slug, agent in scopes:
            try:
                page = self.runner.run("get_page", {"slug": slug})
            except GBrainCommandError as exc:
                if "page_not_found" not in str(exc):
                    raise
                self.runner.run(
                    "put_page",
                    {
                        "slug": slug,
                        "content": render_artifact_collection_page(
                            slug=slug,
                            title=self._artifact_collection_title(slug),
                            agent=agent,
                        ),
                    },
                )
                page = self.runner.run("get_page", {"slug": slug})
            if not isinstance(page, Mapping) or page.get("type") != "collection":
                raise GBrainProtocolError(
                    f"reserved Artifact slug {slug} is not a collection page"
                )
            if agent is not None:
                for target, link_type, context in (
                    (
                        ARTIFACTS_ROOT,
                        "part_of",
                        "This Agent Artifact collection belongs to Mission Control Artifacts.",
                    ),
                    (
                        agent,
                        "for_agent",
                        "This collection stores durable output from this Agent.",
                    ),
                ):
                    existing = self.runner.run("get_links", {"slug": slug})
                    if not isinstance(existing, list):
                        raise GBrainProtocolError(
                            f"{slug} collection links readback was not a list"
                        )
                    if not any(
                        isinstance(edge, Mapping)
                        and edge.get("from_slug") == slug
                        and edge.get("to_slug") == target
                        and edge.get("link_type") == link_type
                        for edge in existing
                    ):
                        self.runner.run(
                            "add_link",
                            {
                                "from": slug,
                                "to": target,
                                "link_type": link_type,
                                "context": context,
                                "link_source": "gtasks",
                            },
                        )
            links = self.runner.run("get_links", {"slug": slug})
            self._verify_artifact_collection(slug, page, links)

    @staticmethod
    def _verify_artifact_graph(
        artifact: AgentArtifact,
        edges: Sequence[Mapping[str, Any]],
        *,
        require_gtasks_source: bool = False,
    ) -> None:
        outgoing = [
            edge
            for edge in edges
            if isinstance(edge, Mapping) and edge.get("from_slug") == artifact.slug
        ]
        quadruples = {
            (
                edge.get("from_slug"),
                edge.get("to_slug"),
                edge.get("link_type"),
                edge.get("link_source"),
            )
            for edge in outgoing
        }
        expected = {
            (artifact.slug, artifact.agent_collection, "member_of", "gtasks"),
            (artifact.slug, artifact.created_by, "created_by", "gtasks"),
            (artifact.slug, artifact.produced_for, "produced_for", "gtasks"),
        }
        for target, link_type in (
            (artifact.project, "supports_project"),
            (artifact.goal, "supports_goal"),
            (artifact.supersedes, "supersedes"),
        ):
            if target:
                expected.add((artifact.slug, target, link_type, "gtasks"))
        triples = {(source, target, kind) for source, target, kind, _source in quadruples}
        expected_triples = {
            (source, target, kind) for source, target, kind, _source in expected
        }
        if (
            len(outgoing) != len(expected)
            or triples != expected_triples
            or require_gtasks_source
            and quadruples != expected
        ):
            raise GBrainProtocolError(
                "Artifact must have exactly the requested typed relationships"
                + (
                    " from the canonical gtasks source"
                    if require_gtasks_source
                    else ""
                )
            )

    def get_agent_artifact(
        self,
        slug: str,
        *,
        require_gtasks_source: bool = False,
    ) -> AgentArtifact:
        if not isinstance(slug, str) or not slug.startswith("artifacts/"):
            raise ValueError("Artifact slug must start with artifacts/")
        page = self.runner.run("get_page", {"slug": slug})
        links = self.runner.run("get_links", {"slug": slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("Artifact page or link readback was not structured")
        artifact = AgentArtifact.from_page(page, edges=links)
        self._verify_artifact_graph(
            artifact,
            links,
            require_gtasks_source=require_gtasks_source,
        )
        return artifact

    def list_agent_artifacts(
        self,
        *,
        agent: str | None = None,
        task: str | None = None,
        project: str | None = None,
        goal: str | None = None,
        kind: str | None = None,
        cursor: int = 0,
        limit: int = 25,
    ) -> ArtifactRead:
        if agent is not None and agent not in ARTIFACT_BY_AGENT:
            raise ValueError("Artifact Agent filter is invalid")
        for value, namespace, label in (
            (task, "tasks", "task"),
            (project, "projects", "project"),
            (goal, "goals", "goal"),
        ):
            if value is not None:
                self._require_canonical_uuid_slug(value, namespace, label)
        if cursor < 0 or limit < 1 or limit > 50:
            raise ValueError("Artifact pagination is invalid")
        collections = (
            (ARTIFACT_BY_AGENT[agent],)
            if agent is not None
            else tuple(collection for _agent, collection in ARTIFACT_AGENT_SCOPES)
        )
        candidate_slugs: list[str] = []
        for collection in collections:
            backlinks = self.runner.run("get_backlinks", {"slug": collection})
            if not isinstance(backlinks, list):
                raise GBrainProtocolError(
                    f"{collection} Artifact backlinks were not a list"
                )
            candidate_slugs.extend(
                str(edge["from_slug"])
                for edge in backlinks
                if isinstance(edge, Mapping)
                and edge.get("to_slug") == collection
                and edge.get("link_type") == "member_of"
                and isinstance(edge.get("from_slug"), str)
                and str(edge["from_slug"]).startswith("artifacts/")
            )
        candidates = set(candidate_slugs)
        relation_context: dict[str, set[str]] = {}
        issues: list[CollectionIssue] = []
        if task is not None:
            backlinks = self.runner.run("get_backlinks", {"slug": task})
            task_links = self.runner.run("get_links", {"slug": task})
            if not isinstance(backlinks, list) or not isinstance(task_links, list):
                raise GBrainProtocolError("Artifact task relationship reads were not lists")
            produced = {
                str(edge["from_slug"])
                for edge in backlinks
                if isinstance(edge, Mapping)
                and edge.get("to_slug") == task
                and edge.get("link_type") == "produced_for"
                and isinstance(edge.get("from_slug"), str)
            }
            reviewed = {
                str(edge["to_slug"])
                for edge in task_links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == task
                and edge.get("link_type") == "reviews_artifact"
                and isinstance(edge.get("to_slug"), str)
            }
            for slug in sorted(reviewed - candidates):
                issues.append(CollectionIssue(
                    slug=slug,
                    message=(
                        "reviews_artifact target is not a canonical Agent Artifact "
                        "collection member"
                    ),
                    category="artifact_data",
                    impact=(
                        "This review reference remains in GBrain but cannot be "
                        "browsed until its Artifact membership is repaired."
                    ),
                ))
            candidates &= produced | reviewed
            for slug in candidates:
                contexts: set[str] = set()
                if slug in produced:
                    contexts.add("produced_for")
                if slug in reviewed:
                    contexts.add("referenced_for_review")
                if contexts:
                    relation_context[slug] = contexts
        for target, link_type in (
            (project, "supports_project"),
            (goal, "supports_goal"),
        ):
            if target is None:
                continue
            backlinks = self.runner.run("get_backlinks", {"slug": target})
            if not isinstance(backlinks, list):
                raise GBrainProtocolError("Artifact filter backlinks were not a list")
            linked = {
                str(edge["from_slug"])
                for edge in backlinks
                if isinstance(edge, Mapping)
                and edge.get("to_slug") == target
                and edge.get("link_type") == link_type
                and isinstance(edge.get("from_slug"), str)
            }
            candidates &= linked

        def read_one(slug: str) -> tuple[AgentArtifact | None, CollectionIssue | None]:
            try:
                artifact = self.get_agent_artifact(slug)
                if kind is not None and artifact.artifact_kind != kind:
                    return None, None
                return artifact, None
            except (DomainValidationError, GBrainError, ValueError) as exc:
                return None, CollectionIssue(
                    slug=slug,
                    message=str(exc),
                    category="artifact_data",
                    impact=(
                        "This canonical Artifact remains in GBrain but cannot be "
                        "browsed until its page and typed relationships are repaired."
                    ),
                )

        artifacts: list[AgentArtifact] = []
        for artifact, issue in self._bounded_map(read_one, sorted(candidates)):
            if artifact is not None:
                artifacts.append(artifact)
            if issue is not None:
                issues.append(issue)
        artifacts.sort(
            key=lambda item: (item.updated_at or item.created_at, item.created_at, item.slug),
            reverse=True,
        )
        selected = artifacts[cursor : cursor + limit]
        next_cursor = cursor + limit if cursor + limit < len(artifacts) else None
        return ArtifactRead(
            tuple(selected),
            tuple(issues),
            next_cursor,
            {
                artifact.slug: tuple(sorted(relation_context.get(artifact.slug, set())))
                for artifact in selected
                if artifact.slug in relation_context
            },
        )

    def add_artifact_review_reference(
        self, task_slug: str, artifact_slug: str
    ) -> ArtifactReviewReferenceReceipt:
        with self._artifact_review_reference_lock:
            return self._add_artifact_review_reference_locked(
                task_slug, artifact_slug
            )

    def _add_artifact_review_reference_locked(
        self, task_slug: str, artifact_slug: str
    ) -> ArtifactReviewReferenceReceipt:
        self._require_canonical_uuid_slug(task_slug, "tasks", "review task")
        self._require_canonical_uuid_slug(artifact_slug, "artifacts", "review Artifact")
        self.get_task(task_slug)
        # Review links may be added to older, already-valid Artifacts whose
        # typed provenance predates the gtasks link_source marker.  The
        # Artifact graph must still match its canonical page exactly; only the
        # newly-created reviews_artifact edge is required to use gtasks.
        artifact = self.get_agent_artifact(artifact_slug)
        task_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(task_links, list):
            raise GBrainProtocolError("Artifact review task links were not a list")
        matching = [
            edge for edge in task_links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == task_slug
            and edge.get("to_slug") == artifact.slug
            and edge.get("link_type") == "reviews_artifact"
        ]
        if matching and all(edge.get("link_source") == "gtasks" for edge in matching):
            return ArtifactReviewReferenceReceipt(task_slug, artifact.slug, True, True)
        if matching:
            raise GBrainProtocolError(
                "Artifact review reference exists without the canonical gtasks source"
            )
        try:
            self.runner.run(
                "add_link",
                {
                    "from": task_slug,
                    "to": artifact.slug,
                    "link_type": "reviews_artifact",
                    "context": "Canonical Task review reference for this Artifact.",
                    "link_source": "gtasks",
                },
            )
            self.get_task(task_slug)
            stored = self.get_agent_artifact(artifact.slug)
            links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(links, list) or not any(
                isinstance(edge, Mapping)
                and edge.get("from_slug") == task_slug
                and edge.get("to_slug") == artifact.slug
                and edge.get("link_type") == "reviews_artifact"
                and edge.get("link_source") == "gtasks"
                for edge in links
            ):
                raise GBrainProtocolError("Artifact review reference was not verified")
            artifact_links = self.runner.run("get_links", {"slug": stored.slug})
            if not isinstance(artifact_links, list) or not any(
                isinstance(edge, Mapping)
                and edge.get("from_slug") == stored.slug
                and edge.get("to_slug") == stored.produced_for
                and edge.get("link_type") == "produced_for"
                for edge in artifact_links
            ):
                raise GBrainProtocolError("Artifact produced_for provenance changed")
        except (DomainValidationError, GBrainError, ValueError) as exc:
            raise PartialMutationError(
                task_slug,
                "Artifact review reference was not fully verified. Inspect the Task and Artifact links before retrying: "
                + str(exc),
            ) from exc
        return ArtifactReviewReferenceReceipt(task_slug, artifact.slug, True)

    @staticmethod
    def _require_canonical_uuid_slug(value: object, namespace: str, label: str) -> str:
        if not isinstance(value, str) or not value.startswith(f"{namespace}/"):
            raise ValueError(f"Artifact {label} filter must be a canonical UUID slug")
        suffix = value.split("/", 1)[1]
        try:
            parsed = uuid.UUID(suffix)
        except (AttributeError, ValueError) as exc:
            raise ValueError(
                f"Artifact {label} filter must be a canonical UUID slug"
            ) from exc
        if str(parsed) != suffix.lower() or parsed.version not in {4, 5}:
            raise ValueError(f"Artifact {label} filter must be a canonical UUID slug")
        return value

    def _preflight_artifact_task(
        self,
        artifact: AgentArtifact,
        *,
        expected_owner: str | None = None,
    ) -> Task:
        self._require_canonical_uuid_slug(artifact.produced_for, "tasks", "produced_for")
        page = self.runner.run("get_page", {"slug": artifact.produced_for})
        links = self.runner.run("get_links", {"slug": artifact.produced_for})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError(
                "Artifact produced_for task readback was not structured"
            )
        task = Task.from_page(page, edges=links)
        owner = expected_owner or artifact.created_by
        expected_work_root = dict(AGENT_SCOPES)[owner]
        scope_memberships = [
            edge.get("to_slug")
            for edge in links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == artifact.produced_for
            and edge.get("link_type") == "member_of"
            and edge.get("to_slug") in TASK_SCOPE_ROOTS
        ]
        assignments = [
            edge.get("to_slug")
            for edge in links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == artifact.produced_for
            and edge.get("link_type") == "assigned_to"
            and isinstance(edge.get("to_slug"), str)
            and str(edge.get("to_slug")).startswith("agents/")
        ]
        is_agent_work = not (
            task.slug != artifact.produced_for
            or task.status not in {"planned", "active", "blocked", "completed"}
            or task.lifecycle_root != expected_work_root
            or task.owner_agent != owner
            or scope_memberships != [expected_work_root]
            or assignments != [owner]
        )
        is_completed_agent_qa_fixture = (
            task.slug == artifact.produced_for
            and task.status == "completed"
            and task.completed_at is not None
            and task.lifecycle_root == QA_FIXTURES_ROOT
            and task.qa_fixture
            and bool(task.qa_owner)
            and task.owner_agent == owner
            and scope_memberships == [QA_FIXTURES_ROOT]
            and assignments == [owner]
        )
        if not is_agent_work and not is_completed_agent_qa_fixture:
            raise DomainValidationError(
                "Artifact produced_for must be an approved canonical Agent task "
                "or completed Agent QA fixture owned by the verified execution owner with exact "
                "collection membership"
            )
        return task

    def ensure_qa_fixture_collection(self) -> None:
        try:
            page = self.runner.run("get_page", {"slug": QA_FIXTURES_ROOT})
        except GBrainCommandError as exc:
            if "page_not_found" not in str(exc):
                raise
            self.runner.run(
                "put_page",
                {
                    "slug": QA_FIXTURES_ROOT,
                    "content": render_qa_fixtures_collection_page(),
                },
            )
            page = self.runner.run("get_page", {"slug": QA_FIXTURES_ROOT})
        if not isinstance(page, Mapping):
            raise GBrainProtocolError(
                "QA fixture collection readback was not structured"
            )
        frontmatter = page.get("frontmatter")
        if (
            page.get("slug") != QA_FIXTURES_ROOT
            or page.get("type") != "collection"
            or not isinstance(frontmatter, Mapping)
            or frontmatter.get("collection_kind")
            != "mission_control_qa_fixtures"
            or frontmatter.get("member_type") != "task"
        ):
            raise GBrainProtocolError(
                "reserved QA fixture root has the wrong collection contract"
            )

    def create_agent_qa_fixture_task(
        self,
        task: Task,
        agent_slug: str,
    ) -> MutationReceipt:
        approved_agents = {agent for agent, _work_root in AGENT_SCOPES}
        try:
            self._require_canonical_uuid_slug(task.slug, "tasks", "QA fixture task")
        except ValueError as exc:
            raise ValueError(
                "QA fixture task must use a canonical tasks UUID slug"
            ) from exc
        if (
            agent_slug not in approved_agents
            or task.owner_agent != agent_slug
            or task.lifecycle_root != QA_FIXTURES_ROOT
            or task.status != "completed"
            or task.completed_at is None
            or not task.qa_fixture
            or not task.qa_owner
            or task.inbox
        ):
            raise ValueError(
                "Agent QA fixture must be completed, explicitly QA-owned, "
                "and assigned to one approved Agent"
            )
        if (
            task.project is not None
            or task.goal is not None
            or task.parent is not None
            or task.dependencies
            or task.blockers
        ):
            raise ValueError(
                "QA fixture cannot contain project, goal, parent, dependency, "
                "or blocker relationships"
            )
        self.ensure_qa_fixture_collection()
        expected_outgoing = {
            (QA_FIXTURES_ROOT, "member_of"),
            (agent_slug, "assigned_to"),
        }
        existing_page: Mapping[str, Any] | None = None
        existing_links: list[Mapping[str, Any]] = []
        try:
            page_candidate = self.runner.run("get_page", {"slug": task.slug})
        except GBrainCommandError as exc:
            if "page_not_found" not in str(exc):
                raise
        else:
            links_candidate = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(page_candidate, Mapping) or not isinstance(
                links_candidate, list
            ):
                raise ValueError("Existing QA fixture readback was not structured")
            try:
                existing_task = Task.from_page(page_candidate)
            except DomainValidationError as exc:
                raise ValueError(
                    "Existing QA fixture page does not match the requested fixture"
                ) from exc
            frontmatter = page_candidate.get("frontmatter")
            page_links = (
                frontmatter.get("links") if isinstance(frontmatter, Mapping) else None
            )
            page_outgoing = [
                (link.get("to"), link.get("type"))
                for link in page_links
                if isinstance(link, Mapping)
            ] if isinstance(page_links, list) else []
            expected_page_outgoing = [
                (QA_FIXTURES_ROOT, "member_of"),
                (agent_slug, "assigned_to"),
            ]
            if (
                replace(existing_task, owner_agent=task.owner_agent).to_dict()
                != task.to_dict()
                or page_outgoing != expected_page_outgoing
            ):
                raise ValueError(
                    "Existing QA fixture page does not match the requested fixture"
                )
            existing_links = [
                edge
                for edge in links_candidate
                if isinstance(edge, Mapping) and edge.get("from_slug") == task.slug
            ]
            existing_outgoing = {
                (edge.get("to_slug"), edge.get("link_type"))
                for edge in existing_links
            }
            if (
                not existing_outgoing.issubset(expected_outgoing)
                or any(edge.get("link_source") != "gtasks" for edge in existing_links)
            ):
                raise ValueError(
                    "Existing QA fixture relationships do not match the resumable contract"
                )
            existing_page = page_candidate

        references: dict[str, SystemTicketReference | None] = {}
        expected_body = ""
        if existing_page is None:
            references = self._verified_system_ticket_references((task.detail,))
            expected_body = render_task_body(task.title, task.detail, references)

        try:
            if existing_page is None:
                self.runner.run(
                    "put_page",
                    {
                        "slug": task.slug,
                        "content": render_task_page(task, body=expected_body),
                    },
                )
            existing_pairs = {
                (edge.get("to_slug"), edge.get("link_type"))
                for edge in existing_links
            }
            for target, link_type, context in (
                (
                    QA_FIXTURES_ROOT,
                    "member_of",
                    "Isolated Mission Control QA fixture membership.",
                ),
                (
                    agent_slug,
                    "assigned_to",
                    "Executing Agent for this isolated release canary.",
                ),
            ):
                if (target, link_type) in existing_pairs:
                    continue
                self.runner.run(
                    "add_link",
                    {
                        "from": task.slug,
                        "to": target,
                        "link_type": link_type,
                        "context": context,
                        "link_source": "gtasks",
                    },
                )
            page = self.runner.run("get_page", {"slug": task.slug})
            links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise GBrainProtocolError(
                    "QA fixture task readback was not structured"
                )
            stored = Task.from_page(page, edges=links)
            if stored.to_dict() != task.to_dict():
                raise GBrainProtocolError(
                    "QA fixture task readback did not match the requested content"
                )
            if expected_body:
                self._verify_compiled_markdown_body(
                    page, expected_body, label="QA fixture task"
                )
            scope_memberships = [
                edge.get("to_slug")
                for edge in links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == task.slug
                and edge.get("link_type") == "member_of"
                and edge.get("to_slug") in TASK_SCOPE_ROOTS
            ]
            assignments = [
                edge.get("to_slug")
                for edge in links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == task.slug
                and edge.get("link_type") == "assigned_to"
            ]
            outgoing = {
                (edge.get("to_slug"), edge.get("link_type"))
                for edge in links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == task.slug
            }
            if (
                scope_memberships != [QA_FIXTURES_ROOT]
                or assignments != [agent_slug]
                or outgoing != expected_outgoing
                or any(
                    edge.get("link_source") != "gtasks"
                    for edge in links
                    if isinstance(edge, Mapping)
                    and edge.get("from_slug") == task.slug
                )
            ):
                raise GBrainProtocolError(
                    "QA fixture task relationship readback was incomplete"
                )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task.slug,
                f"QA fixture task write could not be verified: {exc}",
            ) from exc
        return MutationReceipt(slug=task.slug, verified=True)

    def create_agent_artifact(
        self,
        artifact: AgentArtifact,
        *,
        executing_agent: str,
        idempotency_key: str | None = None,
        execution_claim: ArtifactExecutionClaim | None = None,
    ) -> ArtifactMutationReceipt:
        with self._artifact_create_lock:
            return self._create_agent_artifact_locked(
                artifact,
                executing_agent=executing_agent,
                idempotency_key=idempotency_key,
                execution_claim=execution_claim,
            )

    def _create_agent_artifact_locked(
        self,
        artifact: AgentArtifact,
        *,
        executing_agent: str,
        idempotency_key: str | None = None,
        execution_claim: ArtifactExecutionClaim | None = None,
    ) -> ArtifactMutationReceipt:
        if (
            executing_agent != artifact.created_by
            or ARTIFACT_BY_AGENT.get(executing_agent) != artifact.agent_collection
        ):
            raise DomainValidationError(
                "Artifact publisher identity does not match its installed execution contract"
            )
        if artifact.delegation_ref is None:
            if execution_claim is not None:
                raise DomainValidationError(
                    "delegation claim is forbidden when delegation_ref is absent"
                )
        elif (
            not isinstance(execution_claim, ArtifactExecutionClaim)
            or not execution_claim.matches(
                artifact, executing_agent=executing_agent
            )
        ):
            raise DomainValidationError(
                "Artifact delegation claim does not match task, executor, owner, and delegation_ref"
            )
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 200
            or "\n" in idempotency_key
            or "\r" in idempotency_key
        ):
            raise ValueError(
                "Artifact idempotency_key must be 1 to 200 characters on one line"
            )
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
        if AGENT_RUNTIME_BY_SLUG.get(executing_agent) == "openclaw":
            activation = self._active_openclaw_activation(executing_agent)
            if (
                artifact.agent_collection
                != activation["canonical_artifact_collection"]
            ):
                raise DomainValidationError(
                    "Artifact collection does not match the activated logical OpenClaw identity"
                )
            self._openclaw_profile_from_activation(activation)
            self._verify_openclaw_task_anchor(activation)
            self._verify_openclaw_artifact_anchor(activation)
            self._preflight_artifact_task(
                artifact,
                expected_owner=(
                    execution_claim.permanent_owner
                    if execution_claim is not None
                    else None
                ),
            )
        else:
            self._preflight_artifact_task(artifact)
            self.ensure_artifact_collections()
        backlinks = (
            self.runner.run(
                "get_backlinks", {"slug": artifact.agent_collection}
            )
            if idempotency_key is not None
            else []
        )
        if not isinstance(backlinks, list):
            raise GBrainProtocolError("Artifact collection backlinks were not a list")
        matches: list[AgentArtifact] = []
        for edge in backlinks:
            if (
                not isinstance(edge, Mapping)
                or edge.get("to_slug") != artifact.agent_collection
                or edge.get("link_type") != "member_of"
                or not isinstance(edge.get("from_slug"), str)
                or not str(edge["from_slug"]).startswith("artifacts/")
            ):
                continue
            page = self.runner.run(
                "get_page", {"slug": str(edge["from_slug"])}
            )
            frontmatter = (
                page.get("frontmatter") if isinstance(page, Mapping) else None
            )
            if (
                isinstance(frontmatter, Mapping)
                and idempotency_key is not None
                and frontmatter.get("idempotency_key") == idempotency_key
            ):
                matches.append(
                    self.get_agent_artifact(
                        str(edge["from_slug"]),
                        require_gtasks_source=True,
                    )
                )
        if len(matches) > 1:
            raise GBrainProtocolError(
                "Artifact idempotency key has multiple canonical matches"
            )
        if matches:
            existing_fields = matches[0].to_dict()
            incoming_fields = artifact.to_dict()
            for field in ("slug", "created_at", "updated_at"):
                existing_fields.pop(field)
                incoming_fields.pop(field)
            if existing_fields != incoming_fields:
                raise ArtifactIdempotencyConflict(
                    "Artifact idempotency key already identifies different content"
                )
            return ArtifactMutationReceipt(matches[0], True, True)
        try:
            self.runner.run(
                "put_page",
                {
                    "slug": artifact.slug,
                    "content": render_agent_artifact_page(
                        artifact, idempotency_key=idempotency_key
                    ),
                },
            )
            relationships = [
                (artifact.agent_collection, "member_of", "Producing Agent Artifact collection."),
                (artifact.created_by, "created_by", "Canonical producing Agent."),
                (artifact.produced_for, "produced_for", "Authorized canonical Task."),
            ]
            for target, link_type, context in (
                (artifact.project, "supports_project", "Supported canonical Project."),
                (artifact.goal, "supports_goal", "Supported canonical Goal."),
                (artifact.supersedes, "supersedes", "Earlier Artifact replaced by this output."),
            ):
                if target:
                    relationships.append((target, link_type, context))
            for target, link_type, context in relationships:
                self.runner.run(
                    "add_link",
                    {
                        "from": artifact.slug,
                        "to": target,
                        "link_type": link_type,
                        "context": context,
                        "link_source": "gtasks",
                    },
                )
            stored = self.get_agent_artifact(
                artifact.slug,
                require_gtasks_source=True,
            )
            stored_fields = stored.to_dict()
            requested_fields = artifact.to_dict()
            stored_fields.pop("updated_at")
            requested_fields.pop("updated_at")
            if stored_fields != requested_fields:
                raise GBrainProtocolError(
                    "Artifact page readback did not match the requested content"
                )
            return ArtifactMutationReceipt(stored, True)
        except ArtifactIdempotencyConflict:
            raise
        except (DomainValidationError, GBrainError, ValueError) as exc:
            raise PartialMutationError(
                artifact.slug,
                "Artifact publication was not fully verified. Inspect the page and typed links before retrying: "
                + str(exc),
            ) from exc

    @staticmethod
    def _identity_namespace(slug: str) -> str:
        if not isinstance(slug, str) or "/" not in slug:
            raise ValueError("canonical identity slug must include a namespace")
        return slug.split("/", 1)[0]

    @staticmethod
    def _identity_entity_kind(slug: str) -> str:
        if slug.startswith("goals/"):
            return "goals"
        if slug.startswith("projects/"):
            return "projects"
        if slug.startswith("tasks/") or any(
            slug.startswith(f"{root}/") for root in AGENT_WORK_ROOTS
        ):
            return "tasks"
        raise ValueError("identity migration supports goals, projects, and canonical tasks only")

    @classmethod
    def _validate_identity_mapping(cls, mapping: Mapping[str, str]) -> dict[str, str]:
        if not mapping:
            raise ValueError("identity migration mapping is required")
        normalized: dict[str, str] = {}
        for old_slug, new_slug in mapping.items():
            old_namespace = cls._identity_entity_kind(old_slug)
            new_namespace = cls._identity_entity_kind(new_slug)
            if new_namespace != old_namespace:
                raise ValueError("identity migration must preserve the same namespace kind")
            if not new_slug.startswith(f"{new_namespace}/"):
                raise ValueError("new canonical identity must use its entity namespace")
            suffix = new_slug.split("/", 1)[1]
            try:
                parsed = uuid.UUID(suffix)
            except (ValueError, AttributeError) as exc:
                raise ValueError("new canonical identity must use an opaque UUID slug") from exc
            if str(parsed) != suffix.lower():
                raise ValueError("new canonical identity must use an opaque UUID slug")
            if old_slug == new_slug:
                raise ValueError("identity migration source and destination must differ")
            if new_slug in normalized.values():
                raise ValueError("identity migration destinations must be unique")
            normalized[old_slug] = new_slug
        return normalized

    @staticmethod
    def _migration_page_content(
        page: Mapping[str, Any],
        mapping: Mapping[str, str],
    ) -> str:
        compiled = page.get("compiled_truth")
        if not isinstance(compiled, str):
            raise GBrainProtocolError("migration source page has no compiled content")
        kind = GBrainAdapter._identity_entity_kind(str(page.get("slug", "")))
        if kind == "goals" and compiled.startswith("---\n"):
            end = compiled.find("\n---", 4)
            if end < 0:
                raise GBrainProtocolError("migration source has malformed compiled frontmatter")
            header = compiled[:end]
            for old_slug in sorted(mapping, key=len, reverse=True):
                header = header.replace(old_slug, mapping[old_slug])
            # Keep the body byte-for-byte. Historical prose and attachment URLs
            # remain valid through the durable old -> new alias rather than
            # being rewritten into paths whose stored bytes may not exist.
            return header + compiled[end:]

        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise GBrainProtocolError("migration source page has no canonical frontmatter")
        body = compiled
        if body.startswith("---\n"):
            body = GBrainAdapter._migration_body_from_rendered_content(body)
        return _render_preserved_page(
            {**page, "compiled_truth": body},
            GBrainAdapter._migration_rewrite_references(frontmatter, mapping),
        )

    @staticmethod
    def _migration_rewrite_references(value: Any, mapping: Mapping[str, str]) -> Any:
        if isinstance(value, Mapping):
            return {
                key: GBrainAdapter._migration_rewrite_references(item, mapping)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                GBrainAdapter._migration_rewrite_references(item, mapping)
                for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                GBrainAdapter._migration_rewrite_references(item, mapping)
                for item in value
            )
        if isinstance(value, str) and value in mapping:
            return mapping[value]
        return value

    @staticmethod
    def _migration_body_from_rendered_content(content: str) -> str:
        if not content.startswith("---\n"):
            raise GBrainProtocolError("migration content has no approved frontmatter")
        boundary = content.find("\n---\n", 4)
        if boundary < 0:
            raise GBrainProtocolError("migration content has malformed approved frontmatter")
        # GBrain exposes only body text in compiled_truth for raw task/project
        # pages. Remove exactly the structural blank line and the one terminal
        # formatting newline emitted by _render_preserved_page; preserve every
        # other body byte, including intentional leading blank lines.
        remainder = content[boundary + len("\n---\n") :]
        if not remainder.startswith("\n"):
            raise GBrainProtocolError("migration content has no structural body gap")
        body = remainder[1:]
        return body[:-1] if body.endswith("\n") else body

    @staticmethod
    def _parse_migration_rendered_content(
        content: str,
    ) -> tuple[dict[str, Any], str] | None:
        """Parse only the flat, JSON-valued frontmatter emitted by this migration.

        Existing canonical Goal Markdown can use a broader YAML shape; those
        pages are accepted only by the exact-content branch in
        ``_migration_destination_matches``. This parser intentionally does not
        become a general YAML reader for arbitrary pre-existing destinations.
        """
        if not content.startswith("---\n"):
            return None
        closing = content.find("\n---\n", 4)
        if closing < 0:
            return None
        header = content[4:closing]
        remainder = content[closing + len("\n---\n") :]
        if not remainder.startswith("\n"):
            return None
        body = remainder[1:]
        # _render_preserved_page emits one formatting newline after the exact
        # rstripped source body; GBrain exposes only that body in compiled_truth.
        if body.endswith("\n"):
            body = body[:-1]

        frontmatter: dict[str, Any] = {}
        for line in header.splitlines():
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*): (.+)", line)
            if match is None:
                return None
            key, raw_value = match.groups()
            if key in frontmatter:
                return None
            try:
                frontmatter[key] = json.loads(raw_value)
            except json.JSONDecodeError:
                return None
        if not frontmatter:
            return None
        return frontmatter, body

    @classmethod
    def _migration_destination_matches(
        cls,
        destination: Mapping[str, Any],
        expected_content: str,
    ) -> bool:
        """Strictly match exact or documented GBrain-normalized page storage."""
        compiled = destination.get("compiled_truth")
        if not isinstance(compiled, str):
            return False
        if compiled == expected_content:
            return True

        parsed = cls._parse_migration_rendered_content(expected_content)
        if parsed is None:
            return False
        expected_frontmatter, expected_body = parsed
        expected_type = expected_frontmatter.pop("type", None)
        expected_title = expected_frontmatter.pop("title", None)
        if not isinstance(expected_type, str) or not isinstance(expected_title, str):
            return False
        if destination.get("type") != expected_type or destination.get("title") != expected_title:
            return False
        if compiled != expected_body:
            return False

        stored_frontmatter = destination.get("frontmatter")
        if not isinstance(stored_frontmatter, Mapping):
            return False
        actual_frontmatter = dict(stored_frontmatter)
        if "type" in actual_frontmatter or "title" in actual_frontmatter:
            return False

        # GBrain may expose source metadata at the page row instead of inside
        # frontmatter. Accept that documented placement only when the value is
        # byte-for-byte/value-for-value identical; all content fields remain an
        # exact nested mapping comparison.
        provenance_fields = {
            "source_kind",
            "source_uri",
            "ingested_via",
            "ingested_at",
            "source_id",
        }
        for key in provenance_fields:
            if key in expected_frontmatter and key not in actual_frontmatter:
                if destination.get(key) != expected_frontmatter[key]:
                    return False
                expected_frontmatter.pop(key)
        return actual_frontmatter == expected_frontmatter

    @staticmethod
    def _migration_edge_key(edge: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            str(edge.get("from_slug", "")),
            str(edge.get("to_slug", "")),
            str(edge.get("link_type") or ""),
        )

    @staticmethod
    def _migration_link_descriptor(edge: Mapping[str, Any]) -> dict[str, Any]:
        descriptor: dict[str, Any] = {
            "from": str(edge.get("from_slug", "")),
            "to": str(edge.get("to_slug", "")),
            "link_type": str(edge.get("link_type") or ""),
        }
        context = edge.get("context")
        source = edge.get("link_source")
        if isinstance(context, str) and context:
            descriptor["context"] = context
        # GBrain owns the `markdown` provenance during its own reconciliation
        # pass and rejects callers trying to forge it. Retain relationship
        # semantics/context but record migration as the explicit writer.
        if isinstance(source, str) and source and source != "markdown":
            descriptor["link_source"] = source
        elif source == "markdown":
            descriptor["link_source"] = "gtasks-identity-migration"
        return descriptor

    def resolve_canonical_slug(self, slug: str) -> str:
        """Follow one durable legacy alias while failing closed on ambiguity."""
        current = slug
        visited: set[str] = set()
        for _ in range(8):
            if current in visited:
                raise GBrainProtocolError(f"canonical alias cycle detected for {slug}")
            visited.add(current)
            links = self.runner.run("get_links", {"slug": current})
            if not isinstance(links, list):
                raise GBrainProtocolError("canonical alias readback was not a list")
            targets = {
                str(edge.get("to_slug"))
                for edge in links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == current
                and edge.get("link_type") == "canonical_alias_of"
                and isinstance(edge.get("to_slug"), str)
            }
            if not targets:
                return current
            if len(targets) != 1:
                raise GBrainProtocolError(f"canonical alias is ambiguous for {current}")
            target = next(iter(targets))
            if self._identity_entity_kind(target) != self._identity_entity_kind(current):
                raise GBrainProtocolError(f"canonical alias changes namespace for {current}")
            current = target
        raise GBrainProtocolError(f"canonical alias chain is too deep for {slug}")

    def audit_canonical_identity_migration(
        self,
        mapping: Mapping[str, str],
        *,
        excluded: tuple[str, ...] = (),
        allow_matching_destinations: bool = False,
    ) -> dict[str, Any]:
        """Build a mutation-free source/destination and relationship audit."""
        normalized = self._validate_identity_mapping(mapping)
        if set(excluded) & set(normalized):
            raise ValueError("excluded identities must not appear in the migration mapping")
        entities: list[dict[str, Any]] = []
        goal_repairs: list[str] = []
        for old_slug, new_slug in normalized.items():
            page = self.runner.run(
                "get_page", {"slug": old_slug, "include_deleted": True}
            )
            outgoing = self.runner.run("get_links", {"slug": old_slug})
            incoming = self.runner.run("get_backlinks", {"slug": old_slug})
            if not isinstance(page, Mapping) or not isinstance(outgoing, list) or not isinstance(incoming, list):
                raise GBrainProtocolError("identity migration audit was not structured")
            if page.get("deleted_at"):
                raise ValueError(f"migration source is soft-deleted: {old_slug}")
            kind = self._identity_entity_kind(old_slug)
            canonical_roots_by_kind = {
                "goals": {GOALS_ROOT},
                "projects": {PROJECTS_ROOT},
                "tasks": {*TASK_SCOPE_ROOTS, SYSTEM_TICKETS_ROOT},
            }
            scope_roots = {
                str(edge.get("to_slug"))
                for edge in outgoing
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == old_slug
                and edge.get("to_slug") in canonical_roots_by_kind[kind]
                and edge.get("link_type") in {"member_of", "", None}
            }
            if len(scope_roots) != 1:
                raise ValueError(
                    f"migration source does not have exactly one canonical scope root: {old_slug}"
                )
            if kind == "goals":
                Goal.from_page(page, edges=outgoing)
                if any(
                    isinstance(edge, Mapping)
                    and edge.get("from_slug") == old_slug
                    and edge.get("to_slug") == GOALS_ROOT
                    and not edge.get("link_type")
                    for edge in outgoing
                ):
                    goal_repairs.append(old_slug)
            elif kind == "projects" and page.get("type") != "project":
                raise ValueError(f"project has unexpected page type: {old_slug}")
            elif kind == "tasks" and page.get("type") != "task":
                raise ValueError(f"task has unexpected page type: {old_slug}")
            content = self._migration_page_content(page, normalized)
            destination_state = "missing"
            try:
                existing = self.runner.run(
                    "get_page", {"slug": new_slug, "include_deleted": True}
                )
            except GBrainCommandError as exc:
                if "page_not_found" not in str(exc):
                    raise
            else:
                if not isinstance(existing, Mapping):
                    raise GBrainProtocolError("migration destination readback was not structured")
                destination_matches = self._migration_destination_matches(existing, content)
                if existing.get("deleted_at") or not destination_matches:
                    raise ValueError(
                        f"migration destination does not match the approved plan: {new_slug}"
                    )
                elif not allow_matching_destinations:
                    raise ValueError(f"migration destination already exists: {new_slug}")
                elif destination_state == "missing":
                    destination_state = "resumable_verified"
            entities.append(
                {
                    "old_slug": old_slug,
                    "new_slug": new_slug,
                    "kind": kind[:-1],
                    "raw_type": page.get("type"),
                    "scope_root": next(iter(scope_roots)),
                    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "destination_state": destination_state,
                    "outgoing_edges": len([edge for edge in outgoing if isinstance(edge, Mapping)]),
                    "incoming_edges": len([edge for edge in incoming if isinstance(edge, Mapping)]),
                }
            )
        for slug in excluded:
            page = self.runner.run("get_page", {"slug": slug, "include_deleted": True})
            if not isinstance(page, Mapping) or not page.get("deleted_at"):
                raise ValueError(f"excluded identity is not verified soft-deleted: {slug}")
        return {
            "entity_count": len(entities),
            "entities": entities,
            "goal_membership_repairs": goal_repairs,
            "excluded": list(excluded),
            "verified": True,
        }

    def migrate_canonical_identities(
        self,
        mapping: Mapping[str, str],
        *,
        excluded: tuple[str, ...] = (),
    ) -> IdentityMigrationReceipt:
        """Copy and relink canonical entities to opaque IDs without deleting history.

        Old pages remain in place as immutable historical/legacy entry points and
        receive one typed ``canonical_alias_of`` edge. Their active graph edges are
        retired only after every copied page and remapped relationship reads back.
        """
        normalized = self._validate_identity_mapping(mapping)
        excluded_set = set(excluded)
        overlap = excluded_set & set(normalized)
        if overlap:
            raise ValueError("excluded identities must not appear in the migration mapping")

        pages: dict[str, Mapping[str, Any]] = {}
        destination_content: dict[str, str] = {}
        destinations_to_write: set[str] = set()
        source_edges: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        source_edge_keys_by_slug: dict[str, set[tuple[str, str, str]]] = {}
        for old_slug, new_slug in normalized.items():
            page = self.runner.run(
                "get_page", {"slug": old_slug, "include_deleted": True}
            )
            outgoing = self.runner.run("get_links", {"slug": old_slug})
            incoming = self.runner.run("get_backlinks", {"slug": old_slug})
            if not isinstance(page, Mapping) or not isinstance(outgoing, list) or not isinstance(incoming, list):
                raise GBrainProtocolError("identity migration snapshot was not structured")
            if page.get("deleted_at"):
                raise ValueError(f"migration source is soft-deleted: {old_slug}")
            if self._identity_entity_kind(old_slug) == "goals":
                Goal.from_page(page, edges=outgoing)
            elif self._identity_entity_kind(old_slug) == "projects":
                if page.get("type") != "project":
                    raise ValueError(f"project has unexpected page type: {old_slug}")
            elif page.get("type") != "task":
                raise ValueError(f"task has unexpected page type: {old_slug}")
            pages[old_slug] = page
            content = self._migration_page_content(page, normalized)
            destination_content[old_slug] = content
            try:
                existing = self.runner.run(
                    "get_page", {"slug": new_slug, "include_deleted": True}
                )
            except GBrainCommandError as exc:
                if "page_not_found" not in str(exc):
                    raise
                destinations_to_write.add(old_slug)
            else:
                if not isinstance(existing, Mapping):
                    raise GBrainProtocolError("migration destination readback was not structured")
                destination_matches = self._migration_destination_matches(existing, content)
                if existing.get("deleted_at") or not destination_matches:
                    raise PartialMutationError(
                        old_slug,
                        "Existing migration destination does not semantically match the approved plan.",
                    )
            source_edge_keys_by_slug[old_slug] = {
                self._migration_edge_key(raw_edge)
                for raw_edge in [*outgoing, *incoming]
                if isinstance(raw_edge, Mapping)
            }
            for raw_edge in [*outgoing, *incoming]:
                if not isinstance(raw_edge, Mapping):
                    continue
                key = self._migration_edge_key(raw_edge)
                if not key[0] or not key[1]:
                    continue
                source_edges.setdefault(key, raw_edge)

        for old_slug in destinations_to_write:
            self.runner.run(
                "put_page",
                {
                    "slug": normalized[old_slug],
                    "content": destination_content[old_slug],
                    "source_kind": "gtasks-identity-migration",
                    "source_uri": f"gbrain://{old_slug}",
                    "ingested_via": "gtasks-identity-migration",
                },
            )
            readback = self.runner.run("get_page", {"slug": normalized[old_slug]})
            if not isinstance(readback, Mapping) or not self._migration_destination_matches(
                readback, destination_content[old_slug]
            ):
                raise PartialMutationError(
                    old_slug,
                    "Migrated page content did not read back exactly after write.",
                )

        desired_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        migrated_kinds = {
            normalized[old]: self._identity_entity_kind(old)
            for old in normalized
        }
        canonical_roots_by_kind = {
            "goals": {GOALS_ROOT},
            "projects": {PROJECTS_ROOT},
            "tasks": {*TASK_SCOPE_ROOTS, SYSTEM_TICKETS_ROOT},
        }
        migrated_goal_slugs = {
            normalized[old]
            for old in normalized
            if self._identity_entity_kind(old) == "goals"
        }
        for source_edge in source_edges.values():
            from_slug = normalized.get(str(source_edge.get("from_slug")), str(source_edge.get("from_slug")))
            to_slug = normalized.get(str(source_edge.get("to_slug")), str(source_edge.get("to_slug")))
            link_type = str(source_edge.get("link_type") or "")
            if (
                from_slug in migrated_kinds
                and to_slug in canonical_roots_by_kind[migrated_kinds[from_slug]]
                and not link_type
            ):
                link_type = "member_of"
            mapped_edge = {
                **source_edge,
                "from_slug": from_slug,
                "to_slug": to_slug,
                "link_type": link_type,
            }
            desired_edges.setdefault(self._migration_edge_key(mapped_edge), mapped_edge)

        for edge in desired_edges.values():
            self.runner.run("add_link", self._migration_link_descriptor(edge))

        for new_slug, kind in migrated_kinds.items():
            new_links = self.runner.run("get_links", {"slug": new_slug})
            if not isinstance(new_links, list):
                raise GBrainProtocolError("migrated scope links were not structured")
            expected_roots = {
                to_slug
                for from_slug, to_slug, link_type in desired_edges
                if from_slug == new_slug
                and to_slug in canonical_roots_by_kind[kind]
                and link_type == "member_of"
            }
            if len(expected_roots) != 1:
                raise PartialMutationError(
                    new_slug,
                    "Migrated entity does not have exactly one canonical scope root.",
                )
            expected_root = next(iter(expected_roots))
            for edge in new_links:
                if (
                    isinstance(edge, Mapping)
                    and edge.get("from_slug") == new_slug
                    and edge.get("to_slug") in canonical_roots_by_kind[kind]
                    and not edge.get("link_type")
                ):
                    self.runner.run(
                        "remove_link",
                        {
                            "from": new_slug,
                            "to": str(edge.get("to_slug")),
                            "link_type": "",
                        },
                    )
            typed_scope_edges = [
                edge
                for edge in new_links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == new_slug
                and edge.get("to_slug") == expected_root
                and edge.get("link_type") == "member_of"
            ]
            if len(typed_scope_edges) != 1:
                self.runner.run(
                    "remove_link",
                    {
                        "from": new_slug,
                        "to": expected_root,
                        "link_type": "member_of",
                    },
                )
                self.runner.run(
                    "add_link",
                    {
                        "from": new_slug,
                        "to": expected_root,
                        "link_type": "member_of",
                        "context": "Canonical Mission Control scope after immutable identity migration.",
                        "link_source": "gtasks-identity-migration",
                    },
                )
            verified_scope = self.runner.run("get_links", {"slug": new_slug})
            if not isinstance(verified_scope, list):
                raise GBrainProtocolError("migrated scope verification was not structured")
            typed_verified = [
                edge
                for edge in verified_scope
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == new_slug
                and edge.get("to_slug") == expected_root
                and edge.get("link_type") == "member_of"
            ]
            untyped_verified = [
                edge
                for edge in verified_scope
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == new_slug
                and edge.get("to_slug") == expected_root
                and not edge.get("link_type")
            ]
            if len(typed_verified) != 1 or untyped_verified:
                raise PartialMutationError(
                    new_slug,
                    "Canonical scope relationship did not read back exactly once.",
                )

        for old_slug, new_slug in normalized.items():
            self.runner.run(
                "add_link",
                {
                    "from": old_slug,
                    "to": new_slug,
                    "link_type": "canonical_alias_of",
                    "context": "Legacy canonical identity retained for history and inbound compatibility.",
                    "link_source": "gtasks-identity-migration",
                },
            )

        for old_slug, new_slug in normalized.items():
            page = self.runner.run("get_page", {"slug": new_slug})
            links = self.runner.run("get_links", {"slug": new_slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise PartialMutationError(old_slug, "Migrated page readback was not structured.")
            namespace = self._identity_entity_kind(new_slug)
            try:
                if namespace == "goals":
                    Goal.from_page(page, edges=links)
                elif namespace == "projects":
                    if page.get("type") != "project":
                        raise DomainValidationError("migrated project type was not preserved")
                elif page.get("type") != "task":
                    raise DomainValidationError("migrated task type was not preserved")
            except DomainValidationError as exc:
                raise PartialMutationError(old_slug, f"Migrated entity type was not verified: {exc}") from exc

        for key in desired_edges:
            from_slug, to_slug, link_type = key
            links = self.runner.run("get_links", {"slug": from_slug})
            if not isinstance(links, list) or not any(
                isinstance(edge, Mapping)
                and self._migration_edge_key(edge) == (from_slug, to_slug, link_type)
                for edge in links
            ):
                raise PartialMutationError(
                    from_slug,
                    f"Migrated relationship was not verified: {link_type} -> {to_slug}.",
                )

        # Fail before retiring any legacy edge if another writer changed a
        # source page or relationship set during the copy/readback window.
        # Leaving verified copies + aliases is recoverable; deleting a newly
        # added relationship would not be.
        snapshot_fields = (
            "type",
            "title",
            "compiled_truth",
            "frontmatter",
            "content_hash",
            "updated_at",
            "deleted_at",
        )
        for old_slug, snapshot_page in pages.items():
            current_page = self.runner.run(
                "get_page", {"slug": old_slug, "include_deleted": True}
            )
            current_links = self.runner.run("get_links", {"slug": old_slug})
            current_backlinks = self.runner.run("get_backlinks", {"slug": old_slug})
            if not isinstance(current_page, Mapping) or not isinstance(current_links, list) or not isinstance(current_backlinks, list):
                raise PartialMutationError(old_slug, "Source concurrency readback was not structured.")
            if any(
                current_page.get(field) != snapshot_page.get(field)
                for field in snapshot_fields
            ):
                raise PartialMutationError(
                    old_slug,
                    "Source page changed during migration; legacy edges were not retired.",
                )
            current_keys = {
                self._migration_edge_key(edge)
                for edge in [*current_links, *current_backlinks]
                if isinstance(edge, Mapping)
                and edge.get("link_type") != "canonical_alias_of"
            }
            if current_keys != source_edge_keys_by_slug[old_slug]:
                raise PartialMutationError(
                    old_slug,
                    "Source relationships changed during migration; legacy edges were not retired.",
                )

        # Only after the complete replacement graph verifies do legacy edges
        # retire. The pages themselves remain as durable history/redirects.
        for source_edge in source_edges.values():
            from_slug = str(source_edge.get("from_slug"))
            to_slug = str(source_edge.get("to_slug"))
            if from_slug not in normalized and to_slug not in normalized:
                continue
            descriptor = {
                "from": from_slug,
                "to": to_slug,
                "link_type": str(source_edge.get("link_type") or ""),
            }
            self.runner.run("remove_link", descriptor)

        for old_slug, new_slug in normalized.items():
            old_links = self.runner.run("get_links", {"slug": old_slug})
            old_backlinks = self.runner.run("get_backlinks", {"slug": old_slug})
            if not isinstance(old_links, list) or not isinstance(old_backlinks, list):
                raise PartialMutationError(old_slug, "Legacy alias readback was not structured.")
            aliases = [
                edge
                for edge in old_links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == old_slug
                and edge.get("to_slug") == new_slug
                and edge.get("link_type") == "canonical_alias_of"
            ]
            residual = [
                edge
                for edge in [*old_links, *old_backlinks]
                if isinstance(edge, Mapping)
                and edge.get("link_type") != "canonical_alias_of"
            ]
            if len(aliases) != 1 or residual:
                raise PartialMutationError(old_slug, "Legacy identity was not reduced to one verified alias.")

        return IdentityMigrationReceipt(
            mapping=dict(normalized),
            migrated=tuple(normalized.values()),
            excluded=tuple(excluded),
            verified=True,
        )

    def get_tony_profile(self) -> dict[str, Any]:
        """Read Tony's Board identity from the canonical GBrain person page."""
        page = self.runner.run("get_page", {"slug": TONY_PROFILE_SLUG})
        if not isinstance(page, Mapping):
            raise GBrainProtocolError("Tony profile readback was not structured")
        if page.get("type") != "person":
            raise DomainValidationError("people/tony-guan is not a person page")
        title = page.get("title")
        if not isinstance(title, str) or not title.strip():
            raise DomainValidationError("Tony profile title is required")
        name = title.strip()
        avatar: dict[str, str] = {
            "kind": "initials",
            "value": "".join(part[0].upper() for part in name.split())[:2] or "T",
        }
        frontmatter = page.get("frontmatter")
        frontmatter = frontmatter if isinstance(frontmatter, Mapping) else {}
        configured_avatar = frontmatter.get("avatar")
        if (
            isinstance(configured_avatar, Mapping)
            and configured_avatar.get("kind") == "attachment"
            and isinstance(configured_avatar.get("value"), str)
            and str(configured_avatar["value"]).startswith("/media/")
        ):
            avatar = {"kind": "attachment", "value": str(configured_avatar["value"])}
        else:
            body = page.get("compiled_truth")
            if isinstance(body, str):
                for match in _MARKDOWN_ATTACHMENT.finditer(body):
                    relative_path = match.group(1).strip()
                    if (
                        relative_path.startswith(f"{TONY_PROFILE_SLUG}/")
                        and ".." not in relative_path.split("/")
                    ):
                        avatar = {
                            "kind": "attachment",
                            "value": f"/media/{quote(relative_path, safe='/')}",
                        }
                        break
        return {"slug": TONY_PROFILE_SLUG, "name": name, "avatar": avatar}

    def _bounded_map(self, function: Any, values: list[Any]) -> list[Any]:
        if len(values) < 2 or isinstance(self.runner, SubprocessCommandRunner):
            return [function(value) for value in values]
        with ThreadPoolExecutor(max_workers=min(8, len(values))) as executor:
            return list(executor.map(function, values))

    def _empty_collection_root_issue(
        self,
        root_slug: str,
        *,
        raw_backlinks: list[object] | None = None,
    ) -> CollectionIssue:
        """Return an actionable issue instead of a false successful empty root.

        A missing/empty canonical root is different from a valid empty projection.
        Only this bounded empty-root path performs the root page read, so healthy
        populated collections retain their existing fan-out and latency profile.
        """
        try:
            page = self.runner.run("get_page", {"slug": root_slug})
            if not isinstance(page, Mapping) or page.get("slug") != root_slug:
                raise GBrainProtocolError("canonical root page readback was not structured")
            if page.get("type") != "collection":
                raise DomainValidationError("canonical root is not a collection page")
            message = (
                f"Canonical root {root_slug} has zero verified member_of backlinks."
            )
        except (DomainValidationError, GBrainError) as exc:
            message = f"Canonical root {root_slug} could not be read: {exc}"
        return CollectionIssue(
            slug=root_slug,
            message=message,
            category="canonical_root_data",
            impact=(
                "Mission Control is withholding this empty surface until the canonical "
                "root page and typed membership backlinks are restored."
            ),
            repair_action=(
                "Refresh the canonical GBrain root and restore its typed member_of "
                "links; do not create replacement records from the dashboard."
            ),
        )

    def list_collection_tasks(self, root_slug: str) -> CollectionRead:
        if root_slug not in APPROVED_ROOTS:
            raise ValueError("collection root is not approved for GTasks")
        try:
            raw_backlinks = self.runner.run("get_backlinks", {"slug": root_slug})
        except GBrainError:
            return CollectionRead(
                root_slug=root_slug,
                tasks=(),
                issues=(self._empty_collection_root_issue(root_slug),),
            )
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("get_backlinks did not return a list")

        member_slugs: dict[str, bool] = {}
        for backlink in raw_backlinks:
            if not isinstance(backlink, Mapping):
                continue
            if (
                backlink.get("to_slug") == root_slug
                and isinstance(backlink.get("from_slug"), str)
            ):
                link_type = backlink.get("link_type")
                if link_type == "member_of":
                    member_slugs[str(backlink["from_slug"])] = False
                elif link_type in {"", None}:
                    member_slugs.setdefault(str(backlink["from_slug"]), True)

        if not member_slugs and root_slug in {ACTIVE_ROOT, COMPLETED_ROOT}:
            return CollectionRead(
                root_slug=root_slug,
                tasks=(),
                issues=(
                    self._empty_collection_root_issue(
                        root_slug,
                        raw_backlinks=raw_backlinks,
                    ),
                ),
            )
        if not member_slugs:
            return CollectionRead(root_slug=root_slug, tasks=(), issues=())

        def read_task(
            item: tuple[str, bool],
        ) -> tuple[Task | None, list[CollectionIssue]]:
            slug, legacy_untyped = item
            item_issues: list[CollectionIssue] = []
            try:
                page = self.runner.run("get_page", {"slug": slug})
                if not isinstance(page, Mapping):
                    raise GBrainProtocolError("get_page did not return an object")
                frontmatter = page.get("frontmatter")
                if (
                    legacy_untyped
                    and (
                        not isinstance(frontmatter, Mapping)
                        or frontmatter.get("collection") != root_slug
                    )
                ):
                    return None, []
                relationship_warning: CollectionIssue | None = None
                try:
                    raw_edges = self.runner.run("get_links", {"slug": slug})
                    if not isinstance(raw_edges, list):
                        raise GBrainProtocolError("get_links did not return a list")
                    edges = raw_edges
                except GBrainError:
                    edges = []
                    relationship_warning = _visible_warning(
                        slug,
                        "Optional task relationships could not be read from GBrain.",
                        category="optional_relationship",
                        impact=(
                            "The task is shown from its core fields, but goal, project, "
                            "dependency, and blocker links may be incomplete."
                        ),
                    )
                normalized_page, normalized_edges, warnings = (
                    _normalize_collection_task(
                        page,
                        edges,
                        root_slug,
                        legacy_untyped_backlink=legacy_untyped,
                    )
                )
                task = Task.from_page(normalized_page, edges=normalized_edges)
                if task.lifecycle_root != root_slug:
                    raise DomainValidationError(
                        "page frontmatter does not match its lifecycle root edge"
                    )
                item_issues.extend(warnings)
                if relationship_warning is not None:
                    item_issues.append(relationship_warning)
                return task, item_issues
            except (DomainValidationError, GBrainError) as exc:
                item_issues.append(
                    CollectionIssue(
                        slug=slug,
                        message=str(exc),
                        impact=(
                            "This linked page is not shown because a required task "
                            "field or lifecycle rule is invalid."
                        ),
                    )
                )
                return None, item_issues

        tasks: list[Task] = []
        issues: list[CollectionIssue] = []
        for task, item_issues in self._bounded_map(
            read_task,
            list(member_slugs.items()),
        ):
            if task is not None:
                tasks.append(task)
            issues.extend(item_issues)

        return CollectionRead(
            root_slug=root_slug,
            tasks=tuple(tasks),
            issues=tuple(issues),
        )

    def _agent_scopes(self) -> tuple[tuple[str, str], ...]:
        """Read agent work scopes from canonical Agent nodes with legacy fallback."""
        legacy = dict(EXISTING_CODEX_AGENT_SCOPES)
        try:
            raw = self.runner.run("list_pages", {"type": "agent"})
        except (GBrainError, KeyError):
            return EXISTING_CODEX_AGENT_SCOPES
        pages = raw.get("pages", raw) if isinstance(raw, Mapping) else raw
        if not isinstance(pages, list):
            raise GBrainProtocolError("agent directory list was not a list")
        scopes: list[tuple[str, str]] = []
        for item in pages:
            if not isinstance(item, Mapping):
                continue
            slug = item.get("slug")
            if (
                not isinstance(slug, str)
                or not slug.startswith("agents/")
                or slug.endswith("-oc")
            ):
                continue
            frontmatter = item.get("frontmatter")
            frontmatter = frontmatter if isinstance(frontmatter, Mapping) else {}
            work_root = frontmatter.get("work_root")
            if not isinstance(work_root, str) or not work_root.startswith("collections/"):
                work_root = legacy.get(slug)
            if isinstance(work_root, str) and work_root.startswith("collections/"):
                scopes.append((slug, work_root))
        # The original three GTasks agents predate the typed directory field.
        # Keep reading those exact canonical slugs during migration so a damaged
        # profile is reported rather than silently disappearing from controls.
        known = {slug for slug, _root in scopes}
        scopes.extend(
            (slug, root)
            for slug, root in EXISTING_CODEX_AGENT_SCOPES
            if slug not in known
        )
        return tuple(dict.fromkeys(scopes))

    def _activated_openclaw_profiles(self) -> tuple[Mapping[str, Any], ...]:
        """Return only profiles named by Stargraph's CAS-active manifest.

        The regular GBrain agent directory intentionally cannot activate an
        `-oc` identity; staged pages become visible here only after the NATS
        control key selected their immutable manifest.
        """
        if self.openclaw_profiles is None:
            return ()
        projection = self.openclaw_profiles.active_projection()
        if not isinstance(projection, Mapping):
            raise GBrainProtocolError(
                "OpenClaw active profile projection was invalid"
            )
        generation = projection.get("generation")
        active_manifest = projection.get("active_manifest")
        manifest_digest = projection.get("manifest_digest")
        raw_profiles = projection.get("profiles")
        if not isinstance(raw_profiles, list):
            raise GBrainProtocolError("OpenClaw active profile projection was invalid")
        if not _openclaw_active_manifest_identity_is_valid(
            generation, active_manifest, manifest_digest
        ):
            raise GBrainProtocolError(
                "OpenClaw active profile manifest identity was invalid"
            )
        if generation == 0:
            if raw_profiles:
                raise GBrainProtocolError(
                    "OpenClaw generation zero cannot expose active profiles"
                )
            return ()
        assert isinstance(generation, int)
        assert isinstance(active_manifest, str)
        operation_id = active_manifest.split(f"g{generation:06d}-", 1)[1]
        staged_prefix = (
            "system/openclaw-profile-staging/"
            f"g{generation:06d}-{operation_id}/staged/"
        )
        expected = {
            "canonical_agent_slug",
            "canonical_task_collection",
            "canonical_artifact_collection",
            "staged_agent_slug",
            "staged_task_collection",
            "staged_artifact_collection",
            "page_hashes",
            "metadata",
        }
        if len(raw_profiles) != len(APPROVED_OPENCLAW_DECLARATIONS):
            raise GBrainProtocolError(
                "OpenClaw active profile manifest must contain exactly three Agents"
            )
        profiles: list[Mapping[str, Any]] = []
        for item in raw_profiles:
            if not isinstance(item, Mapping) or set(item) != expected:
                raise GBrainProtocolError("OpenClaw active profile manifest was malformed")
            values = {
                key: item.get(key)
                for key in expected
                if key not in {"page_hashes", "metadata"}
            }
            page_hashes = item.get("page_hashes")
            metadata = item.get("metadata")
            if (
                not all(isinstance(value, str) and value for value in values.values())
                or not isinstance(page_hashes, Mapping)
                or not isinstance(metadata, Mapping)
            ):
                raise GBrainProtocolError("OpenClaw active profile manifest was malformed")
            canonical = str(values["canonical_agent_slug"])
            staged_agent = str(values["staged_agent_slug"])
            staged_tasks = str(values["staged_task_collection"])
            staged_artifacts = str(values["staged_artifact_collection"])
            declaration = APPROVED_OPENCLAW_DECLARATIONS.get(canonical)
            if (
                declaration is None
                or values["canonical_task_collection"]
                != declaration["task_collection"]
                or values["canonical_artifact_collection"]
                != declaration["artifact_collection"]
                or staged_agent != f"{staged_prefix}{canonical}"
                or staged_tasks
                != f"{staged_prefix}{declaration['task_collection']}"
                or staged_artifacts
                != f"{staged_prefix}{declaration['artifact_collection']}"
                or set(page_hashes) != {
                    staged_agent,
                    staged_tasks,
                    staged_artifacts,
                }
                or not all(
                    isinstance(digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", digest)
                    for digest in page_hashes.values()
                )
            ):
                raise GBrainProtocolError(
                    "OpenClaw active profile manifest has an invalid approved identity mapping"
                )
            metadata_frontmatter = metadata.get("frontmatter")
            try:
                metadata_digest = _canonical_json_digest(metadata)
            except (TypeError, ValueError) as exc:
                raise GBrainProtocolError(
                    "OpenClaw active profile metadata was not canonical JSON"
                ) from exc
            if (
                metadata.get("slug") != staged_agent
                or metadata.get("type") != "agent"
                or not isinstance(metadata.get("title"), str)
                or not str(metadata["title"]).strip()
                or not isinstance(metadata.get("compiled_truth"), str)
                or not isinstance(metadata_frontmatter, Mapping)
                or metadata_frontmatter.get("runtime") != "openclaw"
                or metadata_frontmatter.get("route") != declaration["route"]
                or metadata_frontmatter.get("activation_generation") != generation
                or metadata_frontmatter.get("activation_operation_id")
                != operation_id
                or metadata_frontmatter.get("canonical_slug") != canonical
                or metadata_frontmatter.get("staged") is not True
                or not hmac.compare_digest(
                    metadata_digest,
                    str(page_hashes[staged_agent]),
                )
            ):
                raise GBrainProtocolError(
                    "OpenClaw active profile metadata was not bound to its approved declaration"
                )
            profiles.append(
                {
                    **{key: str(value) for key, value in values.items()},
                    "metadata": deepcopy(dict(metadata)),
                }
            )
        if {
            str(item["canonical_agent_slug"]) for item in profiles
        } != set(APPROVED_OPENCLAW_DECLARATIONS):
            raise GBrainProtocolError(
                "OpenClaw active profile manifest did not contain the approved identities"
            )
        return tuple(sorted(profiles, key=lambda item: item["canonical_agent_slug"]))

    def _openclaw_profile_from_activation(
        self, activation: Mapping[str, Any]
    ) -> AgentProfile:
        canonical_slug = str(activation["canonical_agent_slug"])
        logical_page = self.runner.run("get_page", {"slug": canonical_slug})
        logical_edges = self.runner.run("get_links", {"slug": canonical_slug})
        if not isinstance(logical_page, Mapping) or not isinstance(
            logical_edges, list
        ):
            raise GBrainProtocolError(
                f"{canonical_slug} logical Agent anchor readback was not structured"
            )
        frontmatter = logical_page.get("frontmatter")
        if (
            logical_page.get("slug") != canonical_slug
            or logical_page.get("type") != "agent"
            or not isinstance(frontmatter, Mapping)
            or frontmatter.get("runtime") != "openclaw"
            or frontmatter.get("logical_anchor") is not True
        ):
            raise GBrainProtocolError(
                f"{canonical_slug} is not a canonical logical OpenClaw Agent anchor"
            )

        generation_metadata = activation.get("metadata")
        if not isinstance(generation_metadata, Mapping):
            raise GBrainProtocolError(
                "activated OpenClaw profile generation metadata was not structured"
            )
        generation_frontmatter = generation_metadata.get("frontmatter")
        if not isinstance(generation_frontmatter, Mapping):
            raise GBrainProtocolError(
                "activated OpenClaw profile generation frontmatter was not structured"
            )
        composed_page = deepcopy(dict(generation_metadata))
        composed_frontmatter = deepcopy(dict(generation_frontmatter))
        # Generation metadata owns presentation, except for the two explicitly
        # mutable logical-anchor authorities: avatar here and Goal edges below.
        composed_frontmatter.pop("avatar", None)
        if "avatar" in frontmatter:
            composed_frontmatter["avatar"] = deepcopy(frontmatter["avatar"])
        composed_page.update(
            {
                "slug": canonical_slug,
                "type": "agent",
                "frontmatter": composed_frontmatter,
            }
        )
        profile = AgentProfile.from_page(
            composed_page,
            work_root=str(activation["canonical_task_collection"]),
            edges=logical_edges,
        )
        if profile.runtime != "openclaw":
            raise GBrainProtocolError(
                "activated OpenClaw profile has the wrong runtime"
            )
        return profile

    def _active_openclaw_activation(
        self, agent_slug: str
    ) -> Mapping[str, Any]:
        if AGENT_RUNTIME_BY_SLUG.get(agent_slug) != "openclaw":
            raise ValueError(f"{agent_slug} is not an approved OpenClaw Agent")
        activation = next(
            (
                item
                for item in self._activated_openclaw_profiles()
                if item["canonical_agent_slug"] == agent_slug
            ),
            None,
        )
        if activation is None:
            raise ValueError(f"OpenClaw Agent {agent_slug} is not activated")
        return activation

    def _verify_openclaw_task_anchor(
        self, activation: Mapping[str, Any]
    ) -> None:
        agent_slug = str(activation["canonical_agent_slug"])
        collection_slug = str(activation["canonical_task_collection"])
        page = self.runner.run("get_page", {"slug": collection_slug})
        links = self.runner.run("get_links", {"slug": collection_slug})
        frontmatter = page.get("frontmatter") if isinstance(page, Mapping) else None
        if not isinstance(links, list):
            raise GBrainProtocolError(
                f"{collection_slug} logical task collection links were not a list"
            )
        exact_for_agent = [
            edge
            for edge in links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == collection_slug
            and edge.get("to_slug") == agent_slug
            and edge.get("link_type") == "for_agent"
            and edge.get("context") == "Logical OpenClaw task scope."
        ]
        if (
            not isinstance(page, Mapping)
            or page.get("slug") != collection_slug
            or page.get("type") != "collection"
            or not isinstance(frontmatter, Mapping)
            or frontmatter.get("collection_kind")
            != "mission_control_agent_tasks"
            or frontmatter.get("agent") != agent_slug
            or frontmatter.get("logical_anchor") is not True
            or len(links) != 1
            or len(exact_for_agent) != 1
        ):
            raise GBrainProtocolError(
                f"{collection_slug} is not the verified logical task collection for {agent_slug}"
            )

    def _verify_openclaw_artifact_anchor(
        self, activation: Mapping[str, Any]
    ) -> None:
        collection_slug = str(activation["canonical_artifact_collection"])
        page = self.runner.run("get_page", {"slug": collection_slug})
        links = self.runner.run("get_links", {"slug": collection_slug})
        frontmatter = page.get("frontmatter") if isinstance(page, Mapping) else None
        if (
            not isinstance(frontmatter, Mapping)
            or frontmatter.get("logical_anchor") is not True
        ):
            raise GBrainProtocolError(
                f"{collection_slug} is not a verified logical Artifact collection"
            )
        try:
            self._verify_artifact_collection(collection_slug, page, links)
        except GBrainProtocolError as exc:
            raise GBrainProtocolError(
                f"{collection_slug} is not a verified logical Artifact collection: {exc}"
            ) from exc

    def _require_task_openclaw_activation(self, task: Task) -> None:
        owner = task.owner_agent
        if owner is None or AGENT_RUNTIME_BY_SLUG.get(owner) != "openclaw":
            return
        activation = self._active_openclaw_activation(owner)
        if task.lifecycle_root != activation["canonical_task_collection"]:
            raise GBrainProtocolError(
                "OpenClaw task is not in its stable logical task collection"
            )
        self._openclaw_profile_from_activation(activation)
        self._verify_openclaw_task_anchor(activation)

    def _require_openclaw_assignment_target(self, agent_slug: str) -> str | None:
        if AGENT_RUNTIME_BY_SLUG.get(agent_slug) != "openclaw":
            return None
        activation = self._active_openclaw_activation(agent_slug)
        self._openclaw_profile_from_activation(activation)
        self._verify_openclaw_task_anchor(activation)
        return str(activation["canonical_task_collection"])

    def list_agent_profiles(self) -> AgentRead:
        def read_agent(
            scope: tuple[str, str],
        ) -> tuple[AgentProfile | None, CollectionIssue | None]:
            agent_slug, work_root = scope
            try:
                page = self.runner.run("get_page", {"slug": agent_slug})
                edges = self.runner.run("get_links", {"slug": agent_slug})
                if not isinstance(page, Mapping) or not isinstance(edges, list):
                    raise GBrainProtocolError(
                        "agent profile readback was not structured"
                    )
                return (
                    AgentProfile.from_page(
                        page,
                        work_root=work_root,
                        edges=edges,
                    ),
                    None,
                )
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(
                    slug=agent_slug,
                    message=str(exc),
                    owner_agent=agent_slug,
                    impact=(
                        "This agent profile is unavailable until its canonical "
                        "GBrain page is repaired."
                    ),
                )

        agents: list[AgentProfile] = []
        issues: list[CollectionIssue] = []
        for agent, issue in self._bounded_map(read_agent, list(self._agent_scopes())):
            if agent is not None:
                agents.append(agent)
            if issue is not None:
                issues.append(issue)
        try:
            activated_openclaw = tuple(
                self._openclaw_profile_from_activation(activation)
                for activation in self._activated_openclaw_profiles()
            )
        except (DomainValidationError, GBrainError, ValueError) as exc:
            activated_openclaw = self._last_verified_openclaw_profiles
            issues.append(
                CollectionIssue(
                    slug="system/openclaw-profile-activation",
                    message=str(exc),
                    category="openclaw_activation",
                    impact=(
                        "Last verified OpenClaw profiles remain visible while activation readback recovers."
                        if activated_openclaw
                        else "Existing Codex Agents remain available; OpenClaw activation could not be read."
                    ),
                )
            )
        else:
            self._last_verified_openclaw_profiles = activated_openclaw
        agents.extend(activated_openclaw)
        return AgentRead(agents=tuple(agents), issues=tuple(issues))

    def set_agent_avatar(self, agent_slug: str, served_url: str) -> AgentProfile:
        """Store only Stargraph's verified attachment reference on an agent page."""
        if not served_url.startswith("/media/"):
            raise ValueError("avatar attachment must be a local Stargraph media reference")
        profile = self.get_agent_profile(agent_slug)
        work_root = profile.work_root
        page = self.runner.run("get_page", {"slug": agent_slug})
        links = self.runner.run("get_links", {"slug": agent_slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("agent avatar snapshot was not structured")
        AgentProfile.from_page(page, work_root=work_root, edges=links)
        frontmatter = deepcopy(dict(page.get("frontmatter") or {}))
        # Memory Stargraph's attachment boundary may rewrite its page snapshot
        # with the generic `concept` type.  Reassert the canonical agent
        # identity in the follow-up page write so an avatar replacement cannot
        # make the profile disappear from the Agent Directory.
        frontmatter["type"] = "agent"
        frontmatter["avatar"] = {"kind": "attachment", "value": served_url}
        content = _render_preserved_page(page, frontmatter)
        self.runner.run("put_page", {"slug": agent_slug, "content": content})
        stored_page = self.runner.run("get_page", {"slug": agent_slug})
        stored_links = self.runner.run("get_links", {"slug": agent_slug})
        if not isinstance(stored_page, Mapping) or not isinstance(stored_links, list):
            raise GBrainProtocolError("agent avatar readback was not structured")
        stored = AgentProfile.from_page(stored_page, work_root=work_root, edges=stored_links)
        if stored.avatar_kind != "attachment" or stored.avatar_value != served_url:
            raise GBrainProtocolError("agent avatar reference did not read back from GBrain")
        if profile.runtime == "openclaw":
            return self.get_agent_profile(agent_slug)
        return stored

    def get_agent_profile(self, agent_slug: str) -> AgentProfile:
        """Read one exact canonical agent slug; never derive it from a name."""
        scope_by_agent = dict(self._agent_scopes())
        work_root = scope_by_agent.get(agent_slug)
        if work_root is not None:
            page = self.runner.run("get_page", {"slug": agent_slug})
            links = self.runner.run("get_links", {"slug": agent_slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise GBrainProtocolError("agent profile readback was not structured")
            return AgentProfile.from_page(page, work_root=work_root, edges=links)
        if AGENT_RUNTIME_BY_SLUG.get(agent_slug) == "openclaw":
            activation = next(
                (
                    item
                    for item in self._activated_openclaw_profiles()
                    if item["canonical_agent_slug"] == agent_slug
                ),
                None,
            )
            if activation is None:
                raise ValueError(
                    f"OpenClaw Agent {agent_slug} is not activated"
                )
            return self._openclaw_profile_from_activation(activation)
        else:
            raise ValueError(
                "Agent profile is not available in the active directory. Refresh and select the listed agent."
            )

    @staticmethod
    def _openclaw_declaration(declaration: Mapping[str, str]) -> dict[str, str]:
        required = {
            "slug",
            "name",
            "runtime",
            "route",
            "task_collection",
            "artifact_collection",
        }
        if set(declaration) != required or not all(
            isinstance(value, str) and value.strip() for value in declaration.values()
        ):
            raise ValueError("OpenClaw declaration must contain the exact public fields")
        normalized = {key: value.strip() for key, value in declaration.items()}
        if APPROVED_OPENCLAW_DECLARATIONS.get(normalized["slug"]) != normalized:
            raise ValueError("OpenClaw declaration does not match the approved scope")
        return normalized

    @staticmethod
    def _openclaw_receipt(
        declaration: Mapping[str, str],
    ) -> AgentProvisioningReceipt:
        agent = declaration["slug"]
        task_collection = declaration["task_collection"]
        artifact_collection = declaration["artifact_collection"]
        return AgentProvisioningReceipt(
            agent_slug=agent,
            collection_slugs=(task_collection, artifact_collection),
            default_goal_slugs=(),
            operations=(
                f"put_page:{agent}",
                f"put_page:{task_collection}",
                f"put_page:{artifact_collection}",
                f"add_link:{task_collection}->{agent}:for_agent",
                f"add_link:{artifact_collection}->{ARTIFACTS_ROOT}:part_of",
                f"add_link:{artifact_collection}->{agent}:for_agent",
            ),
            verified=False,
            mutated=False,
        )

    def provision_agent_profiles(
        self,
        declarations: Sequence[Mapping[str, str]],
        *,
        execute: bool,
    ) -> tuple[AgentProvisioningReceipt, ...]:
        """Validate legacy plans without directly provisioning OpenClaw pages."""
        if execute:
            raise GBrainProtocolError(
                "direct OpenClaw provisioning is disabled; use Memory Stargraph activation"
            )
        items = tuple(
            self._openclaw_declaration(declaration)
            for declaration in declarations
        )
        if not items:
            raise ValueError(
                "OpenClaw provisioning requires at least one declaration"
            )
        if len({item["slug"] for item in items}) != len(items):
            raise ValueError(
                "OpenClaw declarations must not share canonical identities"
            )
        return tuple(self._openclaw_receipt(item) for item in items)

    def provision_agent_profile(
        self,
        declaration: Mapping[str, str],
        *,
        execute: bool,
    ) -> AgentProvisioningReceipt:
        """Return one legacy dry-run plan or reject direct execution."""
        return self.provision_agent_profiles((declaration,), execute=execute)[0]

    def read_handoff_dispatcher_registration(
        self,
        agent_slug: str,
        registration_id: str,
    ) -> AgentRegistration | None:
        """Read one verified dispatcher route from the canonical Agent page."""
        if (
            not isinstance(agent_slug, str)
            or re.fullmatch(r"agents/[a-z0-9][a-z0-9._-]{0,63}", agent_slug)
            is None
            or not isinstance(registration_id, str)
            or not registration_id
        ):
            raise ValueError("dispatcher registration lookup requires exact identities")
        registration_reference = hashlib.sha256(
            registration_id.encode("utf-8")
        ).hexdigest()
        return self._read_handoff_dispatcher_registration(
            agent_slug,
            registration_reference,
            registration_id=registration_id,
        )

    def read_handoff_dispatcher_registration_by_reference(
        self,
        agent_slug: str,
        registration_reference: str,
    ) -> AgentRegistration | None:
        """Read one canonical route using only its configured private digest."""
        if (
            not isinstance(agent_slug, str)
            or re.fullmatch(r"agents/[a-z0-9][a-z0-9._-]{0,63}", agent_slug)
            is None
            or not isinstance(registration_reference, str)
            or re.fullmatch(r"[0-9a-f]{64}", registration_reference) is None
        ):
            raise ValueError("dispatcher registration lookup requires exact identities")
        return self._read_handoff_dispatcher_registration(
            agent_slug,
            registration_reference,
            registration_id=registration_reference,
        )

    def _read_handoff_dispatcher_registration(
        self,
        agent_slug: str,
        registration_reference: str,
        *,
        registration_id: str,
    ) -> AgentRegistration | None:
        page = self.runner.run("get_page", {"slug": agent_slug})
        if (
            not isinstance(page, Mapping)
            or page.get("slug") != agent_slug
            or page.get("type") != "agent"
        ):
            raise GBrainProtocolError(
                "dispatcher registration Agent readback was not canonical"
            )
        if page.get("deleted_at"):
            return None
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise GBrainProtocolError(
                "dispatcher registration Agent frontmatter was unavailable"
            )
        dispatcher = frontmatter.get("handoff_dispatcher")
        if not isinstance(dispatcher, Mapping):
            return None
        expected = dispatcher.get("registration_sha256")
        route = dispatcher.get("route")
        canonical_route = HANDOFF_ROUTE_BY_AGENT.get(agent_slug)
        if (
            dispatcher.get("verified") is not True
            or not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
            or not hmac.compare_digest(registration_reference, expected)
            or not isinstance(route, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,127}", route) is None
            or route != canonical_route
        ):
            return None
        return AgentRegistration(
            registration_id=registration_id,
            agent_slug=agent_slug,
            route=route,
            verified=True,
            _registration_reference=registration_reference,
        )

    def set_agent_default_goal(
        self,
        agent_slug: str,
        goal_slug: str,
        *,
        assigned: bool,
    ) -> AgentProfile:
        """Change one canonical default_agent_for edge and verify both views."""
        profile = self.get_agent_profile(agent_slug)
        goals = {goal.slug for goal in self.list_goals().goals}
        if goal_slug not in goals:
            raise ValueError("goal is not a member of Tony's Goals")
        if assigned:
            # A goal has at most one default agent. Replace the one typed edge,
            # rather than storing a mirrored assignment list anywhere.
            for candidate in self.list_agent_profiles().agents:
                if candidate.slug != agent_slug and goal_slug in candidate.default_goal_slugs:
                    self.runner.run(
                        "remove_link",
                        {
                            "from": candidate.slug,
                            "to": goal_slug,
                            "link_type": "default_agent_for",
                        },
                    )
            if goal_slug not in profile.default_goal_slugs:
                self.runner.run(
                    "add_link",
                    {
                        "from": agent_slug,
                        "to": goal_slug,
                        "link_type": "default_agent_for",
                        "context": "Mission Control default goal ownership.",
                        "link_source": "gtasks",
                    },
                )
        elif goal_slug in profile.default_goal_slugs:
            self.runner.run(
                "remove_link",
                {
                    "from": agent_slug,
                    "to": goal_slug,
                    "link_type": "default_agent_for",
                },
            )

        stored = self.get_agent_profile(agent_slug)
        backlinks = self.runner.run("get_backlinks", {"slug": goal_slug})
        reciprocal = isinstance(backlinks, list) and any(
            isinstance(edge, Mapping)
            and edge.get("from_slug") == agent_slug
            and edge.get("to_slug") == goal_slug
            and edge.get("link_type") == "default_agent_for"
            for edge in backlinks
        )
        if (goal_slug in stored.default_goal_slugs) != assigned or reciprocal != assigned:
            raise GBrainProtocolError(
                "default agent relationship did not read back from both views"
            )
        return stored

    def list_agent_work(self, *, include_todos: bool = True) -> AgentWorkRead:
        profiles = self.list_agent_profiles()
        tasks: list[dict[str, Any]] = []
        issues: list[CollectionIssue] = list(profiles.issues)
        for agent in profiles.agents:
            root_slug = agent.work_root
            try:
                raw_backlinks = self.runner.run(
                    "get_backlinks",
                    {"slug": root_slug},
                )
            except GBrainError as exc:
                issues.append(
                    CollectionIssue(
                        slug=root_slug,
                        message=str(exc),
                        owner_agent=agent.slug,
                        impact=(
                            f"{agent.name}'s work could not be read. Tony's "
                            "personal tasks remain unaffected."
                        ),
                    )
                )
                continue
            if not isinstance(raw_backlinks, list):
                issues.append(
                    CollectionIssue(
                        slug=root_slug,
                        message="agent work backlinks were not a list",
                        owner_agent=agent.slug,
                        impact=(
                            f"{agent.name}'s work could not be read. Tony's "
                            "personal tasks remain unaffected."
                        ),
                    )
                )
                continue
            member_slugs = tuple(
                dict.fromkeys(
                    str(edge["from_slug"])
                    for edge in raw_backlinks
                    if isinstance(edge, Mapping)
                    and edge.get("to_slug") == root_slug
                    and edge.get("link_type") == "member_of"
                    and isinstance(edge.get("from_slug"), str)
                )
            )
            for slug in member_slugs:
                try:
                    page = self.runner.run("get_page", {"slug": slug})
                    edges = self.runner.run("get_links", {"slug": slug})
                    if not isinstance(page, Mapping) or not isinstance(edges, list):
                        raise GBrainProtocolError(
                            "agent task readback was not structured"
                        )
                    frontmatter = page.get("frontmatter")
                    if (
                        isinstance(frontmatter, Mapping)
                        and frontmatter.get("status") == "proposed"
                        and page.get("type") != "task"
                    ):
                        raise DomainValidationError(
                            "proposed agent task must have canonical type task; "
                            f"found {page.get('type') or 'missing'}"
                        )
                    task = Task.from_page(page, edges=edges)
                    lifecycle_edges = _lifecycle_edges(task.slug, edges)
                    if len(lifecycle_edges) != 1:
                        issues.append(
                            replace(
                                _visible_warning(
                                    task.slug,
                                    "Task does not have one verified lifecycle membership.",
                                    category="lifecycle_membership",
                                    impact=(
                                        "It is shown from its core fields, but changes are "
                                        "disabled until its lifecycle membership is repaired."
                                    ),
                                ),
                                owner_agent=agent.slug,
                            )
                        )
                    if (
                        task.lifecycle_root != root_slug
                        or task.owner_agent != agent.slug
                    ):
                        raise DomainValidationError(
                            "agent task owner does not match its typed work collection"
                        )
                    if include_todos:
                        todo_read = self._list_task_todos_for_task(task, limit=100)
                        issues.extend(
                            replace(issue, owner_agent=agent.slug)
                            for issue in todo_read.issues
                        )
                        task = replace(task, todos=todo_read.todos)
                    tasks.append(
                        {
                            **task.to_dict(),
                            "open_todos": [
                                todo.to_dict()
                                for todo in task.todos
                                if todo.status == "not_done"
                            ],
                            "owner": {
                                "slug": agent.slug,
                                "name": agent.name,
                                "avatar": {
                                    "kind": agent.avatar_kind,
                                    "value": agent.avatar_value,
                                },
                            },
                            "agent_work": True,
                            "read_only": False,
                        }
                    )
                except (DomainValidationError, GBrainError) as exc:
                    issues.append(
                        CollectionIssue(
                            slug=slug,
                            message=str(exc),
                            owner_agent=agent.slug,
                            impact=(
                                f"This malformed {agent.name} work item is "
                                "reported in Inbox and is not shown on Board."
                            ),
                        )
                    )
        deduped: dict[str, dict[str, Any]] = {}
        for task in tasks:
            deduped.setdefault(str(task["slug"]), task)
        return AgentWorkRead(
            tasks=tuple(deduped.values()),
            issues=tuple(issues),
            roots=tuple(agent.work_root for agent in profiles.agents),
        )

    def _verify_agent_delegation_root(self) -> None:
        root = self.runner.run("get_page", {"slug": AGENT_DELEGATIONS_ROOT})
        frontmatter = root.get("frontmatter") if isinstance(root, Mapping) else None
        if (
            not isinstance(root, Mapping)
            or root.get("slug") != AGENT_DELEGATIONS_ROOT
            or root.get("type") != "collection"
            or not isinstance(frontmatter, Mapping)
            or frontmatter.get("collection_kind")
            != "mission_control_agent_delegations"
        ):
            raise GBrainProtocolError(
                "Mission Control Agent Delegations root is not canonical"
            )

    def list_agent_delegations(self) -> tuple[AgentDelegationLease, ...]:
        self._verify_agent_delegation_root()
        raw = self.runner.run("get_backlinks", {"slug": AGENT_DELEGATIONS_ROOT})
        if not isinstance(raw, list):
            raise GBrainProtocolError("agent delegation backlinks were not a list")
        slugs = tuple(
            dict.fromkeys(
                str(edge["from_slug"])
                for edge in raw
                if isinstance(edge, Mapping)
                and edge.get("to_slug") == AGENT_DELEGATIONS_ROOT
                and edge.get("link_type") == "member_of"
                and isinstance(edge.get("from_slug"), str)
                and str(edge["from_slug"]).startswith("agent-delegations/")
            )
        )
        leases: list[AgentDelegationLease] = []
        for slug in slugs:
            page = self.runner.run("get_page", {"slug": slug})
            links = self.runner.run("get_links", {"slug": slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise GBrainProtocolError("agent delegation readback was not structured")
            lease, _receipts = _agent_delegation_from_page(page, links)
            leases.append(lease)
        return tuple(sorted(leases, key=lambda lease: (lease.created_at, lease.slug)))

    def _read_agent_delegation(
        self, slug: str
    ) -> tuple[AgentDelegationLease, tuple[Mapping[str, Any], ...]]:
        page = self.runner.run("get_page", {"slug": slug})
        links = self.runner.run("get_links", {"slug": slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("agent delegation readback was not structured")
        return _agent_delegation_from_page(page, links)

    def create_agent_delegation(
        self, lease: AgentDelegationLease
    ) -> MutationReceipt:
        if not isinstance(lease, AgentDelegationLease):
            raise TypeError("lease must be an AgentDelegationLease")
        with self._delegation_mutation_lock:
            self._verify_agent_delegation_root()
            try:
                page = self.runner.run("get_page", {"slug": lease.slug})
            except GBrainCommandError as exc:
                if not is_page_not_found_error(exc):
                    raise
            else:
                try:
                    links = self.runner.run("get_links", {"slug": lease.slug})
                    if not isinstance(page, Mapping) or not isinstance(links, list):
                        raise GBrainProtocolError(
                            "agent delegation readback was not structured"
                        )
                    existing, _receipts = _agent_delegation_from_page(page, links)
                except (GBrainError, ValueError) as exc:
                    raise PartialMutationError(
                        lease.slug,
                        "Agent delegation existing page is not verified; inspect before retrying.",
                    ) from exc
                if existing != lease:
                    raise ValueError(
                        "delegation idempotency input conflicts with canonical lease"
                    )
                return MutationReceipt(lease.slug, True)

            self._active_openclaw_activation(lease.executor_agent)
            receipt = {
                "action": "created",
                "authorized_by": TONY_PROFILE_SLUG,
                "occurred_at": lease.created_at.isoformat(),
                "version": lease.updated_at.isoformat(),
                "source_agent": lease.source_agent,
                "executor_agent": lease.executor_agent,
                "starts_at": lease.starts_at.isoformat(),
                "previous_ends_at": None,
                "ends_at": lease.ends_at.isoformat(),
                "display_timezone": lease.display_timezone,
                "allowed_operations": list(lease.allowed_operations),
                "previous_state": None,
                "state": lease.state.value,
            }
            try:
                self.runner.run(
                    "put_page",
                    {
                        "slug": lease.slug,
                        "content": render_agent_delegation_page(lease, (receipt,)),
                    },
                )
                self.runner.run(
                    "add_link",
                    {
                        "from": lease.slug,
                        "to": AGENT_DELEGATIONS_ROOT,
                        "link_type": "member_of",
                        "context": "Tony-authorized temporary Agent delegation.",
                        "link_source": "gtasks",
                    },
                )
                stored, receipts = self._read_agent_delegation(lease.slug)
                if stored != lease or receipts != (receipt,):
                    raise GBrainProtocolError(
                        "agent delegation creation readback did not match the write"
                    )
            except (GBrainError, ValueError) as exc:
                raise PartialMutationError(
                    lease.slug,
                    "Agent delegation creation was not verified; inspect before retrying.",
                ) from exc
            return MutationReceipt(lease.slug, True)

    def update_agent_delegation(
        self,
        lease: AgentDelegationLease,
        *,
        expected_version: str,
    ) -> MutationReceipt:
        if not isinstance(lease, AgentDelegationLease):
            raise TypeError("lease must be an AgentDelegationLease")
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError("expected_version must be the exact canonical version")
        with self._delegation_mutation_lock:
            self._verify_agent_delegation_root()
            existing, receipts = self._read_agent_delegation(lease.slug)
            canonical_version = existing.updated_at.isoformat()
            if expected_version != canonical_version:
                raise ConcurrentAgentDelegationUpdateError(lease.slug)
            immutable = (
                "slug",
                "source_agent",
                "executor_agent",
                "authorized_by",
                "starts_at",
                "display_timezone",
                "allowed_operations",
                "created_at",
            )
            if any(getattr(existing, field) != getattr(lease, field) for field in immutable):
                raise ValueError("agent delegation immutable fields cannot change")
            if lease.updated_at <= existing.updated_at:
                if lease == existing:
                    return MutationReceipt(lease.slug, True)
                raise ValueError("agent delegation updated_at must advance")
            effective_existing = lease_state_at(existing, lease.updated_at)
            if effective_existing in {
                DelegationState.COMPLETED,
                DelegationState.EXPIRED,
                DelegationState.REVOKED,
            }:
                raise ValueError(
                    f"{effective_existing.value} agent delegation cannot be changed"
                )
            action: str
            receipt: dict[str, Any] = {
                "authorized_by": TONY_PROFILE_SLUG,
                "occurred_at": lease.updated_at.isoformat(),
                "version": lease.updated_at.isoformat(),
                "source_agent": lease.source_agent,
                "executor_agent": lease.executor_agent,
                "starts_at": lease.starts_at.isoformat(),
                "previous_ends_at": existing.ends_at.isoformat(),
                "ends_at": lease.ends_at.isoformat(),
                "display_timezone": lease.display_timezone,
                "allowed_operations": list(lease.allowed_operations),
                "previous_state": effective_existing.value,
                "state": lease.state.value,
            }
            if (
                lease.ends_at > existing.ends_at
                and lease.state == effective_existing
            ):
                action = "extended"
                self._active_openclaw_activation(lease.executor_agent)
            elif (
                lease.ends_at == existing.ends_at
                and lease.state in {DelegationState.COMPLETED, DelegationState.REVOKED}
            ):
                action = lease.state.value
            else:
                raise ValueError(
                    "agent delegation update must be an extension, completion, or revocation"
                )
            receipt = {"action": action, **receipt}
            new_receipts = (*receipts, receipt)
            latest, latest_receipts = self._read_agent_delegation(lease.slug)
            if latest != existing or latest_receipts != receipts:
                raise ConcurrentAgentDelegationUpdateError(lease.slug)
            try:
                self.runner.run(
                    "put_page",
                    {
                        "slug": lease.slug,
                        "content": render_agent_delegation_page(lease, new_receipts),
                    },
                )
                stored, stored_receipts = self._read_agent_delegation(lease.slug)
                if stored != lease or stored_receipts != new_receipts:
                    raise GBrainProtocolError(
                        "agent delegation update readback did not match the write"
                    )
            except (GBrainError, ValueError) as exc:
                raise PartialMutationError(
                    lease.slug,
                    "Agent delegation update was not verified; inspect before retrying.",
                ) from exc
            return MutationReceipt(lease.slug, True)

    def list_proposals(self) -> ProposalRead:
        # The current contract is an ordinary, agent-owned task with status
        # proposed.  Keep legacy task_proposal pages readable during rollout,
        # but do not create or approve through that old, duplicating path.
        proposals: list[TaskProposal] = []
        issues: list[CollectionIssue] = []
        raw_backlinks = self.runner.run(
            "get_backlinks",
            {"slug": PROPOSALS_ROOT},
        )
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError(
                "proposal collection backlinks were not a list"
            )
        proposal_slugs = tuple(
            dict.fromkeys(
                str(edge["from_slug"])
                for edge in raw_backlinks
                if isinstance(edge, Mapping)
                and edge.get("to_slug") == PROPOSALS_ROOT
                and edge.get("link_type") == "member_of"
                and isinstance(edge.get("from_slug"), str)
            )
        )

        def read_proposal(
            slug: str,
        ) -> tuple[TaskProposal | None, CollectionIssue | None]:
            try:
                page = self.runner.run("get_page", {"slug": slug})
                edges = self.runner.run("get_links", {"slug": slug})
                if not isinstance(page, Mapping) or not isinstance(edges, list):
                    raise GBrainProtocolError(
                        "proposal readback was not structured"
                    )
                return TaskProposal.from_page(page, edges=edges), None
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(
                    slug=slug,
                    message=str(exc),
                    impact=(
                        "This proposal remains canonical in GBrain, but cannot "
                        "be reviewed until its required proposal fields and "
                        "typed relationships are repaired."
                    ),
                )

        for proposal, issue in self._bounded_map(
            read_proposal,
            list(proposal_slugs),
        ):
            if (
                proposal is not None
                and proposal.status == "proposed"
                and proposal.decision is None
            ):
                proposals.append(proposal)
            if issue is not None:
                issues.append(issue)
        try:
            # Proposal Inbox needs proposal fields, not every unrelated task's
            # child checklist. Skipping that fan-out keeps this read bounded
            # while the full Agent Work view retains its richer projection.
            agent_work = self.list_agent_work(include_todos=False)
        except (GBrainError, IndexError):
            agent_work = AgentWorkRead(tasks=(), issues=(), roots=())
        issues.extend(agent_work.issues)
        for item in agent_work.tasks:
            decision = item.get("proposal_decision")
            if item.get("status") != "proposed" or decision is not None:
                continue
            submitted = item.get("proposal_submitted_at") or item.get("created_at") or item.get("updated_at")
            updated = item.get("updated_at") or submitted
            try:
                review_status = "proposed"
                decision_events = tuple(
                    ProposalDecisionEvent.from_value(value)
                    for value in item.get("proposal_decision_events", [])
                )
                proposals.append(TaskProposal(
                    slug=str(item["slug"]), title=str(item["title"]), status=review_status,
                    recipient=str(item.get("proposal_recipient") or "agent"), proposing_agent=str(item.get("owner_agent") or ""),
                    rationale=str(item.get("detail") or ""), proposed_next_step=str(item.get("next_action") or ""),
                    due_day=date.fromisoformat(str(item["due_day"])[:10]),
                    submitted_at=datetime.fromisoformat(str(submitted).replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(str(updated).replace("Z", "+00:00")),
                    linked_goal=item.get("goal") if isinstance(item.get("goal"), str) else None,
                    reviewed_at=(
                        datetime.fromisoformat(str(item["proposal_decided_at"]).replace("Z", "+00:00"))
                        if item.get("proposal_decided_at") else None
                    ),
                    decision_note=str(item.get("proposal_decision_note") or ""),
                    source_kind="task", decision=decision,
                    decision_at=(
                        datetime.fromisoformat(str(item["proposal_decided_at"]).replace("Z", "+00:00"))
                        if item.get("proposal_decided_at") else None
                    ),
                    resulting_status=(
                        str(item.get("status")) if decision in {"approve", "reject"} else None
                    ),
                    decision_events=decision_events,
                ))
            except (KeyError, TypeError, ValueError):
                issues.append(CollectionIssue(slug=str(item.get("slug", "agent task")), message="proposed agent task is missing required task timing data", impact="This proposed task remains in GBrain but cannot be reviewed until its core task fields are repaired."))
        proposals.sort(key=lambda proposal: proposal.updated_at, reverse=True)
        return ProposalRead(
            proposals=tuple(proposals),
            issues=tuple(issues),
        )

    def list_goals(self) -> GoalRead:
        try:
            raw_backlinks = self.runner.run("get_backlinks", {"slug": GOALS_ROOT})
        except GBrainError:
            return GoalRead(goals=(), issues=(self._empty_collection_root_issue(GOALS_ROOT),))
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("goals get_backlinks did not return a list")
        goal_slugs = [
            str(backlink["from_slug"])
            for backlink in raw_backlinks
            if isinstance(backlink, Mapping)
            and backlink.get("to_slug") == GOALS_ROOT
            and isinstance(backlink.get("from_slug"), str)
            and str(backlink["from_slug"]).startswith("goals/")
        ]
        goal_slugs = list(dict.fromkeys(goal_slugs))
        if not goal_slugs:
            return GoalRead(goals=(), issues=(self._empty_collection_root_issue(GOALS_ROOT, raw_backlinks=raw_backlinks),))

        def read_goal(slug: str) -> tuple[Goal | None, CollectionIssue | None]:
            try:
                page = self.runner.run("get_page", {"slug": slug})
                if not isinstance(page, Mapping):
                    raise GBrainProtocolError("goal get_page did not return an object")
                return Goal.from_page(page), None
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(slug=slug, message=str(exc))

        goals: list[Goal] = []
        issues: list[CollectionIssue] = []
        for goal, issue in self._bounded_map(
            read_goal,
            list(dict.fromkeys(goal_slugs)),
        ):
            if goal is not None:
                goals.append(goal)
            if issue is not None:
                issues.append(issue)
        return GoalRead(goals=tuple(goals), issues=tuple(issues))

    def get_goal(self, goal_slug: str) -> Goal:
        page = self.runner.run("get_page", {"slug": goal_slug})
        links = self.runner.run("get_links", {"slug": goal_slug})
        if not isinstance(page, Mapping):
            raise GBrainProtocolError("goal get_page did not return an object")
        if not isinstance(links, list):
            raise GBrainProtocolError("goal get_links did not return a list")
        return Goal.from_page(page, edges=links)

    def create_goal(self, goal: Goal) -> GoalMutationReceipt:
        self.runner.run(
            "put_page",
            {"slug": goal.slug, "content": render_goal_page(goal)},
        )
        try:
            page = self.runner.run("get_page", {"slug": goal.slug})
            if not isinstance(page, Mapping):
                raise GBrainProtocolError("goal page readback was not an object")
            stored_goal = Goal.from_page(page)
            if stored_goal.to_dict() != goal.to_dict():
                raise GBrainProtocolError("goal page readback did not match the write")
            self.runner.run(
                "add_link",
                {
                    "from": goal.slug,
                    "to": GOALS_ROOT,
                    "link_type": "member_of",
                    "context": "This goal belongs to Tony's Goals.",
                    "link_source": "gtasks",
                },
            )
            links = self.runner.run("get_links", {"slug": goal.slug})
            if not isinstance(links, list) or not any(
                isinstance(link, Mapping)
                and link.get("from_slug") == goal.slug
                and link.get("to_slug") == GOALS_ROOT
                and link.get("link_type") == "member_of"
                for link in links
            ):
                raise GBrainProtocolError(
                    "goal collection relationship readback was not verified"
                )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                goal.slug,
                (
                    "Goal creation was not fully verified. "
                    "Do not retry until this slug is inspected: "
                    f"{exc}"
                ),
            ) from exc
        return GoalMutationReceipt(
            goal_slug=goal.slug,
            goal=stored_goal,
            verified=True,
        )

    def set_goal_paused(self, goal_slug: str) -> GoalMutationReceipt:
        page = self.runner.run("get_page", {"slug": goal_slug})
        links = self.runner.run("get_links", {"slug": goal_slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("goal pause snapshot was not structured")
        goal = Goal.from_page(page, edges=links)
        if goal.status == "paused":
            return GoalMutationReceipt(
                goal_slug=goal_slug,
                goal=goal,
                verified=True,
            )
        frontmatter = page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise GBrainProtocolError("goal page has no frontmatter")
        original_frontmatter = deepcopy(dict(frontmatter))
        original_frontmatter["type"] = "goal"
        original_content = _render_preserved_page(page, original_frontmatter)
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_frontmatter["status"] = "paused"
        write_succeeded = False
        try:
            self.runner.run(
                "put_page",
                {
                    "slug": goal_slug,
                    "content": _render_preserved_page(page, desired_frontmatter),
                },
            )
            write_succeeded = True
            stored_page = self.runner.run("get_page", {"slug": goal_slug})
            stored_links = self.runner.run("get_links", {"slug": goal_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError("goal pause readback was not structured")
            stored_goal = Goal.from_page(stored_page, edges=stored_links)
            # GBrain intentionally stores Markdown-backed Goals as raw
            # `concept` rows. Goal.from_page validates the compiled/frontmatter
            # contract, which is the only canonical type assertion here.
            if stored_goal.status != "paused":
                raise GBrainProtocolError("goal pause readback did not match")
            for expected in links:
                if not isinstance(expected, Mapping):
                    continue
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "a goal relationship was missing after pause"
                    )
        except (DomainValidationError, GBrainError) as exc:
            if not write_succeeded:
                raise
            rollback_verified = False
            try:
                self.runner.run(
                    "put_page",
                    {"slug": goal_slug, "content": original_content},
                )
                rollback_page = self.runner.run("get_page", {"slug": goal_slug})
                rollback_goal = Goal.from_page(rollback_page)
                rollback_verified = (
                    isinstance(rollback_page, Mapping)
                    and rollback_goal.status == goal.status
                )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the goal before retrying."
            )
            raise PartialMutationError(
                goal_slug,
                f"Goal pause was not verified. {outcome}",
            ) from exc
        return GoalMutationReceipt(
            goal_slug=goal_slug,
            goal=stored_goal,
            verified=True,
        )

    def update_goal(self, goal_slug: str, *, title: str, outcome: str,
                    success_criteria: str, strategy: str,
                    review_cadence: str, constraints: str,
                    target_day: date) -> GoalMutationReceipt:
        """Update goal fields while preserving canonical type and relationships."""
        page = self.runner.run("get_page", {"slug": goal_slug})
        links = self.runner.run("get_links", {"slug": goal_slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("goal edit snapshot was not structured")
        goal = Goal.from_page(page, edges=links)
        values = {
            "title": title.strip(), "outcome": outcome.strip(),
            "success_criteria": success_criteria.strip(), "strategy": strategy.strip(),
            "review_cadence": review_cadence.strip(), "constraints": constraints.strip(),
        }
        if any(not value for value in values.values()):
            raise DomainValidationError("all goal fields are required")
        if len(values["title"]) > 160:
            raise DomainValidationError("goal title must be 160 characters or fewer")
        desired = replace(goal, **values, target_day=target_day)
        original_frontmatter = deepcopy(dict(page.get("frontmatter") or {}))
        original_frontmatter["type"] = "goal"
        original_content = _render_preserved_page(page, original_frontmatter)
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_frontmatter.update({**values, "target_day": target_day.isoformat()})
        write_succeeded = False
        try:
            self.runner.run("put_page", {"slug": goal_slug, "content": _render_preserved_page(page, desired_frontmatter)})
            write_succeeded = True
            stored_page = self.runner.run("get_page", {"slug": goal_slug})
            stored_links = self.runner.run("get_links", {"slug": goal_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(stored_links, list):
                raise GBrainProtocolError("goal edit readback was not structured")
            stored_goal = Goal.from_page(stored_page, edges=stored_links)
            if stored_goal.to_dict() != desired.to_dict():
                raise GBrainProtocolError("goal edit readback did not match the write")
            for expected in links:
                if isinstance(expected, Mapping) and not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError("a goal relationship was missing after edit")
        except (DomainValidationError, GBrainError) as exc:
            if write_succeeded:
                try:
                    self.runner.run("put_page", {"slug": goal_slug, "content": original_content})
                except GBrainError:
                    pass
                raise PartialMutationError(goal_slug, "Goal edit was not verified. Inspect the goal before retrying.") from exc
            raise
        return GoalMutationReceipt(goal_slug=goal_slug, goal=stored_goal, verified=True)

    def delete_goal(self, goal_slug: str) -> GoalDeletionReceipt:
        page = self.runner.run("get_page", {"slug": goal_slug})
        outgoing = self.runner.run("get_links", {"slug": goal_slug})
        incoming = self.runner.run("get_backlinks", {"slug": goal_slug})
        if (
            not isinstance(page, Mapping)
            or not isinstance(outgoing, list)
            or not isinstance(incoming, list)
        ):
            raise GBrainProtocolError("goal delete snapshot was not structured")
        Goal.from_page(page, edges=outgoing)
        forward_tasks = {
            str(edge["from_slug"])
            for edge in incoming
            if isinstance(edge, Mapping)
            and edge.get("to_slug") == goal_slug
            and edge.get("link_type") == "advances_goal"
            and isinstance(edge.get("from_slug"), str)
            and str(edge["from_slug"]).startswith("tasks/")
        }
        reverse_tasks = {
            str(edge["to_slug"])
            for edge in outgoing
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == goal_slug
            and edge.get("link_type") == "advanced_by"
            and isinstance(edge.get("to_slug"), str)
            and str(edge["to_slug"]).startswith("tasks/")
        }
        task_slugs = tuple(sorted(forward_tasks | reverse_tasks))
        unlinked_tasks: list[str] = []
        delete_succeeded = False
        try:
            for task_slug in task_slugs:
                self.set_task_goal(task_slug, None)
                unlinked_tasks.append(task_slug)
            remaining_outgoing = self.runner.run("get_links", {"slug": goal_slug})
            remaining_incoming = self.runner.run("get_backlinks", {"slug": goal_slug})
            if not isinstance(remaining_outgoing, list) or not isinstance(
                remaining_incoming, list
            ):
                raise GBrainProtocolError(
                    "goal relationship removal readback was not structured"
                )
            if any(
                isinstance(edge, Mapping)
                and edge.get("link_type") == "advanced_by"
                and edge.get("to_slug") in task_slugs
                for edge in remaining_outgoing
            ) or any(
                isinstance(edge, Mapping)
                and edge.get("link_type") == "advances_goal"
                and edge.get("from_slug") in task_slugs
                for edge in remaining_incoming
            ):
                raise GBrainProtocolError(
                    "goal task relationships remained after removal"
                )
            self.runner.run("delete_page", {"slug": goal_slug})
            delete_succeeded = True
            deleted_page = self.runner.run(
                "get_page",
                {"slug": goal_slug, "include_deleted": True},
            )
            if (
                not isinstance(deleted_page, Mapping)
                or deleted_page.get("slug") != goal_slug
                or not deleted_page.get("deleted_at")
            ):
                raise GBrainProtocolError("goal soft-delete readback was not verified")
        except (DomainValidationError, ValueError, GBrainError) as exc:
            rollback_verified = False
            try:
                if delete_succeeded:
                    self.runner.run("restore_page", {"slug": goal_slug})
                for task_slug in unlinked_tasks:
                    self.set_task_goal(task_slug, goal_slug)
                restored_page = self.runner.run("get_page", {"slug": goal_slug})
                Goal.from_page(restored_page)
                restored_outgoing = self.runner.run(
                    "get_links", {"slug": goal_slug}
                )
                restored_incoming = self.runner.run(
                    "get_backlinks", {"slug": goal_slug}
                )
                rollback_verified = (
                    isinstance(restored_outgoing, list)
                    and isinstance(restored_incoming, list)
                    and all(
                        any(
                            isinstance(edge, Mapping)
                            and edge.get("from_slug") == task_slug
                            and edge.get("to_slug") == goal_slug
                            and edge.get("link_type") == "advances_goal"
                            for edge in restored_incoming
                        )
                        and any(
                            isinstance(edge, Mapping)
                            and edge.get("from_slug") == goal_slug
                            and edge.get("to_slug") == task_slug
                            and edge.get("link_type") == "advanced_by"
                            for edge in restored_outgoing
                        )
                        for task_slug in unlinked_tasks
                    )
                )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the goal before retrying."
            )
            raise PartialMutationError(
                goal_slug,
                f"Goal deletion was not verified. {outcome}",
            ) from exc
        return GoalDeletionReceipt(
            goal_slug=goal_slug,
            removed_task_links=task_slugs,
            recoverable_until_hours=72,
            verified=True,
        )

    def _system_ticket_snapshot_path(self) -> Path:
        configured = os.environ.get("GTASKS_READ_CACHE_FILE")
        if configured:
            return Path(configured).expanduser()
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "GTasks"
            / "read-snapshots.json"
        )

    def _parse_snapshot_datetime(self, value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _load_verified_system_ticket_snapshot(
        self,
        member_slugs: Sequence[str],
    ) -> tuple[dict[str, SystemTicket], dict[str, str]] | None:
        try:
            raw = json.loads(
                self._system_ticket_snapshot_path().read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            return None
        surfaces = raw.get("surfaces")
        if not isinstance(surfaces, Mapping):
            return None
        record = surfaces.get("system_tickets_all")
        if not isinstance(record, Mapping):
            return None
        payload = record.get("payload")
        if (
            not isinstance(payload, Mapping)
            or payload.get("root_slug") != SYSTEM_TICKETS_ROOT
            or payload.get("issues") not in ([], ())
        ):
            return None
        raw_tickets = payload.get("tickets")
        if not isinstance(raw_tickets, list):
            return None
        tickets: dict[str, SystemTicket] = {}
        display: dict[str, str] = {}
        for item in raw_tickets:
            if not isinstance(item, Mapping):
                return None
            slug = item.get("slug")
            title = item.get("title")
            status = item.get("status")
            verbatim_request = item.get("verbatim_request")
            target_subsystem = item.get("target_subsystem")
            priority = item.get("priority")
            acceptance_criteria = item.get("acceptance_criteria", "")
            linked_evidence = item.get("linked_evidence", [])
            implementation_receipts = item.get("implementation_receipts", [])
            qa_receipts = item.get("qa_receipts", [])
            if (
                not isinstance(slug, str)
                or not slug.startswith("tasks/")
                or not isinstance(title, str)
                or not title.strip()
                or status not in SYSTEM_TICKET_STATUSES
                or not isinstance(verbatim_request, str)
                or not verbatim_request.strip()
                or not isinstance(target_subsystem, str)
                or not isinstance(priority, str)
                or not isinstance(acceptance_criteria, str)
                or not isinstance(linked_evidence, list)
                or not isinstance(implementation_receipts, list)
                or not isinstance(qa_receipts, list)
                or any(not isinstance(value, str) for value in linked_evidence)
                or any(not isinstance(value, str) for value in implementation_receipts)
                or any(not isinstance(value, str) for value in qa_receipts)
            ):
                return None
            tickets[slug] = SystemTicket(
                slug=slug,
                title=title.strip(),
                status=str(status),
                verbatim_request=verbatim_request.strip(),
                target_subsystem=target_subsystem,
                priority=priority,
                acceptance_criteria=acceptance_criteria,
                linked_evidence=tuple(linked_evidence),
                implementation_receipts=tuple(implementation_receipts),
                qa_receipts=tuple(qa_receipts),
                created_at=self._parse_snapshot_datetime(item.get("created_at")),
                updated_at=self._parse_snapshot_datetime(item.get("updated_at")),
            )
            markdown = item.get("display_markdown")
            if isinstance(markdown, str):
                display[slug] = markdown
        if any(slug not in tickets for slug in member_slugs):
            return None
        return tickets, display

    def _invalidate_system_ticket_snapshot_cache(
        self,
        ticket: SystemTicket | None = None,
        *,
        display_markdown: str | None = None,
    ) -> None:
        path = self._system_ticket_snapshot_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        surfaces = raw.get("surfaces") if isinstance(raw, Mapping) else None
        if not isinstance(surfaces, Mapping):
            return
        changed = False
        updated_surfaces = dict(surfaces)
        if "system_tickets" in updated_surfaces:
            updated_surfaces.pop("system_tickets", None)
            changed = True
        if ticket is not None:
            record = updated_surfaces.get("system_tickets_all")
            payload = record.get("payload") if isinstance(record, Mapping) else None
            raw_tickets = payload.get("tickets") if isinstance(payload, Mapping) else None
            if isinstance(record, Mapping) and isinstance(payload, Mapping) and isinstance(raw_tickets, list):
                replacement = ticket.to_dict()
                if display_markdown is not None:
                    replacement["display_markdown"] = display_markdown
                next_tickets = [
                    replacement if isinstance(item, Mapping) and item.get("slug") == ticket.slug else item
                    for item in raw_tickets
                ]
                if next_tickets != raw_tickets:
                    next_payload = dict(payload)
                    next_payload["tickets"] = next_tickets
                    next_record = dict(record)
                    next_record["payload"] = next_payload
                    next_record["last_valid_at"] = time()
                    updated_surfaces["system_tickets_all"] = next_record
                    changed = True
        if not changed:
            return
        next_payload = dict(raw)
        next_payload["surfaces"] = updated_surfaces
        temporary_path: str | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = temporary.name
                os.chmod(temporary_path, 0o600)
                json.dump(next_payload, temporary, ensure_ascii=False, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            os.chmod(path, 0o600)
        except OSError:
            return
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def list_system_tickets(self, *, include_completed: bool = True) -> SystemTicketRead:
        raw_backlinks = self.runner.run("get_backlinks", {"slug": SYSTEM_TICKETS_ROOT})
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("system tickets get_backlinks did not return a list")
        slugs = list(dict.fromkeys(str(link["from_slug"]) for link in raw_backlinks if isinstance(link, Mapping) and link.get("to_slug") == SYSTEM_TICKETS_ROOT and link.get("link_type") == "member_of" and isinstance(link.get("from_slug"), str) and str(link["from_slug"]).startswith("tasks/")))
        snapshot = self._load_verified_system_ticket_snapshot(slugs)
        cached_tickets: dict[str, SystemTicket] = {}
        cached_display: dict[str, str] = {}
        if snapshot is not None:
            cached_tickets, cached_display = snapshot
        read_slugs = slugs
        if cached_tickets:
            read_slugs = [
                slug
                for slug in slugs
                if cached_tickets[slug].status != "completed"
            ]
        def read(slug: str) -> tuple[SystemTicket | None, CollectionIssue | None, str | None]:
            try:
                page = self.runner.run("get_page", {"slug": slug})
                frontmatter = (
                    page.get("frontmatter")
                    if isinstance(page, Mapping)
                    else None
                )
                if (
                    not include_completed
                    and isinstance(frontmatter, Mapping)
                    and frontmatter.get("status") == "completed"
                ):
                    return None, None, None
                links = self.runner.run("get_links", {"slug": slug})
                if not isinstance(page, Mapping) or not isinstance(links, list):
                    raise GBrainProtocolError("system ticket page or links were not structured")
                ticket = SystemTicket.from_page(page, links)
                display_markdown = self._validated_system_ticket_display_markdown(
                    ticket, page
                )
                if not include_completed and ticket.status == "completed":
                    return None, None, None
                return ticket, None, display_markdown
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(slug=slug, message=str(exc), category="system_ticket_data", impact="This System Ticket cannot be dispatched until its canonical task data is repaired."), None
        tickets, issues = [], []
        projections: list[tuple[str, str]] = []
        for ticket, issue, display_markdown in self._bounded_map(read, read_slugs):
            if ticket:
                tickets.append(ticket)
                if display_markdown is not None:
                    projections.append((ticket.slug, display_markdown))
            if issue: issues.append(issue)
        if include_completed and cached_tickets:
            hydrated = {ticket.slug for ticket in tickets}
            for slug in slugs:
                ticket = cached_tickets[slug]
                if ticket.status == "completed" and slug not in hydrated:
                    tickets.append(ticket)
                    display_markdown = cached_display.get(slug)
                    if display_markdown is not None:
                        projections.append((slug, display_markdown))
        tickets.sort(key=lambda ticket: ((ticket.updated_at or datetime.min), ticket.title.casefold()), reverse=True)
        return SystemTicketRead(tuple(tickets), tuple(issues), tuple(projections))

    def create_system_ticket(self, ticket: SystemTicket) -> MutationReceipt:
        root = self.runner.run("get_page", {"slug": SYSTEM_TICKETS_ROOT})
        if not isinstance(root, Mapping) or root.get("type") != "collection":
            raise GBrainProtocolError("Mission Control System Tickets root is not a canonical collection")
        references = self._verified_system_ticket_references(
            (
                ticket.verbatim_request,
                ticket.acceptance_criteria,
                *ticket.linked_evidence,
                *ticket.implementation_receipts,
                *ticket.qa_receipts,
            )
        )
        expected_body = render_system_ticket_body(
            ticket.title,
            ticket.verbatim_request,
            acceptance_criteria=ticket.acceptance_criteria,
            linked_evidence=ticket.linked_evidence,
            implementation_receipts=ticket.implementation_receipts,
            qa_receipts=ticket.qa_receipts,
            references=references,
        )
        self.runner.run(
            "put_page",
            {
                "slug": ticket.slug,
                "content": render_system_ticket_page(ticket, body=expected_body),
            },
        )
        try:
            self.runner.run("add_link", {"from": ticket.slug, "to": SYSTEM_TICKETS_ROOT, "link_type":"member_of", "context":"This task is a Mission Control System Ticket.", "link_source":"gtasks"})
            page = self.runner.run("get_page", {"slug": ticket.slug})
            links = self.runner.run("get_links", {"slug": ticket.slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise GBrainProtocolError(
                    "System Ticket creation readback was not structured"
                )
            if not any(
                isinstance(edge, Mapping)
                and edge.get("from_slug") == ticket.slug
                and edge.get("to_slug") == SYSTEM_TICKETS_ROOT
                and edge.get("link_type") == "member_of"
                for edge in links
            ):
                raise GBrainProtocolError(
                    "exact live System Tickets membership was not verified"
                )
            if SystemTicket.from_page(page, links).to_dict() != ticket.to_dict():
                raise GBrainProtocolError(
                    "System Ticket creation readback did not match the write"
                )
            self._verify_compiled_markdown_body(
                page, expected_body, label="System Ticket"
            )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                ticket.slug, f"System Ticket creation was not verified: {exc}"
            ) from exc
        self._invalidate_system_ticket_snapshot_cache()
        return MutationReceipt(ticket.slug, True)

    def update_system_ticket(self, ticket: SystemTicket) -> MutationReceipt:
        page = self.runner.run("get_page", {"slug": ticket.slug})
        links = self.runner.run("get_links", {"slug": ticket.slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("system ticket edit snapshot was not structured")
        existing = SystemTicket.from_page(page, links)
        if existing.slug != ticket.slug:
            raise GBrainProtocolError("system ticket edit slug did not match snapshot")
        raw_frontmatter = page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("system ticket page has no frontmatter")
        marked_unified = self._has_unified_markdown_contract(page)
        expected_body: str | None = None
        if marked_unified:
            references = self._verified_system_ticket_references(
                (
                    ticket.verbatim_request,
                    ticket.acceptance_criteria,
                    *ticket.linked_evidence,
                    *ticket.implementation_receipts,
                    *ticket.qa_receipts,
                )
            )
            expected_body = render_system_ticket_body(
                ticket.title,
                ticket.verbatim_request,
                acceptance_criteria=ticket.acceptance_criteria,
                linked_evidence=ticket.linked_evidence,
                implementation_receipts=ticket.implementation_receipts,
                qa_receipts=ticket.qa_receipts,
                references=references,
            )
        frontmatter = deepcopy(dict(raw_frontmatter))
        frontmatter.update({
            "type": "task",
            "title": ticket.title,
            "status": ticket.status,
            "priority": ticket.priority,
            "verbatim_request": ticket.verbatim_request,
            "target_subsystem": ticket.target_subsystem,
            "acceptance_criteria": ticket.acceptance_criteria,
            "linked_evidence": list(ticket.linked_evidence),
            "implementation_receipts": list(ticket.implementation_receipts),
            "qa_receipts": list(ticket.qa_receipts),
            "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        })
        self.runner.run("put_page", {
            "slug": ticket.slug,
            "content": _render_preserved_page(
                page, frontmatter, body=expected_body
            ),
        })
        try:
            read_page = self.runner.run("get_page", {"slug": ticket.slug})
            read_links = self.runner.run("get_links", {"slug": ticket.slug})
            if not isinstance(read_page, Mapping) or not isinstance(read_links, list):
                raise GBrainProtocolError("system ticket edit readback was not structured")
            stored = SystemTicket.from_page(read_page, read_links)
            if stored.to_dict() != ticket.to_dict():
                raise GBrainProtocolError("system ticket edit readback did not match the write")
            if expected_body is not None:
                self._verify_compiled_markdown_body(
                    read_page, expected_body, label="System Ticket edit"
                )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                ticket.slug,
                f"System Ticket edit was not verified. Inspect this slug before retrying: {exc}",
            ) from exc
        self._invalidate_system_ticket_snapshot_cache(
            ticket,
            display_markdown=expected_body,
        )
        return MutationReceipt(ticket.slug, True)

    def planned_system_tickets(self) -> tuple[SystemTicket, ...]:
        """Return every planned nightly-build candidate without changing it.

        Scheduling, batching, and dispatch intentionally live outside Mission
        Control. This selector protects that boundary: only normal planned
        tasks in the dedicated System Tickets collection can be handed to a
        nightly runner. The runner must report an outcome for every returned
        ticket; ordering is deterministic but does not silently exclude work.
        """
        tickets = self.list_system_tickets().tickets
        planned = [ticket for ticket in tickets if ticket.status == "planned"]
        return tuple(sorted(
            planned,
            key=lambda ticket: (ticket.created_at or datetime.max, ticket.slug),
        ))

    def list_projects(self) -> ProjectRead:
        try:
            raw_backlinks = self.runner.run("get_backlinks", {"slug": PROJECTS_ROOT})
        except GBrainError:
            return ProjectRead(projects=(), issues=(self._empty_collection_root_issue(PROJECTS_ROOT),))
        if not isinstance(raw_backlinks, list):
            raise GBrainProtocolError("projects get_backlinks did not return a list")
        project_slugs = list(
            dict.fromkeys(
                str(backlink["from_slug"])
                for backlink in raw_backlinks
                if isinstance(backlink, Mapping)
                and backlink.get("to_slug") == PROJECTS_ROOT
                and backlink.get("link_type") == "member_of"
                and isinstance(backlink.get("from_slug"), str)
                and str(backlink["from_slug"]).startswith("projects/")
            )
        )
        if not project_slugs:
            return ProjectRead(projects=(), issues=(self._empty_collection_root_issue(PROJECTS_ROOT, raw_backlinks=raw_backlinks),))
        def read_project(
            slug: str,
        ) -> tuple[Project | None, CollectionIssue | None]:
            try:
                page = self.runner.run("get_page", {"slug": slug})
                links = self.runner.run("get_links", {"slug": slug})
                if not isinstance(page, Mapping) or not isinstance(links, list):
                    raise GBrainProtocolError(
                        "project page or relationship readback was not structured"
                    )
                return Project.from_page(page, edges=links), None
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(
                    slug=slug,
                    message=str(exc),
                    category="project_data",
                    impact=(
                        "This scoped project is not counted or offered for task "
                        "assignment until its core project data is repaired."
                    ),
                )

        projects: list[Project] = []
        issues: list[CollectionIssue] = []
        for project, issue in self._bounded_map(read_project, project_slugs):
            if project is not None:
                projects.append(project)
            if issue is not None:
                issues.append(issue)
        projects.sort(key=lambda project: project.title.casefold())
        return ProjectRead(projects=tuple(projects), issues=tuple(issues))

    def _ensure_projects_root(self) -> None:
        try:
            page = self.runner.run("get_page", {"slug": PROJECTS_ROOT})
        except GBrainCommandError as exc:
            if "page_not_found" not in str(exc):
                raise
            self.runner.run(
                "put_page",
                {
                    "slug": PROJECTS_ROOT,
                    "content": render_projects_collection_page(),
                },
            )
            page = self.runner.run("get_page", {"slug": PROJECTS_ROOT})
        if not isinstance(page, Mapping):
            raise GBrainProtocolError(
                "Tony's Projects collection readback was not an object"
            )
        if (
            page.get("slug") != PROJECTS_ROOT
            or page.get("type") != "collection"
            or page.get("title") not in {"Tony's Projects", "Tony’s Projects"}
        ):
            raise GBrainProtocolError(
                f"{PROJECTS_ROOT} is not the canonical Tony's Projects collection"
            )

    def create_project(self, project: Project) -> ProjectMutationReceipt:
        try:
            self._ensure_projects_root()
        except GBrainError as exc:
            raise PartialMutationError(
                PROJECTS_ROOT,
                (
                    "Project creation did not start because the GTasks project "
                    f"scope collection could not be verified: {exc}"
                ),
            ) from exc
        content = render_project_page(project)
        self.runner.run(
            "put_page",
            {"slug": project.slug, "content": content},
        )
        try:
            page = self.runner.run("get_page", {"slug": project.slug})
            if not isinstance(page, Mapping):
                raise GBrainProtocolError("project page readback was not an object")
            stored_project = Project.from_page(page)
            if (
                stored_project.slug != project.slug
                or stored_project.title != project.title
                or stored_project.status != project.status
            ):
                raise GBrainProtocolError(
                    "project page readback did not match the write"
                )
            self.runner.run(
                "add_link",
                {
                    "from": project.slug,
                    "to": PROJECTS_ROOT,
                    "link_type": "member_of",
                    "context": "This project is explicitly owned by GTasks.",
                    "link_source": "gtasks",
                },
            )
            for goal_slug in project.supporting_goal_slugs:
                self.runner.run(
                    "add_link",
                    {
                        "from": project.slug,
                        "to": goal_slug,
                        "link_type": "supports_goal",
                        "context": "This project supports the canonical goal.",
                        "link_source": "gtasks",
                    },
                )
            links = self.runner.run("get_links", {"slug": project.slug})
            if not isinstance(links, list) or not any(
                isinstance(link, Mapping)
                and link.get("from_slug") == project.slug
                and link.get("to_slug") == PROJECTS_ROOT
                and link.get("link_type") == "member_of"
                for link in links
            ):
                raise GBrainProtocolError(
                    "project collection relationship readback was not verified"
                )
            stored_project = Project.from_page(page, edges=links)
            if stored_project.supporting_goal_slugs != project.supporting_goal_slugs:
                raise GBrainProtocolError("project goal relationships were not verified")
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                project.slug,
                (
                    "Project creation was not fully verified. "
                    "Do not retry until this slug is inspected: "
                    f"{exc}"
                ),
            ) from exc
        return ProjectMutationReceipt(project_slug=project.slug, verified=True)

    def update_project(self, project: Project) -> ProjectMutationReceipt:
        page = self.runner.run("get_page", {"slug": project.slug})
        links = self.runner.run("get_links", {"slug": project.slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("project edit snapshot was not structured")
        existing = Project.from_page(page, edges=links)
        raw_frontmatter = page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("project page has no frontmatter")
        frontmatter = deepcopy(dict(raw_frontmatter))
        # Preserve any user-authored fields/body rather than replacing the page
        # just to update the project properties managed by Mission Control.
        frontmatter.update({
            "type": "project", "title": project.title, "summary": project.summary,
            "status": project.status,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        })
        self.runner.run("put_page", {"slug": project.slug, "content": _render_preserved_page(page, frontmatter)})
        existing_goals = set(existing.supporting_goal_slugs)
        requested_goals = set(project.supporting_goal_slugs)
        for goal_slug in existing_goals - requested_goals:
            self.runner.run("remove_link", {"from": project.slug, "to": goal_slug, "link_type": "supports_goal"})
        for goal_slug in requested_goals - existing_goals:
            self.runner.run("add_link", {"from": project.slug, "to": goal_slug, "link_type": "supports_goal", "context": "This project supports the canonical goal.", "link_source": "gtasks"})
        read_page = self.runner.run("get_page", {"slug": project.slug})
        read_links = self.runner.run("get_links", {"slug": project.slug})
        if not isinstance(read_page, Mapping) or not isinstance(read_links, list):
            raise GBrainProtocolError("project edit readback was not structured")
        stored = Project.from_page(read_page, edges=read_links)
        if stored.to_dict() != project.to_dict():
            raise GBrainProtocolError("project edit readback did not match the write")
        return ProjectMutationReceipt(project_slug=project.slug, verified=True)

    def read_goal_relationships(self, goal_slug: str) -> GoalRelationshipRead:
        goal_slug = self.resolve_canonical_slug(goal_slug)
        page = self.runner.run("get_page", {"slug": goal_slug})
        if not isinstance(page, Mapping):
            raise GBrainProtocolError("goal get_page did not return an object")
        edges = self.runner.run("get_links", {"slug": goal_slug})
        if not isinstance(edges, list):
            raise GBrainProtocolError("goal get_links did not return a list")
        goal = Goal.from_page(page, edges=edges)
        return GoalRelationshipRead(
            goal_slug=goal.slug,
            task_slugs=goal.advanced_by,
        )

    def create_inbox(self, task: Task) -> MutationReceipt:
        """Create a new Tony Task with only verified Ticket references."""
        return self._create_inbox(
            task,
            references=self._verified_system_ticket_references((task.detail,)),
            verify_final_task=True,
        )

    def _create_inbox(
        self,
        task: Task,
        *,
        references: Mapping[str, SystemTicketReference | None],
        verify_final_task: bool,
    ) -> MutationReceipt:
        if task.lifecycle_root != ACTIVE_ROOT:
            raise ValueError("Inbox task must belong to the active GTasks root")
        if task.status != "planned" or not task.inbox:
            raise ValueError("Inbox task must be planned and marked inbox")
        if task.due_day is None:
            raise ValueError("Inbox task must have a due date")

        expected_body = render_task_body(task.title, task.detail, references)

        self.runner.run(
            "put_page",
            {"slug": task.slug, "content": render_task_page(task, body=expected_body)},
        )
        try:
            raw_page = self.runner.run("get_page", {"slug": task.slug})
            if not isinstance(raw_page, Mapping):
                raise GBrainProtocolError("get_page did not return an object")
            stored_task = Task.from_page(raw_page)
            expected = (
                task.slug,
                task.title,
                task.summary,
                task.status,
                task.due_day,
                task.lifecycle_root,
                task.inbox,
            )
            actual = (
                stored_task.slug,
                stored_task.title,
                stored_task.summary,
                stored_task.status,
                stored_task.due_day,
                stored_task.lifecycle_root,
                stored_task.inbox,
            )
            if actual != expected:
                raise GBrainProtocolError("task page readback did not match the write")
            self._verify_compiled_markdown_body(
                raw_page, expected_body, label="Task"
            )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task.slug,
                f"Task page was written but page readback failed: {exc}",
            ) from exc

        try:
            raw_links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(raw_links, list):
                raise GBrainProtocolError("get_links did not return a list")
            existing = _lifecycle_edges(task.slug, raw_links)
            if len(existing) > 1 or (
                existing and existing[0].get("to_slug") != ACTIVE_ROOT
            ):
                raise LifecycleIntegrityError(task.slug, existing)
            if not existing:
                self.runner.run(
                    "add_link",
                    {
                        "from": task.slug,
                        "to": ACTIVE_ROOT,
                        "link_type": "member_of",
                        "context": "GTasks active task membership.",
                        "link_source": "gtasks",
                    },
                )
            final_links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(final_links, list):
                raise GBrainProtocolError("get_links did not return a list")
            lifecycle = _require_single_lifecycle_edge(task.slug, final_links)
            if lifecycle.get("to_slug") != ACTIVE_ROOT:
                raise LifecycleIntegrityError(task.slug, [lifecycle])
            final_page = self.runner.run("get_page", {"slug": task.slug})
            if not isinstance(final_page, Mapping):
                raise GBrainProtocolError("final Task page readback was not structured")
            unexpected_assignments = [
                edge
                for edge in final_links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == task.slug
                and edge.get("link_type") == "assigned_to"
            ]
            if unexpected_assignments:
                raise GBrainProtocolError(
                    "Tony Task ownership retained an unexpected assigned_to edge"
                )
            final_task = Task.from_page(final_page, edges=final_links)
            if verify_final_task and final_task != task:
                raise GBrainProtocolError(
                    "final canonical Task did not match the requested domain object"
                )
        except (DomainValidationError, GBrainError, LifecycleIntegrityError) as exc:
            raise PartialMutationError(
                task.slug,
                f"Task page exists but membership readback failed: {exc}",
            ) from exc

        return MutationReceipt(slug=task.slug, verified=True)

    def create_task(self, task: Task) -> MutationReceipt:
        references = self._verified_system_ticket_references((task.detail,))
        if task.project:
            project_page = self.runner.run("get_page", {"slug": task.project})
            project_links = self.runner.run("get_links", {"slug": task.project})
            if not isinstance(project_page, Mapping) or not isinstance(
                project_links, list
            ):
                raise ValueError("selected project could not be verified")
            try:
                Project.from_page(project_page, edges=project_links)
            except DomainValidationError as exc:
                raise ValueError(
                    "project is not a durable member of Tony's Projects"
                ) from exc
        if task.goal:
            goal_page = self.runner.run("get_page", {"slug": task.goal})
            if not isinstance(goal_page, Mapping):
                raise ValueError("selected goal could not be verified")
            try:
                Goal.from_page(goal_page)
            except DomainValidationError as exc:
                raise ValueError("goal is not a member of Tony's Goals") from exc

        receipt = self._create_inbox(
            task,
            references=references,
            verify_final_task=False,
        )
        try:
            if task.project:
                self.runner.run(
                    "add_link",
                    {
                        "from": task.slug,
                        "to": task.project,
                        "link_type": "member_of",
                        "context": "GTasks project membership.",
                        "link_source": "gtasks",
                    },
                )
            if task.goal:
                self.runner.run(
                    "add_link",
                    {
                        "from": task.slug,
                        "to": task.goal,
                        "link_type": "advances_goal",
                        "context": "This task advances the linked Tony goal.",
                        "link_source": "gtasks",
                    },
                )
                self.runner.run(
                    "add_link",
                    {
                        "from": task.goal,
                        "to": task.slug,
                        "link_type": "advanced_by",
                        "context": "This goal is advanced by the linked GTasks task.",
                        "link_source": "gtasks",
                    },
                )

            stored_page = self.runner.run("get_page", {"slug": task.slug})
            stored_links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "full task creation readback was not structured"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            expected = (
                task.title,
                task.summary,
                task.detail,
                task.status,
                task.priority,
                task.next_action,
                task.due_day,
                task.inbox,
                task.lifecycle_root,
                task.project,
                task.goal,
                task.progress_metric,
                task.event_progress,
            )
            actual = (
                stored_task.title,
                stored_task.summary,
                stored_task.detail,
                stored_task.status,
                stored_task.priority,
                stored_task.next_action,
                stored_task.due_day,
                stored_task.inbox,
                stored_task.lifecycle_root,
                stored_task.project,
                stored_task.goal,
                stored_task.progress_metric,
                stored_task.event_progress,
            )
            if actual != expected:
                raise GBrainProtocolError(
                    "full task page readback did not match the requested task"
                )
            self._verify_compiled_markdown_body(
                stored_page,
                render_task_body(task.title, task.detail, references),
                label="Task",
            )
            typed_edges = {
                (
                    edge.get("from_slug"),
                    edge.get("to_slug"),
                    edge.get("link_type"),
                )
                for edge in stored_links
                if isinstance(edge, Mapping)
            }
            if (
                task.project
                and (task.slug, task.project, "member_of") not in typed_edges
            ):
                raise GBrainProtocolError(
                    "task project relationship readback was not verified"
                )
            if task.goal:
                if (
                    task.slug,
                    task.goal,
                    "advances_goal",
                ) not in typed_edges:
                    raise GBrainProtocolError(
                        "task goal relationship readback was not verified"
                    )
                goal_links = self.runner.run("get_links", {"slug": task.goal})
                if not isinstance(goal_links, list) or not any(
                    isinstance(edge, Mapping)
                    and edge.get("from_slug") == task.goal
                    and edge.get("to_slug") == task.slug
                    and edge.get("link_type") == "advanced_by"
                    for edge in goal_links
                ):
                    raise GBrainProtocolError(
                        "goal reciprocal relationship readback was not verified"
                    )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task.slug,
                (
                    "Task creation relationships were not fully verified. "
                    f"Inspect this task before retrying: {exc}"
                ),
            ) from exc
        return receipt

    def read_goal_execution_snapshot(
        self,
        route_health: Mapping[str, bool],
    ) -> GoalExecutionSnapshot:
        """Read one fail-closed, Codex-only planning snapshot from GBrain."""
        from .goal_execution import GoalExecutionSnapshot

        goals = self.list_goals()
        projects = self.list_projects()
        profiles = self.list_agent_profiles()
        required_roots = {
            issue.slug
            for issue in (*goals.issues, *projects.issues)
            if issue.category == "canonical_root_data"
            and issue.slug in {GOALS_ROOT, PROJECTS_ROOT}
        }
        if required_roots:
            raise CanonicalRootError(tuple(required_roots))
        codex_profiles = tuple(
            profile for profile in profiles.agents if profile.runtime == "codex"
        )
        profiles_by_slug = {profile.slug: profile for profile in codex_profiles}
        required_roots.update(
            slug
            for slug in EXISTING_CODEX_AGENT_SLUGS
            if slug not in profiles_by_slug
        )
        required_roots.update(
            issue.slug
            for issue in profiles.issues
            if issue.slug in EXISTING_CODEX_AGENT_SLUGS
        )
        if required_roots:
            raise CanonicalRootError(tuple(required_roots))

        work = self.list_agent_work(include_todos=False)
        codex_roots = {profile.work_root for profile in codex_profiles}
        unsafe_work_roots = {
            issue.owner_agent or issue.slug
            for issue in work.issues
            if issue.owner_agent in profiles_by_slug or issue.slug in codex_roots
        }
        if unsafe_work_roots:
            raise CanonicalRootError(tuple(unsafe_work_roots))

        tasks_by_slug: dict[str, Task] = {}
        for item in work.tasks:
            slug = item.get("slug")
            owner = item.get("owner_agent")
            if not isinstance(slug, str) or owner not in profiles_by_slug:
                continue
            task = self.get_task(slug)
            if task.owner_agent != owner or task.lifecycle_root not in codex_roots:
                raise CanonicalRootError((str(owner),))
            tasks_by_slug.setdefault(task.slug, task)
        return GoalExecutionSnapshot(
            goals=tuple(goals.goals),
            projects=tuple(projects.projects),
            agents=tuple(codex_profiles),
            tasks=tuple(tasks_by_slug.values()),
            route_health=dict(route_health),
        )

    def _goal_execution_lock(self, fingerprint: str) -> Lock:
        with self._goal_execution_locks_guard:
            return self._goal_execution_locks.setdefault(fingerprint, Lock())

    @staticmethod
    def _derived_task_from_candidate(
        candidate: GoalExecutionCandidate,
        agent: AgentProfile,
        now: datetime,
    ) -> Task:
        from .goal_execution import derived_task_slug

        derivation = GoalDerivationReceipt.from_value(
            {
                "planner_version": "goal-execution-v1",
                "fingerprint": candidate.fingerprint,
                "action_kind": candidate.action_kind,
                "authority_class": "auto_eligible",
                "goal_slug": candidate.goal_slug,
                "project_slug": candidate.project_slug,
                "expected_evidence": candidate.expected_evidence,
            }
        )
        task = new_task(
            title=candidate.title,
            detail=candidate.detail,
            now=now,
            identity=candidate.fingerprint,
            next_action="Publish the verified internal progress brief and one bounded next step.",
            project=candidate.project_slug,
            goal=candidate.goal_slug,
        )
        return replace(
            task,
            slug=derived_task_slug(candidate.fingerprint),
            lifecycle_root=agent.work_root,
            owner_agent=agent.slug,
            goal_derivation=derivation,
        )

    @staticmethod
    def _derived_task_matches(
        actual: Task,
        expected: Task,
    ) -> bool:
        return (
            actual.slug == expected.slug
            and actual.title == expected.title
            and actual.summary == expected.summary
            and actual.detail == expected.detail
            and actual.status in {"planned", "active", "blocked", "completed"}
            and actual.priority == expected.priority
            and actual.next_action == expected.next_action
            and actual.inbox == expected.inbox
            and actual.lifecycle_root == expected.lifecycle_root
            and actual.owner_agent == expected.owner_agent
            and actual.project == expected.project
            and actual.goal == expected.goal
            and actual.goal_derivation == expected.goal_derivation
        )

    @staticmethod
    def _derived_task_edges(task: Task) -> tuple[dict[str, str], ...]:
        descriptors = [
            {
                "from": task.slug,
                "to": task.lifecycle_root,
                "link_type": "member_of",
                "context": "Canonical agent work collection membership.",
                "link_source": "gtasks",
            },
            {
                "from": task.slug,
                "to": str(task.owner_agent),
                "link_type": "assigned_to",
                "context": "Tony explicitly assigned this work to the agent.",
                "link_source": "gtasks",
            },
            {
                "from": task.slug,
                "to": str(task.goal),
                "link_type": "advances_goal",
                "context": "This agent task advances the linked Tony goal.",
                "link_source": "gtasks",
            },
        ]
        if task.project:
            descriptors.append(
                {
                    "from": task.slug,
                    "to": task.project,
                    "link_type": "member_of",
                    "context": "GTasks project membership.",
                    "link_source": "gtasks",
                }
            )
        return tuple(descriptors)

    def _adopt_existing_derived_task(
        self,
        expected: Task,
        page: Mapping[str, Any],
    ) -> TaskEditReceipt:
        try:
            links = self.runner.run("get_links", {"slug": expected.slug})
            if not isinstance(links, list):
                raise GBrainProtocolError("derived task links were not a list")
            expected_descriptors = self._derived_task_edges(expected)
            expected_keys = {
                (item["from"], item["to"], item["link_type"])
                for item in expected_descriptors
            }
            reciprocal_key = (expected.goal, expected.slug, "advanced_by")
            actual_keys = {
                (
                    str(edge.get("from_slug")),
                    str(edge.get("to_slug")),
                    str(edge.get("link_type")),
                )
                for edge in links
                if isinstance(edge, Mapping)
            }
            relevant = {
                key
                for key in actual_keys
                if key[0] == expected.slug
                and (
                    key[2] in {"assigned_to", "advances_goal"}
                    or key[1] in TASK_SCOPE_ROOTS
                    or key[1].startswith("projects/")
                )
            }
            if not relevant.issubset(expected_keys):
                raise GBrainProtocolError(
                    "existing deterministic slug has conflicting typed relationships"
                )
            completed_links = list(links)
            for descriptor in expected_descriptors:
                key = (
                    descriptor["from"],
                    descriptor["to"],
                    descriptor["link_type"],
                )
                if key not in actual_keys:
                    completed_links.append(
                        {
                            "from_slug": descriptor["from"],
                            "to_slug": descriptor["to"],
                            "link_type": descriptor["link_type"],
                            "context": descriptor["context"],
                            "link_source": descriptor["link_source"],
                        }
                    )
            actual = Task.from_page(page, edges=completed_links)
            if not self._derived_task_matches(actual, expected):
                raise GBrainProtocolError(
                    "existing deterministic slug does not match its derivation receipt"
                )
        except (DomainValidationError, GBrainError, ValueError) as exc:
            raise PartialMutationError(
                expected.slug,
                "Derived Agent task cannot be safely adopted; no change was made. "
                + str(exc),
            ) from exc

        try:
            for descriptor in expected_descriptors:
                key = (
                    descriptor["from"],
                    descriptor["to"],
                    descriptor["link_type"],
                )
                if key not in actual_keys:
                    self.runner.run("add_link", descriptor)
            goal_links = self.runner.run("get_links", {"slug": str(expected.goal)})
            if not isinstance(goal_links, list):
                raise GBrainProtocolError("derived task reciprocal links were not a list")
            reciprocal_keys = {
                (
                    str(edge.get("from_slug")),
                    str(edge.get("to_slug")),
                    str(edge.get("link_type")),
                )
                for edge in goal_links
                if isinstance(edge, Mapping)
            }
            if reciprocal_key not in reciprocal_keys:
                self.runner.run(
                    "add_link",
                    {
                        "from": str(expected.goal),
                        "to": expected.slug,
                        "link_type": "advanced_by",
                        "context": "This goal is advanced by the assigned agent task.",
                        "link_source": "gtasks",
                    },
                )
            stored = self.get_task(expected.slug)
            stored_links = self.runner.run("get_links", {"slug": expected.slug})
            stored_goal_links = self.runner.run(
                "get_links", {"slug": str(expected.goal)}
            )
            if not isinstance(stored_links, list) or not isinstance(
                stored_goal_links, list
            ):
                raise GBrainProtocolError("derived task readback was not structured")
            stored_keys = {
                (
                    str(edge.get("from_slug")),
                    str(edge.get("to_slug")),
                    str(edge.get("link_type")),
                )
                for edge in (*stored_links, *stored_goal_links)
                if isinstance(edge, Mapping)
            }
            if not expected_keys.issubset(stored_keys) or reciprocal_key not in stored_keys:
                raise GBrainProtocolError(
                    "derived task relationships were not fully verified"
                )
            if not self._derived_task_matches(stored, expected):
                raise GBrainProtocolError("derived task readback changed during adoption")
            return TaskEditReceipt(expected.slug, stored, True)
        except (DomainValidationError, GBrainError, ValueError) as exc:
            raise PartialMutationError(
                expected.slug,
                "Derived Agent task partial write was not fully verified. " + str(exc),
            ) from exc

    def create_or_adopt_derived_agent_task(
        self,
        candidate: GoalExecutionCandidate,
        now: datetime,
    ) -> TaskEditReceipt:
        """Create once or safely resume one deterministic Goal-derived task."""
        from .goal_execution import GoalExecutionCandidate

        if not isinstance(candidate, GoalExecutionCandidate):
            raise TypeError("candidate must be a GoalExecutionCandidate")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("derived task creation time must include a timezone")
        with self._goal_execution_lock(candidate.fingerprint):
            profiles = self.list_agent_profiles()
            agent = next(
                (
                    profile
                    for profile in profiles.agents
                    if profile.slug == candidate.agent_slug
                    and profile.runtime == "codex"
                ),
                None,
            )
            if agent is None:
                raise CanonicalRootError((candidate.agent_slug,))
            expected = self._derived_task_from_candidate(candidate, agent, now)
            try:
                existing_page = self.runner.run(
                    "get_page", {"slug": expected.slug}
                )
            except GBrainCommandError as exc:
                if not is_page_not_found_error(exc):
                    raise
            else:
                if not isinstance(existing_page, Mapping):
                    raise PartialMutationError(
                        expected.slug,
                        "Derived Agent task readback was not structured; no change was made.",
                    )
                return self._adopt_existing_derived_task(expected, existing_page)

            self.create_agent_task(expected, agent.slug)
            stored = self.get_task(expected.slug)
            if not self._derived_task_matches(stored, expected):
                raise PartialMutationError(
                    expected.slug,
                    "Derived Agent task creation readback did not match the candidate.",
                )
            return TaskEditReceipt(expected.slug, stored, True)

    def create_agent_task(
        self,
        task: Task,
        agent_slug: str,
    ) -> MutationReceipt:
        scope_by_agent = {
            agent.slug: agent.work_root
            for agent in self.list_agent_profiles().agents
        }
        work_root = scope_by_agent.get(agent_slug)
        if work_root is None:
            raise ValueError("assignee must be Tony, Toddy, Timmy, or Tammy")
        if (
            task.owner_agent != agent_slug
            or task.lifecycle_root != work_root
            or task.status != "planned"
            or not task.inbox
        ):
            raise ValueError(
                "new agent work must start planned/queued in exactly the "
                "selected agent work collection"
            )
        if AGENT_RUNTIME_BY_SLUG.get(agent_slug) == "openclaw":
            activation = self._active_openclaw_activation(agent_slug)
            self._openclaw_profile_from_activation(activation)
            self._verify_openclaw_task_anchor(activation)
        else:
            agent_page = self.runner.run("get_page", {"slug": agent_slug})
            agent_links = self.runner.run("get_links", {"slug": agent_slug})
            if not isinstance(agent_page, Mapping) or not isinstance(
                agent_links, list
            ):
                raise ValueError("selected agent profile could not be verified")
            AgentProfile.from_page(
                agent_page,
                work_root=work_root,
                edges=agent_links,
            )
        if task.project:
            project_page = self.runner.run("get_page", {"slug": task.project})
            project_links = self.runner.run("get_links", {"slug": task.project})
            if not isinstance(project_page, Mapping) or not isinstance(
                project_links, list
            ):
                raise ValueError("selected project could not be verified")
            Project.from_page(project_page, edges=project_links)
        if task.goal:
            goal_page = self.runner.run("get_page", {"slug": task.goal})
            if not isinstance(goal_page, Mapping):
                raise ValueError("selected goal could not be verified")
            Goal.from_page(goal_page)

        references = self._verified_system_ticket_references((task.detail,))
        expected_body = render_task_body(task.title, task.detail, references)

        self.runner.run(
            "put_page",
            {"slug": task.slug, "content": render_task_page(task, body=expected_body)},
        )
        try:
            preexisting_links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(preexisting_links, list):
                raise GBrainProtocolError("agent task lifecycle readback was not structured")
            preexisting_lifecycle = _lifecycle_edges(task.slug, preexisting_links)
            if len(preexisting_lifecycle) > 1 or (
                preexisting_lifecycle
                and preexisting_lifecycle[0].get("to_slug") != work_root
            ):
                raise LifecycleIntegrityError(task.slug, preexisting_lifecycle)
        except (GBrainError, LifecycleIntegrityError) as exc:
            raise PartialMutationError(
                task.slug,
                (
                    "Agent task page was written but initial relationship "
                    f"readback failed: {exc}"
                ),
            ) from exc
        descriptors = [
            {
                "from": task.slug,
                "to": agent_slug,
                "link_type": "assigned_to",
                "context": "Tony explicitly assigned this work to the agent.",
                "link_source": "gtasks",
            },
        ]
        if not preexisting_lifecycle:
            descriptors.insert(
                0,
                {
                    "from": task.slug,
                    "to": work_root,
                    "link_type": "member_of",
                    "context": "Canonical agent work collection membership.",
                    "link_source": "gtasks",
                },
            )
        if task.project:
            descriptors.append(
                {
                    "from": task.slug,
                    "to": task.project,
                    "link_type": "member_of",
                    "context": "GTasks project membership.",
                    "link_source": "gtasks",
                }
            )
        if task.parent:
            descriptors.append(
                {
                    "from": task.slug,
                    "to": task.parent,
                    "link_type": "child_of",
                    "context": "GTasks parent task.",
                    "link_source": "gtasks",
                }
            )
        if task.goal:
            descriptors.append(
                {
                    "from": task.slug,
                    "to": task.goal,
                    "link_type": "advances_goal",
                    "context": "This agent task advances the linked Tony goal.",
                    "link_source": "gtasks",
                }
            )
        try:
            for descriptor in descriptors:
                self.runner.run("add_link", descriptor)
            if task.goal:
                self.runner.run(
                    "add_link",
                    {
                        "from": task.goal,
                        "to": task.slug,
                        "link_type": "advanced_by",
                        "context": "This goal is advanced by the assigned agent task.",
                        "link_source": "gtasks",
                    },
                )
            page = self.runner.run("get_page", {"slug": task.slug})
            links = self.runner.run("get_links", {"slug": task.slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise GBrainProtocolError(
                    "agent task readback was not structured"
                )
            stored = Task.from_page(page, edges=links)
            if stored != task:
                raise GBrainProtocolError(
                    "agent task page readback did not match the requested task"
                )
            self._verify_compiled_markdown_body(
                page, expected_body, label="Agent task"
            )
            typed = {
                (
                    edge.get("from_slug"),
                    edge.get("to_slug"),
                    edge.get("link_type"),
                )
                for edge in links
                if isinstance(edge, Mapping)
            }
            if (task.slug, work_root, "member_of") not in typed or (
                task.slug,
                agent_slug,
                "assigned_to",
            ) not in typed:
                raise GBrainProtocolError(
                    "agent assignment relationships were not verified"
                )
            verified_lifecycle = _require_single_lifecycle_edge(task.slug, links)
            if verified_lifecycle.get("to_slug") != work_root:
                raise LifecycleIntegrityError(task.slug, [verified_lifecycle])
            if any(
                edge[0] == task.slug
                and edge[2] == "member_of"
                and edge[1] in TASK_SCOPE_ROOTS
                and edge[1] != work_root
                for edge in typed
            ):
                raise GBrainProtocolError(
                    "agent task retained another current task scope"
                )
        except (DomainValidationError, GBrainError, LifecycleIntegrityError) as exc:
            raise PartialMutationError(
                task.slug,
                (
                    "Agent task was not fully verified. Do not retry until "
                    f"this slug is inspected: {exc}"
                ),
            ) from exc
        return MutationReceipt(slug=task.slug, verified=True)

    def review_proposal(
        self,
        proposal_slug: str,
        *,
        title: str,
        rationale: str,
        proposed_next_step: str,
        due_day: date,
        now: datetime,
    ) -> ProposalMutationReceipt:
        proposal = next(
            (
                candidate
                for candidate in self.list_proposals().proposals
                if candidate.slug == proposal_slug
            ),
            None,
        )
        if proposal is None:
            raise ValueError("proposal is not in the canonical review scope")
        if proposal.source_kind == "task":
            task = self.get_task(proposal_slug)
            if task.status != "proposed":
                raise ValueError("only proposed work may be edited")
            receipt = self.edit_task(
                proposal_slug,
                title=title,
                detail=rationale,
                priority=task.priority,
                due_day=due_day,
                next_action=proposed_next_step,
                project_slug=task.project,
                goal_slug=task.goal,
                status="proposed",
                assignee_slug=task.owner_agent or "tony",
                progress_metric=task.progress_metric,
                event_progress=task.event_progress,
                handoff_reason="",
                now=now,
            )
            stored = receipt.task
            return ProposalMutationReceipt(
                proposal_slug=proposal_slug,
                status="proposed",
                proposal=replace(
                    proposal, title=stored.title, rationale=stored.detail,
                    proposed_next_step=stored.next_action, due_day=stored.due_day,
                    updated_at=stored.updated_at or now,
                ),
                created_task=None, verified=True,
            )
        raise ValueError(
            "legacy task_proposal pages are read-only compatibility records; "
            "new proposals are canonical agent tasks with status proposed"
        )
        if proposal.status not in {"proposed", "review"}:
            raise ValueError("only proposed or in-review work may be edited")
        updated = replace(
            proposal,
            title=title.strip(),
            rationale=rationale.strip(),
            proposed_next_step=proposed_next_step.strip(),
            due_day=due_day,
            status="review",
            updated_at=now,
        )
        if not updated.title or len(updated.title) > 160:
            raise ValueError("proposal title must be 1 to 160 characters")
        if not updated.rationale or not updated.proposed_next_step:
            raise ValueError("proposal rationale and next step are required")
        self.runner.run(
            "put_page",
            {"slug": proposal_slug, "content": render_proposal_page(updated)},
        )
        try:
            page = self.runner.run("get_page", {"slug": proposal_slug})
            links = self.runner.run("get_links", {"slug": proposal_slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise GBrainProtocolError(
                    "proposal edit readback was not structured"
                )
            stored = TaskProposal.from_page(page, edges=links)
            if stored != updated:
                raise GBrainProtocolError(
                    "proposal edit readback did not match the request"
                )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                proposal_slug,
                f"Proposal edit write was not verified: {exc}",
            ) from exc
        return ProposalMutationReceipt(
            proposal_slug=proposal_slug,
            status=stored.status,
            proposal=stored,
            created_task=None,
            verified=True,
        )

    def decide_proposal(
        self,
        proposal_slug: str,
        *,
        action: str,
        decision_note: str,
        now: datetime,
    ) -> ProposalMutationReceipt:
        proposal = next(
            (
                candidate
                for candidate in self.list_proposals().proposals
                if candidate.slug == proposal_slug
            ),
            None,
        )
        if proposal is None:
            raise ValueError("proposal is not in the canonical review scope")
        if action not in {"approve", "reject"}:
            raise ValueError("proposal decision must be approve or reject")
        if proposal.source_kind == "task":
            task = self.get_task(proposal_slug)
            review_status = "approved" if action == "approve" else "rejected"
            target_status = "planned" if action == "approve" else "cancelled"
            if task.proposal_decision is not None:
                if (
                    task.proposal_decision == action
                    and task.status == target_status
                    and task.proposal_decision_events
                ):
                    return ProposalMutationReceipt(
                        proposal_slug=proposal_slug,
                        status=review_status,
                        proposal=replace(
                            proposal,
                            status=review_status,
                            updated_at=task.updated_at or now,
                            reviewed_at=task.proposal_decided_at,
                            decision_note=task.proposal_decision_note,
                            decision=action,
                            decision_at=task.proposal_decided_at,
                            resulting_status=task.status,
                            decision_events=task.proposal_decision_events,
                        ),
                        created_task=task,
                        verified=True,
                    )
                raise ValueError("proposal already has a final decision")
            if task.status != "proposed":
                raise ValueError("proposal already has a final decision")
            raw_page = self.runner.run("get_page", {"slug": proposal_slug})
            raw_links = self.runner.run("get_links", {"slug": proposal_slug})
            if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
                raise GBrainProtocolError("proposed task page readback was not structured")
            # Do this before writing decision metadata. A malformed lifecycle
            # must never leave a proposal looking approved when its same-task
            # authorization transition could not safely occur.
            lifecycle_edge = _require_single_lifecycle_edge(proposal_slug, raw_links)
            if lifecycle_edge.get("to_slug") != task.lifecycle_root:
                raise LifecycleIntegrityError(proposal_slug, [lifecycle_edge])
            frontmatter = raw_page.get("frontmatter")
            if not isinstance(frontmatter, Mapping):
                raise GBrainProtocolError("proposed task page has no frontmatter")
            changed = deepcopy(dict(frontmatter))
            event_id = "proposal-decision:" + hashlib.sha256(
                f"{proposal_slug}:{action}".encode("utf-8")
            ).hexdigest()[:24]
            event = ProposalDecisionEvent.from_value(
                {
                    "event_id": event_id,
                    "event_type": "proposal_decision",
                    "occurred_at": now.isoformat(),
                    "actor": TONY_PROFILE_SLUG,
                    "source": "mission_control",
                    "decision": action,
                    "decision_note": decision_note.strip(),
                    "previous_status": "proposed",
                    "resulting_status": target_status,
                    "proposal_slug": proposal_slug,
                }
            )
            existing_events = changed.get("proposal_decision_events") or []
            if not isinstance(existing_events, list):
                raise GBrainProtocolError(
                    "proposal decision event history is not a list"
                )
            changed["proposal_decision_note"] = decision_note.strip()
            changed["proposal_decided_at"] = now.isoformat()
            changed["proposal_decision"] = action
            changed["proposal_decision_events"] = [
                *existing_events,
                event.to_dict(),
            ]
            changed["status"] = target_status
            changed["completed_at"] = None
            changed["updated_at"] = now.isoformat()
            self.runner.run(
                "put_page",
                {
                    "slug": proposal_slug,
                    "content": _render_preserved_task_page(raw_page, changed),
                },
            )
            try:
                stored_page = self.runner.run("get_page", {"slug": proposal_slug})
                stored_links = self.runner.run("get_links", {"slug": proposal_slug})
                if not isinstance(stored_page, Mapping) or not isinstance(stored_links, list):
                    raise GBrainProtocolError(
                        "proposal decision readback was not structured"
                    )
                stored = Task.from_page(stored_page, edges=stored_links)
                if (
                    stored.status != target_status
                    or stored.proposal_decision != action
                    or stored.proposal_decided_at != now
                    or stored.proposal_decision_note != decision_note.strip()
                    or stored.proposal_decision_events[-1] != event
                    or stored.lifecycle_root != task.lifecycle_root
                ):
                    raise GBrainProtocolError(
                        "proposal decision page readback did not match the request"
                    )
                for expected in raw_links:
                    if not isinstance(expected, Mapping):
                        continue
                    if not any(
                        isinstance(actual, Mapping)
                        and actual.get("from_slug") == expected.get("from_slug")
                        and actual.get("to_slug") == expected.get("to_slug")
                        and actual.get("link_type") == expected.get("link_type")
                        for actual in stored_links
                    ):
                        raise GBrainProtocolError(
                            "proposal decision lost an unrelated relationship"
                        )
            except (DomainValidationError, GBrainError) as exc:
                rollback_verified = False
                try:
                    self.runner.run(
                        "put_page",
                        {
                            "slug": proposal_slug,
                            "content": _render_preserved_task_page(
                                raw_page, deepcopy(dict(frontmatter))
                            ),
                        },
                    )
                    rollback_page = self.runner.run(
                        "get_page", {"slug": proposal_slug}
                    )
                    rollback_links = self.runner.run(
                        "get_links", {"slug": proposal_slug}
                    )
                    rollback_verified = (
                        isinstance(rollback_page, Mapping)
                        and isinstance(rollback_links, list)
                        and Task.from_page(
                            rollback_page, edges=rollback_links
                        ).status == "proposed"
                    )
                except (DomainValidationError, GBrainError):
                    rollback_verified = False
                raise PartialMutationError(
                    proposal_slug,
                    "Proposal decision was not fully verified. "
                    + ("Rollback verified. " if rollback_verified else "Rollback was not verified. ")
                    + "Inspect this same task before retrying. "
                    + str(exc),
                ) from exc
            return ProposalMutationReceipt(
                proposal_slug=proposal_slug, status=review_status,
                proposal=replace(
                    proposal,
                    status=review_status,
                    updated_at=stored.updated_at or now,
                    reviewed_at=now,
                    decision_note=decision_note.strip(),
                    decision=action,
                    decision_at=now,
                    resulting_status=stored.status,
                    decision_events=stored.proposal_decision_events,
                ),
                created_task=stored, verified=True,
            )
        raise ValueError(
            "legacy task_proposal pages are read-only compatibility records; "
            "new proposals are canonical agent tasks with status proposed"
        )
        if proposal.status in {"approved", "rejected"}:
            if (
                proposal.status == "approved"
                and action == "approve"
                and proposal.approved_task
            ):
                page = self.runner.run(
                    "get_page",
                    {"slug": proposal.approved_task},
                )
                links = self.runner.run(
                    "get_links",
                    {"slug": proposal.approved_task},
                )
                if not isinstance(page, Mapping) or not isinstance(links, list):
                    raise GBrainProtocolError(
                        "approved task readback was not structured"
                    )
                task = Task.from_page(page, edges=links)
                return ProposalMutationReceipt(
                    proposal_slug=proposal.slug,
                    status=proposal.status,
                    proposal=proposal,
                    created_task=task,
                    verified=True,
                )
            raise ValueError("proposal already has a final decision")

        created_task: Task | None = None
        if action == "approve":
            identity = hashlib.sha256(
                proposal.slug.encode("utf-8")
            ).hexdigest()[:12]
            created_task = new_task(
                title=proposal.title,
                detail=proposal.rationale,
                next_action=proposal.proposed_next_step,
                due_day=proposal.due_day,
                goal=proposal.linked_goal,
                now=now,
                identity=identity,
            )
            if proposal.recipient == "agent":
                work_root = dict(AGENT_SCOPES)[proposal.proposing_agent]
                created_task = replace(
                    created_task,
                    lifecycle_root=work_root,
                    owner_agent=proposal.proposing_agent,
                )
                self.create_agent_task(
                    created_task,
                    proposal.proposing_agent,
                )
            else:
                self.create_task(created_task)

        decided = replace(
            proposal,
            status="approved" if action == "approve" else "rejected",
            approved_task=created_task.slug if created_task else None,
            reviewed_at=now,
            updated_at=now,
            decision_note=decision_note.strip(),
        )
        self.runner.run(
            "put_page",
            {"slug": proposal.slug, "content": render_proposal_page(decided)},
        )
        if created_task:
            self.runner.run(
                "add_link",
                {
                    "from": proposal.slug,
                    "to": created_task.slug,
                    "link_type": "approved_as",
                    "context": "Tony explicitly approved this proposal as a task.",
                    "link_source": "gtasks",
                },
            )
        try:
            page = self.runner.run("get_page", {"slug": proposal.slug})
            links = self.runner.run("get_links", {"slug": proposal.slug})
            if not isinstance(page, Mapping) or not isinstance(links, list):
                raise GBrainProtocolError(
                    "proposal decision readback was not structured"
                )
            stored = TaskProposal.from_page(page, edges=links)
            if (
                stored.status != decided.status
                or stored.approved_task != decided.approved_task
                or stored.reviewed_at != now
            ):
                raise GBrainProtocolError(
                    "proposal decision readback did not match the request"
                )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                proposal.slug,
                (
                    "Proposal decision was not fully verified. Inspect the "
                    f"proposal and any approved task before retrying: {exc}"
                ),
            ) from exc
        return ProposalMutationReceipt(
            proposal_slug=proposal.slug,
            status=stored.status,
            proposal=stored,
            created_task=created_task,
            verified=True,
        )

    def duplicate_task(
        self,
        source_slug: str,
        task: Task,
    ) -> MutationReceipt:
        self._approved_task(source_slug)
        if task.slug == source_slug:
            raise ValueError("duplicate task must receive a new identity")
        if (
            task.status != "planned"
            or not task.inbox
            or task.completed_at is not None
            or task.lifecycle_root != ACTIVE_ROOT
        ):
            raise ValueError(
                "duplicate task must start planned in the active Inbox "
                "without completion history"
            )
        if task.event_progress and (
            task.event_progress.baseline_count
            or
            task.event_progress.evidence_slugs
            or task.event_progress.receipt_ids
        ):
            raise ValueError(
                "duplicate task may not copy event evidence or receipts"
            )
        if (
            task.progress_metric
            and task.progress_metric.event_binding
            and task.progress_metric.current != 0
        ):
            raise ValueError(
                "duplicate event-bound task progress must start at 0"
            )
        return self.create_task(task)

    def repair_active_membership(
        self,
        task_slug: str,
    ) -> MembershipRepairReceipt:
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        if not isinstance(raw_page, Mapping):
            raise GBrainProtocolError("membership repair get_page was not an object")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; "
                "repair the task type before repairing membership"
            )
        raw_frontmatter = raw_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise ValueError("task is not eligible for active membership repair")
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_links, list):
            raise GBrainProtocolError("membership repair get_links was not a list")

        legacy_edges = [
            edge
            for edge in raw_links
            if isinstance(edge, Mapping)
            and edge.get("from_slug") == task_slug
            and edge.get("to_slug") == ACTIVE_ROOT
            and edge.get("link_type") in {"", None}
        ]
        typed_edges = _lifecycle_edges(task_slug, raw_links)
        if (
            raw_frontmatter.get("collection") != ACTIVE_ROOT
            or len(legacy_edges) != 1
            or typed_edges
        ):
            raise ValueError("task is not eligible for active membership repair")

        repaired_frontmatter = deepcopy(dict(raw_frontmatter))
        repaired_frontmatter["type"] = "task"
        repaired_links = repaired_frontmatter.get("links")
        if repaired_links is None:
            repaired_links = []
        if not isinstance(repaired_links, list):
            raise ValueError("task is not eligible for active membership repair")
        repaired_links = deepcopy(repaired_links)
        repaired_links.append({"to": ACTIVE_ROOT, "type": "member_of"})
        repaired_frontmatter["links"] = repaired_links

        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        original_content = _render_preserved_task_page(raw_page, original_frontmatter)
        repaired_content = _render_preserved_task_page(raw_page, repaired_frontmatter)
        typed_descriptor = {
            "from": task_slug,
            "to": ACTIVE_ROOT,
            "link_type": "member_of",
            "context": "GTasks active task membership repair.",
            "link_source": "gtasks",
        }
        legacy_descriptor = {
            "from": task_slug,
            "to": ACTIVE_ROOT,
            "link_type": "",
        }
        journal: list[str] = []
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": repaired_content},
            )
            journal.append("put_page")
            self.runner.run("add_link", typed_descriptor)
            journal.append("add_typed")
            self.runner.run("remove_link", legacy_descriptor)
            journal.append("remove_legacy")

            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "membership repair readback was not structured"
                )
            if stored_page.get("type") != "task":
                raise GBrainProtocolError(
                    "membership repair changed the page type away from task"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            verified_typed = _lifecycle_edges(task_slug, stored_links)
            if (
                stored_task.lifecycle_root != ACTIVE_ROOT
                or len(verified_typed) != 1
                or any(
                    isinstance(edge, Mapping)
                    and edge.get("from_slug") == task_slug
                    and edge.get("to_slug") == ACTIVE_ROOT
                    and edge.get("link_type") in {"", None}
                    for edge in stored_links
                )
            ):
                raise GBrainProtocolError(
                    "membership repair readback did not match the requested state"
                )
        except (DomainValidationError, GBrainError) as exc:
            rollback_verified = False
            try:
                if "remove_legacy" in journal:
                    self.runner.run(
                        "add_link",
                        {
                            **legacy_descriptor,
                            "context": "Restored legacy GTasks collection link.",
                            "link_source": "gtasks",
                        },
                    )
                if "add_typed" in journal:
                    self.runner.run(
                        "remove_link",
                        {
                            "from": task_slug,
                            "to": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                    )
                if "put_page" in journal:
                    self.runner.run(
                        "put_page",
                        {"slug": task_slug, "content": original_content},
                    )
                rollback_page = self.runner.run("get_page", {"slug": task_slug})
                rollback_links = self.runner.run("get_links", {"slug": task_slug})
                rollback_verified = (
                    isinstance(rollback_page, Mapping)
                    and rollback_page.get("type") == "task"
                    and isinstance(rollback_links, list)
                    and any(
                        isinstance(edge, Mapping)
                        and edge.get("from_slug") == task_slug
                        and edge.get("to_slug") == ACTIVE_ROOT
                        and edge.get("link_type") in {"", None}
                        for edge in rollback_links
                    )
                    and not _lifecycle_edges(task_slug, rollback_links)
                )
            except GBrainError:
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Active membership repair was not verified. {outcome}",
            ) from exc

        return MembershipRepairReceipt(task_slug=task_slug, verified=True)

    def archive_due_completed_tony_tasks(
        self,
        now: datetime,
        *,
        task_slugs: Sequence[str] | None = None,
    ) -> CompletedArchiveReceipt:
        """Move completed Tony tasks from active to completed root after boundary.

        Completion itself intentionally keeps a task in Tony's active root so it can
        remain visible through the current week. This operation is the explicit
        lifecycle/archive boundary: after the next Monday in Tony's timezone, a
        completed Tony task is moved to the completed root with add-before-remove
        relationship ordering and exact readback.
        """
        if now.tzinfo is None:
            raise ValueError("archive boundary time must include a timezone")
        if task_slugs is None:
            active_read = self.list_collection_tasks(ACTIVE_ROOT)
            task_slugs = tuple(task.slug for task in active_read.tasks)

        archived: list[str] = []
        skipped: list[str] = []
        issues: list[CollectionIssue] = []
        for task_slug in tuple(task_slugs):
            try:
                outcome = self._archive_due_completed_tony_task(task_slug, now)
            except (DomainValidationError, GBrainError, ValueError) as exc:
                issues.append(
                    CollectionIssue(
                        slug=task_slug,
                        message=(
                            "Completed task archive boundary repair failed: "
                            + str(exc)
                        ),
                        severity="error",
                        task_visible=False,
                        category="lifecycle_archive",
                        impact=(
                            "The task was not verified as safely archived; inspect "
                            "the canonical page and member_of links before retrying."
                        ),
                        repair_action="archive_due_completed_tony_tasks",
                    )
                )
                continue
            if outcome == "archived":
                archived.append(task_slug)
            else:
                skipped.append(task_slug)
        return CompletedArchiveReceipt(
            archived_slugs=tuple(archived),
            skipped_slugs=tuple(skipped),
            issues=tuple(issues),
            verified=not issues,
        )

    def _archive_due_completed_tony_task(
        self,
        task_slug: str,
        now: datetime,
    ) -> str:
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("archive readback was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; "
                "repair the task type before archiving"
            )
        raw_frontmatter = raw_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise ValueError("task is not eligible for archive without frontmatter")
        status = raw_frontmatter.get("status")
        if status != "completed":
            return "skipped"
        completed_at = _parse_required_archive_completed_at(raw_frontmatter)
        if not _completed_archive_boundary_reached(completed_at, now):
            return "skipped"

        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        lifecycle_roots = [
            str(edge.get("to_slug"))
            for edge in lifecycle_edges
            if isinstance(edge.get("to_slug"), str)
        ]
        has_active = ACTIVE_ROOT in lifecycle_roots
        has_completed = COMPLETED_ROOT in lifecycle_roots
        unexpected_roots = [
            root
            for root in lifecycle_roots
            if root not in {ACTIVE_ROOT, COMPLETED_ROOT}
        ]
        if unexpected_roots:
            raise LifecycleIntegrityError(task_slug, lifecycle_edges)
        if not has_active and has_completed and len(lifecycle_edges) == 1:
            return "skipped"
        if not has_active:
            raise LifecycleIntegrityError(task_slug, lifecycle_edges)

        raw_frontmatter_links = raw_frontmatter.get("links")
        if not isinstance(raw_frontmatter_links, list):
            raise GBrainProtocolError("task frontmatter links must be a list")
        retained_links = [
            deepcopy(dict(link))
            for link in raw_frontmatter_links
            if not (
                isinstance(link, Mapping)
                and link.get("type") == "member_of"
                and link.get("to") in TASK_SCOPE_ROOTS
            )
        ]
        retained_links.append(
            {
                "to": COMPLETED_ROOT,
                "type": "member_of",
                "context": "Mission Control completed-task archive boundary.",
            }
        )
        desired_frontmatter = deepcopy(dict(raw_frontmatter))
        desired_frontmatter["type"] = "task"
        desired_frontmatter["links"] = retained_links
        desired_content = _render_preserved_task_page(raw_page, desired_frontmatter)

        existing_unrelated = [
            link
            for link in raw_links
            if isinstance(link, Mapping)
            and not (
                link.get("from_slug") == task_slug
                and link.get("link_type") == "member_of"
                and link.get("to_slug") in TASK_SCOPE_ROOTS
            )
        ]
        self.runner.run("put_page", {"slug": task_slug, "content": desired_content})
        if not has_completed:
            self.runner.run(
                "add_link",
                {
                    "from": task_slug,
                    "to": COMPLETED_ROOT,
                    "link_type": "member_of",
                    "context": "Mission Control completed-task archive boundary.",
                    "link_source": "gtasks",
                },
            )
        self.runner.run(
            "remove_link",
            {
                "from": task_slug,
                "to": ACTIVE_ROOT,
                "link_type": "member_of",
            },
        )

        stored_page = self.runner.run("get_page", {"slug": task_slug})
        stored_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(stored_page, Mapping) or not isinstance(stored_links, list):
            raise GBrainProtocolError("archive readback was not structured")
        if stored_page.get("type") != "task":
            raise GBrainProtocolError(
                "archive write changed the canonical page type away from task"
            )
        stored_task = Task.from_page(stored_page, edges=stored_links)
        if (
            stored_task.status != "completed"
            or stored_task.lifecycle_root != COMPLETED_ROOT
            or stored_task.completed_at != completed_at
        ):
            raise GBrainProtocolError(
                "archive page readback did not match the requested lifecycle"
            )
        verified_lifecycle = _lifecycle_edges(task_slug, stored_links)
        if (
            len(verified_lifecycle) != 1
            or verified_lifecycle[0].get("to_slug") != COMPLETED_ROOT
        ):
            raise GBrainProtocolError(
                "archive lifecycle edge readback did not match the task page"
            )
        for expected in existing_unrelated:
            if not any(
                isinstance(actual, Mapping)
                and actual.get("from_slug") == expected.get("from_slug")
                and actual.get("to_slug") == expected.get("to_slug")
                and actual.get("link_type") == expected.get("link_type")
                for actual in stored_links
            ):
                raise GBrainProtocolError(
                    "an unrelated task relationship was missing after archive"
                )
        return "archived"

    def _approved_task(self, task_slug: str) -> Task:
        for root_slug in (ACTIVE_ROOT, COMPLETED_ROOT):
            result = self.list_collection_tasks(root_slug)
            for task in result.tasks:
                if task.slug == task_slug:
                    return task
        raise ValueError("task is not a member of an approved GTasks root")

    def _get_task_readback(
        self, task_slug: str
    ) -> tuple[Task, Mapping[str, Any]]:
        task_slug = self.resolve_canonical_slug(task_slug)
        page = self.runner.run("get_page", {"slug": task_slug})
        links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("task readback was not structured")
        if page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {page.get('type') or 'missing'}; repair the task type before editing"
            )
        task = Task.from_page(page, edges=links)
        self._require_task_openclaw_activation(task)
        return task, page

    def get_task(self, task_slug: str) -> Task:
        task, _page = self._get_task_readback(task_slug)
        return task

    def get_task_api_payload(self, task_slug: str) -> dict[str, Any]:
        """Return structured authority plus an optional display-only body."""
        task, page = self._get_task_readback(task_slug)
        payload = task.to_dict()
        display_markdown = self._validated_task_display_markdown(task, page)
        if display_markdown is not None:
            payload["display_markdown"] = display_markdown
        return payload

    def edit_task(
        self,
        task_slug: str,
        *,
        title: str,
        detail: str,
        priority: str,
        due_day: date,
        next_action: str,
        project_slug: str | None,
        goal_slug: str | None,
        status: str,
        assignee_slug: str,
        progress_metric: ProgressMetric | None,
        event_progress: EventProgress | None,
        handoff_reason: str,
        now: datetime,
        parent_slug: str | None = None,
    ) -> TaskEditReceipt:
        """Apply the full detail form through verified canonical mutations.

        Page fields are written together first; relationship and lifecycle changes use
        their existing readback/rollback paths. A later failure is always surfaced as
        a partial mutation, never as an unverified success.
        """
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("task edit snapshot was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; repair the task type before editing"
            )
        task = Task.from_page(raw_page, edges=raw_links)
        self._require_task_openclaw_activation(task)
        self._require_openclaw_assignment_target(assignee_slug)
        if status not in EDITABLE_TASK_STATUSES | {"proposed"}:
            raise ValueError("task status is not supported")
        if task.status == "proposed" and status == "proposed" and assignee_slug != (task.owner_agent or "tony"):
            raise ValueError("the owner of proposed work is immutable until it is approved")
        if assignee_slug != "tony" and assignee_slug not in {
            agent.slug for agent in self.list_agent_profiles().agents
        }:
            raise ValueError("assignee is not an active Agent Directory profile")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
            raise ValueError("title is required and must be 160 characters or fewer")
        if not isinstance(detail, str):
            raise ValueError("detail must be text")
        if not isinstance(next_action, str) or len(next_action.strip()) > 240 or "\n" in next_action:
            raise ValueError("next_action must be one concise line of 240 characters or fewer")
        if priority not in {"low", "normal", "high", "urgent"}:
            raise ValueError("priority is not supported")
        if progress_metric and progress_metric.event_binding:
            if event_progress is None or progress_metric.current != event_progress.derived_current:
                raise ValueError("event-bound metric progress must match its baseline and verified evidence and receipts")

        if project_slug != task.project:
            approved = {project.slug for project in self.list_projects().projects}
            if project_slug is not None and project_slug not in approved:
                raise ValueError("project is not a durable member of Tony's Projects")
        if goal_slug != task.goal:
            approved_goals = {goal.slug for goal in self.list_goals().goals}
            if goal_slug is not None and goal_slug not in approved_goals:
                raise ValueError("goal is not a member of Tony's Goals")
        if parent_slug is not None and parent_slug == task_slug:
            raise ValueError("task cannot be its own parent")

        raw_frontmatter = raw_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        frontmatter = deepcopy(dict(raw_frontmatter))
        raw_frontmatter_links = frontmatter.get("links")
        if raw_frontmatter_links is None:
            raw_frontmatter_links = []
        if not isinstance(raw_frontmatter_links, list):
            raise GBrainProtocolError("task frontmatter links must be a list")
        retained_links = [
            link
            for link in raw_frontmatter_links
            if not (isinstance(link, Mapping) and link.get("type") == "child_of")
        ]
        if parent_slug:
            retained_links.append(
                {
                    "to": parent_slug,
                    "type": "child_of",
                    "context": "GTasks parent task.",
                }
            )
        normalized_next_action = next_action.strip()
        desired_next_action_history = _history_after_next_action_change(
            task,
            normalized_next_action,
            now,
        )
        frontmatter.update(
            {
                "type": "task",
                "title": title.strip(),
                "summary": title.strip(),
                "detail": detail.strip(),
                "priority": priority,
                "due_day": due_day.isoformat(),
                "next_action": normalized_next_action,
                "next_action_history": [
                    entry.to_dict() for entry in desired_next_action_history
                ],
                "links": retained_links,
                "progress_metric": progress_metric.to_dict() if progress_metric else None,
                "event_progress": event_progress.to_dict() if event_progress else None,
                "updated_at": now.isoformat(),
            }
        )
        marked_unified = self._has_unified_markdown_contract(raw_page)
        expected_body: str | None = None
        if marked_unified:
            references = self._verified_system_ticket_references((detail.strip(),))
            expected_body = render_task_body(
                title.strip(), detail.strip(), references
            )
        original_content = _render_preserved_task_page(raw_page, dict(raw_frontmatter))
        desired_content = _render_preserved_task_page(
            raw_page, frontmatter, body=expected_body
        )
        try:
            self.runner.run("put_page", {"slug": task_slug, "content": desired_content})
            if parent_slug != task.parent:
                if parent_slug:
                    self.runner.run(
                        "add_link",
                        {
                            "from": task_slug,
                            "to": parent_slug,
                            "link_type": "child_of",
                            "context": "GTasks parent task.",
                            "link_source": "gtasks",
                        },
                    )
                if task.parent:
                    self.runner.run(
                        "remove_link",
                        {
                            "from": task_slug,
                            "to": task.parent,
                            "link_type": "child_of",
                        },
                    )
            if project_slug != task.project:
                self.set_task_project(task_slug, project_slug)
            if goal_slug != task.goal:
                self.set_task_goal(task_slug, goal_slug)
            if assignee_slug != (task.owner_agent or "tony"):
                self._move_task_assignee(task_slug, assignee_slug, handoff_reason, now)
            if status != task.status:
                self.set_task_status(task_slug, status, now)
            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(stored_links, list):
                raise GBrainProtocolError("task edit readback was not structured")
            stored = Task.from_page(stored_page, edges=stored_links)
            if (
                stored.title != title.strip() or stored.detail != detail.strip()
                or stored.priority != priority or stored.due_day != due_day
                or stored.next_action != normalized_next_action
                or stored.next_action_history != desired_next_action_history
                or stored.project != project_slug
                or stored.parent != parent_slug
                or stored.goal != goal_slug or stored.status != status
                or stored.owner_agent != (None if assignee_slug == "tony" else assignee_slug)
                or stored.progress_metric != progress_metric or stored.event_progress != event_progress
            ):
                raise GBrainProtocolError("task edit readback did not match the requested values")
            if expected_body is not None:
                self._verify_compiled_markdown_body(
                    stored_page, expected_body, label="Task edit"
                )
            if status == "completed":
                stored = self._reconcile_completed_task_todos(
                    task_slug,
                    now=now,
                )
            return TaskEditReceipt(task_slug=task_slug, task=stored, verified=True)
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task_slug,
                "Task edit was not fully verified. Some requested fields may be unchanged; inspect the task before retrying. " + str(exc),
            ) from exc

    def _move_task_assignee(
        self, task_slug: str, assignee_slug: str, handoff_reason: str, now: datetime
    ) -> None:
        page = self.runner.run("get_page", {"slug": task_slug})
        links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("task reassignment snapshot was not structured")
        task = Task.from_page(page, edges=links)
        self._require_task_openclaw_activation(task)
        old_owner = task.owner_agent or "tony"
        old_root = task.lifecycle_root
        target_openclaw_root = self._require_openclaw_assignment_target(assignee_slug)
        if assignee_slug == "tony":
            target_root = ACTIVE_ROOT
        elif target_openclaw_root is not None:
            target_root = target_openclaw_root
        else:
            target_root = {
                agent.slug: agent.work_root
                for agent in self.list_agent_profiles().agents
            }[assignee_slug]
        frontmatter = deepcopy(dict(page.get("frontmatter") or {}))
        raw_frontmatter_links = frontmatter.get("links")
        if not isinstance(raw_frontmatter_links, list):
            raise GBrainProtocolError("task frontmatter links must be a list")
        retained = [
            link for link in raw_frontmatter_links
            if not (isinstance(link, Mapping) and (
                (link.get("type") == "member_of" and link.get("to") in TASK_SCOPE_ROOTS)
                or (link.get("type") == "assigned_to" and str(link.get("to", "")).startswith("agents/"))
            ))
        ]
        retained.append({"to": target_root, "type": "member_of", "context": "GTasks current work scope."})
        if assignee_slug != "tony":
            retained.append({"to": assignee_slug, "type": "assigned_to", "context": "Tony assigned this work to the canonical agent."})
        history = frontmatter.get("assignment_history")
        if not isinstance(history, list):
            history = []
        history.append({"from": old_owner, "to": assignee_slug, "actor": "tony", "at": now.isoformat(), "reason": handoff_reason.strip(), "status": task.status})
        frontmatter["type"] = "task"
        frontmatter["links"] = retained
        frontmatter["assignment_history"] = history[-100:]
        frontmatter["updated_at"] = now.isoformat()
        self.runner.run("put_page", {"slug": task_slug, "content": _render_preserved_task_page(page, frontmatter)})
        if target_root != old_root:
            self.runner.run("add_link", {"from": task_slug, "to": target_root, "link_type": "member_of", "context": "GTasks current work scope.", "link_source": "gtasks"})
        if assignee_slug != "tony":
            self.runner.run("add_link", {"from": task_slug, "to": assignee_slug, "link_type": "assigned_to", "context": "Tony assigned this work to the canonical agent.", "link_source": "gtasks"})
        if old_owner != "tony":
            self.runner.run("remove_link", {"from": task_slug, "to": old_owner, "link_type": "assigned_to"})
        if target_root != old_root:
            self.runner.run("remove_link", {"from": task_slug, "to": old_root, "link_type": "member_of"})
        read_page = self.runner.run("get_page", {"slug": task_slug})
        read_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(read_page, Mapping) or not isinstance(read_links, list):
            raise GBrainProtocolError("task reassignment readback was not structured")
        verified = Task.from_page(read_page, edges=read_links)
        if verified.lifecycle_root != target_root or verified.owner_agent != (None if assignee_slug == "tony" else assignee_slug):
            raise GBrainProtocolError("task reassignment retained a stale owner or collection membership")

    def set_task_status(
        self,
        task_slug: str,
        status: str,
        now: datetime,
    ) -> StatusMutationReceipt:
        if status not in EDITABLE_TASK_STATUSES:
            raise ValueError(
                f"status must be one of {', '.join(sorted(EDITABLE_TASK_STATUSES))}"
            )
        if now.tzinfo is None:
            raise ValueError("status update time must include Tony's local timezone")

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        if not isinstance(raw_page, Mapping):
            raise GBrainProtocolError("get_page did not return an object")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; "
                "repair the task type before changing status"
            )
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_links, list):
            raise GBrainProtocolError("get_links did not return a list")
        initial_lifecycle_edge = _require_single_lifecycle_edge(task_slug, raw_links)
        initial_lifecycle_edges = [initial_lifecycle_edge]
        initial_root = str(initial_lifecycle_edge["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            initial_root,
            legacy_untyped_backlink=False,
        )
        recovering_terminal_handoff = False
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raw_frontmatter = normalized_page.get("frontmatter")
            raw_handoff = (
                raw_frontmatter.get("handoff")
                if isinstance(raw_frontmatter, Mapping)
                else None
            )
            if not (
                status == "completed"
                and isinstance(raw_frontmatter, Mapping)
                and raw_frontmatter.get("status") == status
                and isinstance(raw_handoff, Mapping)
                and raw_handoff.get("state") == "agent_working"
            ):
                raise ValueError(str(exc)) from exc
            recovery_page = deepcopy(dict(normalized_page))
            recovery_frontmatter = deepcopy(dict(raw_frontmatter))
            recovery_frontmatter["status"] = "active"
            recovery_page["frontmatter"] = recovery_frontmatter
            try:
                task = Task.from_page(recovery_page, edges=normalized_links)
            except DomainValidationError as recovery_exc:
                raise ValueError(str(exc)) from recovery_exc
            recovering_terminal_handoff = True
        self._require_task_openclaw_activation(task)
        existing_lifecycle_edges = initial_lifecycle_edges
        existing_lifecycle_edge = _require_single_lifecycle_edge(
            task_slug, raw_links
        )
        if existing_lifecycle_edge.get("to_slug") != task.lifecycle_root:
            raise LifecycleIntegrityError(task_slug, existing_lifecycle_edges)

        if task.status == status and not recovering_terminal_handoff:
            if status == "completed":
                task = self._reconcile_completed_task_todos(task_slug, now=now)
            return StatusMutationReceipt(
                task_slug=task_slug,
                status=status,
                lifecycle_root=task.lifecycle_root,
                completed_at=task.completed_at,
                task=task,
                verified=True,
            )

        unfinished = status not in {"completed", "cancelled"}
        target_root = (
            ACTIVE_ROOT
            if task.lifecycle_root == COMPLETED_ROOT and unfinished
            else task.lifecycle_root
        )
        if recovering_terminal_handoff:
            completed_at = task.completed_at
        else:
            completed_at = now if status == "completed" else None

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        frontmatter = deepcopy(dict(raw_frontmatter))
        frontmatter["type"] = "task"
        frontmatter["status"] = status
        frontmatter["completed_at"] = (
            completed_at.isoformat() if completed_at else None
        )
        frontmatter["updated_at"] = now.isoformat()
        if (
            status == "completed"
            and task.handoff is not None
            and task.handoff.state == "agent_working"
        ):
            frontmatter["handoff"] = None

        if target_root != task.lifecycle_root:
            raw_frontmatter_links = frontmatter.get("links")
            if not isinstance(raw_frontmatter_links, list):
                raise GBrainProtocolError("task frontmatter links must be a list")
            replaced = 0
            for link in raw_frontmatter_links:
                if (
                    isinstance(link, dict)
                    and link.get("type") == "member_of"
                    and link.get("to") == task.lifecycle_root
                ):
                    link["to"] = target_root
                    replaced += 1
            if replaced != 1:
                raise GBrainProtocolError(
                    "task frontmatter lifecycle link could not be updated safely"
                )

        content = _render_preserved_task_page(raw_page, frontmatter)
        self.runner.run("put_page", {"slug": task_slug, "content": content})
        try:
            if target_root != task.lifecycle_root:
                self.runner.run(
                    "add_link",
                    {
                        "from": task_slug,
                        "to": target_root,
                        "link_type": "member_of",
                        "context": "GTasks active task membership.",
                        "link_source": "gtasks",
                    },
                )
                self.runner.run(
                    "remove_link",
                    {
                        "from": task_slug,
                        "to": task.lifecycle_root,
                        "link_type": "member_of",
                    },
                )

            stored_page = self.runner.run("get_page", {"slug": task_slug})
            if not isinstance(stored_page, Mapping):
                raise GBrainProtocolError("status get_page readback was not an object")
            if stored_page.get("type") != "task":
                raise GBrainProtocolError(
                    "status write changed the canonical page type away from task"
                )
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_links, list):
                raise GBrainProtocolError("status get_links readback was not a list")
            stored_task = Task.from_page(stored_page, edges=stored_links)
            if (
                stored_task.status != status
                or stored_task.lifecycle_root != target_root
                or stored_task.completed_at != completed_at
            ):
                raise GBrainProtocolError(
                    "status page readback did not match the requested update"
                )
            verified_lifecycle_edges = _lifecycle_edges(task_slug, stored_links)
            if (
                len(verified_lifecycle_edges) != 1
                or verified_lifecycle_edges[0].get("to_slug") != target_root
            ):
                raise GBrainProtocolError(
                    "status lifecycle edge readback did not match the task page"
                )
            if status == "completed":
                stored_task = self._reconcile_completed_task_todos(
                    task_slug,
                    now=now,
                )

            unrelated_edges = [
                link
                for link in raw_links
                if isinstance(link, Mapping)
                and link not in existing_lifecycle_edges
            ]
            for expected in unrelated_edges:
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "an unrelated task relationship was missing after readback"
                    )
        except (DomainValidationError, GBrainError) as exc:
            raise PartialMutationError(
                task_slug,
                f"Task status write was not verified by page and link readback: {exc}",
            ) from exc

        return StatusMutationReceipt(
            task_slug=task_slug,
            status=status,
            lifecycle_root=target_root,
            completed_at=completed_at,
            task=stored_task,
            verified=True,
        )

    def set_task_next_action(
        self,
        task_slug: str,
        next_action: str,
        now: datetime,
    ) -> NextActionMutationReceipt:
        if not isinstance(next_action, str):
            raise ValueError("next_action must be text")
        normalized_action = next_action.strip()
        if len(normalized_action) > 240:
            raise ValueError("next_action must be 240 characters or fewer")
        if "\n" in normalized_action or "\r" in normalized_action:
            raise ValueError("next_action must be a single concise line")
        if now.tzinfo is None:
            raise ValueError("next action update time must include Tony's local timezone")

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        if not isinstance(raw_page, Mapping):
            raise GBrainProtocolError("get_page did not return an object")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}; "
                "repair the task type before changing its next action"
            )
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_links, list):
            raise GBrainProtocolError("get_links did not return a list")
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        if task.lifecycle_root != lifecycle_root:
            raise ValueError(
                "task lifecycle relationship does not match its canonical page"
            )
        if task.next_action == normalized_action:
            return NextActionMutationReceipt(
                task_slug=task_slug,
                next_action=normalized_action,
                verified=True,
            )

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_frontmatter["next_action"] = normalized_action
        desired_next_action_history = _history_after_next_action_change(
            task,
            normalized_action,
            now,
        )
        desired_frontmatter["next_action_history"] = [
            entry.to_dict() for entry in desired_next_action_history
        ]
        desired_frontmatter["updated_at"] = now.isoformat()
        original_content = _render_preserved_task_page(raw_page, original_frontmatter)
        desired_content = _render_preserved_task_page(raw_page, desired_frontmatter)

        write_succeeded = False
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": desired_content},
            )
            write_succeeded = True
            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "next action readback was not structured"
                )
            if stored_page.get("type") != "task":
                raise GBrainProtocolError(
                    "next action write changed the canonical page type away from task"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            verified_lifecycle_edges = _lifecycle_edges(task_slug, stored_links)
            if (
                stored_task.next_action != normalized_action
                or stored_task.next_action_history != desired_next_action_history
                or stored_task.lifecycle_root != lifecycle_root
                or len(verified_lifecycle_edges) != 1
                or verified_lifecycle_edges[0].get("to_slug") != lifecycle_root
            ):
                raise GBrainProtocolError(
                    "next action page and lifecycle readback did not match the request"
                )
            unrelated_edges = [
                link
                for link in raw_links
                if isinstance(link, Mapping) and link not in lifecycle_edges
            ]
            for expected in unrelated_edges:
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "an unrelated task relationship was missing after readback"
                    )
        except (DomainValidationError, GBrainError) as exc:
            if not write_succeeded:
                raise
            rollback_verified = False
            try:
                self.runner.run(
                    "put_page",
                    {"slug": task_slug, "content": original_content},
                )
                rollback_page = self.runner.run("get_page", {"slug": task_slug})
                rollback_links = self.runner.run("get_links", {"slug": task_slug})
                if isinstance(rollback_page, Mapping) and isinstance(
                    rollback_links, list
                ):
                    rollback_task = Task.from_page(
                        rollback_page,
                        edges=rollback_links,
                    )
                    rollback_lifecycle = _lifecycle_edges(
                        task_slug,
                        rollback_links,
                    )
                    rollback_verified = (
                        rollback_page.get("type") == "task"
                        and rollback_task.next_action == task.next_action
                        and rollback_task.next_action_history
                        == task.next_action_history
                        and rollback_task.lifecycle_root == lifecycle_root
                        and len(rollback_lifecycle) == 1
                        and rollback_lifecycle[0].get("to_slug") == lifecycle_root
                    )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Task next action write was not verified. {outcome}",
            ) from exc

        return NextActionMutationReceipt(
            task_slug=task_slug,
            next_action=normalized_action,
            verified=True,
        )

    def set_task_progress_metric(
        self,
        task_slug: str,
        progress_metric: ProgressMetric | None,
        event_progress: EventProgress | None,
        now: datetime,
    ) -> TaskProgressMetricReceipt:
        if now.tzinfo is None:
            raise ValueError("progress metric update time must include timezone")
        if progress_metric is None and event_progress is not None:
            raise ValueError("event progress requires a progress metric")
        if progress_metric and progress_metric.event_binding:
            if (
                event_progress is None
                or progress_metric.current != event_progress.derived_current
            ):
                raise ValueError(
                    "event-bound metric current must match baseline plus unique event evidence"
                )
        elif event_progress is not None:
            raise ValueError(
                "event progress requires an event-bound progress metric"
            )

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("task metric snapshot was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}"
            )
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        if (
            progress_metric
            and progress_metric.event_binding
            and progress_metric.task_day != task.due_day
        ):
            raise ValueError(
                "event-bound progress metric task_day must match the task due day"
            )
        if (
            task.progress_metric == progress_metric
            and task.event_progress == event_progress
        ):
            return TaskProgressMetricReceipt(
                task_slug=task_slug,
                task=task,
                verified=True,
            )

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_frontmatter["progress_metric"] = (
            progress_metric.to_dict() if progress_metric else None
        )
        desired_frontmatter["event_progress"] = (
            event_progress.to_dict() if event_progress else None
        )
        desired_frontmatter["updated_at"] = now.isoformat()
        original_content = _render_preserved_task_page(
            raw_page,
            original_frontmatter,
        )
        desired_content = _render_preserved_task_page(
            raw_page,
            desired_frontmatter,
        )

        write_succeeded = False
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": desired_content},
            )
            write_succeeded = True
            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if (
                not isinstance(stored_page, Mapping)
                or stored_page.get("type") != "task"
                or not isinstance(stored_links, list)
            ):
                raise GBrainProtocolError(
                    "task metric readback was not structured"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            stored_lifecycle = _lifecycle_edges(task_slug, stored_links)
            if (
                stored_task.progress_metric != progress_metric
                or stored_task.event_progress != event_progress
                or stored_task.lifecycle_root != lifecycle_root
                or len(stored_lifecycle) != 1
                or stored_lifecycle[0].get("to_slug") != lifecycle_root
            ):
                raise GBrainProtocolError(
                    "task metric page and lifecycle readback did not match"
                )
            for expected in raw_links:
                if not isinstance(expected, Mapping):
                    continue
                if not any(
                    isinstance(actual, Mapping)
                    and actual.get("from_slug") == expected.get("from_slug")
                    and actual.get("to_slug") == expected.get("to_slug")
                    and actual.get("link_type") == expected.get("link_type")
                    for actual in stored_links
                ):
                    raise GBrainProtocolError(
                        "an existing task relationship was missing after metric update"
                    )
        except (DomainValidationError, GBrainError) as exc:
            if not write_succeeded:
                raise
            rollback_verified = False
            try:
                self.runner.run(
                    "put_page",
                    {"slug": task_slug, "content": original_content},
                )
                rollback_page = self.runner.run(
                    "get_page",
                    {"slug": task_slug},
                )
                rollback_links = self.runner.run(
                    "get_links",
                    {"slug": task_slug},
                )
                if isinstance(rollback_page, Mapping) and isinstance(
                    rollback_links, list
                ):
                    rollback_task = Task.from_page(
                        rollback_page,
                        edges=rollback_links,
                    )
                    rollback_verified = (
                        rollback_page.get("type") == "task"
                        and rollback_task.progress_metric
                        == task.progress_metric
                        and rollback_task.event_progress
                        == task.event_progress
                    )
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Task progress metric write was not verified. {outcome}",
            ) from exc

        return TaskProgressMetricReceipt(
            task_slug=task_slug,
            task=stored_task,
            verified=True,
        )

    def apply_task_progress_event(
        self,
        task_slug: str,
        *,
        event_binding: str,
        evidence_slug: str,
        receipt_id: str,
        now: datetime,
    ) -> TaskProgressEventReceipt:
        if now.tzinfo is None:
            raise ValueError("progress event time must include timezone")
        for field_name, value in (
            ("event_binding", event_binding),
            ("evidence_slug", evidence_slug),
            ("receipt_id", receipt_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")

        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("progress event task snapshot was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}"
            )
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        try:
            task = Task.from_page(normalized_page, edges=normalized_links)
        except DomainValidationError as exc:
            raise ValueError(str(exc)) from exc
        metric = task.progress_metric
        progress = task.event_progress
        if (
            metric is None
            or metric.event_binding != event_binding
            or progress is None
        ):
            raise ValueError("task progress event binding does not match")
        evidence_present = evidence_slug in progress.evidence_slugs
        receipt_present = receipt_id in progress.receipt_ids
        if evidence_present != receipt_present:
            raise ValueError(
                "progress event conflicts with existing evidence or receipt"
            )
        if evidence_present and receipt_present:
            if (
                progress.evidence_slugs.index(evidence_slug)
                != progress.receipt_ids.index(receipt_id)
            ):
                raise ValueError(
                    "progress event evidence and receipt pairing conflicts"
                )
            verified_task = task
            if (
                metric.current == metric.target
                and metric.auto_complete
                and task.status != "completed"
            ):
                verified_task = self.set_task_status(
                    task_slug,
                    "completed",
                    now,
                ).task
            return TaskProgressEventReceipt(
                task_slug=task_slug,
                task=verified_task,
                duplicate=True,
                verified=True,
            )
        if task.status in {"completed", "cancelled"}:
            raise ValueError("finished task cannot accept new progress events")
        if metric.current >= metric.target:
            raise ValueError("task progress target is already reached")

        updated_progress = EventProgress(
            baseline_count=progress.baseline_count,
            evidence_slugs=(*progress.evidence_slugs, evidence_slug),
            receipt_ids=(*progress.receipt_ids, receipt_id),
        )
        updated_metric = deepcopy(metric)
        updated_metric = ProgressMetric(
            kind=updated_metric.kind,
            label=updated_metric.label,
            unit=updated_metric.unit,
            target=updated_metric.target,
            current=updated_metric.current + 1,
            event_binding=updated_metric.event_binding,
            auto_complete=updated_metric.auto_complete,
            task_day=updated_metric.task_day,
            timezone=updated_metric.timezone,
        )
        metric_receipt = self.set_task_progress_metric(
            task_slug,
            updated_metric,
            updated_progress,
            now,
        )
        verified_task = metric_receipt.task
        if (
            updated_metric.current == updated_metric.target
            and updated_metric.auto_complete
        ):
            verified_task = self.set_task_status(
                task_slug,
                "completed",
                now,
            ).task
        return TaskProgressEventReceipt(
            task_slug=task_slug,
            task=verified_task,
            duplicate=False,
            verified=True,
        )

    def set_task_project(
        self,
        task_slug: str,
        project_slug: str | None,
    ) -> ProjectAssignmentReceipt:
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("task project snapshot was not structured")
        if raw_page.get("type") != "task":
            raise ValueError(
                f"task has unexpected page type {raw_page.get('type') or 'missing'}"
            )
        lifecycle_edges = _lifecycle_edges(task_slug, raw_links)
        if len(lifecycle_edges) != 1:
            raise ValueError(
                "task does not have exactly one verified approved lifecycle edge"
            )
        lifecycle_root = str(lifecycle_edges[0]["to_slug"])
        normalized_page, normalized_links, _warnings = _normalize_collection_task(
            raw_page,
            raw_links,
            lifecycle_root,
            legacy_untyped_backlink=False,
        )
        task = Task.from_page(normalized_page, edges=normalized_links)
        approved_projects = {
            project.slug for project in self.list_projects().projects
        }
        if project_slug is not None and project_slug not in approved_projects:
            raise ValueError("project is not a durable member of Tony's Projects")
        current_project = task.project
        if current_project == project_slug:
            return ProjectAssignmentReceipt(
                task_slug=task_slug,
                project_slug=project_slug,
                verified=True,
            )

        raw_frontmatter = normalized_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("task page has no frontmatter")
        original_frontmatter = deepcopy(dict(raw_frontmatter))
        original_frontmatter["type"] = "task"
        desired_frontmatter = deepcopy(original_frontmatter)
        desired_links = desired_frontmatter.get("links")
        if not isinstance(desired_links, list):
            raise GBrainProtocolError("task frontmatter links must be a list")
        desired_links = [
            link
            for link in deepcopy(desired_links)
            if not (
                isinstance(link, Mapping)
                and link.get("type") == "member_of"
                and link.get("to") not in LIFECYCLE_ROOTS
            )
        ]
        if project_slug is not None:
            desired_links.append({"to": project_slug, "type": "member_of"})
        desired_frontmatter["links"] = desired_links
        desired_frontmatter["project"] = project_slug
        original_content = _render_preserved_task_page(raw_page, original_frontmatter)
        desired_content = _render_preserved_task_page(raw_page, desired_frontmatter)
        journal: list[str] = []
        try:
            self.runner.run(
                "put_page",
                {"slug": task_slug, "content": desired_content},
            )
            journal.append("put_page")
            if project_slug is not None:
                self.runner.run(
                    "add_link",
                    {
                        "from": task_slug,
                        "to": project_slug,
                        "link_type": "member_of",
                        "context": "GTasks task project assignment.",
                        "link_source": "gtasks",
                    },
                )
                journal.append("add_new")
            if current_project is not None:
                self.runner.run(
                    "remove_link",
                    {
                        "from": task_slug,
                        "to": current_project,
                        "link_type": "member_of",
                    },
                )
                journal.append("remove_old")
            stored_page = self.runner.run("get_page", {"slug": task_slug})
            stored_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(stored_page, Mapping) or not isinstance(
                stored_links, list
            ):
                raise GBrainProtocolError(
                    "task project readback was not structured"
                )
            stored_task = Task.from_page(stored_page, edges=stored_links)
            verified_project_edges = [
                edge
                for edge in stored_links
                if isinstance(edge, Mapping)
                and edge.get("from_slug") == task_slug
                and edge.get("link_type") == "member_of"
                and edge.get("to_slug") not in APPROVED_ROOTS
            ]
            expected_projects = [project_slug] if project_slug else []
            if (
                stored_page.get("type") != "task"
                or stored_task.project != project_slug
                or [edge.get("to_slug") for edge in verified_project_edges]
                != expected_projects
                or len(_lifecycle_edges(task_slug, stored_links)) != 1
            ):
                raise GBrainProtocolError(
                    "task project page and relationship readback did not match"
                )
        except (DomainValidationError, GBrainError) as exc:
            rollback_verified = False
            try:
                if "remove_old" in journal and current_project is not None:
                    self.runner.run(
                        "add_link",
                        {
                            "from": task_slug,
                            "to": current_project,
                            "link_type": "member_of",
                            "context": "Restored GTasks project assignment.",
                            "link_source": "gtasks",
                        },
                    )
                if "add_new" in journal and project_slug is not None:
                    self.runner.run(
                        "remove_link",
                        {
                            "from": task_slug,
                            "to": project_slug,
                            "link_type": "member_of",
                        },
                    )
                if "put_page" in journal:
                    self.runner.run(
                        "put_page",
                        {"slug": task_slug, "content": original_content},
                    )
                rollback_page = self.runner.run("get_page", {"slug": task_slug})
                rollback_links = self.runner.run("get_links", {"slug": task_slug})
                rollback_task = Task.from_page(
                    rollback_page,
                    edges=rollback_links,
                )
                rollback_verified = rollback_task.project == current_project
            except (DomainValidationError, GBrainError):
                rollback_verified = False
            outcome = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified; inspect the task before retrying."
            )
            raise PartialMutationError(
                task_slug,
                f"Task project assignment was not verified. {outcome}",
            ) from exc
        return ProjectAssignmentReceipt(
            task_slug=task_slug,
            project_slug=project_slug,
            verified=True,
        )

    def set_task_goal(
        self,
        task_slug: str,
        goal_slug: str | None,
    ) -> GoalLinkReceipt:
        task = self._approved_task(task_slug)
        goal_read = self.list_goals()
        approved_goals = {goal.slug: goal for goal in goal_read.goals}
        if goal_slug is not None and goal_slug not in approved_goals:
            raise ValueError("goal is not a member of Tony's Goals")
        if task.goal is not None and task.goal not in approved_goals:
            raise ValueError("current goal is not a member of Tony's Goals")

        pre_forward = {task.goal} if task.goal else set()
        desired = {goal_slug} if goal_slug else set()
        relevant = pre_forward | desired
        pre_reverse: set[str] = set()
        for selected in [
            goal.slug for goal in goal_read.goals if goal.slug in relevant
        ]:
            raw_goal_links = self.runner.run("get_links", {"slug": selected})
            if not isinstance(raw_goal_links, list):
                raise GBrainProtocolError(
                    "goal reciprocal relationship snapshot was not a list"
                )
            if any(
                isinstance(link, Mapping)
                and link.get("from_slug") == selected
                and link.get("to_slug") == task_slug
                and link.get("link_type") == "advanced_by"
                for link in raw_goal_links
            ):
                pre_reverse.add(selected)
        involved = [
            goal.slug
            for goal in goal_read.goals
            if goal.slug in pre_reverse | desired | pre_forward
        ]

        forward_descriptor = lambda selected: {
            "from": task_slug,
            "to": selected,
            "link_type": "advances_goal",
            "context": "This task advances the linked Tony goal.",
            "link_source": "gtasks",
        }
        reverse_descriptor = lambda selected: {
            "from": selected,
            "to": task_slug,
            "link_type": "advanced_by",
            "context": "This goal is advanced by the linked GTasks task.",
            "link_source": "gtasks",
        }
        journal: list[tuple[str, dict[str, Any]]] = []

        def apply(action: str, descriptor: dict[str, Any]) -> None:
            params = dict(descriptor)
            if action == "remove_link":
                params.pop("context", None)
                params.pop("link_source", None)
            self.runner.run(action, params)
            journal.append((action, descriptor))

        def read_state() -> tuple[set[str], set[str]]:
            raw_task_links = self.runner.run("get_links", {"slug": task_slug})
            if not isinstance(raw_task_links, list):
                raise GBrainProtocolError(
                    "task goal relationship readback was not a list"
                )
            forward = {
                str(link["to_slug"])
                for link in raw_task_links
                if isinstance(link, Mapping)
                and link.get("from_slug") == task_slug
                and link.get("link_type") == "advances_goal"
                and isinstance(link.get("to_slug"), str)
            }
            reverse: set[str] = set()
            for selected in involved:
                raw_goal_links = self.runner.run(
                    "get_links",
                    {"slug": selected},
                )
                if not isinstance(raw_goal_links, list):
                    raise GBrainProtocolError(
                        "goal reciprocal relationship readback was not a list"
                    )
                if any(
                    isinstance(link, Mapping)
                    and link.get("from_slug") == selected
                    and link.get("to_slug") == task_slug
                    and link.get("link_type") == "advanced_by"
                    for link in raw_goal_links
                ):
                    reverse.add(selected)
            return forward, reverse

        try:
            for selected in desired - pre_forward:
                apply("add_link", forward_descriptor(selected))
            for selected in desired - pre_reverse:
                apply("add_link", reverse_descriptor(selected))
            for selected in pre_forward - desired:
                apply("remove_link", forward_descriptor(selected))
            for selected in pre_reverse - desired:
                apply("remove_link", reverse_descriptor(selected))

            final_forward, final_reverse = read_state()
            if final_forward != desired or final_reverse != desired:
                raise GBrainProtocolError(
                    "final bidirectional goal readback did not match selection"
                )
        except GBrainError as exc:
            rollback_commands_ok = True
            for action, descriptor in reversed(journal):
                inverse = "remove_link" if action == "add_link" else "add_link"
                params = dict(descriptor)
                if inverse == "remove_link":
                    params.pop("context", None)
                    params.pop("link_source", None)
                try:
                    self.runner.run(inverse, params)
                except GBrainError:
                    rollback_commands_ok = False
            rollback_verified = False
            if rollback_commands_ok:
                try:
                    rollback_forward, rollback_reverse = read_state()
                    rollback_verified = (
                        rollback_forward == pre_forward
                        and rollback_reverse == pre_reverse
                    )
                except GBrainError:
                    rollback_verified = False
            rollback_message = (
                "Rollback verified."
                if rollback_verified
                else "Rollback could not be verified."
            )
            raise PartialMutationError(
                task_slug,
                f"Bidirectional goal relationship write failed: {exc} "
                f"{rollback_message}",
            ) from exc

        return GoalLinkReceipt(
            task_slug=task_slug,
            goal_slug=goal_slug,
            verified=True,
            reciprocal_verified=True,
            reconciled=(task.goal == goal_slug and bool(journal)),
        )

    @staticmethod
    def _todo_identity(prefix: str, scope: str, idempotency_key: str) -> str:
        identity = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"mission-control:{prefix}:{scope}:{idempotency_key}",
        )
        return f"{prefix}/{identity}"

    @staticmethod
    def _validate_todo_actor_source(
        *,
        actor: str | None,
        source: str,
        task: Task | None = None,
    ) -> None:
        if source not in {"mission_control", "agent", "legacy_next_action"}:
            raise ValueError("todo source is invalid")
        if source == "mission_control" and actor != TONY_PROFILE_SLUG:
            raise ValueError("Mission Control TODO mutations require Tony as actor")
        if source == "agent":
            if not isinstance(actor, str) or not actor.startswith("agents/"):
                raise ValueError("agent TODO mutations require a canonical agent actor")
            if task is not None and task.owner_agent != actor:
                raise ValueError("agent question actor must match the parent task owner")
        if source == "legacy_next_action" and actor is not None:
            raise ValueError("legacy migration does not invent an original actor")

    @staticmethod
    def _validate_todo_timestamp(value: datetime, field: str) -> None:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{field} must include a timezone")

    @staticmethod
    def _normalize_todo_text(text: str, detail: str) -> tuple[str, str]:
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.strip()) > 240
            or "\n" in text.strip()
            or "\r" in text.strip()
        ):
            raise ValueError("todo text must be one line of 1 to 240 characters")
        if not isinstance(detail, str) or len(detail.strip()) > 5000:
            raise ValueError("todo detail must be text up to 5000 characters")
        return text.strip(), detail.strip()

    @staticmethod
    def _normalize_idempotency_key(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value.strip()) > 200
            or "\n" in value
            or "\r" in value
        ):
            raise ValueError("idempotency_key must be one line of 1 to 200 characters")
        return value.strip()

    @staticmethod
    def _page_not_found(exc: GBrainCommandError) -> bool:
        return is_page_not_found_error(exc)

    def _read_todo_comment(
        self,
        slug: str,
        *,
        verified_edges: Sequence[Mapping[str, Any]] | None = None,
    ) -> TodoComment:
        with self._todo_child_cache_lock:
            cached = self._todo_comment_cache.get(slug)
        if cached is not None:
            return cached
        page = self.runner.run("get_page", {"slug": slug})
        links = (
            self.runner.run("get_links", {"slug": slug})
            if verified_edges is None
            else list(verified_edges)
        )
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("todo comment readback was not structured")
        stored = TodoComment.from_page(page, edges=links)
        with self._todo_child_cache_lock:
            self._todo_comment_cache.setdefault(slug, stored)
            return self._todo_comment_cache[slug]

    def _read_todo_event(
        self,
        slug: str,
        *,
        verified_edges: Sequence[Mapping[str, Any]] | None = None,
    ) -> TodoEvent:
        with self._todo_child_cache_lock:
            cached = self._todo_event_cache.get(slug)
        if cached is not None:
            return cached
        page = self.runner.run("get_page", {"slug": slug})
        links = (
            self.runner.run("get_links", {"slug": slug})
            if verified_edges is None
            else list(verified_edges)
        )
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("todo event readback was not structured")
        stored = TodoEvent.from_page(page, edges=links)
        with self._todo_child_cache_lock:
            self._todo_event_cache.setdefault(slug, stored)
            return self._todo_event_cache[slug]

    def _optional_todo_event(self, slug: str) -> TodoEvent | None:
        try:
            return self._read_todo_event(slug)
        except GBrainCommandError as exc:
            if self._page_not_found(exc):
                return None
            raise

    def _optional_todo_comment(self, slug: str) -> TodoComment | None:
        try:
            return self._read_todo_comment(slug)
        except GBrainCommandError as exc:
            if self._page_not_found(exc):
                return None
            raise

    def _read_todo_snapshot(
        self,
        slug: str,
    ) -> tuple[TodoItem, Mapping[str, Any], list[Any]]:
        page = self.runner.run("get_page", {"slug": slug})
        links = self.runner.run("get_links", {"slug": slug})
        if not isinstance(page, Mapping) or not isinstance(links, list):
            raise GBrainProtocolError("todo page or relationships were not structured")
        shallow = TodoItem.from_page(page, edges=links)
        history_slugs = set((*shallow.comment_slugs, *shallow.event_slugs))
        history_edges: dict[str, list[Mapping[str, Any]]] = {
            slug: [] for slug in history_slugs
        }
        if history_slugs:
            raw_backlinks = self.runner.run("get_backlinks", {"slug": slug})
            if not isinstance(raw_backlinks, list):
                raise GBrainProtocolError("todo history backlinks were not a list")
            for edge in raw_backlinks:
                if not isinstance(edge, Mapping):
                    continue
                child_slug = edge.get("from_slug")
                if isinstance(child_slug, str) and child_slug in history_edges:
                    history_edges[child_slug].append(edge)
        comments = tuple(
            self._bounded_map(
                lambda child_slug: self._read_todo_comment(
                    child_slug,
                    verified_edges=history_edges[child_slug],
                ),
                list(shallow.comment_slugs),
            )
        )
        events = tuple(
            self._bounded_map(
                lambda child_slug: self._read_todo_event(
                    child_slug,
                    verified_edges=history_edges[child_slug],
                ),
                list(shallow.event_slugs),
            )
        )
        todo = TodoItem.from_page(
            page,
            edges=links,
            comments=comments,
            events=events,
        )
        return todo, page, links

    def _read_todo(self, slug: str) -> TodoItem:
        todo, _page, _links = self._read_todo_snapshot(slug)
        return todo

    def _optional_todo(self, slug: str) -> TodoItem | None:
        try:
            return self._read_todo(slug)
        except GBrainCommandError as exc:
            if self._page_not_found(exc):
                return None
            raise

    def list_task_todos(
        self,
        task_slug: str,
        *,
        status: str | None = None,
        cursor: int = 0,
        limit: int = 50,
    ) -> TodoRead:
        task = self.get_task(task_slug)
        return self._list_task_todos_for_task(
            task,
            status=status,
            cursor=cursor,
            limit=limit,
        )

    def _list_task_todos_for_task(
        self,
        task: Task,
        *,
        status: str | None = None,
        cursor: int = 0,
        limit: int = 50,
    ) -> TodoRead:
        if status is not None and status not in {"not_done", "done"}:
            raise ValueError("todo status filter must be not_done or done")
        if not isinstance(cursor, int) or cursor < 0:
            raise ValueError("todo cursor must be a nonnegative integer")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("todo limit must be between 1 and 100")
        backlinks = self.runner.run("get_backlinks", {"slug": task.slug})
        if not isinstance(backlinks, list):
            raise GBrainProtocolError("todo parent backlinks were not a list")
        slugs = tuple(
            dict.fromkeys(
                str(edge["from_slug"])
                for edge in backlinks
                if isinstance(edge, Mapping)
                and edge.get("to_slug") == task.slug
                and edge.get("link_type") == "todo_for"
                and isinstance(edge.get("from_slug"), str)
                and str(edge["from_slug"]).startswith("todos/")
            )
        )
        def read(slug: str) -> tuple[TodoItem | None, CollectionIssue | None]:
            try:
                todo = self._read_todo(slug)
                if todo.parent_task != task.slug:
                    raise DomainValidationError("todo parent readback did not match task")
                if status is None or todo.status == status:
                    return todo, None
            except (DomainValidationError, GBrainError) as exc:
                return None, CollectionIssue(
                        slug=slug,
                        message=str(exc),
                        category="todo_data",
                        impact="This malformed TODO remains canonical but is omitted until repaired.",
                )
            return None, None
        todos: list[TodoItem] = []
        issues: list[CollectionIssue] = []
        for todo, issue in self._bounded_map(read, list(slugs)):
            if todo is not None:
                todos.append(todo)
            if issue is not None:
                issues.append(issue)
        todos.sort(
            key=lambda todo: (
                todo.status == "done",
                todo.created_at,
                todo.slug,
            )
        )
        page = tuple(todos[cursor : cursor + limit])
        next_cursor = cursor + limit if cursor + limit < len(todos) else None
        return TodoRead(page, tuple(issues), next_cursor)

    def enrich_tasks_with_todos(
        self,
        tasks: Sequence[Task],
    ) -> tuple[tuple[Task, ...], tuple[CollectionIssue, ...]]:
        def enrich(task: Task) -> tuple[Task, tuple[CollectionIssue, ...]]:
            try:
                todo_read = self._list_task_todos_for_task(task, limit=100)
                return replace(task, todos=todo_read.todos), todo_read.issues
            except (DomainValidationError, GBrainError, ValueError) as exc:
                return task, (
                    CollectionIssue(
                        slug=task.slug,
                        message=str(exc),
                        category="todo_data",
                        impact=(
                            "The task remains visible, but its canonical TODO list "
                            "is unavailable."
                        ),
                    ),
                )
        enriched: list[Task] = []
        issues: list[CollectionIssue] = []
        for task, task_issues in self._bounded_map(enrich, list(tasks)):
            enriched.append(task)
            issues.extend(task_issues)
        return tuple(enriched), tuple(issues)

    def _write_todo_event(self, event: TodoEvent) -> None:
        self.runner.run(
            "put_page",
            {"slug": event.slug, "content": render_todo_event_page(event)},
        )
        self.runner.run(
            "add_link",
            {
                "from": event.slug,
                "to": event.todo_slug,
                "link_type": "event_for",
                "context": "Durable audit history for this TODO.",
                "link_source": "gtasks",
            },
        )
        if self._read_todo_event(event.slug) != event:
            raise GBrainProtocolError("todo event readback did not match the write")

    def _write_todo_comment(self, comment: TodoComment) -> None:
        self.runner.run(
            "put_page",
            {"slug": comment.slug, "content": render_todo_comment_page(comment)},
        )
        self.runner.run(
            "add_link",
            {
                "from": comment.slug,
                "to": comment.todo_slug,
                "link_type": "comment_on",
                "context": "Append-only comment on this TODO.",
                "link_source": "gtasks",
            },
        )
        if self._read_todo_comment(comment.slug) != comment:
            raise GBrainProtocolError("todo comment readback did not match the write")

    @staticmethod
    def _completion_time(todo: TodoItem) -> datetime:
        for event in reversed(todo.events):
            if (
                event.event_type in {"status_changed", "legacy_migrated"}
                and isinstance(event.after, Mapping)
                and event.after.get("status") == "done"
            ):
                return event.occurred_at
        return todo.updated_at

    @staticmethod
    def _links_preserved(
        expected: list[Mapping[str, Any]],
        actual: list[Mapping[str, Any]],
    ) -> bool:
        return all(
            any(
                candidate.get("from_slug") == edge.get("from_slug")
                and candidate.get("to_slug") == edge.get("to_slug")
                and candidate.get("link_type") == edge.get("link_type")
                for candidate in actual
                if isinstance(candidate, Mapping)
            )
            for edge in expected
            if isinstance(edge, Mapping)
        )

    def _sync_task_todo_projection(self, task_slug: str) -> None:
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("todo parent projection snapshot was not structured")
        frontmatter = raw_page.get("frontmatter")
        if not isinstance(frontmatter, Mapping):
            raise GBrainProtocolError("todo parent projection has no frontmatter")
        todos = self.list_task_todos(task_slug, limit=100).todos
        open_items = [todo for todo in todos if todo.status == "not_done"]
        done_items = [todo for todo in todos if todo.status == "done"]
        desired_next_action = open_items[0].text if open_items else ""
        desired_history = [
            NextActionHistoryEntry(
                action=todo.text,
                completed_at=self._completion_time(todo),
            ).to_dict()
            for todo in done_items
        ]
        if (
            frontmatter.get("next_action", "") == desired_next_action
            and frontmatter.get("next_action_history", []) == desired_history
            and frontmatter.get("todo_projection_version") == 1
        ):
            return
        changed = deepcopy(dict(frontmatter))
        changed["next_action"] = desired_next_action
        changed["next_action_history"] = desired_history
        changed["todo_projection_version"] = 1
        self.runner.run(
            "put_page",
            {
                "slug": task_slug,
                "content": _render_preserved_task_page(raw_page, changed),
            },
        )
        stored_page = self.runner.run("get_page", {"slug": task_slug})
        stored_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(stored_page, Mapping) or not isinstance(stored_links, list):
            raise GBrainProtocolError("todo compatibility projection readback was not structured")
        stored = Task.from_page(stored_page, edges=stored_links)
        if (
            stored.next_action != desired_next_action
            or [entry.to_dict() for entry in stored.next_action_history] != desired_history
            or not self._links_preserved(raw_links, stored_links)
        ):
            raise GBrainProtocolError(
                "todo compatibility projection or parent relationships did not read back"
            )

    def _reconcile_completed_task_todos(
        self,
        task_slug: str,
        *,
        now: datetime,
    ) -> Task:
        todos = self.list_task_todos(task_slug, limit=100).todos
        for todo in todos:
            if todo.status != "not_done":
                continue
            self.set_todo_status(
                todo.slug,
                status="done",
                expected_updated_at=todo.updated_at,
                actor=TONY_PROFILE_SLUG,
                source="mission_control",
                idempotency_key=f"parent-task-completed:{task_slug}",
                now=now,
            )
        raw_page = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_page, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError(
                "completed task TODO reconciliation readback was not structured"
            )
        stored = Task.from_page(raw_page, edges=raw_links)
        remaining = [
            todo.slug
            for todo in self.list_task_todos(task_slug, limit=100).todos
            if todo.status == "not_done"
        ]
        if stored.status == "completed" and remaining:
            raise GBrainProtocolError(
                "completed task retained unreconciled not_done TODOs"
            )
        return stored

    def _delete_child_page(self, slug: str) -> bool:
        with self._todo_child_cache_lock:
            self._todo_comment_cache.pop(slug, None)
            self._todo_event_cache.pop(slug, None)
        try:
            self.runner.run("delete_page", {"slug": slug})
            return True
        except GBrainCommandError as exc:
            if self._page_not_found(exc):
                return True
            return False
        except GBrainError:
            return False

    def _restore_page(self, page: Mapping[str, Any]) -> bool:
        frontmatter = page.get("frontmatter")
        slug = page.get("slug")
        if not isinstance(slug, str) or not isinstance(frontmatter, Mapping):
            return False
        try:
            self.runner.run(
                "put_page",
                {
                    "slug": slug,
                    "content": _render_preserved_page(page, deepcopy(dict(frontmatter))),
                },
            )
            return True
        except GBrainError:
            return False

    def _create_todo_record(
        self,
        task: Task,
        *,
        text: str,
        detail: str,
        status: str,
        kind: str,
        actor: str | None,
        source: str,
        idempotency_key: str,
        created_at: datetime,
        updated_at: datetime,
        legacy_provenance: Mapping[str, Any] | None = None,
        event_type: str = "created",
        sync_projection: bool = True,
    ) -> TodoMutationReceipt:
        self._require_task_openclaw_activation(task)
        normalized_text, normalized_detail = self._normalize_todo_text(text, detail)
        key = self._normalize_idempotency_key(idempotency_key)
        self._validate_todo_timestamp(created_at, "todo created_at")
        self._validate_todo_timestamp(updated_at, "todo updated_at")
        if updated_at < created_at:
            raise ValueError("todo updated_at cannot precede created_at")
        if status not in {"not_done", "done"}:
            raise ValueError("todo status must be not_done or done")
        if kind not in {"action", "question", "blocker"}:
            raise ValueError("todo kind is invalid")
        self._validate_todo_actor_source(actor=actor, source=source, task=task)
        slug = self._todo_identity("todos", task.slug, key)
        event_slug = self._todo_identity("todo-events", slug, f"{event_type}:{key}")
        existing = self._optional_todo(slug)
        if existing is not None:
            if (
                existing.parent_task != task.slug
                or existing.text != normalized_text
                or existing.detail != normalized_detail
                or existing.status != status
                or existing.kind != kind
                or existing.source != source
                or existing.creator != actor
            ):
                raise ValueError("idempotency_key already identifies a different TODO request")
            return TodoMutationReceipt(existing, True, idempotent=True)
        parent_page = self.runner.run("get_page", {"slug": task.slug})
        if not isinstance(parent_page, Mapping):
            raise GBrainProtocolError("todo parent snapshot was not structured")
        event = TodoEvent(
            slug=event_slug,
            todo_slug=slug,
            event_type=event_type,
            actor=actor,
            source=source,
            occurred_at=updated_at,
            idempotency_key=key,
            before=None,
            after={
                "text": normalized_text,
                "detail": normalized_detail,
                "status": status,
                "kind": kind,
            },
        )
        todo = TodoItem(
            slug=slug,
            parent_task=task.slug,
            text=normalized_text,
            detail=normalized_detail,
            status=status,
            kind=kind,
            created_at=created_at,
            updated_at=updated_at,
            creator=actor,
            source=source,
            event_slugs=(event.slug,),
            legacy_provenance=(
                dict(legacy_provenance) if legacy_provenance is not None else None
            ),
        )
        created_pages: list[str] = []
        try:
            self.runner.run(
                "put_page", {"slug": todo.slug, "content": render_todo_page(todo)}
            )
            created_pages.append(todo.slug)
            self.runner.run(
                "add_link",
                {
                    "from": todo.slug,
                    "to": task.slug,
                    "link_type": "todo_for",
                    "context": "Canonical parent task for this TODO.",
                    "link_source": "gtasks",
                },
            )
            created_pages.append(event.slug)
            self._write_todo_event(event)
            stored = self._read_todo(todo.slug)
            if stored.to_dict() != replace(todo, events=(event,)).to_dict():
                raise GBrainProtocolError("todo creation readback did not match the write")
            if sync_projection:
                self._sync_task_todo_projection(task.slug)
            return TodoMutationReceipt(stored, True)
        except (DomainValidationError, GBrainError) as exc:
            rollback_ok = all(
                self._delete_child_page(created)
                for created in reversed(created_pages)
            )
            rollback_ok = self._restore_page(parent_page) and rollback_ok
            raise PartialMutationError(
                slug,
                "TODO creation failed. "
                + ("Rollback verified." if rollback_ok else "Rollback could not be verified."),
            ) from exc

    def create_todo(
        self,
        task_slug: str,
        *,
        text: str,
        detail: str,
        kind: str,
        actor: str,
        source: str,
        idempotency_key: str,
        now: datetime,
    ) -> TodoMutationReceipt:
        task = self.get_task(task_slug)
        raw_task = self.runner.run("get_page", {"slug": task_slug})
        raw_frontmatter = (
            raw_task.get("frontmatter") if isinstance(raw_task, Mapping) else None
        )
        if (
            isinstance(raw_frontmatter, Mapping)
            and raw_frontmatter.get("todo_projection_version") != 1
            and (task.next_action or task.next_action_history)
        ):
            self.migrate_legacy_next_actions(task_slug, now=now)
            task = self.get_task(task_slug)
        return self._create_todo_record(
            task,
            text=text,
            detail=detail,
            status="not_done",
            kind=kind,
            actor=actor,
            source=source,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _handoff_frontmatter(
        raw_page: Mapping[str, Any],
        *,
        status: str,
        next_action: str,
        handoff: TaskHandoff | None,
        now: datetime,
        add_blocker: str | None = None,
        remove_blocker: str | None = None,
    ) -> dict[str, Any]:
        raw_frontmatter = raw_page.get("frontmatter")
        if not isinstance(raw_frontmatter, Mapping):
            raise GBrainProtocolError("handoff task page has no frontmatter")
        frontmatter = deepcopy(dict(raw_frontmatter))
        links = frontmatter.get("links")
        if not isinstance(links, list):
            raise GBrainProtocolError("handoff task links must be a list")
        retained: list[dict[str, Any]] = []
        for raw_link in links:
            if not isinstance(raw_link, Mapping):
                raise GBrainProtocolError("handoff task link is not structured")
            link = dict(raw_link)
            if (
                remove_blocker is not None
                and link.get("type") == "blocked_by"
                and link.get("to") == remove_blocker
            ):
                continue
            retained.append(link)
        if add_blocker is not None and not any(
            link.get("type") == "blocked_by" and link.get("to") == add_blocker
            for link in retained
        ):
            retained.append(
                {
                    "to": add_blocker,
                    "type": "blocked_by",
                    "context": "Agent work is blocked pending Tony's answer.",
                }
            )
        frontmatter.update(
            {
                "type": "task",
                "status": status,
                "next_action": next_action,
                "handoff": handoff.to_dict() if handoff else None,
                "updated_at": now.isoformat(),
                "links": retained,
            }
        )
        return frontmatter

    @staticmethod
    def _has_typed_edge(
        links: Sequence[Mapping[str, Any]],
        *,
        from_slug: str,
        to_slug: str,
        link_type: str,
    ) -> bool:
        return any(
            isinstance(edge, Mapping)
            and edge.get("from_slug") == from_slug
            and edge.get("to_slug") == to_slug
            and edge.get("link_type") == link_type
            for edge in links
        )

    def request_agent_input(
        self,
        task_slug: str,
        *,
        question: str,
        question_detail: str,
        resume_action: str,
        agent_slug: str,
        idempotency_key: str,
        now: datetime,
    ) -> HandoffMutationReceipt:
        normalized_question, normalized_detail = self._normalize_todo_text(
            question, question_detail
        )
        normalized_resume, _unused = self._normalize_todo_text(resume_action, "")
        key = self._normalize_idempotency_key(idempotency_key)
        self._validate_todo_timestamp(now, "handoff requested_at")
        raw_task = self.runner.run("get_page", {"slug": task_slug})
        raw_task_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_task, Mapping) or not isinstance(raw_task_links, list):
            raise GBrainProtocolError("handoff task snapshot was not structured")
        task = Task.from_page(raw_task, edges=raw_task_links)
        self._require_task_openclaw_activation(task)
        if task.owner_agent != agent_slug:
            raise ValueError("question Agent must match the task's assigned Agent")
        if task.status in {"proposed", "completed", "cancelled"}:
            raise ValueError("only authorized unfinished Agent work can request input")
        expected_todo_slug = self._todo_identity("todos", task.slug, key)
        if task.handoff and task.handoff.state == "waiting_for_input":
            existing = self._read_todo(task.handoff.question_todo)
            if (
                task.handoff.question_todo == expected_todo_slug
                and existing.text == normalized_question
                and existing.detail == normalized_detail
                and task.handoff.resume_action == normalized_resume
            ):
                return HandoffMutationReceipt(
                    task=task,
                    todo=existing,
                    event=existing.events[-1] if existing.events else None,
                    next_owner=task.handoff.waiting_on,
                    verified=True,
                    idempotent=True,
                )
            raise ValueError("task already has an unanswered blocking question")
        round_number = task.handoff.round + 1 if task.handoff else 1
        created: TodoMutationReceipt | None = None
        blocker_preexisted = self._has_typed_edge(
            raw_task_links,
            from_slug=task.slug,
            to_slug=TONY_PROFILE_SLUG,
            link_type="blocked_by",
        )
        try:
            created = self._create_todo_record(
                task,
                text=normalized_question,
                detail=normalized_detail,
                status="not_done",
                kind="question",
                actor=agent_slug,
                source="agent",
                idempotency_key=key,
                created_at=now,
                updated_at=now,
                sync_projection=False,
            )
            handoff = TaskHandoff(
                state="waiting_for_input",
                question_todo=created.todo.slug,
                waiting_on=TONY_PROFILE_SLUG,
                resume_owner=agent_slug,
                resume_action=normalized_resume,
                requested_at=now,
                answered_at=None,
                acknowledged_at=None,
                round=round_number,
            )
            frontmatter = self._handoff_frontmatter(
                raw_task,
                status="blocked",
                next_action=normalized_question,
                handoff=handoff,
                now=now,
                add_blocker=TONY_PROFILE_SLUG,
            )
            self.runner.run(
                "put_page",
                {
                    "slug": task.slug,
                    "content": _render_preserved_task_page(raw_task, frontmatter),
                },
            )
            if not blocker_preexisted:
                self.runner.run(
                    "add_link",
                    {
                        "from": task.slug,
                        "to": TONY_PROFILE_SLUG,
                        "link_type": "blocked_by",
                        "context": "Agent work is blocked pending Tony's answer.",
                        "link_source": "gtasks",
                    },
                )
            stored_task = self.get_task(task.slug)
            stored_todo = self._read_todo(created.todo.slug)
            if (
                stored_task.status != "blocked"
                or stored_task.handoff != handoff
                or stored_todo.to_dict() != created.todo.to_dict()
                or TONY_PROFILE_SLUG not in stored_task.blockers
            ):
                raise GBrainProtocolError("blocking question readback did not match")
            return HandoffMutationReceipt(
                task=stored_task,
                todo=stored_todo,
                event=stored_todo.events[-1] if stored_todo.events else None,
                next_owner=TONY_PROFILE_SLUG,
                verified=True,
                idempotent=created.idempotent,
            )
        except (DomainValidationError, GBrainError) as exc:
            rollback_ok = self._restore_page(raw_task)
            if not blocker_preexisted:
                try:
                    self.runner.run(
                        "remove_link",
                        {
                            "from": task.slug,
                            "to": TONY_PROFILE_SLUG,
                            "link_type": "blocked_by",
                        },
                    )
                except GBrainError:
                    rollback_ok = False
            if created is not None and not created.idempotent:
                rollback_ok = all(
                    self._delete_child_page(slug)
                    for slug in (*reversed(created.todo.event_slugs), created.todo.slug)
                ) and rollback_ok
            raise PartialMutationError(
                task.slug,
                "Agent blocking question failed. "
                + ("Rollback verified." if rollback_ok else "Rollback could not be verified."),
            ) from exc

    def answer_agent_question(
        self,
        todo_slug: str,
        *,
        answer: str,
        expected_updated_at: datetime,
        actor: str,
        source: str,
        idempotency_key: str,
        now: datetime,
    ) -> HandoffMutationReceipt:
        if not isinstance(answer, str) or not answer.strip() or len(answer.strip()) > 4000:
            raise ValueError("answer must be 1 to 4000 characters")
        if actor != TONY_PROFILE_SLUG or source != "mission_control":
            raise ValueError("blocking questions must be answered by Tony in Mission Control")
        key = self._normalize_idempotency_key(idempotency_key)
        self._validate_todo_timestamp(expected_updated_at, "expected_updated_at")
        self._validate_todo_timestamp(now, "handoff answered_at")
        todo, raw_todo, _todo_links = self._read_todo_snapshot(todo_slug)
        raw_task = self.runner.run("get_page", {"slug": todo.parent_task})
        raw_task_links = self.runner.run("get_links", {"slug": todo.parent_task})
        if not isinstance(raw_task, Mapping) or not isinstance(raw_task_links, list):
            raise GBrainProtocolError("answer handoff task snapshot was not structured")
        task = Task.from_page(raw_task, edges=raw_task_links)
        self._require_task_openclaw_activation(task)
        if task.handoff is None or task.handoff.question_todo != todo.slug:
            raise ValueError("TODO is not the task's current blocking question")
        comment_slug = self._todo_identity("todo-comments", todo.slug, key)
        status_event_slug = self._todo_identity(
            "todo-events", todo.slug, f"answer_status:{key}"
        )
        if todo.status == "done" and task.handoff.state in {"ready_for_agent", "agent_working"}:
            matching = [
                comment for comment in todo.comments
                if comment.slug == comment_slug and comment.body == answer.strip()
            ]
            if len(matching) != 1:
                raise GBrainProtocolError("answer idempotency readback was incomplete")
            return HandoffMutationReceipt(
                task=task,
                todo=todo,
                event=todo.events[-1] if todo.events else None,
                next_owner=task.owner_agent,
                verified=True,
                idempotent=True,
            )
        if todo.updated_at != expected_updated_at:
            raise ConcurrentTodoUpdateError(todo.slug)
        if todo.kind != "question" or todo.status != "not_done":
            raise ValueError("current blocking TODO must be an open question")
        if task.handoff.state != "waiting_for_input":
            raise ValueError("task is not waiting for an answer")
        if now < todo.updated_at:
            raise ValueError("handoff answered_at cannot move backwards")
        raw_existing_children = (*todo.comment_slugs, *todo.event_slugs)
        comment = TodoComment(
            slug=comment_slug,
            todo_slug=todo.slug,
            body=answer.strip(),
            author=actor,
            source=source,
            created_at=now,
            idempotency_key=key,
        )
        comment_event = TodoEvent(
            slug=self._todo_identity("todo-events", todo.slug, f"answer_comment:{key}"),
            todo_slug=todo.slug,
            event_type="comment_added",
            actor=actor,
            source=source,
            occurred_at=now,
            idempotency_key=key,
            before=None,
            after={"comment_slug": comment.slug},
            comment_slug=comment.slug,
        )
        status_event = TodoEvent(
            slug=status_event_slug,
            todo_slug=todo.slug,
            event_type="status_changed",
            actor=actor,
            source=source,
            occurred_at=now,
            idempotency_key=key,
            before={"status": "not_done"},
            after={"status": "done"},
        )
        updated_todo = replace(
            todo,
            status="done",
            updated_at=now,
            comment_slugs=(*todo.comment_slugs, comment.slug),
            event_slugs=(*todo.event_slugs, comment_event.slug, status_event.slug),
            comments=(*todo.comments, comment),
            events=(*todo.events, comment_event, status_event),
        )
        remaining_blockers = tuple(
            blocker for blocker in task.blockers if blocker != TONY_PROFILE_SLUG
        )
        ready_handoff = replace(
            task.handoff,
            state="ready_for_agent",
            waiting_on=None,
            answered_at=now,
            acknowledged_at=None,
        )
        desired_handoff = ready_handoff if not remaining_blockers else None
        desired_status = "active" if not remaining_blockers else "blocked"
        desired_next_action = (
            task.handoff.resume_action if not remaining_blockers else task.next_action
        )
        blocker_removed = False
        new_children = (comment.slug, comment_event.slug, status_event.slug)
        try:
            self._write_todo_comment(comment)
            self._write_todo_event(comment_event)
            self._write_todo_event(status_event)
            self.runner.run(
                "put_page",
                {"slug": todo.slug, "content": render_todo_page(updated_todo)},
            )
            todo_readback = self._read_todo(todo.slug)
            if todo_readback.to_dict() != updated_todo.to_dict():
                raise GBrainProtocolError("answered TODO readback did not match")
            task_frontmatter = self._handoff_frontmatter(
                raw_task,
                status=desired_status,
                next_action=desired_next_action,
                handoff=desired_handoff,
                now=now,
                remove_blocker=TONY_PROFILE_SLUG,
            )
            self.runner.run(
                "put_page",
                {
                    "slug": task.slug,
                    "content": _render_preserved_task_page(raw_task, task_frontmatter),
                },
            )
            if self._has_typed_edge(
                raw_task_links,
                from_slug=task.slug,
                to_slug=TONY_PROFILE_SLUG,
                link_type="blocked_by",
            ):
                self.runner.run(
                    "remove_link",
                    {
                        "from": task.slug,
                        "to": TONY_PROFILE_SLUG,
                        "link_type": "blocked_by",
                    },
                )
                blocker_removed = True
            stored_task = self.get_task(task.slug)
            stored_todo = self._read_todo(todo.slug)
            if (
                stored_task.status != desired_status
                or stored_task.blockers != remaining_blockers
                or stored_task.handoff != desired_handoff
                or stored_task.next_action != desired_next_action
                or stored_todo.to_dict() != updated_todo.to_dict()
            ):
                raise GBrainProtocolError("answer-and-handoff readback did not match")
            return HandoffMutationReceipt(
                task=stored_task,
                todo=stored_todo,
                event=status_event,
                next_owner=stored_task.owner_agent if desired_handoff else None,
                verified=True,
            )
        except (DomainValidationError, GBrainError) as exc:
            rollback_ok = self._restore_page(raw_todo) and self._restore_page(raw_task)
            rollback_ok = all(
                self._delete_child_page(slug)
                for slug in reversed(new_children)
                if slug not in raw_existing_children
            ) and rollback_ok
            if blocker_removed:
                try:
                    self.runner.run(
                        "add_link",
                        {
                            "from": task.slug,
                            "to": TONY_PROFILE_SLUG,
                            "link_type": "blocked_by",
                            "context": "Agent work is blocked pending Tony's answer.",
                            "link_source": "gtasks",
                        },
                    )
                except GBrainError:
                    rollback_ok = False
            raise PartialMutationError(
                todo.slug,
                "Answer and handoff failed. "
                + ("Rollback verified." if rollback_ok else "Rollback could not be verified."),
            ) from exc

    def acknowledge_agent_handoff(
        self,
        task_slug: str,
        *,
        actor: str,
        now: datetime,
    ) -> HandoffMutationReceipt:
        self._validate_todo_timestamp(now, "handoff acknowledged_at")
        raw_task = self.runner.run("get_page", {"slug": task_slug})
        raw_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_task, Mapping) or not isinstance(raw_links, list):
            raise GBrainProtocolError("handoff acknowledgement snapshot was not structured")
        task = Task.from_page(raw_task, edges=raw_links)
        self._require_task_openclaw_activation(task)
        if task.owner_agent != actor:
            raise ValueError("handoff acknowledgement actor must be the assigned Agent")
        if task.handoff is None:
            raise ValueError("task has no Agent handoff to acknowledge")
        todo = self._read_todo(task.handoff.question_todo)
        if task.handoff.state == "agent_working":
            return HandoffMutationReceipt(
                task=task,
                todo=todo,
                event=todo.events[-1] if todo.events else None,
                next_owner=actor,
                verified=True,
                idempotent=True,
            )
        if task.handoff.state != "ready_for_agent":
            raise ValueError("task is not ready for Agent acknowledgement")
        if task.handoff.answered_at is None or now < task.handoff.answered_at:
            raise ValueError("handoff acknowledgement cannot precede the answer")
        working = replace(
            task.handoff,
            state="agent_working",
            acknowledged_at=now,
        )
        try:
            frontmatter = self._handoff_frontmatter(
                raw_task,
                status="active",
                next_action=task.next_action,
                handoff=working,
                now=now,
            )
            self.runner.run(
                "put_page",
                {
                    "slug": task.slug,
                    "content": _render_preserved_task_page(raw_task, frontmatter),
                },
            )
            stored = self.get_task(task.slug)
            if (
                stored.handoff != working
                or stored.status != task.status
                or stored.lifecycle_root != task.lifecycle_root
                or stored.owner_agent != task.owner_agent
            ):
                raise GBrainProtocolError("Agent acknowledgement readback did not match")
            return HandoffMutationReceipt(
                task=stored,
                todo=todo,
                event=todo.events[-1] if todo.events else None,
                next_owner=actor,
                verified=True,
            )
        except (DomainValidationError, GBrainError) as exc:
            rollback_ok = self._restore_page(raw_task)
            raise PartialMutationError(
                task.slug,
                "Agent acknowledgement failed. "
                + ("Rollback verified." if rollback_ok else "Rollback could not be verified."),
            ) from exc

    def repair_answered_agent_handoff(
        self,
        task_slug: str,
        *,
        question_todo_slug: str,
        expected_answer: str,
        resume_action: str,
        agent_slug: str,
        idempotency_key: str,
        now: datetime,
    ) -> HandoffMutationReceipt:
        """Promote one verified legacy answered TODO into the handoff contract.

        This deliberately narrow migration path is for a task whose answer was
        captured through the old editable TODO detail before atomic handoffs
        existed.  It refuses to infer an answer or owner, preserves every prior
        event, and appends an immutable answer comment plus repair evidence.
        """
        normalized_answer = expected_answer.strip() if isinstance(expected_answer, str) else ""
        if not normalized_answer or len(normalized_answer) > 4000:
            raise ValueError("expected_answer must be 1 to 4000 characters")
        normalized_resume, _unused = self._normalize_todo_text(resume_action, "")
        key = self._normalize_idempotency_key(idempotency_key)
        self._validate_todo_timestamp(now, "handoff repair timestamp")
        todo, raw_todo, _todo_links = self._read_todo_snapshot(question_todo_slug)
        raw_task = self.runner.run("get_page", {"slug": task_slug})
        raw_task_links = self.runner.run("get_links", {"slug": task_slug})
        if not isinstance(raw_task, Mapping) or not isinstance(raw_task_links, list):
            raise GBrainProtocolError("handoff repair task snapshot was not structured")
        task = Task.from_page(raw_task, edges=raw_task_links)
        self._require_task_openclaw_activation(task)
        if todo.parent_task != task.slug:
            raise ValueError("legacy question TODO does not belong to the task")
        if task.owner_agent != agent_slug:
            raise ValueError("repair Agent must match the task's assigned Agent")
        if task.status in {"proposed", "completed", "cancelled"}:
            raise ValueError("only authorized unfinished Agent work can be repaired")
        if todo.status != "done":
            raise ValueError("legacy question TODO must already be Done")
        if todo.detail.strip() != normalized_answer:
            raise ValueError("legacy answer does not exactly match expected_answer")
        if any(blocker != TONY_PROFILE_SLUG for blocker in task.blockers):
            raise ValueError("task has another blocker and cannot be returned to the Agent")

        comment_slug = self._todo_identity("todo-comments", todo.slug, key)
        comment_event_slug = self._todo_identity(
            "todo-events", todo.slug, f"repair_comment:{key}"
        )
        repair_event_slug = self._todo_identity(
            "todo-events", todo.slug, f"repair_kind:{key}"
        )
        if task.handoff and task.handoff.state == "ready_for_agent":
            matching = [
                comment for comment in todo.comments
                if comment.slug == comment_slug and comment.body == normalized_answer
            ]
            if (
                task.handoff.question_todo != todo.slug
                or task.handoff.resume_owner != agent_slug
                or task.handoff.resume_action != normalized_resume
                or task.next_action != normalized_resume
                or todo.kind != "question"
                or len(matching) != 1
            ):
                raise GBrainProtocolError("handoff repair idempotency readback was incomplete")
            return HandoffMutationReceipt(
                task=task,
                todo=todo,
                event=todo.events[-1] if todo.events else None,
                next_owner=agent_slug,
                verified=True,
                idempotent=True,
            )
        if task.handoff is not None:
            raise ValueError("task already has a different handoff")

        answered_at = self._completion_time(todo)
        if now < answered_at:
            raise ValueError("handoff repair cannot precede the recorded answer")
        comment = TodoComment(
            slug=comment_slug,
            todo_slug=todo.slug,
            body=normalized_answer,
            author=TONY_PROFILE_SLUG,
            source="mission_control",
            created_at=now,
            idempotency_key=key,
        )
        comment_event = TodoEvent(
            slug=comment_event_slug,
            todo_slug=todo.slug,
            event_type="comment_added",
            actor=TONY_PROFILE_SLUG,
            source="mission_control",
            occurred_at=now,
            idempotency_key=key,
            before=None,
            after={"comment_slug": comment.slug},
            comment_slug=comment.slug,
        )
        repair_event = TodoEvent(
            slug=repair_event_slug,
            todo_slug=todo.slug,
            event_type="edited",
            actor=TONY_PROFILE_SLUG,
            source="mission_control",
            occurred_at=now,
            idempotency_key=key,
            before={"kind": todo.kind},
            after={"kind": "question", "migration": "answered_agent_handoff"},
        )
        updated_todo = replace(
            todo,
            kind="question",
            updated_at=now,
            comment_slugs=(*todo.comment_slugs, comment.slug),
            event_slugs=(*todo.event_slugs, comment_event.slug, repair_event.slug),
            comments=(*todo.comments, comment),
            events=(*todo.events, comment_event, repair_event),
        )
        handoff = TaskHandoff(
            state="ready_for_agent",
            question_todo=todo.slug,
            waiting_on=None,
            resume_owner=agent_slug,
            resume_action=normalized_resume,
            requested_at=todo.created_at,
            answered_at=answered_at,
            acknowledged_at=None,
            round=1,
        )
        blocker_preexisted = self._has_typed_edge(
            raw_task_links,
            from_slug=task.slug,
            to_slug=TONY_PROFILE_SLUG,
            link_type="blocked_by",
        )
        new_children = (comment.slug, comment_event.slug, repair_event.slug)
        blocker_removed = False
        try:
            self._write_todo_comment(comment)
            self._write_todo_event(comment_event)
            self._write_todo_event(repair_event)
            self.runner.run(
                "put_page", {"slug": todo.slug, "content": render_todo_page(updated_todo)}
            )
            frontmatter = self._handoff_frontmatter(
                raw_task,
                status="active",
                next_action=normalized_resume,
                handoff=handoff,
                now=now,
                remove_blocker=TONY_PROFILE_SLUG,
            )
            self.runner.run(
                "put_page",
                {
                    "slug": task.slug,
                    "content": _render_preserved_task_page(raw_task, frontmatter),
                },
            )
            if blocker_preexisted:
                self.runner.run(
                    "remove_link",
                    {
                        "from": task.slug,
                        "to": TONY_PROFILE_SLUG,
                        "link_type": "blocked_by",
                    },
                )
                blocker_removed = True
            stored_task = self.get_task(task.slug)
            stored_todo = self._read_todo(todo.slug)
            if (
                stored_task.status != "active"
                or stored_task.owner_agent != agent_slug
                or stored_task.lifecycle_root != task.lifecycle_root
                or stored_task.blockers
                or stored_task.handoff != handoff
                or stored_task.next_action != normalized_resume
                or stored_todo.to_dict() != updated_todo.to_dict()
            ):
                raise GBrainProtocolError("answered handoff repair readback did not match")
            return HandoffMutationReceipt(
                task=stored_task,
                todo=stored_todo,
                event=repair_event,
                next_owner=agent_slug,
                verified=True,
            )
        except (DomainValidationError, GBrainError) as exc:
            rollback_ok = self._restore_page(raw_todo) and self._restore_page(raw_task)
            rollback_ok = all(
                self._delete_child_page(slug) for slug in reversed(new_children)
            ) and rollback_ok
            if blocker_removed:
                try:
                    self.runner.run(
                        "add_link",
                        {
                            "from": task.slug,
                            "to": TONY_PROFILE_SLUG,
                            "link_type": "blocked_by",
                            "context": "Agent work is blocked pending Tony's answer.",
                            "link_source": "gtasks",
                        },
                    )
                except GBrainError:
                    rollback_ok = False
            raise PartialMutationError(
                task.slug,
                "Answered Agent handoff repair failed. "
                + ("Rollback verified." if rollback_ok else "Rollback could not be verified."),
            ) from exc

    def is_active_handoff_question(self, todo_slug: str) -> bool:
        todo = self._read_todo(todo_slug)
        task = self.get_task(todo.parent_task)
        return bool(
            task.handoff
            and task.handoff.state == "waiting_for_input"
            and task.handoff.question_todo == todo.slug
            and todo.status == "not_done"
        )

    def get_todo(self, todo_slug: str) -> TodoItem:
        """Return the authoritative hydrated To Do used for mutation snapshots."""
        todo = self._read_todo(todo_slug)
        self.get_task(todo.parent_task)
        return todo

    def _todo_mutation_snapshot(
        self,
        todo_slug: str,
        *,
        expected_updated_at: datetime,
        event_slug: str,
    ) -> tuple[TodoItem, Mapping[str, Any], Mapping[str, Any]] | TodoMutationReceipt:
        existing_event = self._optional_todo_event(event_slug)
        if existing_event is not None:
            todo = self._read_todo(todo_slug)
            self.get_task(todo.parent_task)
            return TodoMutationReceipt(todo, True, idempotent=True)
        todo = self._read_todo(todo_slug)
        if todo.updated_at != expected_updated_at:
            raise ConcurrentTodoUpdateError(todo_slug)
        raw_todo = self.runner.run("get_page", {"slug": todo_slug})
        raw_parent = self.runner.run("get_page", {"slug": todo.parent_task})
        if not isinstance(raw_todo, Mapping) or not isinstance(raw_parent, Mapping):
            raise GBrainProtocolError("todo mutation snapshot was not structured")
        return todo, raw_todo, raw_parent

    def _rollback_existing_todo(
        self,
        *,
        todo: TodoItem,
        raw_todo: Mapping[str, Any],
        raw_parent: Mapping[str, Any],
        new_pages: Iterable[str],
    ) -> bool:
        deleted = all(self._delete_child_page(slug) for slug in new_pages)
        restored = self._restore_page(raw_todo) and self._restore_page(raw_parent)
        if not (deleted and restored):
            return False
        try:
            return self._read_todo(todo.slug).to_dict() == todo.to_dict()
        except (DomainValidationError, GBrainError):
            return False

    def edit_todo(
        self,
        todo_slug: str,
        *,
        text: str,
        detail: str,
        expected_updated_at: datetime,
        actor: str,
        source: str,
        idempotency_key: str,
        now: datetime,
    ) -> TodoMutationReceipt:
        normalized_text, normalized_detail = self._normalize_todo_text(text, detail)
        key = self._normalize_idempotency_key(idempotency_key)
        self._validate_todo_timestamp(expected_updated_at, "expected_updated_at")
        self._validate_todo_timestamp(now, "todo updated_at")
        event_slug = self._todo_identity("todo-events", todo_slug, f"edited:{key}")
        snapshot = self._todo_mutation_snapshot(
            todo_slug,
            expected_updated_at=expected_updated_at,
            event_slug=event_slug,
        )
        if isinstance(snapshot, TodoMutationReceipt):
            return snapshot
        todo, raw_todo, raw_parent = snapshot
        self._validate_todo_actor_source(
            actor=actor, source=source, task=self.get_task(todo.parent_task)
        )
        if now < todo.updated_at:
            raise ValueError("todo updated_at cannot move backwards")
        event = TodoEvent(
            slug=event_slug,
            todo_slug=todo.slug,
            event_type="edited",
            actor=actor,
            source=source,
            occurred_at=now,
            idempotency_key=key,
            before={"text": todo.text, "detail": todo.detail},
            after={"text": normalized_text, "detail": normalized_detail},
        )
        updated = replace(
            todo,
            text=normalized_text,
            detail=normalized_detail,
            updated_at=now,
            event_slugs=(*todo.event_slugs, event.slug),
            events=(*todo.events, event),
        )
        try:
            self._write_todo_event(event)
            self.runner.run(
                "put_page",
                {"slug": todo.slug, "content": render_todo_page(updated)},
            )
            stored = self._read_todo(todo.slug)
            if stored.to_dict() != updated.to_dict():
                raise GBrainProtocolError("todo edit readback did not match the write")
            self._sync_task_todo_projection(todo.parent_task)
            return TodoMutationReceipt(stored, True)
        except (DomainValidationError, GBrainError) as exc:
            rollback = self._rollback_existing_todo(
                todo=todo,
                raw_todo=raw_todo,
                raw_parent=raw_parent,
                new_pages=(event.slug,),
            )
            raise PartialMutationError(
                todo.slug,
                "TODO edit failed. "
                + ("Rollback verified." if rollback else "Rollback could not be verified."),
            ) from exc

    def add_todo_comment(
        self,
        todo_slug: str,
        *,
        body: str,
        expected_updated_at: datetime,
        author: str,
        source: str,
        idempotency_key: str,
        now: datetime,
    ) -> TodoMutationReceipt:
        if not isinstance(body, str) or not body.strip() or len(body.strip()) > 4000:
            raise ValueError("todo comment body must be 1 to 4000 characters")
        key = self._normalize_idempotency_key(idempotency_key)
        self._validate_todo_timestamp(expected_updated_at, "expected_updated_at")
        self._validate_todo_timestamp(now, "todo updated_at")
        comment_slug = self._todo_identity("todo-comments", todo_slug, key)
        event_slug = self._todo_identity("todo-events", todo_slug, f"comment_added:{key}")
        def read_idempotency_record(
            item: tuple[str, str],
        ) -> TodoComment | TodoEvent | None:
            kind, slug = item
            return (
                self._optional_todo_comment(slug)
                if kind == "comment"
                else self._optional_todo_event(slug)
            )

        existing_comment, existing_event = self._bounded_map(
            read_idempotency_record,
            [("comment", comment_slug), ("event", event_slug)],
        )
        if existing_comment is not None or existing_event is not None:
            if (
                existing_comment is None
                or existing_event is None
                or existing_comment.body != body.strip()
                or existing_comment.author != author
            ):
                raise GBrainProtocolError("comment idempotency readback was incomplete")
            todo = self._read_todo(todo_slug)
            self.get_task(todo.parent_task)
            return TodoMutationReceipt(todo, True, idempotent=True)
        todo, raw_todo, _todo_links = self._read_todo_snapshot(todo_slug)
        if todo.updated_at != expected_updated_at:
            raise ConcurrentTodoUpdateError(todo_slug)
        raw_parent = self.runner.run("get_page", {"slug": todo.parent_task})
        raw_parent_links = self.runner.run("get_links", {"slug": todo.parent_task})
        if not isinstance(raw_parent, Mapping) or not isinstance(raw_parent_links, list):
            raise GBrainProtocolError("todo comment parent snapshot was not structured")
        task = Task.from_page(raw_parent, edges=raw_parent_links)
        self._require_task_openclaw_activation(task)
        self._validate_todo_actor_source(actor=author, source=source, task=task)
        if now < todo.updated_at:
            raise ValueError("todo updated_at cannot move backwards")
        comment = TodoComment(
            slug=comment_slug,
            todo_slug=todo.slug,
            body=body.strip(),
            author=author,
            source=source,
            created_at=now,
            idempotency_key=key,
        )
        event = TodoEvent(
            slug=event_slug,
            todo_slug=todo.slug,
            event_type="comment_added",
            actor=author,
            source=source,
            occurred_at=now,
            idempotency_key=key,
            before=None,
            after={"comment_slug": comment.slug},
            comment_slug=comment.slug,
        )
        updated = replace(
            todo,
            updated_at=now,
            comment_slugs=(*todo.comment_slugs, comment.slug),
            event_slugs=(*todo.event_slugs, event.slug),
            comments=(*todo.comments, comment),
            events=(*todo.events, event),
        )
        try:
            def write_child(item: tuple[str, TodoComment | TodoEvent]) -> None:
                kind, child = item
                if kind == "comment":
                    self._write_todo_comment(child)  # type: ignore[arg-type]
                else:
                    self._write_todo_event(child)  # type: ignore[arg-type]

            self._bounded_map(
                write_child,
                [("comment", comment), ("event", event)],
            )
            self.runner.run(
                "put_page", {"slug": todo.slug, "content": render_todo_page(updated)}
            )
            stored = self._read_todo(todo.slug)
            if stored.to_dict() != updated.to_dict():
                raise GBrainProtocolError("todo comment append did not read back")
            return TodoMutationReceipt(stored, True)
        except (DomainValidationError, GBrainError) as exc:
            rollback = self._rollback_existing_todo(
                todo=todo,
                raw_todo=raw_todo,
                raw_parent=raw_parent,
                new_pages=(event.slug, comment.slug),
            )
            raise PartialMutationError(
                todo.slug,
                "TODO comment append failed. "
                + ("Rollback verified." if rollback else "Rollback could not be verified."),
            ) from exc

    def set_todo_status(
        self,
        todo_slug: str,
        *,
        status: str,
        expected_updated_at: datetime,
        actor: str,
        source: str,
        idempotency_key: str,
        now: datetime,
    ) -> TodoMutationReceipt:
        if status not in {"not_done", "done"}:
            raise ValueError("todo status must be not_done or done")
        key = self._normalize_idempotency_key(idempotency_key)
        self._validate_todo_timestamp(expected_updated_at, "expected_updated_at")
        self._validate_todo_timestamp(now, "todo updated_at")
        event_slug = self._todo_identity("todo-events", todo_slug, f"status_changed:{key}")
        snapshot = self._todo_mutation_snapshot(
            todo_slug,
            expected_updated_at=expected_updated_at,
            event_slug=event_slug,
        )
        if isinstance(snapshot, TodoMutationReceipt):
            return snapshot
        todo, raw_todo, raw_parent = snapshot
        self._validate_todo_actor_source(
            actor=actor, source=source, task=self.get_task(todo.parent_task)
        )
        if todo.status == status:
            raise ValueError("todo already has the requested status")
        if now < todo.updated_at:
            raise ValueError("todo updated_at cannot move backwards")
        event = TodoEvent(
            slug=event_slug,
            todo_slug=todo.slug,
            event_type="status_changed",
            actor=actor,
            source=source,
            occurred_at=now,
            idempotency_key=key,
            before={"status": todo.status},
            after={"status": status},
        )
        updated = replace(
            todo,
            status=status,
            updated_at=now,
            event_slugs=(*todo.event_slugs, event.slug),
            events=(*todo.events, event),
        )
        try:
            self._write_todo_event(event)
            self.runner.run(
                "put_page", {"slug": todo.slug, "content": render_todo_page(updated)}
            )
            stored = self._read_todo(todo.slug)
            if stored.to_dict() != updated.to_dict():
                raise GBrainProtocolError("todo status readback did not match the write")
            self._sync_task_todo_projection(todo.parent_task)
            return TodoMutationReceipt(stored, True)
        except (DomainValidationError, GBrainError) as exc:
            rollback = self._rollback_existing_todo(
                todo=todo,
                raw_todo=raw_todo,
                raw_parent=raw_parent,
                new_pages=(event.slug,),
            )
            raise PartialMutationError(
                todo.slug,
                "TODO status change failed. "
                + ("Rollback verified." if rollback else "Rollback could not be verified."),
            ) from exc

    def migrate_legacy_next_actions(
        self,
        task_slug: str,
        *,
        now: datetime,
    ) -> TodoRead:
        self._validate_todo_timestamp(now, "migration timestamp")
        task = self.get_task(task_slug)
        for index, entry in enumerate(task.next_action_history):
            history_key = "legacy-history:" + hashlib.sha256(
                json.dumps(
                    {
                        "index": index,
                        "action": entry.action,
                        "completed_at": entry.completed_at.isoformat(),
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            self._create_todo_record(
                task,
                text=entry.action,
                detail="",
                status="done",
                kind="action",
                actor=None,
                source="legacy_next_action",
                idempotency_key=history_key,
                created_at=entry.completed_at,
                updated_at=entry.completed_at,
                legacy_provenance={
                    "field": "next_action_history",
                    "index": index,
                    "completed_at": entry.completed_at.isoformat(),
                },
                event_type="legacy_migrated",
                sync_projection=False,
            )
        if task.next_action:
            current_time = task.updated_at or task.created_at or now
            current_key = "legacy-current:" + hashlib.sha256(
                task.next_action.encode("utf-8")
            ).hexdigest()
            self._create_todo_record(
                task,
                text=task.next_action,
                detail="",
                status="not_done",
                kind="action",
                actor=None,
                source="legacy_next_action",
                idempotency_key=current_key,
                created_at=current_time,
                updated_at=current_time,
                legacy_provenance={
                    "field": "next_action",
                    "timestamp_basis": (
                        "task.updated_at"
                        if task.updated_at
                        else "task.created_at" if task.created_at else "migration_time"
                    ),
                },
                event_type="legacy_migrated",
                sync_projection=False,
            )
        self._sync_task_todo_projection(task_slug)
        return self.list_task_todos(task_slug, limit=100)
