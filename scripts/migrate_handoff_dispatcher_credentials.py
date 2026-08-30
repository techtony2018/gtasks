#!/usr/bin/env python3
"""Atomically retire OpenClaw dispatcher hashes without exposing credentials."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile


CODEX_AGENTS = ("agents/tammy", "agents/timmy", "agents/toddy")
RETIRED_AGENTS = (
    "agents/tammy-oc",
    "agents/timmy-oc",
    "agents/toddy-oc",
)
ENTRY_FIELDS = {"agent_slug", "registration_sha256", "token_sha256"}


def _load(path: Path) -> dict[str, object]:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Dispatcher credentials must be a regular non-symbolic file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError("Dispatcher credentials must use mode 0600")
    value = json.loads(path.read_text(encoding="utf-8"))
    identities = value.get("identities") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "identities"}
        or value.get("schema_version") != 1
        or not isinstance(identities, list)
        or any(not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS for entry in identities)
    ):
        raise ValueError("Dispatcher credentials have the wrong schema")
    return value


def migrate(path: Path) -> dict[str, object]:
    value = _load(path)
    entries = value["identities"]
    assert isinstance(entries, list)
    entry_by_agent = {entry["agent_slug"]: entry for entry in entries}
    configured = tuple(entry_by_agent)
    if set(configured) == set(CODEX_AGENTS):
        return {"ok": True, "status": "already_codex_only", "agents": list(CODEX_AGENTS)}
    expected_legacy = set(CODEX_AGENTS) | set(RETIRED_AGENTS)
    if len(entries) != 6 or set(configured) != expected_legacy:
        raise ValueError("Dispatcher credentials are neither the reviewed six-agent legacy set nor the Codex-only set")
    migrated = {
        "schema_version": 1,
        "identities": [entry_by_agent[agent] for agent in CODEX_AGENTS],
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.codex-only.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(migrated, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    verified = _load(path)
    verified_agents = [entry["agent_slug"] for entry in verified["identities"]]
    if verified_agents != list(CODEX_AGENTS):
        raise RuntimeError("Codex-only dispatcher credential readback failed")
    return {"ok": True, "status": "migrated", "agents": verified_agents}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retire OpenClaw dispatcher hash entries from one private credential file."
    )
    parser.add_argument("--credentials", type=Path, required=True)
    args = parser.parse_args()
    result = migrate(args.credentials.expanduser().resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
