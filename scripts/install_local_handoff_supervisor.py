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
import re
import shutil
import stat
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
LEGACY_LABEL = "com.tony.gtasks-handoff-dispatcher"
PLIST_KEYS = frozenset(
    {
        "Label",
        "ProgramArguments",
        "WorkingDirectory",
        "EnvironmentVariables",
        "RunAtLoad",
        "KeepAlive",
        "ProcessType",
    }
)


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
    legacy_state: str
    transition_state: str
    activated: bool


@dataclass(frozen=True, slots=True)
class LaunchLabelSnapshot:
    label: str
    state: str
    loaded: bool
    disabled: bool
    plist_exists: bool
    plist: dict[str, object] | None

    @property
    def enabled(self) -> bool:
        return self.plist_exists and not self.disabled


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    exists: bool
    content: bytes | None
    mode: int | None


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
        / f"{LEGACY_LABEL}.plist",
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
    placeholders = set(re.findall(r"__([A-Z][A-Z0-9_]*)__", template))
    if placeholders - set(replacements):
        raise ValueError("plist template contains an unresolved placeholder")
    rendered = template
    for name, value in replacements.items():
        rendered = rendered.replace(f"__{name}__", escape(value))
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


def _expected_supervisor_plist(
    *,
    label: str,
    arguments: list[str],
    working_directory: str,
    module_root: str,
) -> dict[str, object]:
    return {
        "Label": label,
        "ProgramArguments": arguments,
        "WorkingDirectory": working_directory,
        "EnvironmentVariables": {"PYTHONPATH": module_root},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
    }


def _parse_exact_plist(
    content: bytes,
    *,
    expected: dict[str, object],
    description: str,
) -> dict[str, object]:
    try:
        value = plistlib.loads(content)
    except plistlib.InvalidFileException as exc:
        raise ValueError(f"{description} is invalid") from exc
    if not isinstance(value, dict) or not _plist_value_matches_exactly(value, expected):
        raise ValueError(f"{description} does not match the exact canonical contract")
    return value


