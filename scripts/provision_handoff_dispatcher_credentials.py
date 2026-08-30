#!/usr/bin/env python3
"""Provision the central dispatcher credential hashes without printing secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


CONFIG_FIELDS = {
    "schema_version",
    "agent_slug",
    "registration_id",
    "fixed_thread_id",
    "mission_control_url",
    "token_file",
}
REVIEWED_AGENT_SLUGS = (
    "agents/tammy",
    "agents/timmy",
    "agents/toddy",
)


def _require_private_regular_file(path: Path, description: str) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"Every {description} must not be a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Every {description} must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError(f"Every {description} must use mode 0600")


def _read_private_json(path: Path) -> dict[str, object]:
    _require_private_regular_file(path, "identity config")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) != CONFIG_FIELDS
        or value.get("schema_version") != 1
    ):
        raise ValueError("Every identity config must use the exact schema version 1")
    return value


def _read_private_token(path: Path) -> str:
    _require_private_regular_file(path, "dispatcher token file")
    token = path.read_text(encoding="utf-8").strip()
    if not token or len(token) > 512 or "\n" in token or "\r" in token:
        raise ValueError("Every dispatcher token file must contain one bounded token")
    return token


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def provision(identity_configs: list[Path], output: Path) -> dict[str, object]:
    if len(identity_configs) != 3:
        raise ValueError("Exactly three --identity-config files are required")
    entries: list[dict[str, str]] = []
    for config_path in identity_configs:
        config = _read_private_json(config_path)
        agent_slug = config.get("agent_slug")
        registration_id = config.get("registration_id")
        token_file = config.get("token_file")
        if (
            not isinstance(agent_slug, str)
            or re.fullmatch(r"agents/[a-z0-9][a-z0-9._-]{0,63}", agent_slug)
            is None
            or not isinstance(registration_id, str)
            or not registration_id
            or not isinstance(token_file, str)
            or not token_file
        ):
            raise ValueError("Identity config contains an invalid dispatcher identity")
        token_path = Path(token_file).expanduser()
        if not token_path.is_absolute():
            token_path = config_path.parent / token_path
        token = _read_private_token(token_path)
        entries.append(
            {
                "agent_slug": agent_slug,
                "registration_sha256": _sha256(registration_id),
                "token_sha256": _sha256(token),
            }
        )
    configured_slugs = [entry["agent_slug"] for entry in entries]
    if set(configured_slugs) != set(REVIEWED_AGENT_SLUGS):
        raise ValueError("Dispatcher identities must be exactly the three reviewed Codex Agents")
    for field in ("agent_slug", "registration_sha256", "token_sha256"):
        values = [entry[field] for entry in entries]
        if len(set(values)) != len(values):
            raise ValueError(f"Dispatcher {field} values must be unique")
    entry_by_slug = {entry["agent_slug"]: entry for entry in entries}
    entries = [entry_by_slug[agent_slug] for agent_slug in REVIEWED_AGENT_SLUGS]
    payload: dict[str, object] = {"schema_version": 1, "identities": entries}
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        output.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hash six private host dispatcher identities for Mission Control."
    )
    parser.add_argument(
        "--identity-config", action="append", type=Path, default=[], dest="identities"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        provision(args.identities, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": "provisioned", "identity_count": 6, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
