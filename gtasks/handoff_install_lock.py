"""One private interprocess mutex for local handoff installer transactions."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
from functools import wraps
import math
import os
from pathlib import Path
import stat
import time
from typing import Callable, Iterator, TypeVar


DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
LOCK_FILE_NAME = ".install.lock"

_Result = TypeVar("_Result")


def install_lock_path(home_directory: str | Path) -> Path:
    """Return the one canonical mutex path shared by both installers."""
    return (
        Path(home_directory).resolve()
        / "Library"
        / "Application Support"
        / "GTasks"
        / "handoff-dispatcher"
        / LOCK_FILE_NAME
    )


def _open_private_regular_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    created = False
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ValueError(
                "handoff install lock must be a regular non-symbolic link "
                "file with mode 0600"
            ) from exc
    except OSError as exc:
        raise ValueError(
            "handoff install lock could not be created as a private regular file"
        ) from exc

    try:
        if created:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            parent_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        descriptor_details = os.fstat(descriptor)
        path_details = path.lstat()
        if (
            not stat.S_ISREG(descriptor_details.st_mode)
            or stat.S_ISLNK(path_details.st_mode)
            or (descriptor_details.st_dev, descriptor_details.st_ino)
            != (path_details.st_dev, path_details.st_ino)
            or stat.S_IMODE(descriptor_details.st_mode) != 0o600
        ):
            raise ValueError(
                "handoff install lock must be a regular non-symbolic link "
                "file with mode 0600"
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def acquire_install_lock(
    home_directory: str | Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    """Hold the canonical installer mutex for one complete transaction."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds < 0
    ):
        raise ValueError("install lock timeout must be a finite non-negative number")
    path = install_lock_path(home_directory)
    descriptor = _open_private_regular_lock(path)
    deadline = time.monotonic() + float(timeout_seconds)
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise RuntimeError(
                        "handoff install lock acquisition failed"
                    ) from exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "handoff install lock is busy; "
                        "no installer mutation was attempted"
                    ) from exc
                time.sleep(min(0.05, remaining))
        locked_path_details = path.lstat()
        descriptor_details = os.fstat(descriptor)
        if (
            stat.S_ISLNK(locked_path_details.st_mode)
            or not stat.S_ISREG(locked_path_details.st_mode)
            or (descriptor_details.st_dev, descriptor_details.st_ino)
            != (locked_path_details.st_dev, locked_path_details.st_ino)
            or stat.S_IMODE(locked_path_details.st_mode) != 0o600
        ):
            raise ValueError(
                "handoff install lock must remain a regular non-symbolic link "
                "file with mode 0600"
            )
        yield path
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def locked_handoff_install(
    function: Callable[..., _Result],
) -> Callable[..., _Result]:
    """Acquire the shared lock before entering either installer body."""

    @wraps(function)
    def locked(*args, **kwargs):
        home_directory = kwargs.get("home_directory")
        timeout_seconds = kwargs.get(
            "lock_timeout_seconds", DEFAULT_LOCK_TIMEOUT_SECONDS
        )
        with acquire_install_lock(
            home_directory if home_directory is not None else Path.home(),
            timeout_seconds=timeout_seconds,
        ):
            return function(*args, **kwargs)

    return locked
