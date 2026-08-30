#!/usr/bin/env python3
"""Read-only verifier for one deployed Mission Control handoff worker runtime."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtasks.local_handoff_dispatcher import (  # noqa: E402
    DispatcherConfig,
    RejectRedirectHandler,
)

ROUTE_BY_AGENT = {
    "agents/tammy": "hosts/tammy",
    "agents/timmy": "hosts/timmy",
    "agents/toddy": "hosts/toddy",
}


@dataclass(frozen=True, slots=True)
class CompletedProbe:
    returncode: int
    stdout: str
    stderr: str


def _sha12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _completed_probe(result: object) -> CompletedProbe:
    return CompletedProbe(
        int(getattr(result, "returncode", 1)),
        str(getattr(result, "stdout", "") or ""),
        str(getattr(result, "stderr", "") or ""),
    )


def _default_run(command: Sequence[str], **kwargs: object) -> CompletedProbe:
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    return _completed_probe(result)


def _post_preflight(
    worker: DispatcherConfig,
    *,
    opener: Callable[..., object] | None,
    timeout: float,
) -> tuple[str, Mapping[str, object] | None]:
    token = worker.read_token()
    request = Request(
        f"{worker.mission_control_url.rstrip('/')}/api/handoffs/preflight",
        data=json.dumps(
            {"registration_id": worker.registration_id},
            separators=(",", ":"),
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    open_request = opener or build_opener(
        ProxyHandler({}), RejectRedirectHandler()
    ).open
    try:
        with open_request(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code == 401:
            return "preflight_unauthorized", None
        if exc.code == 403:
            return "preflight_forbidden", None
        if exc.code == 404:
            return "preflight_not_found", None
        return f"preflight_http_{int(exc.code)}", None
    except (OSError, TimeoutError, URLError):
        return "preflight_unreachable", None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "preflight_invalid_json", None
    if not isinstance(payload, dict):
        return "preflight_invalid_shape", None
    return "preflight_ok", payload


def _read_repo_head(
    repo_path: Path | None,
    *,
    run: Callable[..., CompletedProbe],
) -> str | None:
    if repo_path is None:
        return None
    result = run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        timeout=10,
    )
    if result.returncode != 0:
        return None
    head = result.stdout.strip()
    return head or None


def _launch_label_loaded(
    label: str | None,
    *,
    run: Callable[..., CompletedProbe],
) -> bool | None:
    if not label:
        return None
    result = run(["launchctl", "list"], timeout=10)
    if result.returncode != 0:
        return None
    return label in result.stdout


def verify_worker_runtime(
    *,
    config_path: str | Path,
    expected_agent_slug: str,
    expected_commit: str | None = None,
    repo_path: str | Path | None = None,
    launch_label: str | None = None,
    opener: Callable[..., object] | None = None,
    run: Callable[..., CompletedProbe] | None = None,
    timeout: float = 15,
) -> dict[str, object]:
    worker = DispatcherConfig.from_file(Path(config_path))
    execute = run or _default_run
    expected_route = ROUTE_BY_AGENT.get(worker.agent_slug)
    issues: list[str] = []
    if expected_route is None:
        issues.append("unsupported_agent_identity")
    if worker.agent_slug != expected_agent_slug:
        issues.append("agent_identity_mismatch")

    status, payload = _post_preflight(worker, opener=opener, timeout=timeout)
    if status != "preflight_ok":
        issues.append(status)
        preflight_verified = False
        route: str | None = None
        registration_ref: str | None = None
    else:
        route = payload.get("route") if isinstance(payload, dict) else None
        registration_ref = (
            payload.get("registration_ref") if isinstance(payload, dict) else None
        )
        preflight_verified = bool(
            isinstance(payload, dict)
            and payload.get("verified") is True
            and payload.get("agent_slug") == worker.agent_slug
            and route == expected_route
            and isinstance(registration_ref, str)
            and registration_ref
        )
        if not preflight_verified:
            issues.append("preflight_mismatch")

    repo = Path(repo_path) if repo_path is not None else None
    repo_head = _read_repo_head(repo, run=execute)
    if expected_commit is not None and repo_head != expected_commit:
        issues.append("repo_head_mismatch")

    launch_loaded = _launch_label_loaded(launch_label, run=execute)
    if launch_label and launch_loaded is not True:
        issues.append("launch_label_not_loaded")

    return {
        "ok": not issues,
        "agent_slug": worker.agent_slug,
        "expected_agent_slug": expected_agent_slug,
        "route": route,
        "expected_route": expected_route,
        "preflight_verified": preflight_verified,
        "registration_ref_sha12": registration_ref[:12]
        if isinstance(registration_ref, str)
        else None,
        "mission_control_url": worker.mission_control_url,
        "config_path": str(Path(config_path).resolve()),
        "token_file_sha12": _sha12(str(worker.token_file.resolve())),
        "fixed_thread_sha12": _sha12(worker.fixed_thread_id),
        "repo_path": str(repo.resolve()) if repo is not None else None,
        "repo_head": repo_head,
        "expected_commit": expected_commit,
        "launch_label": launch_label,
        "launch_loaded": launch_loaded,
        "issues": issues,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="private dispatcher config")
    parser.add_argument("--expected-agent", required=True, help="expected Agent slug")
    parser.add_argument("--expected-commit", help="expected GTasks commit SHA")
    parser.add_argument("--repo", help="GTasks checkout path")
    parser.add_argument("--launch-label", help="expected launchd label")
    parser.add_argument("--timeout", type=float, default=15)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify_worker_runtime(
        config_path=args.config,
        expected_agent_slug=args.expected_agent,
        expected_commit=args.expected_commit,
        repo_path=args.repo,
        launch_label=args.launch_label,
        timeout=args.timeout,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
