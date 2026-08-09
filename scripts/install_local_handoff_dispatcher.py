#!/usr/bin/env python3
"""Install one deterministic private local handoff Dispatcher LaunchAgent."""

from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import plistlib
import re
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


DEFAULT_LABEL = "com.tony.gtasks-handoff-dispatcher"
SUPERVISOR_LABEL = "com.tony.gtasks-handoff-dispatcher-supervisor"
OVERRIDE_ABSENT = "absent"
OVERRIDE_EXPLICITLY_ENABLED = "explicitly_enabled"
OVERRIDE_EXPLICITLY_DISABLED = "explicitly_disabled"


def canonical_single_worker_install_paths(
    home_directory: str | Path,
) -> tuple[Path, Path]:
    """Return the legacy one-worker paths retained through supervisor canaries."""
    home = Path(home_directory).resolve()
    return (
        home / "Library" / "Application Support" / "GTasks" / "handoff-dispatcher.json",
        home / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist",
    )


def canonical_install_paths(home_directory: str | Path) -> tuple[Path, Path]:
    """Backward-compatible name for the existing one-worker installer."""
    return canonical_single_worker_install_paths(home_directory)


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


class InstallReceipt(NamedTuple):
    label: str
    agent_slug: str
    config_path: str
    config_sha256: str
    plist_path: str
    plist_sha256: str
    codex_version: str
    codex_path: str


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _render_template(template: str, replacements: dict[str, str]) -> str:
    placeholders = set(re.findall(r"__([A-Z][A-Z0-9_]*)__", template))
    if placeholders - set(replacements):
        raise ValueError("plist template contains an unresolved placeholder")
    rendered = template
    for name, value in replacements.items():
        rendered = rendered.replace(f"__{name}__", escape(value))
    return rendered


def _parse_launchctl_contract(output: str) -> tuple[list[str], str | None, dict[str, str]]:
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


def supervisor_recovery_marker_path(home_directory: str | Path) -> Path:
    return (
        Path(home_directory).resolve()
        / "Library"
        / "Application Support"
        / "GTasks"
        / "handoff-dispatcher"
        / ".install-recovery.json"
    )


def _require_no_supervisor_recovery_marker(home_directory: str | Path) -> None:
    marker = supervisor_recovery_marker_path(home_directory)
    try:
        marker.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(
            "supervisor recovery marker state could not be inspected; "
            "follow the paired supervisor recovery procedure before legacy install"
        ) from exc
    raise ValueError(
        "supervisor recovery marker is present; follow the paired supervisor "
        "recovery procedure before legacy install"
    )


def _supervisor_marker_present(home_directory: str | Path) -> bool:
    marker = (
        Path(home_directory).resolve()
        / "Library"
        / "LaunchAgents"
        / f"{SUPERVISOR_LABEL}.plist"
    )
    if marker.is_symlink():
        raise ValueError("supervisor fence marker must not be a symbolic link")
    if not marker.exists():
        return False
    try:
        value = plistlib.loads(marker.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError("supervisor fence marker is invalid") from exc
    arguments = value.get("ProgramArguments") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("Label") != SUPERVISOR_LABEL
        or not isinstance(arguments, list)
        or len(arguments) < 3
        or arguments[1:3] != ["-m", "gtasks.local_handoff_supervisor"]
    ):
        raise ValueError("supervisor fence marker is invalid")
    return True


def _supervisor_loaded_contract(output: str) -> bool:
    arguments, _working_directory, environment = _parse_launchctl_contract(output)
    return (
        len(arguments) >= 3
        and arguments[1:3] == ["-m", "gtasks.local_handoff_supervisor"]
        and environment.get("XPC_SERVICE_NAME") == SUPERVISOR_LABEL
    )


