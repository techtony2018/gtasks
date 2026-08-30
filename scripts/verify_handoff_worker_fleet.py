#!/usr/bin/env python3
"""Read-only fleet verifier for the three Mission Control Codex workers."""

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


COMMON_WORKER_KEYS = frozenset(
    {
        "name",
        "transport",
        "expected_agent_slug",
        "expected_route",
        "config_path",
        "repo_path",
        "launch_label",
    }
)
SSH_WORKER_KEYS = COMMON_WORKER_KEYS | {"ssh_target"}
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
        or payload.get("schema_version") != 2
        or not isinstance(payload.get("workers"), list)
        or not payload["workers"]
    ):
        raise ValueError("remote worker inventory must match the exact schema")
    names: set[str] = set()
    for worker in payload["workers"]:
        expected_keys = (
            SSH_WORKER_KEYS
            if isinstance(worker, dict) and worker.get("transport") == "ssh"
            else COMMON_WORKER_KEYS
        )
        if not isinstance(worker, dict) or set(worker) != expected_keys:
            raise ValueError("remote worker inventory must match the exact worker schema")
        for key in expected_keys:
            if not isinstance(worker[key], str) or not worker[key]:
                raise ValueError("remote worker inventory fields must be non-empty strings")
        if worker["transport"] not in {"local", "ssh"}:
            raise ValueError("worker transport must be local or ssh")
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


def _local_command(
    worker: dict[str, str], *, expected_commit: str
) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts" / "verify_handoff_worker_runtime.py"),
        "--config",
        worker["config_path"],
        "--expected-agent",
        worker["expected_agent_slug"],
        "--expected-commit",
        expected_commit,
        "--repo",
        worker["repo_path"],
        "--launch-label",
        worker["launch_label"],
        "--timeout",
        "20",
    ]


def _local_head() -> str:
    result = _default_run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        timeout=10,
    )
    head = result.stdout.strip()
    if result.returncode != 0 or not head:
        raise RuntimeError("could not determine local repository HEAD")
    return head


def _worker_failure(worker: dict[str, str], issue: str, stderr: str = "") -> dict[str, object]:
    return {
        "ok": False,
        "name": worker["name"],
        "transport": worker["transport"],
        "target": worker.get("ssh_target", "local"),
        "expected_agent_slug": worker["expected_agent_slug"],
        "expected_route": worker["expected_route"],
        "issues": [issue],
        "stderr_summary": stderr[:240] if stderr else "",
    }


def _tailscale_peer_for_target(
    ssh_target: str,
    *,
    run: Callable[..., CompletedProbe] | None = None,
) -> dict[str, object] | None:
    host = ssh_target.rsplit("@", 1)[-1]
    execute = run or _default_run
    result = execute(["tailscale", "status", "--json"], timeout=10)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    peers = payload.get("Peer") if isinstance(payload, dict) else None
    if not isinstance(peers, dict):
        return None
    for raw_peer in peers.values():
        if not isinstance(raw_peer, dict):
            continue
        ips = raw_peer.get("TailscaleIPs")
        dns = raw_peer.get("DNSName")
        names = {
            str(dns).rstrip(".") if dns is not None else "",
            str(raw_peer.get("HostName") or ""),
        }
        ip_values = [str(ip) for ip in ips] if isinstance(ips, list) else []
        if host not in ip_values and host.rstrip(".") not in names:
            continue
        return {
            "host": str(raw_peer.get("HostName") or ""),
            "dns": str(dns or ""),
            "ips": ip_values,
            "online": bool(raw_peer.get("Online")),
            "expired": bool(raw_peer.get("Expired")),
            "last_seen": str(raw_peer.get("LastSeen") or ""),
        }
    return None


def verify_fleet(
    *,
    inventory_path: str | Path,
    expected_commit: str | None = None,
    run: Callable[..., CompletedProbe] | None = None,
    ssh_timeout: int = 8,
) -> dict[str, object]:
    inventory = load_inventory(inventory_path)
    execute = run or _default_run
    expected = expected_commit or _local_head()
    reports: list[dict[str, object]] = []
    for raw_worker in inventory["workers"]:
        worker = dict(raw_worker)
        is_local = worker["transport"] == "local"
        result = execute(
            (
                _local_command(worker, expected_commit=expected)
                if is_local
                else _ssh_command(
                    worker, expected_commit=expected, ssh_timeout=ssh_timeout
                )
            ),
            timeout=35 if is_local else max(ssh_timeout + 30, 35),
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
                remote_report["transport"] = worker["transport"]
                remote_report["target"] = worker.get("ssh_target", "local")
                reports.append(remote_report)
            else:
                failure = _worker_failure(
                    worker,
                    "local_probe_failed" if is_local else "ssh_unreachable",
                    result.stderr,
                )
                peer = (
                    None
                    if is_local
                    else _tailscale_peer_for_target(worker["ssh_target"], run=execute)
                )
                if peer is not None:
                    failure["tailscale_peer"] = peer
                    if peer.get("expired"):
                        failure["issues"].append("tailscale_key_expired")
                    if peer.get("online") is False:
                        failure["issues"].append("tailscale_peer_offline")
                reports.append(failure)
            continue
        if remote_report is None:
            reports.append(_worker_failure(worker, "invalid_worker_report"))
            continue
        report = dict(remote_report)
        report["name"] = worker["name"]
        report["transport"] = worker["transport"]
        report["target"] = worker.get("ssh_target", "local")
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
