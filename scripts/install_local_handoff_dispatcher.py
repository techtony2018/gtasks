#!/usr/bin/env python3
"""Install one deterministic private local handoff Dispatcher LaunchAgent."""

from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
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


class InstallReceipt(NamedTuple):
    label: str
    agent_slug: str
    config_path: str
    config_sha256: str
    plist_path: str
    plist_sha256: str
    codex_version: str


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
    rendered = template
    for name, value in replacements.items():
        rendered = rendered.replace(f"__{name}__", escape(value))
    if "__" in rendered:
        raise ValueError("plist template contains an unresolved placeholder")
    return rendered


def install(
    *,
    source_config: str | Path,
    destination_config: str | Path,
    plist_template: str | Path,
    plist_destination: str | Path,
    python_path: str,
    runner_path: str | Path,
    codex_path: str,
    working_directory: str | Path,
    label: str = DEFAULT_LABEL,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> InstallReceipt:
    source_path = Path(source_config)
    destination_path = Path(destination_config)
    plist_path = Path(plist_destination)
    config = DispatcherConfig.from_file(source_path)
    config.read_token()
    if not Path(runner_path).is_file():
        raise ValueError("local Dispatcher runner path must exist")

    if destination_path.exists():
        existing = DispatcherConfig.from_file(destination_path)
        if (
            existing.agent_slug != config.agent_slug
            or existing.registration_id != config.registration_id
        ):
            raise ValueError("existing config belongs to a second Agent identity")
        if existing.fixed_thread_id != config.fixed_thread_id:
            raise ValueError("existing fixed thread id must be preserved")

    adapter = CodexResumeAdapter(
        codex_path,
        fixed_thread_id=config.fixed_thread_id,
        working_directory=working_directory,
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
            "PYTHON_PATH": str(python_path),
            "CONFIG_PATH": str(destination_path.resolve()),
            "CODEX_PATH": str(codex_path),
            "WORKING_DIRECTORY": str(Path(working_directory).resolve()),
        },
    )
    _atomic_write(destination_path, serialized_config, 0o600)
    _atomic_write(plist_path, plist_text.encode("utf-8"), 0o644)

    launch_domain = f"gui/{os.getuid()}"
    launch_ref = f"{launch_domain}/{label}"
    run(
        ["/bin/launchctl", "bootout", launch_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    bootstrap = run(
        ["/bin/launchctl", "bootstrap", launch_domain, str(plist_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if bootstrap.returncode != 0:
        raise RuntimeError("LaunchAgent bootstrap failed")
    readback = run(
        ["/bin/launchctl", "print", launch_ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if readback.returncode != 0:
        raise RuntimeError("LaunchAgent readback failed")

    installed_config = DispatcherConfig.from_file(destination_path)
    if installed_config.to_json_dict() != config.to_json_dict():
        raise RuntimeError("installed config readback does not match source")
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
    )


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", required=True, type=Path)
    parser.add_argument("--destination-config", required=True, type=Path)
    parser.add_argument(
        "--plist-template",
        type=Path,
        default=root / "config" / "handoff-dispatcher" / "agent.plist.template",
    )
    parser.add_argument("--plist-destination", required=True, type=Path)
    parser.add_argument("--python-path", default="/usr/bin/python3")
    parser.add_argument(
        "--runner-path",
        type=Path,
        default=root / "gtasks" / "local_handoff_dispatcher.py",
    )
    parser.add_argument("--codex-path", default="codex")
    parser.add_argument("--working-directory", type=Path, default=root)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    receipt = install(
        source_config=args.source_config,
        destination_config=args.destination_config,
        plist_template=args.plist_template,
        plist_destination=args.plist_destination,
        python_path=args.python_path,
        runner_path=args.runner_path,
        codex_path=args.codex_path,
        working_directory=args.working_directory,
        label=args.label,
    )
    print(json.dumps(receipt._asdict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
