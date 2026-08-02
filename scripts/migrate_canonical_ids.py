#!/usr/bin/env python3
"""Audit or execute the one-time Mission Control opaque-ID migration."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gtasks.gbrain import GBrainAdapter, GBrainProtocolError  # noqa: E402


DEFAULT_PLAN = REPO_ROOT / "migrations" / "2026-08-01-immutable-canonical-ids.json"


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported identity migration plan schema")
    if not isinstance(plan.get("mapping"), Mapping):
        raise ValueError("migration plan mapping is required")
    if not isinstance(plan.get("excluded"), list):
        raise ValueError("migration plan excluded list is required")
    if not isinstance(plan.get("scope_roots"), list):
        raise ValueError("migration plan scope_roots list is required")
    return plan


def scoped_members(adapter: GBrainAdapter, roots: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for root in roots:
        backlinks = adapter.runner.run("get_backlinks", {"slug": root})
        if not isinstance(backlinks, list):
            raise GBrainProtocolError(f"scope root backlinks were not a list: {root}")
        result[root] = list(
            dict.fromkeys(
                str(edge["from_slug"])
                for edge in backlinks
                if isinstance(edge, Mapping)
                and edge.get("to_slug") == root
                and edge.get("link_type") in {"member_of", "", None}
                and isinstance(edge.get("from_slug"), str)
            )
        )
    return result


def verify_scope(
    adapter: GBrainAdapter,
    plan: Mapping[str, Any],
    *,
    migrated: bool,
) -> dict[str, list[str]]:
    roots = [str(root) for root in plan["scope_roots"]]
    actual = scoped_members(adapter, roots)
    source = set(str(slug) for slug in plan["mapping"])
    destination = set(str(slug) for slug in plan["mapping"].values())
    excluded = set(str(slug) for slug in plan["excluded"])
    expected = (destination if migrated else source) | excluded
    scoped = {slug for members in actual.values() for slug in members}
    missing = sorted(expected - scoped)
    unexpected = sorted(scoped - expected)
    if missing or unexpected:
        raise ValueError(
            "migration plan no longer matches canonical scope roots; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return actual


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("audit", "execute"))
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument(
        "--confirm",
        help="execute requires the exact migration_id as a deliberate live-data guard",
    )
    args = parser.parse_args()

    plan = load_plan(args.plan)
    adapter = GBrainAdapter()
    before_scope = verify_scope(adapter, plan, migrated=False)
    audit = adapter.audit_canonical_identity_migration(
        plan["mapping"], excluded=tuple(plan["excluded"])
    )
    result: dict[str, Any] = {
        "migration_id": plan["migration_id"],
        "mode": args.mode,
        "timestamp": datetime.now().astimezone().isoformat(),
        "plan": str(args.plan.resolve()),
        "before_scope": before_scope,
        "audit": audit,
    }

    if args.mode == "execute":
        if args.confirm != plan["migration_id"]:
            raise ValueError(
                "execute requires --confirm with the exact migration_id"
            )
        receipt = adapter.migrate_canonical_identities(
            plan["mapping"], excluded=tuple(plan["excluded"])
        )
        result["mutation"] = receipt.to_dict()
        result["after_scope"] = verify_scope(adapter, plan, migrated=True)
        result["verified"] = bool(receipt.verified)
    else:
        result["verified"] = bool(audit["verified"])

    if args.receipt:
        write_json_atomic(args.receipt, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
