"""Run one approved Codex/OpenClaw worker pair without merging their state."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hmac
import json
import os
from pathlib import Path
import sys
import threading
from typing import Callable, Sequence

from .local_handoff_dispatcher import (
    CodexResumeAdapter,
    DispatcherConfig,
    LocalDispatcherClient,
    PrivateClaimStore,
    PrivateWakeInbox,
    _require_private_regular_file,
    install_signal_handlers,
    run_forever,
)
from .openclaw_adapter import OpenClawSessionAdapter


SUPERVISOR_SCHEMA_VERSION = 1
SUPERVISOR_CONFIG_KEYS = frozenset({"schema_version", "worker_config_paths"})
_WORKER_DECLARATIONS: dict[str, tuple[str, str]] = {
    "agents/tammy": ("codex", "hosts/tammy"),
    "agents/tammy-oc": ("openclaw", "hosts/tammy"),
    "agents/timmy": ("codex", "hosts/timmy"),
    "agents/timmy-oc": ("openclaw", "hosts/timmy"),
    "agents/toddy": ("codex", "hosts/toddy"),
    "agents/toddy-oc": ("openclaw", "hosts/toddy"),
}
_APPROVED_WORKER_PAIRS = frozenset(
    {
        frozenset({"agents/tammy", "agents/tammy-oc"}),
        frozenset({"agents/timmy", "agents/timmy-oc"}),
        frozenset({"agents/toddy", "agents/toddy-oc"}),
    }
)


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    schema_version: int
    worker_config_paths: tuple[Path, Path]

    def __post_init__(self) -> None:
        if self.schema_version != SUPERVISOR_SCHEMA_VERSION:
            raise ValueError("supervisor schema_version must be 1")
        if (
            not isinstance(self.worker_config_paths, tuple)
            or len(self.worker_config_paths) != 2
            or any(not isinstance(path, Path) for path in self.worker_config_paths)
        ):
            raise ValueError("supervisor must contain exactly two worker config paths")

    @classmethod
    def from_file(cls, path: str | Path) -> "SupervisorConfig":
        config_path = Path(path)
        _require_private_regular_file(config_path, "supervisor config")
        try:
            value = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("supervisor config must contain valid UTF-8 JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != SUPERVISOR_CONFIG_KEYS
            or value.get("schema_version") != SUPERVISOR_SCHEMA_VERSION
        ):
            raise ValueError("supervisor config must contain exactly the documented fields")
        raw_paths = value.get("worker_config_paths")
        if (
            not isinstance(raw_paths, list)
            or len(raw_paths) != 2
            or any(not isinstance(item, str) or not item for item in raw_paths)
        ):
            raise ValueError("supervisor must contain exactly two worker config paths")
        resolved_paths: list[Path] = []
        for raw_path in raw_paths:
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = config_path.parent / candidate
            if candidate.is_symlink():
                raise ValueError("worker config path must not be a symbolic link")
            resolved_paths.append(candidate.resolve())
        return cls(
            schema_version=SUPERVISOR_SCHEMA_VERSION,
            worker_config_paths=(resolved_paths[0], resolved_paths[1]),
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "worker_config_paths": [str(path) for path in self.worker_config_paths],
        }


def worker_runtime(config: DispatcherConfig) -> str:
    try:
        return _WORKER_DECLARATIONS[config.agent_slug][0]
    except KeyError as exc:
        raise ValueError("worker Agent identity is not reviewed") from exc


def worker_route(config: DispatcherConfig) -> str:
    try:
        return _WORKER_DECLARATIONS[config.agent_slug][1]
    except KeyError as exc:
        raise ValueError("worker Agent identity is not reviewed") from exc


def claim_store_path_for(config_path: str | Path) -> Path:
    path = Path(config_path)
    return path.with_name(f"{path.stem}.active-claim.json").resolve()


def _wake_inbox_path_for(claim_path: Path) -> Path:
    legacy = claim_path.with_name(f"{claim_path.stem}.wake-dedupe.sqlite3")
    if legacy.exists():
        return legacy
    return claim_path.with_name(f"{claim_path.stem}.wake-inbox.sqlite3")


def load_isolated_workers(
    config: SupervisorConfig,
) -> tuple[DispatcherConfig, DispatcherConfig]:
    """Load and validate one exact host pair while retaining two config objects."""
    if any(path.is_symlink() for path in config.worker_config_paths):
        raise ValueError("worker config path must not be a symbolic link")
    paths = tuple(path.resolve() for path in config.worker_config_paths)
    if paths[0] == paths[1]:
        raise ValueError("worker config paths must be distinct")
    workers = (
        DispatcherConfig.from_file(paths[0]),
        DispatcherConfig.from_file(paths[1]),
    )
    agent_slugs = tuple(worker.agent_slug for worker in workers)
    if len(set(agent_slugs)) != 2:
        raise ValueError("workers must use distinct Agent identities")
    if frozenset(agent_slugs) not in _APPROVED_WORKER_PAIRS:
        raise ValueError(
            "workers must be one reviewed Codex/OpenClaw pair on one approved host route"
        )
    runtimes = tuple(worker_runtime(worker) for worker in workers)
    if set(runtimes) != {"codex", "openclaw"}:
        raise ValueError("supervisor requires one Codex and one OpenClaw worker")
    routes = tuple(worker_route(worker) for worker in workers)
    if routes[0] != routes[1]:
        raise ValueError("workers must use the same approved host route")
    if workers[0].mission_control_url != workers[1].mission_control_url:
        raise ValueError("workers must use the same Mission Control origin")
    if hmac.compare_digest(workers[0].registration_id, workers[1].registration_id):
        raise ValueError("worker registrations must be distinct")
    token_paths = tuple(worker.token_file.resolve() for worker in workers)
    if token_paths[0] == token_paths[1]:
        raise ValueError("worker credential files and tokens must be distinct")
    first_token = workers[0].read_token()
    second_token = workers[1].read_token()
    if hmac.compare_digest(first_token, second_token):
        raise ValueError("worker credential files and tokens must be distinct")
    claim_paths = tuple(claim_store_path_for(path) for path in paths)
    if claim_paths[0] == claim_paths[1]:
        raise ValueError("workers must use separate private claim stores")
    return workers


@dataclass(frozen=True, slots=True)
class WorkerFailure:
    agent_slug: str
    runtime: str
    error_type: str


WorkerRunner = Callable[[Path, DispatcherConfig, Callable[[], bool]], None]


def _privacy_safe_failure(config: DispatcherConfig, error: Exception) -> WorkerFailure:
    error_type = type(error).__name__
    if not error_type.isidentifier() or len(error_type) > 128:
        error_type = "WorkerError"
    return WorkerFailure(
        agent_slug=config.agent_slug,
        runtime=worker_runtime(config),
        error_type=error_type,
    )


def _report_failure(failure: WorkerFailure) -> None:
    print(json.dumps(asdict(failure), sort_keys=True), file=sys.stderr, flush=True)


def _run_worker(
    config_path: Path,
    config: DispatcherConfig,
    stop_requested: Callable[[], bool],
    *,
    codex_path: str,
    openclaw_path: str,
    working_directory: str | Path,
    wait_seconds: int,
    lease_seconds: int,
    codex_resume_timeout: float,
    openclaw_timeout: int,
) -> None:
    claim_path = claim_store_path_for(config_path)
    claim_store = PrivateClaimStore(claim_path)
    wake_inbox = PrivateWakeInbox(_wake_inbox_path_for(claim_path))
    try:
        client = LocalDispatcherClient(
            config.mission_control_url,
            registration_id=config.registration_id,
            bearer_token=config.read_token(),
            agent_slug=config.agent_slug,
        )
        runtime = worker_runtime(config)
        if runtime == "codex":
            adapter = CodexResumeAdapter(
                codex_path,
                fixed_thread_id=config.fixed_thread_id,
                working_directory=working_directory,
                resume_timeout=codex_resume_timeout,
                acknowledgement_helper=(
                    sys.executable,
                    "-m",
                    "gtasks.local_handoff_dispatcher",
                    "ack",
                    "--config",
                    str(config_path.resolve()),
                    "--claim-file",
                    str(claim_path),
                ),
            )
        else:
            adapter = OpenClawSessionAdapter(
                executable=openclaw_path,
                session_key=config.fixed_thread_id,
                timeout_seconds=openclaw_timeout,
                working_directory=working_directory,
            )
        adapter.verify_contract()
        run_forever(
            client,
            adapter,
            wait_seconds=wait_seconds,
            lease_seconds=lease_seconds,
            stop_requested=stop_requested,
            claim_store=claim_store,
            wake_inbox=wake_inbox,
        )
    finally:
        wake_inbox.close()


def run_supervisor(
    config: SupervisorConfig,
    *,
    worker_runner: WorkerRunner | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
    report_failure: Callable[[WorkerFailure], None] = _report_failure,
    poll_interval: float = 0.05,
    codex_path: str = "codex",
    openclaw_path: str = "openclaw",
    working_directory: str | Path = ".",
    wait_seconds: int = 25,
    lease_seconds: int = 120,
    codex_resume_timeout: float = 300,
    openclaw_timeout: int = 300,
) -> tuple[WorkerFailure, ...]:
    """Supervise two threads; one failure never transfers its config or state."""
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    workers = load_isolated_workers(config)
    worker_paths = tuple(path.resolve() for path in config.worker_config_paths)
    stop_event = threading.Event()
    failures: list[tuple[int, WorkerFailure]] = []
    failure_lock = threading.Lock()

    if worker_runner is None:
        def selected_runner(
            config_path: Path,
            worker: DispatcherConfig,
            worker_stop_requested: Callable[[], bool],
        ) -> None:
            _run_worker(
                config_path,
                worker,
                worker_stop_requested,
                codex_path=codex_path,
                openclaw_path=openclaw_path,
                working_directory=working_directory,
                wait_seconds=wait_seconds,
                lease_seconds=lease_seconds,
                codex_resume_timeout=codex_resume_timeout,
                openclaw_timeout=openclaw_timeout,
            )
    else:
        selected_runner = worker_runner

    def combined_stop_requested() -> bool:
        return stop_event.is_set() or stop_requested()

    def run_one(index: int, path: Path, worker: DispatcherConfig) -> None:
        try:
            selected_runner(path, worker, combined_stop_requested)
        except Exception as exc:
            failure = _privacy_safe_failure(worker, exc)
            with failure_lock:
                failures.append((index, failure))
            report_failure(failure)

    threads = [
        threading.Thread(
            target=run_one,
            args=(index, path, worker),
            name=f"handoff-{worker_runtime(worker)}-worker",
        )
        for index, (path, worker) in enumerate(zip(worker_paths, workers))
    ]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            if stop_requested():
                stop_event.set()
            for thread in threads:
                thread.join(poll_interval)
    finally:
        stop_event.set()
        for thread in threads:
            thread.join()
    return tuple(failure for _index, failure in sorted(failures))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--codex-path", default="codex")
    parser.add_argument("--openclaw-path", default="openclaw")
    parser.add_argument("--working-directory", default=os.getcwd())
    parser.add_argument("--wait-seconds", type=int, default=25)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--codex-resume-timeout", type=float, default=300)
    parser.add_argument("--openclaw-timeout", type=int, default=300)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    failures = run_supervisor(
        SupervisorConfig.from_file(args.config),
        stop_requested=install_signal_handlers(),
        codex_path=args.codex_path,
        openclaw_path=args.openclaw_path,
        working_directory=args.working_directory,
        wait_seconds=args.wait_seconds,
        lease_seconds=args.lease_seconds,
        codex_resume_timeout=args.codex_resume_timeout,
        openclaw_timeout=args.openclaw_timeout,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
