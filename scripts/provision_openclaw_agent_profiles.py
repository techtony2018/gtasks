#!/usr/bin/env python3
"""Provision the declared OpenClaw Agent profiles only when explicitly executed."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtasks.gbrain import GBrainError, MemoryStargraphOpenClawProfileClient  # noqa: E402


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
    return tuple(declarations)


def provision(
    declarations: tuple[dict[str, str],
    ...], *, execute: bool, client: MemoryStargraphOpenClawProfileClient | None = None
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
    activation = active_client.provision(
        declarations,
        owner="gtasks-provisioner",
        operation_id=str(uuid.uuid4()),
    )
    return {
        "agent_count": len(declarations),
        "agent_slugs": [item["slug"] for item in declarations],
        "collection_count": len(collection_slugs),
        "collection_slugs": collection_slugs,
        "default_goal_link_count": int(activation.get("default_goal_link_count", -1)),
        "mutated": True,
        "verified": int(activation.get("default_goal_link_count", -1)) == 0,
        "activation": dict(activation),
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
    args = parser.parse_args()
    try:
        result = provision(load_declarations(args.config), execute=args.execute)
    except (OSError, ValueError, json.JSONDecodeError, GBrainError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
