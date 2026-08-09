"""Private gated subprocess shim for one durable handoff launch.

The controller may prepare and spawn this shim before Mission Control grants the
semantic execution start.  The shim writes ready evidence and then waits.  It
cannot invoke the target argv until an atomic gate containing the exact launch
identity and a reference to the server grant exists.
"""

from __future__ import annotations

import sys

# Executing this file directly would otherwise put ``gtasks/`` first on
# ``sys.path`` and shadow the standard-library ``warnings`` module with
# ``gtasks.warnings`` before the shim can start.
if __package__ in {None, ""} and sys.path:
    sys.path.pop(0)

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from typing import Mapping, Sequence


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}")
_REQUEST_KEYS = frozenset(
    {"schema_version", "launch_id", "argv", "working_directory", "timeout_seconds"}
)
_READY_KEYS = frozenset({"schema_version", "launch_id", "pid", "ready_at"})
_GATE_KEYS = frozenset({"launch_id", "launch_grant_ref"})
_CANCEL_KEYS = frozenset({"launch_id"})
_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "launch_id",
        "outcome",
        "reason",
        "returncode",
        "finished_at",
    }
)


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be one bounded identity value")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_private_directory(path: Path, field: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{field} must not be a symbolic link")
    details = path.stat()
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError(f"{field} must be a directory")
    if stat.S_IMODE(details.st_mode) != 0o700:
        raise ValueError(f"{field} mode must be exactly 0700")
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        raise ValueError(f"{field} must be owned by the current user")


def _ensure_private_directory(path: Path, field: str) -> None:
    if path.exists() or path.is_symlink():
        _validate_private_directory(path, field)
        return
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    path.chmod(0o700)
    _validate_private_directory(path, field)