def _plist_value_matches_exactly(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False
        return all(
            _plist_value_matches_exactly(actual[key], expected_value)
            for key, expected_value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _plist_value_matches_exactly(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected


def _parse_disabled_state(output: str, label: str) -> bool:
    for raw_line in output.splitlines():
        if "=>" not in raw_line:
            continue
        raw_key, raw_value = raw_line.rsplit("=>", 1)
        if raw_key.strip().strip('"') != label:
            continue
        value = raw_value.strip().lower()
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("launchctl returned an invalid legacy disabled state")
    return False


def _read_label_disabled_state(
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    label: str,
) -> bool:
    disabled_readback = run(
        ["/bin/launchctl", "print-disabled", launch_domain],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if disabled_readback.returncode != 0:
        raise ValueError("LaunchAgent disabled state could not be verified")
    return _parse_disabled_state(disabled_readback.stdout, label)


def _read_legacy_disabled_state(
    run: Callable[..., subprocess.CompletedProcess[str]], launch_domain: str
) -> bool:
    return _read_label_disabled_state(run, launch_domain, LEGACY_LABEL)


def _validated_legacy_plist(
    plist_path: Path,
    config_path: Path,
) -> dict[str, object]:
    if plist_path.is_symlink():
        raise ValueError("legacy LaunchAgent plist must not be a symbolic link")
    try:
        value = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError("legacy LaunchAgent plist is invalid") from exc
    if not isinstance(value, dict) or set(value) != PLIST_KEYS:
        raise ValueError("legacy LaunchAgent plist does not have the exact contract")
    arguments = value.get("ProgramArguments")
    environment = value.get("EnvironmentVariables")
    working_directory = value.get("WorkingDirectory")
    if (
        value.get("Label") != LEGACY_LABEL
        or value.get("RunAtLoad") is not True
        or value.get("KeepAlive") is not True
        or value.get("ProcessType") != "Background"
        or not isinstance(arguments, list)
        or len(arguments) != 9
        or any(not isinstance(item, str) or not item for item in arguments)
        or arguments[1:4]
        != ["-m", "gtasks.local_handoff_dispatcher", "--config"]
        or Path(arguments[4]).resolve() != config_path.resolve()
        or arguments[5] != "--codex-path"
        or arguments[7] != "--working-directory"
        or not Path(arguments[0]).is_absolute()
        or not Path(arguments[6]).is_absolute()
        or not isinstance(working_directory, str)
        or arguments[8] != working_directory
        or not isinstance(environment, dict)
        or set(environment) != {"PYTHONPATH"}
        or not isinstance(environment.get("PYTHONPATH"), str)
    ):
        raise ValueError("legacy LaunchAgent plist does not have the exact contract")
    DispatcherConfig.from_file(config_path).read_token()
    return value


def _inspect_legacy_state(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    legacy_ref: str,
    legacy_config: Path,
    legacy_plist: Path,
    disabled: bool | None = None,
    loaded_readback: subprocess.CompletedProcess[str] | None = None,
) -> LaunchLabelSnapshot:
    if disabled is None:
        disabled = _read_legacy_disabled_state(run, launch_domain)
    if loaded_readback is None:
        loaded_readback = run(
            ["/bin/launchctl", "print", legacy_ref],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    loaded = loaded_readback.returncode == 0
    plist_present = legacy_plist.exists() or legacy_plist.is_symlink()
    parsed_plist: dict[str, object] | None = None
    if loaded or (plist_present and not disabled):
        if not plist_present:
            raise ValueError(
                "loaded legacy LaunchAgent cannot be verified without its canonical plist"
            )
        parsed_plist = _validated_legacy_plist(legacy_plist, legacy_config)
        if loaded and not _loaded_contract_matches(
            loaded_readback.stdout,
            expected_arguments=list(parsed_plist["ProgramArguments"]),
            expected_working_directory=str(parsed_plist["WorkingDirectory"]),
            expected_module_root=str(
                dict(parsed_plist["EnvironmentVariables"])["PYTHONPATH"]
            ),
        ):
            raise ValueError("loaded legacy LaunchAgent contract could not be verified")
    state = _launch_state_name(
        loaded=loaded,
        disabled=disabled,
        plist_exists=plist_present,
    )
    return LaunchLabelSnapshot(
        label=LEGACY_LABEL,
        state=state,
        loaded=loaded,
        disabled=disabled,
        plist_exists=plist_present,
        plist=parsed_plist,
    )


def _launch_state_name(*, loaded: bool, disabled: bool, plist_exists: bool) -> str:
    if loaded:
        return "loaded"
    if disabled:
        return "disabled"
    if plist_exists:
        return "enabled"
    return "absent"


def _capture_file_snapshot(path: Path, description: str) -> FileSnapshot:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return FileSnapshot(exists=False, content=None, mode=None)
    except OSError as exc:
        raise ValueError(f"{description} could not be inspected") from exc
    if stat.S_ISLNK(details.st_mode):
        raise ValueError(f"{description} must not be a symbolic link")
    if not stat.S_ISREG(details.st_mode):
        raise ValueError(f"{description} must be a regular file")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{description} could not be read") from exc
    return FileSnapshot(
        exists=True,
        content=content,
        mode=stat.S_IMODE(details.st_mode),
    )


def _restore_file_snapshot(path: Path, snapshot: FileSnapshot) -> None:
    if snapshot.exists:
        if snapshot.content is None or snapshot.mode is None:
            raise RuntimeError("file rollback snapshot is incomplete")
        _atomic_write(path, snapshot.content, snapshot.mode)
    else:
        path.unlink(missing_ok=True)


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
    replace_legacy: bool = False,
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
            "-B",
            "-c",
            (
                "from pathlib import Path; "
                "import gtasks.local_handoff_supervisor as module; "
                "print(Path(module.__file__).resolve())"
            ),
        ],
        cwd=str(resolved_working_directory),
        env={
            "PYTHONPATH": str(resolved_module_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
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
    expected_plist = _expected_supervisor_plist(
        label=label,
        arguments=expected_arguments,
        working_directory=str(resolved_working_directory),
        module_root=str(resolved_module_root),
    )
    _parse_exact_plist(
        plist_bytes,
        expected=expected_plist,
        description="rendered supervisor plist",
    )
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
    _validate_existing_worker(paths.codex_worker_config, ordered_workers[0])
    _validate_existing_worker(paths.openclaw_worker_config, ordered_workers[1])
    if paths.supervisor_config.exists():
        existing_supervisor = SupervisorConfig.from_file(paths.supervisor_config)
        if existing_supervisor != installed_supervisor:
            raise ValueError("existing supervisor config does not match canonical workers")
    prior_supervisor_plist = _capture_file_snapshot(
        paths.plist, "existing canonical supervisor plist"
    )
    if prior_supervisor_plist.exists:
        if prior_supervisor_plist.content is None:
            raise ValueError("existing canonical supervisor plist is invalid")
        _parse_exact_plist(
            prior_supervisor_plist.content,
            expected=expected_plist,
            description="existing canonical supervisor plist",
        )

    launch_domain = f"gui/{os.getuid()}"
    launch_ref = f"{launch_domain}/{label}"
    legacy_ref = f"{launch_domain}/{LEGACY_LABEL}"
    legacy_config, legacy_plist = canonical_single_worker_install_paths(
        home_directory if home_directory is not None else Path.home()
    )
    disabled_readback = run(
        ["/bin/launchctl", "print-disabled", launch_domain],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if disabled_readback.returncode != 0:
        raise ValueError("LaunchAgent disabled states could not be snapshotted")
    supervisor_disabled = _parse_disabled_state(disabled_readback.stdout, label)
    legacy_disabled = _parse_disabled_state(
        disabled_readback.stdout, LEGACY_LABEL
    )
    supervisor_readback = run(
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
    if supervisor_readback.returncode == 0 and not canonical_files_present:
        raise ValueError(
            "loaded supervisor identity cannot be verified without canonical files"
        )
    if supervisor_readback.returncode == 0 and not _loaded_contract_matches(
        supervisor_readback.stdout,
        expected_arguments=expected_arguments,
        expected_working_directory=str(resolved_working_directory),
        expected_module_root=str(resolved_module_root),
    ):
        raise ValueError("loaded supervisor does not match the exact canonical contract")
    supervisor = LaunchLabelSnapshot(
        label=label,
        state=_launch_state_name(
            loaded=supervisor_readback.returncode == 0,
            disabled=supervisor_disabled,
            plist_exists=prior_supervisor_plist.exists,
        ),
        loaded=supervisor_readback.returncode == 0,
        disabled=supervisor_disabled,
        plist_exists=prior_supervisor_plist.exists,
        plist=(expected_plist if prior_supervisor_plist.exists else None),
    )
    legacy_loaded_readback = run(
        ["/bin/launchctl", "print", legacy_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    legacy = _inspect_legacy_state(
        run=run,
        launch_domain=launch_domain,
        legacy_ref=legacy_ref,
        legacy_config=legacy_config,
        legacy_plist=legacy_plist,
        disabled=legacy_disabled,
        loaded_readback=legacy_loaded_readback,
    )
    legacy_active = legacy.loaded or legacy.enabled
    if (supervisor.loaded and legacy.loaded) or (
        supervisor.enabled and legacy.enabled
    ):
        raise ValueError(
            "concurrent legacy and supervisor state is unsafe; both are loaded or enabled"
        )
    if legacy_active and replace_legacy:
        transition_state = "would_replace_legacy" if dry_run else "legacy_replaced"
    elif legacy_active:
        transition_state = f"blocked_legacy_{legacy.state}"
    elif dry_run:
        transition_state = "would_fence_legacy"
    elif legacy.disabled:
        transition_state = "legacy_fence_preserved"
    else:
        transition_state = "legacy_fenced"
    receipt = InstallReceipt(
        label=label,
        supervisor_config_path=str(paths.supervisor_config),
        supervisor_config_sha256=sha256(supervisor_bytes).hexdigest(),
        plist_path=str(paths.plist),
        plist_sha256=sha256(plist_bytes).hexdigest(),
        workers=(worker_receipts[0], worker_receipts[1]),
        legacy_state=legacy.state,
        transition_state=transition_state,
        activated=not dry_run,
    )
    if dry_run:
        return receipt
    if legacy_active and not replace_legacy:
        raise ValueError(
            "legacy LaunchAgent is active; pass --replace-legacy for a verified transition"
        )

    try:
        _atomic_write(paths.codex_worker_config, worker_bytes[0], 0o600)
        _atomic_write(paths.openclaw_worker_config, worker_bytes[1], 0o600)
        _atomic_write(paths.supervisor_config, supervisor_bytes, 0o600)
        _atomic_write(paths.plist, plist_bytes, 0o644)
    except OSError as exc:
        try:
            _restore_file_snapshot(paths.plist, prior_supervisor_plist)
        except (OSError, RuntimeError):
            raise RuntimeError(
                "supervisor file staging failed and plist rollback failed"
            ) from exc
        raise RuntimeError("supervisor file staging failed; plist rolled back") from exc

    def launchctl(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def label_disabled(label_name: str) -> bool:
        return _read_label_disabled_state(run, launch_domain, label_name)

    def set_label_disabled(reference: str, label_name: str, value: bool) -> bool:
        operation = "disable" if value else "enable"
        result = launchctl(["/bin/launchctl", operation, reference])
        if result.returncode != 0:
            return False
        try:
            return label_disabled(label_name) is value
        except ValueError:
            return False

    def loaded_readback(reference: str) -> subprocess.CompletedProcess[str]:
        return launchctl(["/bin/launchctl", "print", reference])

    def force_unloaded(reference: str) -> bool:
        launchctl(["/bin/launchctl", "bootout", reference])
        return loaded_readback(reference).returncode != 0

    def stop_loaded(reference: str) -> bool:
        stopped = launchctl(["/bin/launchctl", "bootout", reference])
        return stopped.returncode == 0 and loaded_readback(reference).returncode != 0

    def loaded_contract_matches(
        snapshot: LaunchLabelSnapshot,
        readback: subprocess.CompletedProcess[str],
    ) -> bool:
        if readback.returncode != 0 or snapshot.plist is None:
            return False
        return _loaded_contract_matches(
            readback.stdout,
            expected_arguments=list(snapshot.plist["ProgramArguments"]),
            expected_working_directory=str(snapshot.plist["WorkingDirectory"]),
            expected_module_root=str(
                dict(snapshot.plist["EnvironmentVariables"])["PYTHONPATH"]
            ),
        )

    def restore_label(
        snapshot: LaunchLabelSnapshot,
        reference: str,
        plist_path: Path,
    ) -> bool:
        current = loaded_readback(reference)
        if snapshot.loaded:
            if not set_label_disabled(reference, snapshot.label, False):
                return False
            if current.returncode != 0:
                restored = launchctl(
                    ["/bin/launchctl", "bootstrap", launch_domain, str(plist_path)]
                )
                if restored.returncode != 0:
                    return False
                current = loaded_readback(reference)
            if not loaded_contract_matches(snapshot, current):
                return False
            if snapshot.disabled and not set_label_disabled(
                reference, snapshot.label, True
            ):
                return False
            return True
        if current.returncode == 0 and not force_unloaded(reference):
            return False
        return set_label_disabled(
            reference, snapshot.label, snapshot.disabled
        )

    def file_snapshot_restored() -> bool:
        try:
            current = _capture_file_snapshot(
                paths.plist, "restored canonical supervisor plist"
            )
        except ValueError:
            return False
        return current == prior_supervisor_plist

    def rollback_exact_pre_state() -> bool:
        try:
            set_label_disabled(launch_ref, label, True)
            if not force_unloaded(launch_ref):
                return False
            legacy_safely_disabled = set_label_disabled(
                legacy_ref, LEGACY_LABEL, True
            )
            if not legacy_safely_disabled and (
                supervisor.loaded or supervisor.enabled
            ):
                return False
            legacy_current = loaded_readback(legacy_ref)
            if (
                legacy_current.returncode == 0
                and not legacy.loaded
                and not force_unloaded(legacy_ref)
            ):
                return False
            _restore_file_snapshot(paths.plist, prior_supervisor_plist)
            if not restore_label(supervisor, launch_ref, paths.plist):
                return False
            if not restore_label(legacy, legacy_ref, legacy_plist):
                return False

            supervisor_final = loaded_readback(launch_ref)
            legacy_final = loaded_readback(legacy_ref)
            supervisor_loaded_final = supervisor_final.returncode == 0
            legacy_loaded_final = legacy_final.returncode == 0
            if supervisor_loaded_final != supervisor.loaded:
                return False
            if legacy_loaded_final != legacy.loaded:
                return False
            if supervisor.loaded and not loaded_contract_matches(
                supervisor, supervisor_final
            ):
                return False
            if legacy.loaded and not loaded_contract_matches(legacy, legacy_final):
                return False
            if label_disabled(label) is not supervisor.disabled:
                return False
            if label_disabled(LEGACY_LABEL) is not legacy.disabled:
                return False
            if not file_snapshot_restored():
                return False
            if supervisor_loaded_final and legacy_loaded_final:
                return False
            supervisor_enabled_final = (
                prior_supervisor_plist.exists and not supervisor.disabled
            )
            legacy_enabled_final = (
                (legacy_plist.exists() or legacy_plist.is_symlink())
                and not legacy.disabled
            )
            if supervisor_enabled_final and legacy_enabled_final:
                return False
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def activation_failed(message: str) -> None:
        if rollback_exact_pre_state():
            raise RuntimeError(f"{message}; exact pre-state rolled back")
        raise RuntimeError(f"{message}; exact pre-state rollback failed")

    if not set_label_disabled(legacy_ref, LEGACY_LABEL, True):
        activation_failed("legacy LaunchAgent could not be durably disabled")
    if legacy.loaded and not stop_loaded(legacy_ref):
        activation_failed("legacy LaunchAgent could not be stopped")
    if supervisor.loaded and not stop_loaded(launch_ref):
        activation_failed("existing supervisor LaunchAgent could not be stopped")
    if not set_label_disabled(launch_ref, label, False):
        activation_failed("supervisor LaunchAgent could not be durably enabled")

    bootstrap = launchctl(
        ["/bin/launchctl", "bootstrap", launch_domain, str(paths.plist)]
    )
    if bootstrap.returncode != 0:
        activation_failed("supervisor LaunchAgent bootstrap failed")
    supervisor_final = loaded_readback(launch_ref)
    if supervisor_final.returncode != 0 or not _loaded_contract_matches(
        supervisor_final.stdout,
        expected_arguments=expected_arguments,
        expected_working_directory=str(resolved_working_directory),
        expected_module_root=str(resolved_module_root),
    ):
        activation_failed("supervisor LaunchAgent readback failed")
    legacy_final = loaded_readback(legacy_ref)
    try:
        final_legacy_disabled = label_disabled(LEGACY_LABEL)
        final_supervisor_disabled = label_disabled(label)
    except ValueError:
        final_legacy_disabled = False
        final_supervisor_disabled = True
    if (
        legacy_final.returncode == 0
        or not final_legacy_disabled
        or final_supervisor_disabled
    ):
        activation_failed("LaunchAgent final isolation readback failed")

    try:
        installed = SupervisorConfig.from_file(paths.supervisor_config)
        installed_workers = load_isolated_workers(installed)
    except (OSError, ValueError):
        activation_failed("installed worker configuration readback failed")
    if installed != installed_supervisor or tuple(
        worker.to_json_dict() for worker in installed_workers
    ) != tuple(worker.to_json_dict() for worker in ordered_workers):
        activation_failed("installed worker configuration readback failed")
    try:
        _parse_exact_plist(
            paths.plist.read_bytes(),
            expected=expected_plist,
            description="rendered supervisor plist",
        )
    except (OSError, ValueError):
        activation_failed("rendered supervisor LaunchAgent failed readback")
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
    parser.add_argument("--replace-legacy", action="store_true")
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
            replace_legacy=args.replace_legacy,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        _parser().error(str(exc))
    print(json.dumps(asdict(receipt), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
