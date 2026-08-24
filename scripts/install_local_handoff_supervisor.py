#!/usr/bin/env python3
"""Install or dry-run one deterministic paired local handoff supervisor."""

from __future__ import annotations

import argparse
import base64
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
import time
from typing import Callable, NamedTuple, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gtasks.handoff_install_lock import (  # noqa: E402
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    install_lock_path,
    locked_handoff_install,
)
from gtasks.local_handoff_dispatcher import (  # noqa: E402
    CodexResumeAdapter,
    DispatcherConfig,
    LocalDispatcherClient,
)
from gtasks.local_handoff_supervisor import (  # noqa: E402
    SupervisorConfig,
    claim_store_path_for,
    load_isolated_workers,
    worker_route,
    worker_runtime,
)
from gtasks.openclaw_adapter import OpenClawSessionAdapter  # noqa: E402


DEFAULT_LABEL = "com.tony.gtasks-handoff-dispatcher-supervisor"
LEGACY_LABEL = "com.tony.gtasks-handoff-dispatcher"
OVERRIDE_ABSENT = "absent"
OVERRIDE_EXPLICITLY_ENABLED = "explicitly_enabled"
OVERRIDE_EXPLICITLY_DISABLED = "explicitly_disabled"
LAUNCHCTL_UNLOAD_TIMEOUT_SECONDS = 10.0
LAUNCHCTL_POLL_INTERVAL_SECONDS = 0.05
LAUNCHCTL_STABLE_READBACK_SECONDS = 0.25
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
RECOVERY_SCHEMA_VERSION = 2
RECOVERY_FILE_KEYS = frozenset(
    {
        "codex_worker_config",
        "openclaw_worker_config",
        "supervisor_config",
        "plist",
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
    override_state: str
    plist_exists: bool
    plist: dict[str, object] | None

    @property
    def disabled(self) -> bool:
        return self.override_state == OVERRIDE_EXPLICITLY_DISABLED

    @property
    def enabled(self) -> bool:
        return self.plist_exists and not self.disabled


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    exists: bool
    content: bytes | None
    mode: int | None


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    status: str
    supervisor: LaunchLabelSnapshot
    legacy: LaunchLabelSnapshot
    files: dict[str, FileSnapshot]
    last_error_type: str | None = None
    rollback_error_types: tuple[str, ...] = ()


class LaunchctlCallError(RuntimeError):
    def __init__(self, stage: str, error_type: str) -> None:
        super().__init__(f"launchctl {stage} failed with {error_type}")
        self.error_type = error_type


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


def recovery_marker_path(paths: CanonicalInstallPaths) -> Path:
    return paths.supervisor_config.parent / ".install-recovery.json"


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


def _resolve_command_parent(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        launcher = candidate.absolute()
    else:
        discovered = shutil.which(value)
        if discovered is None:
            raise ValueError("executable could not be resolved to an absolute path")
        launcher = Path(discovered).absolute()
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ValueError("executable launcher must exist and be executable")
    return launcher.parent.resolve()


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
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
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
    expected_runtime_path: str | None = None,
) -> bool:
    arguments, working_directory, environment = _parse_launchctl_contract(output)
    return (
        arguments == expected_arguments
        and working_directory == expected_working_directory
        and environment.get("PYTHONPATH") == expected_module_root
        and (
            expected_runtime_path is None
            or environment.get("PATH") == expected_runtime_path
        )
    )


def _expected_supervisor_plist(
    *,
    label: str,
    arguments: list[str],
    working_directory: str,
    module_root: str,
    runtime_path: str | None = None,
) -> dict[str, object]:
    environment = {"PYTHONPATH": module_root}
    if runtime_path is not None:
        environment["PATH"] = runtime_path
    return {
        "Label": label,
        "ProgramArguments": arguments,
        "WorkingDirectory": working_directory,
        "EnvironmentVariables": environment,
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


def _parse_override_state(output: str, label: str) -> str:
    for raw_line in output.splitlines():
        if "=>" not in raw_line:
            continue
        raw_key, raw_value = raw_line.rsplit("=>", 1)
        if raw_key.strip().strip('"') != label:
            continue
        value = raw_value.strip().strip('"').lower()
        if value in {"disabled", "true"}:
            return OVERRIDE_EXPLICITLY_DISABLED
        if value in {"enabled", "false"}:
            return OVERRIDE_EXPLICITLY_ENABLED
        raise ValueError("launchctl returned an invalid label override state")
    return OVERRIDE_ABSENT


def _read_label_override_state(
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    label: str,
) -> str:
    disabled_readback = _run_launchctl(
        run,
        ["/bin/launchctl", "print-disabled", launch_domain],
        stage="snapshot_disabled_labels",
    )
    if disabled_readback.returncode != 0:
        raise ValueError("LaunchAgent disabled state could not be verified")
    return _parse_override_state(disabled_readback.stdout, label)


def _read_legacy_override_state(
    run: Callable[..., subprocess.CompletedProcess[str]], launch_domain: str
) -> str:
    return _read_label_override_state(run, launch_domain, LEGACY_LABEL)


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
    override_state: str | None = None,
    loaded_readback: subprocess.CompletedProcess[str] | None = None,
) -> LaunchLabelSnapshot:
    if override_state is None:
        override_state = _read_legacy_override_state(run, launch_domain)
    disabled = override_state == OVERRIDE_EXPLICITLY_DISABLED
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
        override_state=override_state,
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


def _canonical_file_paths(paths: CanonicalInstallPaths) -> dict[str, Path]:
    return {
        "codex_worker_config": paths.codex_worker_config,
        "openclaw_worker_config": paths.openclaw_worker_config,
        "supervisor_config": paths.supervisor_config,
        "plist": paths.plist,
    }


def _label_snapshot_payload(snapshot: LaunchLabelSnapshot) -> dict[str, object]:
    return {
        "loaded": snapshot.loaded,
        "override_state": snapshot.override_state,
        "plist_exists": snapshot.plist_exists,
        "plist": snapshot.plist,
    }


def _label_snapshot_from_payload(
    value: object, *, label: str
) -> LaunchLabelSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "loaded",
        "override_state",
        "plist_exists",
        "plist",
    }:
        raise ValueError("recovery label snapshot is invalid")
    loaded = value["loaded"]
    override_state = value["override_state"]
    plist_exists = value["plist_exists"]
    plist = value["plist"]
    disabled = override_state == OVERRIDE_EXPLICITLY_DISABLED
    if (
        type(loaded) is not bool
        or override_state
        not in {
            OVERRIDE_ABSENT,
            OVERRIDE_EXPLICITLY_ENABLED,
            OVERRIDE_EXPLICITLY_DISABLED,
        }
        or type(plist_exists) is not bool
        or (plist is not None and not isinstance(plist, dict))
        or ((loaded or (plist_exists and not disabled)) and plist is None)
    ):
        raise ValueError("recovery label snapshot is invalid")
    return LaunchLabelSnapshot(
        label=label,
        state=_launch_state_name(
            loaded=loaded,
            disabled=disabled,
            plist_exists=plist_exists,
        ),
        loaded=loaded,
        override_state=override_state,
        plist_exists=plist_exists,
        plist=plist,
    )


def _file_snapshot_payload(snapshot: FileSnapshot) -> dict[str, object]:
    encoded = (
        base64.b64encode(snapshot.content).decode("ascii")
        if snapshot.content is not None
        else None
    )
    return {
        "exists": snapshot.exists,
        "content_base64": encoded,
        "mode": snapshot.mode,
        "sha256": (
            sha256(snapshot.content).hexdigest()
            if snapshot.content is not None
            else None
        ),
    }


def _file_snapshot_from_payload(value: object) -> FileSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "exists",
        "content_base64",
        "mode",
        "sha256",
    }:
        raise ValueError("recovery file snapshot is invalid")
    exists = value["exists"]
    encoded = value["content_base64"]
    mode = value["mode"]
    digest = value["sha256"]
    if type(exists) is not bool:
        raise ValueError("recovery file snapshot is invalid")
    if not exists:
        if encoded is not None or mode is not None or digest is not None:
            raise ValueError("recovery file snapshot is invalid")
        return FileSnapshot(exists=False, content=None, mode=None)
    if (
        not isinstance(encoded, str)
        or type(mode) is not int
        or mode < 0
        or mode > 0o7777
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError("recovery file snapshot is invalid")
    try:
        content = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("recovery file snapshot is invalid") from exc
    if sha256(content).hexdigest() != digest:
        raise ValueError("recovery file snapshot checksum is invalid")
    return FileSnapshot(exists=True, content=content, mode=mode)


def _recovery_record_payload(record: RecoveryRecord) -> dict[str, object]:
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "status": record.status,
        "supervisor": _label_snapshot_payload(record.supervisor),
        "legacy": _label_snapshot_payload(record.legacy),
        "files": {
            name: _file_snapshot_payload(snapshot)
            for name, snapshot in sorted(record.files.items())
        },
        "last_error_type": record.last_error_type,
        "rollback_error_types": list(record.rollback_error_types),
    }


