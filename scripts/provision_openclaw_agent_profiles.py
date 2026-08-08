#!/usr/bin/env python3
"""Provision the declared OpenClaw Agent profiles only when explicitly executed."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtasks.gbrain import (  # noqa: E402
    GBrainCommandError,
    GBrainError,
    MemoryStargraphOpenClawProfileClient,
)


DECLARATION_FIELDS = frozenset(
    {
        "slug",
        "name",
        "runtime",
        "route",
        "task_collection",
        "artifact_collection",
    }
)
APPROVED = {
    "agents/tammy-oc": ("Tammy-OC", "hosts/tammy", "collections/tammy-oc-tasks", "collections/tammy-oc-artifacts"),
    "agents/timmy-oc": ("Timmy-OC", "hosts/timmy", "collections/timmy-oc-tasks", "collections/timmy-oc-artifacts"),
    "agents/toddy-oc": ("Toddy-OC", "hosts/toddy", "collections/toddy-oc-tasks", "collections/toddy-oc-artifacts"),
}
NONTERMINAL_OPERATION_STATUSES = frozenset(
    {"created", "accepted", "running", "recovery_required"}
)


def default_operation_file() -> Path:
    configured = os.environ.get("GTASKS_OPENCLAW_ACTIVATION_OPERATION_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "GTasks"
        / "openclaw-profile-activation-operation.json"
    )


def _declarations_digest(declarations: tuple[dict[str, str], ...]) -> str:
    payload = json.dumps(
        list(declarations), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@contextmanager
def _operation_file_lock(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_operation_state_unlocked(path: Path, state: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(dict(state), sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_operation_state(path: Path, state: Mapping[str, Any]) -> None:
    with _operation_file_lock(path):
        _write_operation_state_unlocked(path, state)


def _load_retriable_operation(
    path: Path, declarations_digest: str
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("OpenClaw activation operation state is invalid") from error
    if (
        not isinstance(state, Mapping)
        or state.get("schema_version") != 1
        or not isinstance(state.get("operation_id"), str)
        or not isinstance(state.get("owner"), str)
        or state.get("declarations_digest") != declarations_digest
        or state.get("status")
        not in NONTERMINAL_OPERATION_STATUSES | {"completed", "failed"}
    ):
        raise ValueError("OpenClaw activation operation state is invalid")
    path.chmod(0o600)
    return dict(state)


def _load_or_create_operation(
    path: Path,
    declarations_digest: str,
    *,
    operation_id_factory: Callable[[], str],
    owner_factory: Callable[[], str],
) -> dict[str, Any]:
    with _operation_file_lock(path):
        state = _load_retriable_operation(path, declarations_digest)
        if state is not None:
            return state
        state = {
            "schema_version": 1,
            "operation_id": operation_id_factory(),
            "owner": owner_factory(),
            "declarations_digest": declarations_digest,
            "status": "created",
            "fence_generation": None,
            "receipt": None,
            "error": None,
        }
        _write_operation_state_unlocked(path, state)
        return state


def _persist_operation_status(
    path: Path,
    declarations_digest: str,
    operation_id: str,
    status: Mapping[str, Any],
) -> dict[str, Any]:
    with _operation_file_lock(path):
        current = _load_retriable_operation(path, declarations_digest)
        if current is None:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValueError("OpenClaw activation operation state is invalid")
            current = dict(raw)
        if current.get("operation_id") != operation_id:
            raise GBrainError("activation operation file changed identity")
        if current.get("status") in {"completed", "failed"}:
            return current
        incoming_status = str(status.get("status") or "")
        if incoming_status not in NONTERMINAL_OPERATION_STATUSES | {
            "completed",
            "failed",
        }:
            raise GBrainError("Memory Stargraph returned an invalid activation status")
        order = {"created": 0, "accepted": 1, "running": 2, "recovery_required": 2}
        if (
            incoming_status in order
            and str(current.get("status")) in order
            and order[incoming_status] < order[str(current.get("status"))]
        ):
            return current
        updated = dict(current)
        updated.update(
            {
                "status": incoming_status,
                "fence_generation": status.get("fence_generation"),
                "receipt": status.get("receipt"),
                "error": status.get("error"),
            }
        )
        _write_operation_state_unlocked(path, updated)
        return updated


def load_declarations(path: Path) -> tuple[dict[str, str], ...]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or set(payload) != {"schema_version", "agents"}:
        raise ValueError("OpenClaw declaration config has an unexpected schema")
    if payload["schema_version"] != 1 or not isinstance(payload["agents"], list):
        raise ValueError("OpenClaw declaration config has an unsupported schema version")
    declarations: list[dict[str, str]] = []
    for item in payload["agents"]:
        if (
            not isinstance(item, Mapping)
            or set(item) != DECLARATION_FIELDS
            or not all(isinstance(value, str) and value.strip() for value in item.values())
        ):
            raise ValueError("OpenClaw declaration config contains an invalid Agent")
        declarations.append({key: item[key].strip() for key in DECLARATION_FIELDS})
    if len(declarations) != 3 or len({item["slug"] for item in declarations}) != 3:
        raise ValueError("OpenClaw declaration config must contain exactly three Agents")
    if {item["slug"] for item in declarations} != set(APPROVED) or any(
        (item["name"], item["route"], item["task_collection"], item["artifact_collection"]) != APPROVED[item["slug"]]
        for item in declarations
    ):
        raise ValueError("OpenClaw declaration config must match the approved Agent contracts")
    return tuple(declarations)


def provision(
    declarations: tuple[dict[str, str],
    ...], *, execute: bool, client: MemoryStargraphOpenClawProfileClient | None = None,
    operation_file: Path | None = None,
    operation_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    owner_factory: Callable[[], str] = lambda: f"gtasks-provisioner-{uuid.uuid4()}",
) -> dict[str, object]:
    collection_slugs = [collection for item in declarations for collection in (item["task_collection"], item["artifact_collection"])]
    if not execute:
        return {
            "agent_count": len(declarations),
            "agent_slugs": [item["slug"] for item in declarations],
            "collection_count": len(collection_slugs),
            "collection_slugs": collection_slugs,
            "default_goal_link_count": 0,
            "mutated": False,
            "verified": False,
            "activation": None,
        }
    active_client = client or MemoryStargraphOpenClawProfileClient.from_environment()
    state_path = operation_file or default_operation_file()
    declarations_digest = _declarations_digest(declarations)
    state = _load_or_create_operation(
        state_path,
        declarations_digest,
        operation_id_factory=operation_id_factory,
        owner_factory=owner_factory,
    )

    operation_id = str(state["operation_id"])
    owner = str(state["owner"])

    def persist_status(status: Mapping[str, Any]) -> None:
        if status.get("operation_id") != operation_id:
            raise GBrainError("Memory Stargraph returned another activation operation")
        persisted = _persist_operation_status(
            state_path,
            declarations_digest,
            operation_id,
            status,
        )
        state.clear()
        state.update(persisted)

    try:
        accepted = active_client.submit(
            declarations, owner=owner, operation_id=operation_id
        )
        persist_status(accepted)
        operation = active_client.wait(
            operation_id, initial=accepted, on_status=persist_status
        )
    except GBrainError as error:
        raise GBrainCommandError(
            f"{error} Operation ID: {operation_id}"
        ) from error
    receipt = operation.get("receipt")
    if not isinstance(receipt, Mapping):
        raise GBrainError(f"OpenClaw activation {operation_id} completed without a receipt")
    return {
        "agent_count": len(declarations),
        "agent_slugs": [item["slug"] for item in declarations],
        "collection_count": len(collection_slugs),
        "collection_slugs": collection_slugs,
        "default_goal_link_count": int(receipt.get("default_goal_link_count", -1)),
        "mutated": True,
        "verified": int(receipt.get("default_goal_link_count", -1)) == 0,
        "operation_id": operation_id,
        "activation": dict(operation),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "openclaw-agents" / "agents.json",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--operation-file",
        type=Path,
        default=default_operation_file(),
        help="Private durable operation state used to resume the same submission.",
    )
    args = parser.parse_args()
    try:
        result = provision(
            load_declarations(args.config),
            execute=args.execute,
            operation_file=args.operation_file,
        )
    except (OSError, ValueError, json.JSONDecodeError, GBrainError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
