#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtasks.domain import ARTIFACT_BY_AGENT


def initialize_token_files(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise ValueError("Publisher token directory must not be group/world accessible")
    result: dict[str, Path] = {}
    for agent in sorted(ARTIFACT_BY_AGENT):
        key = agent.split("/", 1)[1]
        path = directory / f"{key}.token"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ValueError("Private publisher token already exists") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        result[agent] = path
    return result


def _read_token(path: Path) -> str:
    try:
        mode = path.stat().st_mode & 0o777
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("Private publisher token is unavailable") from exc
    if mode != 0o600:
        raise ValueError("Private publisher token must use mode 0600")
    if len(token) < 32 or len(token) > 512 or "\n" in token or "\r" in token:
        raise ValueError("Private publisher token is invalid")
    return token


def provision(output: Path, token_files: dict[str, Path]) -> dict[str, object]:
    expected_agents = set(ARTIFACT_BY_AGENT)
    if set(token_files) != expected_agents:
        raise ValueError("Publisher token files must cover the installed identities exactly")
    publishers = []
    digests: set[str] = set()
    for agent in sorted(token_files):
        token = _read_token(token_files[agent])
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if digest in digests:
            raise ValueError("Private publisher tokens must be unique")
        digests.add(digest)
        publishers.append({"agent_slug": agent, "token_sha256": digest})

    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.parent.stat().st_mode & 0o077:
        raise ValueError("Publisher credential directory must not be group/world accessible")
    payload = {"schema_version": 1, "publishers": publishers}
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        text=True,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return {
        "credential_file": str(output),
        "publisher_count": len(publishers),
        "verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision hashed Mission Control Artifact publisher credentials."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--token-file",
        action="append",
        default=[],
        metavar="AGENT_SLUG=PATH",
    )
    parser.add_argument("--initialize-token-dir", type=Path)
    args = parser.parse_args()
    token_files: dict[str, Path] = {}
    for item in args.token_file:
        agent, separator, raw_path = item.partition("=")
        if not separator or not agent or not raw_path or agent in token_files:
            parser.error("--token-file must be a unique AGENT_SLUG=PATH value")
        token_files[agent] = Path(raw_path)
    if args.initialize_token_dir:
        if token_files:
            parser.error("--initialize-token-dir cannot be combined with --token-file")
        try:
            token_files = initialize_token_files(args.initialize_token_dir)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        print(json.dumps(provision(args.output, token_files), sort_keys=True))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
