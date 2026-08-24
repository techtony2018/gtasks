#!/usr/bin/env python3
"""Read-only fleet verifier for remote Mission Control handoff workers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


WORKER_KEYS = frozenset(
    {
        "name",
        "ssh_target",
        "expected_agent_slug",
        "expected_route",
        "config_path",
        "repo_path",
        "launch_label",
    }
)
SECRET_KEY_FRAGMENTS = ("token", "secret", "credential", "registration", "thread")


@dataclass(frozen=True, slots=True)
class CompletedProbe:
    returncode: int
    stdout: str
    stderr: str


def _default_run(command: Sequence[str], **kwargs: object) -> CompletedProbe:
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    return CompletedProbe(
        int(result.returncode),
        str(result.stdout or ""),
        str(result.stderr or ""),
    )


def _reject_secret_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS):
                raise ValueError("remote worker inventory must not contain secrets")
            _reject_secret_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret_keys(child)


def load_inventory(path: str | Path) -> dict[str, object]:
    inventory_path = Path(path)
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("remote worker inventory must be valid JSON") from exc
    _reject_secret_keys(payload)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "workers"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("workers"), list)
        or not payload["workers"]
    ):
        raise ValueError("remote worker inventory must match the exact schema")
    names: set[str] = set()
    for worker in payload["workers"]:
        if not isinstance(worker, dict) or set(worker) != WORKER_KEYS:
            raise ValueError("remote worker inventory must match the exact worker schema")
        for key in WORKER_KEYS:
            if not isinstance(worker[key], str) or not worker[key]:
                raise ValueError("remote worker inventory fields must be non-empty strings")
        if worker["name"] in names:
            raise ValueError("remote worker inventory worker names must be unique")
        names.add(worker["name"])
    return payload


def _ssh_command(
    worker: dict[str, str],
    *,
    expected_commit: str | None,
    ssh_timeout: int,
) -> list[str]:
    expected_commit_arg = json.dumps(expected_commit) if expected_commit else '"$expected"'
    remote_script = (
        "set -euo pipefail\n"
        f"cd {json.dumps(worker['repo_path'])}\n"
        "expected=$(git rev-parse HEAD)\n"
        f"python3 scripts/verify_handoff_worker_runtime.py "
        f"--config {json.dumps(worker['config_path'])} "
        f"--expected-agent {json.dumps(worker['expected_agent_slug'])} "
        f"--expected-commit {expected_commit_arg} "
        f"--repo {json.dumps(worker['repo_path'])} "
        f"--launch-label {json.dumps(worker['launch_label'])} "
        "--timeout 20\n"
    )
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={ssh_timeout}",
        worker["ssh_target"],
        f"bash -lc {shlex.quote(remote_script)}",
    ]


def _worker_failure(worker: dict[str, str], issue: str, stderr: str = "") -> dict[str, object]:
    return {
        "ok": False,
        "name": worker["name"],
        "ssh_target": worker["ssh_target"],
        "expected_agent_slug": worker["expected_agent_slug"],
        "expected_route": worker["expected_route"],
        "issues": [issue],
        "stderr_summary": stderr[:240] if stderr else "",
    }


def verify_fleet(
    *,
    inventory_path: str | Path,
    expected_commit: str | None = None,
    run: Callable[..., CompletedProbe] | None = None,
    ssh_timeout: int = 8,
) -> dict[str, object]:
    inventory = load_inventory(inventory_path)
    execute = run or _default_run
    reports: list[dict[str, object]] = []
    for raw_worker in inventory["workers"]:
        worker = dict(raw_worker)
        result = execute(
            _ssh_command(worker, expected_commit=expected_commit, ssh_timeout=ssh_timeout),
            timeout=max(ssh_timeout + 30, 35),
        )
        remote_report: dict[str, object] | None = None
        if result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
                if isinstance(parsed, dict):
                    remote_report = parsed
            except json.JSONDecodeError:
                remote_report = None
        if result.returncode != 0:
            if remote_report is not None:
                remote_report = dict(remote_report)
                remote_report["name"] = worker["name"]
                remote_report["ssh_target"] = worker["ssh_target"]
                reports.append(remote_report)
            else:
                reports.append(_worker_failure(worker, "ssh_unreachable", result.stderr))
            continue
        if remote_report is None:
            reports.append(_worker_failure(worker, "invalid_worker_report"))
            continue
        report = dict(remote_report)
        report["name"] = worker["name"]
        report["ssh_target"] = worker["ssh_target"]
        reports.append(report)
    ok_count = sum(1 for report in reports if report.get("ok") is True)
    failed_count = len(reports) - ok_count
    return {
        "ok": failed_count == 0,
        "inventory_path": str(Path(inventory_path).resolve()),
        "summary": {"ok": ok_count, "failed": failed_count},
        "workers": reports,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--ssh-timeout", type=int, default=8)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify_fleet(
        inventory_path=args.inventory,
        expected_commit=args.expected_commit,
        ssh_timeout=args.ssh_timeout,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
