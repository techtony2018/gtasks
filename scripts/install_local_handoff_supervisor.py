#!/usr/bin/env python3
"""Install or dry-run one deterministic paired local handoff supervisor."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, NamedTuple, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtasks.local_handoff_dispatcher import (  # noqa: E402
    CodexResumeAdapter,
    DispatcherConfig,
)
from gtasks.local_handoff_supervisor import (  # noqa: E402
    SupervisorConfig,
    claim_store_path_for,
    load_isolated_workers,
    worker_runtime,
)
from gtasks.openclaw_adapter import OpenClawSessionAdapter  # noqa: E402


DEFAULT_LABEL = "com.tony.gtasks-handoff-dispatcher-supervisor"


class CanonicalInstallPaths(NamedTuple):
    supervisor_config: Path
    codex_worker_config: Path
    openclaw_worker_config: Path
    plist: Path


@dataclass(frozen=True, slots=True)
class WorkerInstallReceipt:
    agent_slug: str
    runtime: str
    config_path: str
    config_sha256: str
    claim_store_path: str
    executable_path: str
    runtime_version: str


@dataclass(frozen=True, slots=True)
class InstallReceipt:
    label: str
    supervisor_config_path: str
    supervisor_config_sha256: str
    plist_path: str
    plist_sha256: str
    workers: tuple[WorkerInstallReceipt, WorkerInstallReceipt]
    activated: bool


def canonical_install_paths(home_directory: str | Path) -> CanonicalInstallPaths:
    home = Path(home_directory).resolve()
    base = (
        home
        / "Library"
        / "Application Support"
        / "GTasks"
        / "handoff-dispatcher"
    )
    return CanonicalInstallPaths(
        supervisor_config=base / "supervisor.json",
        codex_worker_config=base / "workers" / "codex.json",
        openclaw_worker_config=base / "workers" / "openclaw.json",
        plist=home / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist",
    )


def canonical_single_worker_install_paths(
    home_directory: str | Path,
) -> tuple[Path, Path]:
    """Retain the existing one-worker install contract during canary rollout."""
    home = Path(home_directory).resolve()
    return (
        home / "Library" / "Application Support" / "GTasks" / "handoff-dispatcher.json",
        home
        / "Library"
        / "LaunchAgents"
        / "com.tony.gtasks-handoff-dispatcher.plist",
    )


def _resolve_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise ValueError("executable must exist and be executable")
        return str(resolved)
    discovered = shutil.which(value)
    if discovered is None:
        raise ValueError("executable could not be resolved to an absolute path")
    return str(Path(discovered).resolve())


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _render_template(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for name, value in replacements.items():
        rendered = rendered.replace(f"__{name}__", escape(value))
    if "__" in rendered:
        raise ValueError("plist template contains an unresolved placeholder")
    return rendered


def _parse_launchctl_contract(
    output: str,
) -> tuple[list[str], str | None, dict[str, str]]:
    arguments: list[str] = []
    working_directory: str | None = None
    environment: dict[str, str] = {}
    section: str | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line == "arguments = {":
            section = "arguments"
            continue
        if line == "environment = {":
            section = "environment"
            continue
        if line == "}":
            section = None
            continue
        if raw_line.startswith("\tworking directory = "):
            working_directory = raw_line.split(" = ", 1)[1]
            continue
        if section == "arguments" and line:
            arguments.append(line)
        elif section == "environment" and " => " in line:
            key, value = line.split(" => ", 1)
            environment[key] = value
    return arguments, working_directory, environment


def _loaded_contract_matches(
    output: str,
    *,
    expected_arguments: list[str],
    expected_working_directory: str,
    expected_module_root: str,
) -> bool:
    arguments, working_directory, environment = _parse_launchctl_contract(output)
    return (
        arguments == expected_arguments
        and working_directory == expected_working_directory
        and environment.get("PYTHONPATH") == expected_module_root
    )


def _serialized_config(config: DispatcherConfig) -> bytes:
    return (json.dumps(config.to_json_dict(), indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _validate_existing_worker(
    destination: Path, expected: DispatcherConfig
) -> None:
    if not destination.exists():
        return
    existing = DispatcherConfig.from_file(destination)
    if (
        existing.agent_slug != expected.agent_slug
        or existing.registration_id != expected.registration_id
    ):
        raise ValueError("existing worker config belongs to another identity")
    if existing.fixed_thread_id != expected.fixed_thread_id:
        raise ValueError("existing worker fixed runtime binding must be preserved")


def install(
    *,
    source_worker_configs: tuple[str | Path, str | Path],
    plist_template: str | Path,
    python_path: str,
    module_root: str | Path,
    runner_path: str | Path,
    codex_path: str,
    openclaw_path: str,
    working_directory: str | Path,
    home_directory: str | Path | None = None,
    label: str = DEFAULT_LABEL,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    dry_run: bool = False,
) -> InstallReceipt:
    if not isinstance(source_worker_configs, tuple) or len(source_worker_configs) != 2:
        raise ValueError("exactly two source worker configs are required")
    if label != DEFAULT_LABEL:
        raise ValueError("installer requires the canonical supervisor label")
    paths = canonical_install_paths(
        home_directory if home_directory is not None else Path.home()
    )
    source_supervisor = SupervisorConfig(
        schema_version=1,
        worker_config_paths=(
            Path(source_worker_configs[0]).expanduser(),
            Path(source_worker_configs[1]).expanduser(),
        ),
    )
    source_workers = load_isolated_workers(source_supervisor)
    source_by_runtime = {worker_runtime(worker): worker for worker in source_workers}
    ordered_workers = (source_by_runtime["codex"], source_by_runtime["openclaw"])
    destination_by_runtime = {
        "codex": paths.codex_worker_config,
        "openclaw": paths.openclaw_worker_config,
    }

    resolved_python = _resolve_executable(python_path)
    if Path(resolved_python) == Path("/usr/bin/python3"):
        raise ValueError("installer must not use /usr/bin/python3")
    resolved_codex = _resolve_executable(codex_path)
    resolved_openclaw = _resolve_executable(openclaw_path)
    resolved_module_root = Path(module_root).resolve()
    resolved_runner = Path(runner_path).resolve()
    expected_runner = resolved_module_root / "gtasks" / "local_handoff_supervisor.py"
    if not resolved_module_root.is_dir():
        raise ValueError("module root must be an existing directory")
    if not (resolved_module_root / "gtasks" / "__init__.py").is_file():
        raise ValueError("module root must contain the GTasks package")
    if resolved_runner != expected_runner.resolve() or not resolved_runner.is_file():
        raise ValueError("runner must be the local supervisor module under module root")
    resolved_working_directory = Path(working_directory).resolve()
    if not resolved_working_directory.is_dir():
        raise ValueError("Agent working directory must be an existing directory")

    import_probe = run(
        [
            resolved_python,
            "-c",
            (
                "from pathlib import Path; "
                "import gtasks.local_handoff_supervisor as module; "
                "print(Path(module.__file__).resolve())"
            ),
        ],
        cwd=str(resolved_working_directory),
        env={"PYTHONPATH": str(resolved_module_root)},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if import_probe.returncode != 0 or import_probe.stdout.strip() != str(
        resolved_runner
    ):
        raise ValueError("configured Python does not resolve the verified supervisor module")

    codex_version = CodexResumeAdapter(
        resolved_codex,
        fixed_thread_id=source_by_runtime["codex"].fixed_thread_id,
        working_directory=resolved_working_directory,
        run=run,
    ).verify_contract()
    openclaw_version = OpenClawSessionAdapter(
        executable=resolved_openclaw,
        session_key=source_by_runtime["openclaw"].fixed_thread_id,
        timeout_seconds=10,
        working_directory=resolved_working_directory,
        run=run,
    ).verify_contract()

    worker_bytes = tuple(_serialized_config(worker) for worker in ordered_workers)
    installed_supervisor = SupervisorConfig(
        schema_version=1,
        worker_config_paths=(
            paths.codex_worker_config.resolve(),
            paths.openclaw_worker_config.resolve(),
        ),
    )
    supervisor_bytes = (
        json.dumps(installed_supervisor.to_json_dict(), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    expected_arguments = [
        resolved_python,
        "-m",
        "gtasks.local_handoff_supervisor",
        "--config",
        str(paths.supervisor_config.resolve()),
        "--codex-path",
        resolved_codex,
        "--openclaw-path",
        resolved_openclaw,
        "--working-directory",
        str(resolved_working_directory),
    ]
    template_text = Path(plist_template).read_text(encoding="utf-8")
    plist_text = _render_template(
        template_text,
        {
            "LABEL": label,
            "PYTHON_PATH": resolved_python,
            "SUPERVISOR_CONFIG_PATH": str(paths.supervisor_config.resolve()),
            "CODEX_PATH": resolved_codex,
            "OPENCLAW_PATH": resolved_openclaw,
            "WORKING_DIRECTORY": str(resolved_working_directory),
            "MODULE_ROOT": str(resolved_module_root),
        },
    )
    plist_bytes = plist_text.encode("utf-8")
    worker_receipts = tuple(
        WorkerInstallReceipt(
            agent_slug=worker.agent_slug,
            runtime=runtime,
            config_path=str(destination_by_runtime[runtime]),
            config_sha256=sha256(content).hexdigest(),
            claim_store_path=str(claim_store_path_for(destination_by_runtime[runtime])),
            executable_path=(resolved_codex if runtime == "codex" else resolved_openclaw),
            runtime_version=(codex_version if runtime == "codex" else openclaw_version),
        )
        for worker, content, runtime in zip(
            ordered_workers, worker_bytes, ("codex", "openclaw")
        )
    )
    receipt = InstallReceipt(
        label=label,
        supervisor_config_path=str(paths.supervisor_config),
        supervisor_config_sha256=sha256(supervisor_bytes).hexdigest(),
        plist_path=str(paths.plist),
        plist_sha256=sha256(plist_bytes).hexdigest(),
        workers=(worker_receipts[0], worker_receipts[1]),
        activated=not dry_run,
    )
    _validate_existing_worker(paths.codex_worker_config, ordered_workers[0])
    _validate_existing_worker(paths.openclaw_worker_config, ordered_workers[1])
    if paths.supervisor_config.exists():
        existing_supervisor = SupervisorConfig.from_file(paths.supervisor_config)
        if existing_supervisor != installed_supervisor:
            raise ValueError("existing supervisor config does not match canonical workers")
    if paths.plist.exists():
        try:
            existing_plist = plistlib.loads(paths.plist.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise ValueError("existing canonical supervisor plist is invalid") from exc
        if (
            existing_plist.get("Label") != label
            or existing_plist.get("ProgramArguments") != expected_arguments
            or existing_plist.get("WorkingDirectory")
            != str(resolved_working_directory)
            or existing_plist.get("EnvironmentVariables")
            != {"PYTHONPATH": str(resolved_module_root)}
        ):
            raise ValueError("existing supervisor plist does not match canonical contract")
    if dry_run:
        return receipt

    launch_domain = f"gui/{os.getuid()}"
    launch_ref = f"{launch_domain}/{label}"
    loaded = run(
        ["/bin/launchctl", "print", launch_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    canonical_files_present = all(
        path.exists()
        for path in (
            paths.supervisor_config,
            paths.codex_worker_config,
            paths.openclaw_worker_config,
            paths.plist,
        )
    )
    if loaded.returncode == 0 and not canonical_files_present:
        raise ValueError(
            "loaded supervisor identity cannot be verified without canonical files"
        )
    if loaded.returncode == 0 and not _loaded_contract_matches(
        loaded.stdout,
        expected_arguments=expected_arguments,
        expected_working_directory=str(resolved_working_directory),
        expected_module_root=str(resolved_module_root),
    ):
        raise ValueError("loaded supervisor does not match the exact canonical contract")

    _atomic_write(paths.codex_worker_config, worker_bytes[0], 0o600)
    _atomic_write(paths.openclaw_worker_config, worker_bytes[1], 0o600)
    _atomic_write(paths.supervisor_config, supervisor_bytes, 0o600)
    _atomic_write(paths.plist, plist_bytes, 0o644)
    if loaded.returncode == 0:
        run(
            ["/bin/launchctl", "bootout", launch_ref],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    bootstrap = run(
        ["/bin/launchctl", "bootstrap", launch_domain, str(paths.plist)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if bootstrap.returncode != 0:
        raise RuntimeError("supervisor LaunchAgent bootstrap failed")
    readback = run(
        ["/bin/launchctl", "print", launch_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if readback.returncode != 0 or not _loaded_contract_matches(
        readback.stdout,
        expected_arguments=expected_arguments,
        expected_working_directory=str(resolved_working_directory),
        expected_module_root=str(resolved_module_root),
    ):
        raise RuntimeError("supervisor LaunchAgent readback failed")

    installed = SupervisorConfig.from_file(paths.supervisor_config)
    installed_workers = load_isolated_workers(installed)
    if installed != installed_supervisor or tuple(
        worker.to_json_dict() for worker in installed_workers
    ) != tuple(worker.to_json_dict() for worker in ordered_workers):
        raise RuntimeError("installed worker configuration readback failed")
    rendered_plist = plistlib.loads(paths.plist.read_bytes())
    if (
        rendered_plist.get("Label") != label
        or rendered_plist.get("ProgramArguments") != expected_arguments
        or rendered_plist.get("WorkingDirectory") != str(resolved_working_directory)
        or rendered_plist.get("EnvironmentVariables")
        != {"PYTHONPATH": str(resolved_module_root)}
    ):
        raise RuntimeError("rendered supervisor LaunchAgent failed readback")
    return receipt


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worker-config", action="append", type=Path, default=[], dest="workers"
    )
    parser.add_argument(
        "--plist-template",
        type=Path,
        default=root
        / "config"
        / "openclaw-agents"
        / "dispatcher-supervisor.plist.template",
    )
    parser.add_argument("--python-path", default=sys.executable)
    parser.add_argument("--module-root", type=Path, default=root)
    parser.add_argument(
        "--runner-path",
        type=Path,
        default=root / "gtasks" / "local_handoff_supervisor.py",
    )
    parser.add_argument("--codex-path", default="codex")
    parser.add_argument("--openclaw-path", default="openclaw")
    parser.add_argument("--working-directory", type=Path, default=root)
    parser.add_argument("--home-directory", type=Path, default=Path.home())
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if len(args.workers) != 2:
            raise ValueError("exactly two --worker-config files are required")
        receipt = install(
            source_worker_configs=(args.workers[0], args.workers[1]),
            plist_template=args.plist_template,
            python_path=args.python_path,
            module_root=args.module_root,
            runner_path=args.runner_path,
            codex_path=args.codex_path,
            openclaw_path=args.openclaw_path,
            working_directory=args.working_directory,
            home_directory=args.home_directory,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _parser().error(str(exc))
    print(json.dumps(asdict(receipt), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