def _recovery_record_from_payload(value: object) -> RecoveryRecord:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "status",
        "supervisor",
        "legacy",
        "files",
        "last_error_type",
        "rollback_error_types",
    }:
        raise ValueError("supervisor recovery marker is invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != RECOVERY_SCHEMA_VERSION
    ):
        raise ValueError("supervisor recovery marker schema is unsupported")
    status = value["status"]
    if status not in {
        "transitioning",
        "recovery_required",
        "safe_disabled_fallback",
    }:
        raise ValueError("supervisor recovery marker status is invalid")
    files_value = value["files"]
    if not isinstance(files_value, dict) or set(files_value) != RECOVERY_FILE_KEYS:
        raise ValueError("supervisor recovery marker files are invalid")
    last_error_type = value["last_error_type"]
    rollback_error_types = value["rollback_error_types"]
    if last_error_type is not None and (
        not isinstance(last_error_type, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", last_error_type)
    ):
        raise ValueError("supervisor recovery marker error type is invalid")
    if (
        not isinstance(rollback_error_types, list)
        or len(rollback_error_types) > 32
        or any(
            not isinstance(item, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", item)
            for item in rollback_error_types
        )
    ):
        raise ValueError("supervisor recovery marker rollback errors are invalid")
    return RecoveryRecord(
        status=status,
        supervisor=_label_snapshot_from_payload(
            value["supervisor"], label=DEFAULT_LABEL
        ),
        legacy=_label_snapshot_from_payload(value["legacy"], label=LEGACY_LABEL),
        files={
            name: _file_snapshot_from_payload(files_value[name])
            for name in sorted(RECOVERY_FILE_KEYS)
        },
        last_error_type=last_error_type,
        rollback_error_types=tuple(rollback_error_types),
    )


def _write_recovery_record(path: Path, record: RecoveryRecord) -> None:
    serialized = (
        json.dumps(_recovery_record_payload(record), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(path, serialized, 0o600)
    restored = _load_recovery_record(path)
    if restored != record:
        raise RuntimeError("supervisor recovery marker readback failed")


def _load_recovery_record(path: Path) -> RecoveryRecord:
    snapshot = _capture_file_snapshot(path, "supervisor recovery marker")
    if not snapshot.exists or snapshot.content is None or snapshot.mode != 0o600:
        raise ValueError("supervisor recovery marker must be a private 0600 file")
    try:
        value = json.loads(snapshot.content)
    except json.JSONDecodeError as exc:
        raise ValueError("supervisor recovery marker is invalid") from exc
    return _recovery_record_from_payload(value)


def _remove_recovery_record(path: Path) -> None:
    path.unlink()
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _safe_exception_type(exc: BaseException) -> str:
    if isinstance(exc, LaunchctlCallError):
        return exc.error_type
    name = type(exc).__name__
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        return name
    return "Exception"


def _run_launchctl(
    run: Callable[..., subprocess.CompletedProcess[str]],
    arguments: list[str],
    *,
    stage: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchctlCallError(stage, type(exc).__name__) from exc


def _label_override_readback(
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    label: str,
    *,
    stage: str,
) -> str:
    readback = _run_launchctl(
        run,
        ["/bin/launchctl", "print-disabled", launch_domain],
        stage=stage,
    )
    if readback.returncode != 0:
        raise RuntimeError("LaunchAgent disabled state readback failed")
    return _parse_override_state(readback.stdout, label)


def _label_disabled_readback(
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    label: str,
    *,
    stage: str,
) -> bool:
    return (
        _label_override_readback(
            run,
            launch_domain,
            label,
            stage=stage,
        )
        == OVERRIDE_EXPLICITLY_DISABLED
    )


def _set_label_disabled(
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    reference: str,
    label: str,
    disabled: bool,
    *,
    stage: str,
) -> bool:
    operation = "disable" if disabled else "enable"
    result = _run_launchctl(
        run,
        ["/bin/launchctl", operation, reference],
        stage=f"{stage}_{operation}",
    )
    if result.returncode != 0:
        return False
    expected_override = (
        OVERRIDE_EXPLICITLY_DISABLED
        if disabled
        else OVERRIDE_EXPLICITLY_ENABLED
    )
    return _label_override_readback(
        run,
        launch_domain,
        label,
        stage=f"{stage}_{operation}_readback",
    ) == expected_override


def _loaded_readback(
    run: Callable[..., subprocess.CompletedProcess[str]],
    reference: str,
    *,
    stage: str,
) -> subprocess.CompletedProcess[str]:
    return _run_launchctl(
        run,
        ["/bin/launchctl", "print", reference],
        stage=stage,
    )


def _force_unloaded(
    run: Callable[..., subprocess.CompletedProcess[str]],
    reference: str,
    *,
    stage: str,
) -> bool:
    _run_launchctl(
        run,
        ["/bin/launchctl", "bootout", reference],
        stage=f"{stage}_bootout",
    )
    deadline = time.monotonic() + LAUNCHCTL_UNLOAD_TIMEOUT_SECONDS
    attempt = 0
    while True:
        attempt += 1
        if _loaded_readback(
            run,
            reference,
            stage=f"{stage}_unloaded_readback_{attempt}",
        ).returncode != 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(LAUNCHCTL_POLL_INTERVAL_SECONDS, remaining))


def _stable_loaded_contract_readback(
    run: Callable[..., subprocess.CompletedProcess[str]],
    reference: str,
    snapshot: LaunchLabelSnapshot,
    *,
    stage: str,
) -> subprocess.CompletedProcess[str] | None:
    deadline = time.monotonic() + LAUNCHCTL_STABLE_READBACK_SECONDS
    attempt = 0
    current: subprocess.CompletedProcess[str] | None = None
    while True:
        attempt += 1
        current = _loaded_readback(
            run,
            reference,
            stage=f"{stage}_stable_contract_readback_{attempt}",
        )
        if not _snapshot_loaded_contract_matches(snapshot, current):
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return current
        time.sleep(min(LAUNCHCTL_POLL_INTERVAL_SECONDS, remaining))


def _disable_and_unload_label(
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    reference: str,
    label: str,
    *,
    stage: str,
) -> bool:
    if not _set_label_disabled(
        run,
        launch_domain,
        reference,
        label,
        True,
        stage=stage,
    ):
        return False
    current = _loaded_readback(
        run,
        reference,
        stage=f"{stage}_loaded_readback",
    )
    if current.returncode != 0:
        return True
    return _force_unloaded(run, reference, stage=stage)


def _snapshot_loaded_contract_matches(
    snapshot: LaunchLabelSnapshot,
    readback: subprocess.CompletedProcess[str],
) -> bool:
    if readback.returncode != 0 or snapshot.plist is None:
        return False
    arguments = snapshot.plist.get("ProgramArguments")
    working_directory = snapshot.plist.get("WorkingDirectory")
    environment = snapshot.plist.get("EnvironmentVariables")
    if (
        not isinstance(arguments, list)
        or any(not isinstance(item, str) for item in arguments)
        or not isinstance(working_directory, str)
        or not isinstance(environment, dict)
        or not isinstance(environment.get("PYTHONPATH"), str)
    ):
        return False
    return _loaded_contract_matches(
        readback.stdout,
        expected_arguments=arguments,
        expected_working_directory=working_directory,
        expected_module_root=environment["PYTHONPATH"],
        expected_runtime_path=environment.get("PATH"),
    )


def _restore_label_snapshot(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    reference: str,
    plist_path: Path,
    snapshot: LaunchLabelSnapshot,
    stage: str,
    remove_plist_for_absent_override: bool = False,
) -> bool:
    if snapshot.override_state == OVERRIDE_ABSENT:
        if not _disable_and_unload_label(
            run,
            launch_domain,
            reference,
            snapshot.label,
            stage=stage,
        ):
            return False
        if remove_plist_for_absent_override:
            try:
                plist_path.unlink(missing_ok=True)
                directory_descriptor = os.open(plist_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
            except OSError:
                return False
        return True
    if not snapshot.loaded:
        if not _force_unloaded(run, reference, stage=stage):
            return False
        return _set_label_disabled(
            run,
            launch_domain,
            reference,
            snapshot.label,
            snapshot.disabled,
            stage=stage,
        )
    if not _force_unloaded(run, reference, stage=stage):
        return False
    if not _set_label_disabled(
        run,
        launch_domain,
        reference,
        snapshot.label,
        False,
        stage=stage,
    ):
        return False
    bootstrap = _run_launchctl(
        run,
        ["/bin/launchctl", "bootstrap", launch_domain, str(plist_path)],
        stage=f"{stage}_bootstrap",
    )
    if bootstrap.returncode != 0:
        return False
    if _stable_loaded_contract_readback(
        run,
        reference,
        snapshot,
        stage=stage,
    ) is None:
        return False
    return _set_label_disabled(
        run,
        launch_domain,
        reference,
        snapshot.label,
        snapshot.disabled,
        stage=stage,
    )


def _file_snapshots_match(
    paths: CanonicalInstallPaths, snapshots: dict[str, FileSnapshot]
) -> bool:
    try:
        return all(
            _capture_file_snapshot(path, f"recovered {name}") == snapshots[name]
            for name, path in _canonical_file_paths(paths).items()
        )
    except ValueError:
        return False


def _restore_file_snapshots(
    paths: CanonicalInstallPaths, snapshots: dict[str, FileSnapshot]
) -> None:
    for name, path in _canonical_file_paths(paths).items():
        _restore_file_snapshot(path, snapshots[name])


def _rollback_recovery_record(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    launch_ref: str,
    legacy_ref: str,
    legacy_plist: Path,
    paths: CanonicalInstallPaths,
    record: RecoveryRecord,
) -> tuple[str | None, tuple[str, ...]]:
    errors: list[str] = []
    try:
        files_changed = not _file_snapshots_match(paths, record.files)
        if files_changed:
            if not _disable_and_unload_label(
                run,
                launch_domain,
                launch_ref,
                DEFAULT_LABEL,
                stage="rollback_supervisor_fence",
            ):
                raise RuntimeError("supervisor rollback fence failed")
            if not _disable_and_unload_label(
                run,
                launch_domain,
                legacy_ref,
                LEGACY_LABEL,
                stage="rollback_legacy_fence",
            ):
                raise RuntimeError("legacy rollback fence failed")
            _restore_file_snapshots(paths, record.files)
        if not _restore_label_snapshot(
            run=run,
            launch_domain=launch_domain,
            reference=launch_ref,
            plist_path=paths.plist,
            snapshot=record.supervisor,
            stage="restore_supervisor",
            remove_plist_for_absent_override=True,
        ):
            raise RuntimeError("supervisor state restoration failed")
        if not _restore_label_snapshot(
            run=run,
            launch_domain=launch_domain,
            reference=legacy_ref,
            plist_path=legacy_plist,
            snapshot=record.legacy,
            stage="restore_legacy",
        ):
            raise RuntimeError("legacy state restoration failed")

        supervisor_final = _loaded_readback(
            run,
            launch_ref,
            stage="rollback_supervisor_final_readback",
        )
        legacy_final = _loaded_readback(
            run,
            legacy_ref,
            stage="rollback_legacy_final_readback",
        )
        supervisor_loaded_expected = (
            record.supervisor.loaded
            if record.supervisor.override_state != OVERRIDE_ABSENT
            else False
        )
        legacy_loaded_expected = (
            record.legacy.loaded
            if record.legacy.override_state != OVERRIDE_ABSENT
            else False
        )
        if (supervisor_final.returncode == 0) is not supervisor_loaded_expected:
            raise RuntimeError("supervisor loaded state restoration failed")
        if (legacy_final.returncode == 0) is not legacy_loaded_expected:
            raise RuntimeError("legacy loaded state restoration failed")
        if supervisor_loaded_expected and not _snapshot_loaded_contract_matches(
            record.supervisor, supervisor_final
        ):
            raise RuntimeError("supervisor contract restoration failed")
        if legacy_loaded_expected and not _snapshot_loaded_contract_matches(
            record.legacy, legacy_final
        ):
            raise RuntimeError("legacy contract restoration failed")
        supervisor_override_expected = (
            OVERRIDE_EXPLICITLY_DISABLED
            if record.supervisor.override_state == OVERRIDE_ABSENT
            else record.supervisor.override_state
        )
        legacy_override_expected = (
            OVERRIDE_EXPLICITLY_DISABLED
            if record.legacy.override_state == OVERRIDE_ABSENT
            else record.legacy.override_state
        )
        if _label_override_readback(
            run,
            launch_domain,
            DEFAULT_LABEL,
            stage="rollback_supervisor_disabled_final",
        ) != supervisor_override_expected:
            raise RuntimeError("supervisor override state restoration failed")
        if _label_override_readback(
            run,
            launch_domain,
            LEGACY_LABEL,
            stage="rollback_legacy_disabled_final",
        ) != legacy_override_expected:
            raise RuntimeError("legacy override state restoration failed")
        files_restored = _file_snapshots_match(paths, record.files)
        if record.supervisor.override_state == OVERRIDE_ABSENT:
            files_restored = (
                not paths.plist.exists()
                and all(
                    _capture_file_snapshot(path, f"recovered {name}")
                    == record.files[name]
                    for name, path in _canonical_file_paths(paths).items()
                    if name != "plist"
                )
            )
        if not files_restored:
            raise RuntimeError("installation file restoration failed")
        supervisor_enabled = (
            record.supervisor.override_state == OVERRIDE_EXPLICITLY_ENABLED
        )
        legacy_enabled = (
            record.legacy.override_state == OVERRIDE_EXPLICITLY_ENABLED
        )
        if (
            record.supervisor.loaded and record.legacy.loaded
        ) or (supervisor_enabled and legacy_enabled):
            raise RuntimeError("unsafe concurrent pre-state cannot be restored")
        fallback_used = (
            record.supervisor.override_state == OVERRIDE_ABSENT
            or record.legacy.override_state == OVERRIDE_ABSENT
        )
        return ("safe_disabled_fallback" if fallback_used else "exact"), ()
    except Exception as exc:
        errors.append(_safe_exception_type(exc))
        return None, tuple(errors)


def _force_both_labels_safe(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    launch_domain: str,
    launch_ref: str,
    legacy_ref: str,
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    safe = True
    for label, reference, stage in (
        (DEFAULT_LABEL, launch_ref, "recovery_supervisor"),
        (LEGACY_LABEL, legacy_ref, "recovery_legacy"),
    ):
        label_safe = False
        for attempt in range(2):
            disabled = False
            unloaded = False
            try:
                disabled = _set_label_disabled(
                    run,
                    launch_domain,
                    reference,
                    label,
                    True,
                    stage=f"{stage}_{attempt + 1}",
                )
                if not disabled:
                    errors.append("RuntimeError")
            except Exception as exc:
                errors.append(_safe_exception_type(exc))
            try:
                unloaded = _force_unloaded(
                    run,
                    reference,
                    stage=f"{stage}_{attempt + 1}",
                )
                if not unloaded:
                    errors.append("RuntimeError")
            except Exception as exc:
                errors.append(_safe_exception_type(exc))
            if disabled and unloaded:
                label_safe = True
                break
        safe = safe and label_safe
    return safe, tuple(errors[:32])


def _validate_recovery_record(
    record: RecoveryRecord,
    *,
    paths: CanonicalInstallPaths,
    legacy_config: Path,
    legacy_plist: Path,
) -> None:
    supervisor_file = record.files["plist"]
    if supervisor_file.exists is not record.supervisor.plist_exists:
        raise ValueError("supervisor recovery plist snapshot is inconsistent")
    if supervisor_file.exists:
        if supervisor_file.content is None or record.supervisor.plist is None:
            raise ValueError("supervisor recovery plist snapshot is incomplete")
        try:
            supervisor_plist = plistlib.loads(supervisor_file.content)
        except plistlib.InvalidFileException as exc:
            raise ValueError("supervisor recovery plist snapshot is invalid") from exc
        if not _plist_value_matches_exactly(
            supervisor_plist, record.supervisor.plist
        ):
            raise ValueError("supervisor recovery plist snapshot is inconsistent")
        arguments = supervisor_plist.get("ProgramArguments")
        working_directory = supervisor_plist.get("WorkingDirectory")
        environment = supervisor_plist.get("EnvironmentVariables")
        if (
            set(supervisor_plist) != PLIST_KEYS
            or supervisor_plist.get("Label") != DEFAULT_LABEL
            or supervisor_plist.get("RunAtLoad") is not True
            or supervisor_plist.get("KeepAlive") is not True
            or supervisor_plist.get("ProcessType") != "Background"
            or not isinstance(arguments, list)
            or len(arguments) != 11
            or any(not isinstance(item, str) or not item for item in arguments)
            or arguments[1:4]
            != ["-m", "gtasks.local_handoff_supervisor", "--config"]
            or Path(arguments[4]).resolve() != paths.supervisor_config.resolve()
            or arguments[5] != "--codex-path"
            or arguments[7] != "--openclaw-path"
            or arguments[9] != "--working-directory"
            or not Path(arguments[0]).is_absolute()
            or not Path(arguments[6]).is_absolute()
            or not Path(arguments[8]).is_absolute()
            or not isinstance(working_directory, str)
            or arguments[10] != working_directory
            or not isinstance(environment, dict)
            or set(environment) not in ({"PYTHONPATH"}, {"PYTHONPATH", "PATH"})
            or not isinstance(environment.get("PYTHONPATH"), str)
            or not Path(environment["PYTHONPATH"]).is_absolute()
            or (
                "PATH" in environment
                and (
                    not isinstance(environment["PATH"], str)
                    or not environment["PATH"]
                )
            )
        ):
            raise ValueError("supervisor recovery plist contract is invalid")
    if record.supervisor.loaded and not all(
        record.files[name].exists
        for name in (
            "codex_worker_config",
            "openclaw_worker_config",
            "supervisor_config",
            "plist",
        )
    ):
        raise ValueError("loaded supervisor recovery snapshot is incomplete")
    if record.legacy.loaded and (
        not record.legacy.plist_exists or record.legacy.plist is None
    ):
        raise ValueError("loaded legacy recovery snapshot is incomplete")
    if record.legacy.plist is not None:
        current_legacy = _validated_legacy_plist(legacy_plist, legacy_config)
        if not _plist_value_matches_exactly(current_legacy, record.legacy.plist):
            raise ValueError("legacy recovery plist snapshot has drifted")
    supervisor_enabled = (
        record.supervisor.override_state == OVERRIDE_EXPLICITLY_ENABLED
    )
    legacy_enabled = record.legacy.override_state == OVERRIDE_EXPLICITLY_ENABLED
    if (record.supervisor.loaded and record.legacy.loaded) or (
        supervisor_enabled and legacy_enabled
    ):
        raise ValueError("supervisor recovery marker contains an unsafe pre-state")


def _recovery_required_record(
    record: RecoveryRecord,
    *,
    error_type: str,
    rollback_errors: tuple[str, ...],
    status: str = "recovery_required",
) -> RecoveryRecord:
    return RecoveryRecord(
        status=status,
        supervisor=record.supervisor,
        legacy=record.legacy,
        files=record.files,
        last_error_type=error_type,
        rollback_error_types=tuple(rollback_errors[:32]),
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


def _preflight_worker_boundary(worker: DispatcherConfig) -> None:
    client = LocalDispatcherClient(
        worker.mission_control_url,
        registration_id=worker.registration_id,
        bearer_token=worker.read_token(),
        agent_slug=worker.agent_slug,
    )
    result = client.preflight()
    if result.get("route") != worker_route(worker):
        raise ValueError("authenticated worker preflight route does not match")


@locked_handoff_install
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
    codex_resume_timeout: float = 1800.0,
    home_directory: str | Path | None = None,
    label: str = DEFAULT_LABEL,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    dry_run: bool = False,
    replace_legacy: bool = False,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> InstallReceipt:
    if not isinstance(source_worker_configs, tuple) or len(source_worker_configs) != 2:
        raise ValueError("exactly two source worker configs are required")
    if label != DEFAULT_LABEL:
        raise ValueError("installer requires the canonical supervisor label")
    if codex_resume_timeout <= 0:
        raise ValueError("codex resume timeout must be positive")
    paths = canonical_install_paths(
        home_directory if home_directory is not None else Path.home()
    )
    launch_domain = f"gui/{os.getuid()}"
    launch_ref = f"{launch_domain}/{label}"
    legacy_ref = f"{launch_domain}/{LEGACY_LABEL}"
    legacy_config, legacy_plist = canonical_single_worker_install_paths(
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
    openclaw_command_parent = _resolve_command_parent(openclaw_path)
    resolved_openclaw = _resolve_executable(openclaw_path)
    runtime_path = os.pathsep.join(
        dict.fromkeys(
            (
                str(openclaw_command_parent),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            )
        )
    )
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

    try:
        for worker in ordered_workers:
            _preflight_worker_boundary(worker)
    except Exception as exc:
        raise ValueError(
            "authenticated worker preflight failed before installation mutation"
        ) from exc

    marker_path = recovery_marker_path(paths)
    if marker_path.exists() or marker_path.is_symlink():
        if dry_run:
            raise RuntimeError(
                "supervisor installation recovery is pending; dry-run cannot resume it"
            )
        try:
            pending_recovery = _load_recovery_record(marker_path)
            _validate_recovery_record(
                pending_recovery,
                paths=paths,
                legacy_config=legacy_config,
                legacy_plist=legacy_plist,
            )
        except Exception as exc:
            _force_both_labels_safe(
                run=run,
                launch_domain=launch_domain,
                launch_ref=launch_ref,
                legacy_ref=legacy_ref,
            )
            raise RuntimeError(
                "supervisor recovery marker could not be validated; recovery_required"
            ) from exc
        recovery_outcome, rollback_errors = _rollback_recovery_record(
            run=run,
            launch_domain=launch_domain,
            launch_ref=launch_ref,
            legacy_ref=legacy_ref,
            legacy_plist=legacy_plist,
            paths=paths,
            record=pending_recovery,
        )
        if recovery_outcome is None:
            _safe, safe_errors = _force_both_labels_safe(
                run=run,
                launch_domain=launch_domain,
                launch_ref=launch_ref,
                legacy_ref=legacy_ref,
            )
            updated_recovery = _recovery_required_record(
                pending_recovery,
                error_type=(pending_recovery.last_error_type or "InterruptedInstall"),
                rollback_errors=rollback_errors + safe_errors,
            )
            try:
                _write_recovery_record(marker_path, updated_recovery)
            except Exception:
                pass
            raise RuntimeError(
                "pending supervisor installation could not restore exact pre-state; "
                "recovery_required"
            )
        if recovery_outcome == "safe_disabled_fallback":
            safe_receipt = _recovery_required_record(
                pending_recovery,
                status="safe_disabled_fallback",
                error_type=(pending_recovery.last_error_type or "InterruptedInstall"),
                rollback_errors=rollback_errors,
            )
            try:
                _write_recovery_record(marker_path, safe_receipt)
            except Exception as exc:
                raise RuntimeError(
                    "pending supervisor installation used safe_disabled_fallback; "
                    "recovery receipt update failed; recovery_required"
                ) from exc
            raise RuntimeError(
                "pending supervisor installation used safe_disabled_fallback; "
                "private recovery receipt retained for operator review"
            )
        try:
            _remove_recovery_record(marker_path)
        except OSError as exc:
            raise RuntimeError(
                "supervisor exact pre-state was restored but recovery marker "
                "cleanup failed; recovery_required"
            ) from exc
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
        "--codex-resume-timeout",
        str(int(codex_resume_timeout))
        if float(codex_resume_timeout).is_integer()
        else str(codex_resume_timeout),
    ]
    previous_arguments = [
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
            "CODEX_RESUME_TIMEOUT": str(int(codex_resume_timeout))
            if float(codex_resume_timeout).is_integer()
            else str(codex_resume_timeout),
            "MODULE_ROOT": str(resolved_module_root),
            "RUNTIME_PATH": runtime_path,
        },
    )
    plist_bytes = plist_text.encode("utf-8")
    expected_plist = _expected_supervisor_plist(
        label=label,
        arguments=expected_arguments,
        working_directory=str(resolved_working_directory),
        module_root=str(resolved_module_root),
        runtime_path=runtime_path,
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
    prior_files = {
        name: _capture_file_snapshot(path, f"existing canonical {name}")
        for name, path in _canonical_file_paths(paths).items()
    }
    prior_supervisor_plist = prior_files["plist"]
    prior_supervisor_runtime_path: str | None = runtime_path
    prior_supervisor_arguments = expected_arguments
    if prior_supervisor_plist.exists:
        if prior_supervisor_plist.content is None:
            raise ValueError("existing canonical supervisor plist is invalid")
        previous_plist = _expected_supervisor_plist(
            label=label,
            arguments=previous_arguments,
            working_directory=str(resolved_working_directory),
            module_root=str(resolved_module_root),
        )
        previous_plist_with_runtime_path = _expected_supervisor_plist(
            label=label,
            arguments=previous_arguments,
            working_directory=str(resolved_working_directory),
            module_root=str(resolved_module_root),
            runtime_path=runtime_path,
        )
        previous_plist_without_runtime_path = _expected_supervisor_plist(
            label=label,
            arguments=expected_arguments,
            working_directory=str(resolved_working_directory),
            module_root=str(resolved_module_root),
        )
        try:
            _parse_exact_plist(
                prior_supervisor_plist.content,
                expected=expected_plist,
                description="existing canonical supervisor plist",
            )
        except ValueError:
            try:
                _parse_exact_plist(
                    prior_supervisor_plist.content,
                    expected=previous_plist_without_runtime_path,
                    description="existing canonical supervisor plist",
                )
                prior_supervisor_arguments = expected_arguments
                prior_supervisor_runtime_path = None
            except ValueError:
                try:
                    _parse_exact_plist(
                        prior_supervisor_plist.content,
                        expected=previous_plist_with_runtime_path,
                        description="existing canonical supervisor plist",
                    )
                    prior_supervisor_arguments = previous_arguments
                    prior_supervisor_runtime_path = runtime_path
                except ValueError:
                    _parse_exact_plist(
                        prior_supervisor_plist.content,
                        expected=previous_plist,
                        description="existing canonical supervisor plist",
                    )
                    prior_supervisor_arguments = previous_arguments
                    prior_supervisor_runtime_path = None

    disabled_readback = _run_launchctl(
        run,
        ["/bin/launchctl", "print-disabled", launch_domain],
        stage="snapshot_disabled_labels",
    )
    if disabled_readback.returncode != 0:
        raise ValueError("LaunchAgent disabled states could not be snapshotted")
    supervisor_override = _parse_override_state(disabled_readback.stdout, label)
    legacy_override = _parse_override_state(
        disabled_readback.stdout, LEGACY_LABEL
    )
    supervisor_readback = _run_launchctl(
        run,
        ["/bin/launchctl", "print", launch_ref],
        stage="snapshot_supervisor",
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
        expected_arguments=prior_supervisor_arguments,
        expected_working_directory=str(resolved_working_directory),
        expected_module_root=str(resolved_module_root),
        expected_runtime_path=prior_supervisor_runtime_path,
    ):
        raise ValueError("loaded supervisor does not match the exact canonical contract")
    supervisor = LaunchLabelSnapshot(
        label=label,
        state=_launch_state_name(
            loaded=supervisor_readback.returncode == 0,
            disabled=supervisor_override == OVERRIDE_EXPLICITLY_DISABLED,
            plist_exists=prior_supervisor_plist.exists,
        ),
        loaded=supervisor_readback.returncode == 0,
        override_state=supervisor_override,
        plist_exists=prior_supervisor_plist.exists,
        plist=(expected_plist if prior_supervisor_plist.exists else None),
    )
    legacy_loaded_readback = _run_launchctl(
        run,
        ["/bin/launchctl", "print", legacy_ref],
        stage="snapshot_legacy",
    )
    legacy = _inspect_legacy_state(
        run=run,
        launch_domain=launch_domain,
        legacy_ref=legacy_ref,
        legacy_config=legacy_config,
        legacy_plist=legacy_plist,
        override_state=legacy_override,
        loaded_readback=legacy_loaded_readback,
    )
    legacy_active = legacy.loaded or legacy.enabled
    if (supervisor.loaded and legacy.loaded) or (
        supervisor.override_state == OVERRIDE_EXPLICITLY_ENABLED
        and legacy.override_state == OVERRIDE_EXPLICITLY_ENABLED
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

    recovery = RecoveryRecord(
        status="transitioning",
        supervisor=supervisor,
        legacy=legacy,
        files=prior_files,
    )
    try:
        _write_recovery_record(marker_path, recovery)
    except Exception as exc:
        raise RuntimeError(
            "supervisor recovery marker could not be persisted before mutation"
        ) from exc

    try:
        if not _disable_and_unload_label(
            run,
            launch_domain,
            launch_ref,
            label,
            stage="transition_supervisor_fence",
        ):
            raise RuntimeError(
                "supervisor LaunchAgent could not be durably disabled and unloaded"
            )
        if not _disable_and_unload_label(
            run,
            launch_domain,
            legacy_ref,
            LEGACY_LABEL,
            stage="transition_legacy_fence",
        ):
            raise RuntimeError(
                "legacy LaunchAgent could not be durably disabled and unloaded"
            )

        _atomic_write(paths.codex_worker_config, worker_bytes[0], 0o600)
        _atomic_write(paths.openclaw_worker_config, worker_bytes[1], 0o600)
        _atomic_write(paths.supervisor_config, supervisor_bytes, 0o600)
        _atomic_write(paths.plist, plist_bytes, 0o644)

        installed = SupervisorConfig.from_file(paths.supervisor_config)
        installed_workers = load_isolated_workers(installed)
        if installed != installed_supervisor or tuple(
            worker.to_json_dict() for worker in installed_workers
        ) != tuple(worker.to_json_dict() for worker in ordered_workers):
            raise RuntimeError("installed worker configuration readback failed")
        _parse_exact_plist(
            paths.plist.read_bytes(),
            expected=expected_plist,
            description="rendered supervisor plist",
        )

        if (
            _loaded_readback(
                run,
                launch_ref,
                stage="preactivation_supervisor_unloaded_readback",
            ).returncode
            == 0
            or _loaded_readback(
                run,
                legacy_ref,
                stage="preactivation_legacy_unloaded_readback",
            ).returncode
            == 0
            or not _label_disabled_readback(
                run,
                launch_domain,
                label,
                stage="preactivation_supervisor_disabled_readback",
            )
            or not _label_disabled_readback(
                run,
                launch_domain,
                LEGACY_LABEL,
                stage="preactivation_legacy_disabled_readback",
            )
        ):
            raise RuntimeError("preactivation isolation readback failed")

        if not _set_label_disabled(
            run,
            launch_domain,
            launch_ref,
            label,
            False,
            stage="activate_supervisor",
        ):
            raise RuntimeError(
                "supervisor LaunchAgent could not be durably enabled"
            )
        bootstrap = _run_launchctl(
            run,
            ["/bin/launchctl", "bootstrap", launch_domain, str(paths.plist)],
            stage="activate_supervisor_bootstrap",
        )
        if bootstrap.returncode != 0:
            raise RuntimeError("supervisor LaunchAgent bootstrap failed")
        supervisor_final = _loaded_readback(
            run,
            launch_ref,
            stage="activate_supervisor_contract_readback",
        )
        if supervisor_final.returncode != 0 or not _loaded_contract_matches(
            supervisor_final.stdout,
            expected_arguments=expected_arguments,
            expected_working_directory=str(resolved_working_directory),
            expected_module_root=str(resolved_module_root),
            expected_runtime_path=runtime_path,
        ):
            raise RuntimeError("supervisor LaunchAgent readback failed")
        legacy_final = _loaded_readback(
            run,
            legacy_ref,
            stage="activate_legacy_isolation_readback",
        )
        if (
            legacy_final.returncode == 0
            or not _label_disabled_readback(
                run,
                launch_domain,
                LEGACY_LABEL,
                stage="activate_legacy_disabled_readback",
            )
            or _label_override_readback(
                run,
                launch_domain,
                label,
                stage="activate_supervisor_enabled_readback",
            )
            != OVERRIDE_EXPLICITLY_ENABLED
        ):
            raise RuntimeError("LaunchAgent final isolation readback failed")
        installed = SupervisorConfig.from_file(paths.supervisor_config)
        installed_workers = load_isolated_workers(installed)
        if installed != installed_supervisor or tuple(
            worker.to_json_dict() for worker in installed_workers
        ) != tuple(worker.to_json_dict() for worker in ordered_workers):
            raise RuntimeError("installed worker configuration readback failed")
        _parse_exact_plist(
            paths.plist.read_bytes(),
            expected=expected_plist,
            description="rendered supervisor plist",
        )
        _remove_recovery_record(marker_path)
    except Exception as original_error:
        recovery_outcome, rollback_errors = _rollback_recovery_record(
            run=run,
            launch_domain=launch_domain,
            launch_ref=launch_ref,
            legacy_ref=legacy_ref,
            legacy_plist=legacy_plist,
            paths=paths,
            record=recovery,
        )
        if recovery_outcome == "exact":
            try:
                if marker_path.exists() or marker_path.is_symlink():
                    _remove_recovery_record(marker_path)
            except OSError as cleanup_error:
                raise RuntimeError(
                    "supervisor installation failed; exact pre-state rolled back; "
                    "recovery marker cleanup failed"
                ) from original_error
            raise RuntimeError(
                "supervisor installation failed; exact pre-state rolled back"
            ) from original_error
        if recovery_outcome == "safe_disabled_fallback":
            safe_receipt = _recovery_required_record(
                recovery,
                status="safe_disabled_fallback",
                error_type=_safe_exception_type(original_error),
                rollback_errors=rollback_errors,
            )
            try:
                _write_recovery_record(marker_path, safe_receipt)
            except Exception:
                raise RuntimeError(
                    "supervisor installation failed; safe_disabled_fallback applied; "
                    "recovery receipt update failed; recovery_required"
                ) from original_error
            raise RuntimeError(
                "supervisor installation failed; safe_disabled_fallback applied; "
                "private recovery receipt retained for operator review"
            ) from original_error

        _safe, safe_errors = _force_both_labels_safe(
            run=run,
            launch_domain=launch_domain,
            launch_ref=launch_ref,
            legacy_ref=legacy_ref,
        )
        recovery_required = _recovery_required_record(
            recovery,
            error_type=_safe_exception_type(original_error),
            rollback_errors=rollback_errors + safe_errors,
        )
        try:
            _write_recovery_record(marker_path, recovery_required)
        except Exception:
            pass
        raise RuntimeError(
            "supervisor installation failed; exact pre-state was not restored; "
            "recovery_required"
        ) from original_error
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
    parser.add_argument("--codex-resume-timeout", type=float, default=1800.0)
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
            codex_resume_timeout=args.codex_resume_timeout,
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