def _read_private_json(path: Path, *, keys: frozenset[str], field: str) -> dict[str, object]:
    if path.is_symlink():
        raise ValueError(f"{field} must not be a symbolic link")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
        raise ValueError(f"{field} must be a private regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must contain valid JSON") from exc
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{field} has an unexpected shape")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_temp_json(directory: Path, payload: Mapping[str, object]) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".handoff-", dir=directory)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        rendered = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = _write_temp_json(path.parent, payload)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_create_json(path: Path, payload: Mapping[str, object]) -> bool:
    """Atomically create without replacing; return False when it already exists."""
    temporary = _write_temp_json(path.parent, payload)
    try:
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    argv: tuple[str, ...]
    working_directory: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.argv, tuple)
            or not self.argv
            or any(not isinstance(value, str) or not value or "\0" in value for value in self.argv)
        ):
            raise ValueError("launch argv must be one non-empty string tuple")
        directory = Path(self.working_directory)
        if not directory.is_absolute():
            raise ValueError("launch working directory must be absolute")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 86400
        ):
            raise ValueError("launch timeout must be between 0 and 86400 seconds")

    def to_dict(self, launch_id: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "launch_id": _identifier(launch_id, "launch_id"),
            "argv": list(self.argv),
            "working_directory": self.working_directory,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LaunchRequest":
        if set(value) != _REQUEST_KEYS or value.get("schema_version") != 1:
            raise ValueError("launch request has an unexpected shape")
        _identifier(value.get("launch_id"), "launch_id")
        argv = value.get("argv")
        if not isinstance(argv, list):
            raise ValueError("launch argv must be a list")
        return cls(
            argv=tuple(argv),
            working_directory=str(value.get("working_directory")),
            timeout_seconds=value.get("timeout_seconds"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class LaunchObservation:
    launch_id: str
    state: str
    pid: int | None
    runner_alive: bool
    outcome: str | None = None
    reason: str | None = None
    returncode: int | None = None


class GatedLaunchController:
    """Prepare, spawn, observe, gate, or cancel one private launch directory."""

    def __init__(
        self,
        root: str | Path,
        *,
        runner_command: Sequence[str] | None = None,
    ) -> None:
        self.root = Path(root)
        _ensure_private_directory(self.root, "launch root")
        self.runner_command = tuple(runner_command or (
            sys.executable,
            str(Path(__file__).resolve()),
        ))
        if not self.runner_command or any(not value for value in self.runner_command):
            raise ValueError("runner command must not be empty")
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def launch_directory(self, launch_id: str) -> Path:
        launch_id = _identifier(launch_id, "launch_id")
        digest = hashlib.sha256(launch_id.encode("utf-8")).hexdigest()
        return self.root / digest

    def start(self, launch_id: str, request: LaunchRequest) -> LaunchObservation:
        launch_id = _identifier(launch_id, "launch_id")
        directory = self.launch_directory(launch_id)
        _ensure_private_directory(directory, "launch directory")
        request_path = directory / "request.json"
        expected = request.to_dict(launch_id)
        if not _atomic_create_json(request_path, expected):
            existing = _read_private_json(
                request_path, keys=_REQUEST_KEYS, field="launch request"
            )
            if existing != expected:
                raise ValueError("launch id is already bound to another request")
        observed = self.observe(launch_id)
        if observed.state not in {"preparing", "spawned"}:
            return observed
        process = subprocess.Popen(
            [
                *self.runner_command,
                "--launch-directory",
                str(directory),
            ],
            cwd=str(self.root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        self._processes[launch_id] = process
        return LaunchObservation(
            launch_id=launch_id,
            state="spawned",
            pid=process.pid,
            runner_alive=True,
        )

    def _runner_alive(self, launch_id: str, pid: int | None) -> bool:
        process = self._processes.get(launch_id)
        if process is not None and (pid is None or process.pid == pid):
            return process.poll() is None
        if process is not None:
            # A recovered controller can briefly spawn a duplicate shim before
            # the original runner publishes its lock.  Reap that losing shim,
            # but trust liveness only for the PID named by durable evidence.
            process.poll()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def observe(self, launch_id: str) -> LaunchObservation:
        launch_id = _identifier(launch_id, "launch_id")
        directory = self.launch_directory(launch_id)
        if not directory.exists():
            return LaunchObservation(launch_id, "absent", None, False)
        _validate_private_directory(directory, "launch directory")
        result_path = directory / "result.json"
        ready_path = directory / "ready.json"
        lock_path = directory / "runner.lock"
        pid: int | None = None
        if ready_path.exists():
            ready = _read_private_json(ready_path, keys=_READY_KEYS, field="launch ready evidence")
            if ready.get("schema_version") != 1 or ready.get("launch_id") != launch_id:
                raise ValueError("launch ready evidence does not match its launch")
            ready_pid = ready.get("pid")
            if isinstance(ready_pid, bool) or not isinstance(ready_pid, int) or ready_pid < 1:
                raise ValueError("launch ready PID is invalid")
            pid = ready_pid
        elif lock_path.exists():
            lock = _read_private_json(
                lock_path,
                keys=frozenset({"launch_id", "pid"}),
                field="launch runner lock",
            )
            if lock.get("launch_id") != launch_id:
                raise ValueError("launch runner lock does not match its launch")
            lock_pid = lock.get("pid")
            if isinstance(lock_pid, int) and not isinstance(lock_pid, bool) and lock_pid > 0:
                pid = lock_pid
        process = self._processes.get(launch_id)
        if pid is None and process is not None:
            pid = process.pid
        alive = self._runner_alive(launch_id, pid)
        if result_path.exists():
            process = self._processes.get(launch_id)
            if process is not None and process.poll() is None:
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
                alive = process.poll() is None
            result = _read_private_json(
                result_path, keys=_RESULT_KEYS, field="launch result evidence"
            )
            if result.get("schema_version") != 1 or result.get("launch_id") != launch_id:
                raise ValueError("launch result evidence does not match its launch")
            outcome = result.get("outcome")
            reason = result.get("reason")
            returncode = result.get("returncode")
            if outcome not in {"completed", "prelaunch_failure", "ambiguous", "cancelled"}:
                raise ValueError("launch result outcome is invalid")
            if not isinstance(reason, str) or not reason:
                raise ValueError("launch result reason is invalid")
            if returncode is not None and (
                isinstance(returncode, bool) or not isinstance(returncode, int)
            ):
                raise ValueError("launch result return code is invalid")
            return LaunchObservation(
                launch_id,
                str(outcome),
                pid,
                alive,
                str(outcome),
                reason,
                returncode,
            )
        gate_open = (directory / "gate.json").exists()
        cancelled = (directory / "cancel.json").exists()
        if gate_open and pid is not None and not alive:
            return LaunchObservation(
                launch_id,
                "ambiguous",
                pid,
                False,
                "ambiguous",
                "runner_lost_after_gate",
                None,
            )
        if cancelled and pid is not None and not alive:
            return LaunchObservation(
                launch_id,
                "cancelled",
                pid,
                False,
                "cancelled",
                "cancelled_before_gate",
                None,
            )
        if gate_open:
            return LaunchObservation(launch_id, "executing", pid, alive)
        if ready_path.exists():
            return LaunchObservation(launch_id, "ready", pid, alive)
        if lock_path.exists():
            return LaunchObservation(launch_id, "spawned", pid, alive)
        return LaunchObservation(launch_id, "preparing", pid, alive)

    def open_gate(self, launch_id: str, launch_grant: str) -> LaunchObservation:
        launch_id = _identifier(launch_id, "launch_id")
        launch_grant = _identifier(launch_grant, "launch_grant")
        observed = self.observe(launch_id)
        if observed.state not in {"ready", "executing", "completed", "ambiguous"}:
            raise ValueError("launch gate requires durable ready evidence")
        path = self.launch_directory(launch_id) / "gate.json"
        value = {
            "launch_id": launch_id,
            "launch_grant_ref": hashlib.sha256(launch_grant.encode("utf-8")).hexdigest(),
        }
        if not _atomic_create_json(path, value):
            existing = _read_private_json(path, keys=_GATE_KEYS, field="launch gate")
            if existing != value:
                raise ValueError("launch gate is already bound to another grant")
        return self.observe(launch_id)

    def cancel(self, launch_id: str) -> LaunchObservation:
        launch_id = _identifier(launch_id, "launch_id")
        directory = self.launch_directory(launch_id)
        if (directory / "gate.json").exists():
            raise ValueError("a gated launch cannot be cancelled as unstarted")
        path = directory / "cancel.json"
        value = {"launch_id": launch_id}
        if not _atomic_create_json(path, value):
            existing = _read_private_json(path, keys=_CANCEL_KEYS, field="launch cancel")
            if existing != value:
                raise ValueError("launch cancellation does not match its launch")
        deadline = time.monotonic() + 0.2
        observed = self.observe(launch_id)
        while observed.state not in {"cancelled", "prelaunch_failure"}:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
            observed = self.observe(launch_id)
        return observed


def _exclusive_runner_lock(directory: Path, launch_id: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(directory / "runner.lock", flags, 0o600)
    try:
        rendered = json.dumps(
            {"launch_id": launch_id, "pid": os.getpid()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        os.write(descriptor, rendered)
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    _fsync_directory(directory)
    return descriptor


def _write_result(
    directory: Path,
    *,
    launch_id: str,
    outcome: str,
    reason: str,
    returncode: int | None,
) -> None:
    _atomic_write_json(
        directory / "result.json",
        {
            "schema_version": 1,
            "launch_id": launch_id,
            "outcome": outcome,
            "reason": reason,
            "returncode": returncode,
            "finished_at": _utc_now(),
        },
    )


def run_launch(directory: str | Path, *, poll_seconds: float = 0.02) -> int:
    launch_directory = Path(directory)
    _validate_private_directory(launch_directory, "launch directory")
    request_value = _read_private_json(
        launch_directory / "request.json",
        keys=_REQUEST_KEYS,
        field="launch request",
    )
    request = LaunchRequest.from_dict(request_value)
    launch_id = _identifier(request_value.get("launch_id"), "launch_id")
    try:
        lock_descriptor = _exclusive_runner_lock(launch_directory, launch_id)
    except FileExistsError:
        return 2
    try:
        _atomic_write_json(
            launch_directory / "ready.json",
            {
                "schema_version": 1,
                "launch_id": launch_id,
                "pid": os.getpid(),
                "ready_at": _utc_now(),
            },
        )
        while True:
            cancel_path = launch_directory / "cancel.json"
            gate_path = launch_directory / "gate.json"
            if cancel_path.exists():
                cancel = _read_private_json(
                    cancel_path, keys=_CANCEL_KEYS, field="launch cancel"
                )
                if cancel.get("launch_id") != launch_id:
                    raise ValueError("launch cancellation does not match its launch")
                _write_result(
                    launch_directory,
                    launch_id=launch_id,
                    outcome="cancelled",
                    reason="cancelled_before_gate",
                    returncode=None,
                )
                return 0
            if gate_path.exists():
                gate = _read_private_json(
                    gate_path, keys=_GATE_KEYS, field="launch gate"
                )
                if (
                    gate.get("launch_id") != launch_id
                    or not isinstance(gate.get("launch_grant_ref"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", str(gate["launch_grant_ref"])) is None
                ):
                    raise ValueError("launch gate does not match its grant")
                break
            time.sleep(poll_seconds)

        try:
            completed = subprocess.run(
                list(request.argv),
                cwd=request.working_directory,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            _write_result(
                launch_directory,
                launch_id=launch_id,
                outcome="ambiguous",
                reason="timeout",
                returncode=None,
            )
            return 0
        except OSError:
            _write_result(
                launch_directory,
                launch_id=launch_id,
                outcome="prelaunch_failure",
                reason="command_not_started",
                returncode=None,
            )
            return 0
        if completed.returncode == 0:
            _write_result(
                launch_directory,
                launch_id=launch_id,
                outcome="completed",
                reason="command_exit_zero",
                returncode=0,
            )
        else:
            _write_result(
                launch_directory,
                launch_id=launch_id,
                outcome="ambiguous",
                reason="nonzero_exit",
                returncode=completed.returncode,
            )
        return 0
    finally:
        os.close(lock_descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-directory", required=True)
    arguments = parser.parse_args(argv)
    try:
        return run_launch(arguments.launch_directory)
    except (OSError, TypeError, ValueError):
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