def _require_supervisor_fence_inactive(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    home_directory: str | Path,
    launch_domain: str,
) -> None:
    _require_no_supervisor_recovery_marker(home_directory)
    disabled = run(
        ["/bin/launchctl", "print-disabled", launch_domain],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if disabled.returncode != 0:
        raise ValueError("supervisor fence state could not be verified")
    supervisor_override = _parse_override_state(disabled.stdout, SUPERVISOR_LABEL)
    supervisor_ref = f"{launch_domain}/{SUPERVISOR_LABEL}"
    _require_no_supervisor_recovery_marker(home_directory)
    loaded = run(
        ["/bin/launchctl", "print", supervisor_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if loaded.returncode == 0:
        detail = (
            "canonical contract"
            if _supervisor_loaded_contract(loaded.stdout)
            else "drifted contract"
        )
        raise ValueError(
            f"reserved supervisor label is loaded with {detail}; "
            "legacy bootstrap is refused"
        )
    if supervisor_override == OVERRIDE_EXPLICITLY_ENABLED:
        raise ValueError(
            "supervisor fence label is durably enabled while unloaded; "
            "legacy bootstrap is refused"
        )
    marker_present = _supervisor_marker_present(home_directory)
    if marker_present:
        raise ValueError(
            "durable supervisor fence is active; legacy bootstrap is refused"
        )


def install(
    *,
    source_config: str | Path,
    destination_config: str | Path,
    plist_template: str | Path,
    plist_destination: str | Path,
    python_path: str,
    module_root: str | Path,
    runner_path: str | Path,
    codex_path: str,
    working_directory: str | Path,
    label: str = DEFAULT_LABEL,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    home_directory: str | Path | None = None,
) -> InstallReceipt:
    source_path = Path(source_config)
    destination_path = Path(destination_config)
    plist_path = Path(plist_destination)
    canonical_config, canonical_plist = canonical_install_paths(
        home_directory if home_directory is not None else Path.home()
    )
    if destination_path.resolve() != canonical_config.resolve():
        raise ValueError("destination must use the canonical config path")
    if plist_path.resolve() != canonical_plist.resolve():
        raise ValueError("destination must use the canonical plist path")
    if label != DEFAULT_LABEL:
        raise ValueError("installer requires the canonical label")
    _require_no_supervisor_recovery_marker(
        home_directory if home_directory is not None else Path.home()
    )
    config = DispatcherConfig.from_file(source_path)
    config.read_token()
    resolved_python = _resolve_executable(python_path)
    if Path(resolved_python) == Path("/usr/bin/python3"):
        raise ValueError("installer must not use /usr/bin/python3")
    resolved_module_root = Path(module_root).resolve()
    resolved_runner = Path(runner_path).resolve()
    expected_runner = resolved_module_root / "gtasks" / "local_handoff_dispatcher.py"
    if not resolved_module_root.is_dir():
        raise ValueError("module root must be an existing directory")
    if not (resolved_module_root / "gtasks" / "__init__.py").is_file():
        raise ValueError("module root must contain the GTasks package")
    if resolved_runner != expected_runner.resolve() or not resolved_runner.is_file():
        raise ValueError("runner must be the local Dispatcher module under module root")
    resolved_working_directory = Path(working_directory).resolve()
    if not resolved_working_directory.is_dir():
        raise ValueError("Agent working directory must be an existing directory")
    resolved_codex = _resolve_executable(codex_path)
    expected_arguments = [
        resolved_python,
        "-m",
        "gtasks.local_handoff_dispatcher",
        "--config",
        str(destination_path.resolve()),
        "--codex-path",
        resolved_codex,
        "--working-directory",
        str(resolved_working_directory),
    ]

    import_probe = run(
        [
            resolved_python,
            "-c",
            (
                "from pathlib import Path; "
                "import gtasks.local_handoff_dispatcher as module; "
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
    if (
        import_probe.returncode != 0
        or import_probe.stdout.strip() != str(resolved_runner)
    ):
        raise ValueError("configured Python does not resolve the verified Dispatcher module")

    existing_config_present = destination_path.exists()
    if existing_config_present:
        existing = DispatcherConfig.from_file(destination_path)
        if (
            existing.agent_slug != config.agent_slug
            or existing.registration_id != config.registration_id
        ):
            raise ValueError("existing config belongs to a second Agent identity")
        if existing.fixed_thread_id != config.fixed_thread_id:
            raise ValueError("existing fixed thread id must be preserved")

    if plist_path.exists():
        try:
            existing_plist = plistlib.loads(plist_path.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise ValueError("existing canonical plist is invalid") from exc
        if (
            existing_plist.get("Label") != DEFAULT_LABEL
            or existing_plist.get("ProgramArguments") != expected_arguments
            or existing_plist.get("WorkingDirectory")
            != str(resolved_working_directory)
            or existing_plist.get("EnvironmentVariables")
            != {"PYTHONPATH": str(resolved_module_root)}
        ):
            raise ValueError("existing loaded plist does not match canonical Dispatcher identity")

    adapter = CodexResumeAdapter(
        resolved_codex,
        fixed_thread_id=config.fixed_thread_id,
        working_directory=resolved_working_directory,
        run=run,
    )
    codex_version = adapter.verify_contract()

    serialized_config = (
        json.dumps(config.to_json_dict(), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    template_text = Path(plist_template).read_text(encoding="utf-8")
    plist_text = _render_template(
        template_text,
        {
            "LABEL": label,
            "PYTHON_PATH": resolved_python,
            "CONFIG_PATH": str(destination_path.resolve()),
            "CODEX_PATH": resolved_codex,
            "WORKING_DIRECTORY": str(resolved_working_directory),
            "MODULE_ROOT": str(resolved_module_root),
        },
    )
    launch_domain = f"gui/{os.getuid()}"
    launch_ref = f"{launch_domain}/{label}"
    install_home = home_directory if home_directory is not None else Path.home()
    _require_no_supervisor_recovery_marker(install_home)
    _require_supervisor_fence_inactive(
        run=run,
        home_directory=install_home,
        launch_domain=launch_domain,
    )
    _require_no_supervisor_recovery_marker(install_home)
    loaded = run(
        ["/bin/launchctl", "print", launch_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if loaded.returncode == 0 and not existing_config_present:
        raise ValueError("loaded LaunchAgent identity cannot be verified without canonical config")
    if loaded.returncode == 0 and not _loaded_contract_matches(
        loaded.stdout,
        expected_arguments=expected_arguments,
        expected_working_directory=str(resolved_working_directory),
        expected_module_root=str(resolved_module_root),
    ):
        raise ValueError("loaded LaunchAgent does not match the exact canonical contract")

    _require_no_supervisor_recovery_marker(install_home)
    _atomic_write(destination_path, serialized_config, 0o600)
    _require_no_supervisor_recovery_marker(install_home)
    _atomic_write(plist_path, plist_text.encode("utf-8"), 0o644)
    if loaded.returncode == 0:
        _require_no_supervisor_recovery_marker(install_home)
        run(
            ["/bin/launchctl", "bootout", launch_ref],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    _require_no_supervisor_recovery_marker(install_home)
    enabled = run(
        ["/bin/launchctl", "enable", launch_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if enabled.returncode != 0:
        raise RuntimeError("LaunchAgent enable failed")
    _require_no_supervisor_recovery_marker(install_home)
    enabled_readback = run(
        ["/bin/launchctl", "print-disabled", launch_domain],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if (
        enabled_readback.returncode != 0
        or _parse_override_state(enabled_readback.stdout, label)
        != OVERRIDE_EXPLICITLY_ENABLED
    ):
        raise RuntimeError("LaunchAgent enable readback failed")
    _require_no_supervisor_recovery_marker(install_home)
    bootstrap = run(
        ["/bin/launchctl", "bootstrap", launch_domain, str(plist_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if bootstrap.returncode != 0:
        raise RuntimeError("LaunchAgent bootstrap failed")
    _require_no_supervisor_recovery_marker(install_home)
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
        raise RuntimeError("LaunchAgent readback failed")

    installed_config = DispatcherConfig.from_file(destination_path)
    if installed_config.to_json_dict() != config.to_json_dict():
        raise RuntimeError("installed config readback does not match source")
    rendered_plist = plistlib.loads(plist_path.read_bytes())
    if (
        rendered_plist.get("Label") != DEFAULT_LABEL
        or rendered_plist.get("ProgramArguments") != expected_arguments
        or rendered_plist.get("WorkingDirectory")
        != str(resolved_working_directory)
        or rendered_plist.get("EnvironmentVariables")
        != {"PYTHONPATH": str(resolved_module_root)}
    ):
        raise RuntimeError("rendered LaunchAgent arguments failed readback")
    config_bytes = destination_path.read_bytes()
    plist_bytes = plist_path.read_bytes()
    return InstallReceipt(
        label=label,
        agent_slug=config.agent_slug,
        config_path=str(destination_path),
        config_sha256=sha256(config_bytes).hexdigest(),
        plist_path=str(plist_path),
        plist_sha256=sha256(plist_bytes).hexdigest(),
        codex_version=codex_version,
        codex_path=resolved_codex,
    )


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", required=True, type=Path)
    parser.add_argument(
        "--plist-template",
        type=Path,
        default=root / "config" / "handoff-dispatcher" / "agent.plist.template",
    )
    parser.add_argument("--python-path", default=sys.executable)
    parser.add_argument("--module-root", type=Path, default=root)
    parser.add_argument(
        "--runner-path",
        type=Path,
        default=root / "gtasks" / "local_handoff_dispatcher.py",
    )
    parser.add_argument("--codex-path", default="codex")
    parser.add_argument("--working-directory", type=Path, default=root)
    parser.add_argument("--home-directory", type=Path, default=Path.home())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    destination_config, plist_destination = canonical_install_paths(args.home_directory)
    receipt = install(
        source_config=args.source_config,
        destination_config=destination_config,
        plist_template=args.plist_template,
        plist_destination=plist_destination,
        python_path=args.python_path,
        module_root=args.module_root,
        runner_path=args.runner_path,
        codex_path=args.codex_path,
        working_directory=args.working_directory,
        home_directory=args.home_directory,
    )
    print(json.dumps(receipt._asdict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
