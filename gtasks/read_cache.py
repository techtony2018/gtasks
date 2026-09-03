from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Thread
from time import time
from typing import Any, Callable, Mapping


READ_CACHE_SCHEMA_VERSION = 2


def default_read_cache_path() -> Path:
    configured = os.environ.get("GTASKS_READ_CACHE_FILE")
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "GTasks"
        / "read-snapshots.json"
    )


@dataclass(frozen=True, slots=True)
class SurfaceRead:
    payload: dict[str, Any] | None
    state: dict[str, Any]


class ReadSnapshotStore:
    """Private atomic storage for last-valid, read-only GBrain projections."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_read_cache_path()

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        if (
            not isinstance(raw, Mapping)
            or raw.get("schema_version") != READ_CACHE_SCHEMA_VERSION
            or not isinstance(raw.get("surfaces"), Mapping)
        ):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for name, record in raw["surfaces"].items():
            if (
                isinstance(name, str)
                and isinstance(record, Mapping)
                and isinstance(record.get("payload"), Mapping)
                and isinstance(record.get("last_valid_at"), (int, float))
            ):
                result[name] = {
                    "payload": deepcopy(dict(record["payload"])),
                    "last_valid_at": float(record["last_valid_at"]),
                }
        return result

    def save(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        payload = {
            "schema_version": READ_CACHE_SCHEMA_VERSION,
            "surfaces": {
                name: {
                    "payload": deepcopy(dict(record["payload"])),
                    "last_valid_at": float(record["last_valid_at"]),
                }
                for name, record in records.items()
                if isinstance(record.get("payload"), Mapping)
                and isinstance(record.get("last_valid_at"), (int, float))
            },
        }
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary_name, 0o600)
                json.dump(payload, temporary, ensure_ascii=False, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.path)
            temporary_name = None
            os.chmod(self.path, 0o600)
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass


class ReadSurfaceCache:
    """Coalesce slow reads and serve an explicitly-labelled last-valid value."""

    def __init__(
        self,
        store: ReadSnapshotStore,
        *,
        clock: Callable[[], float] = time,
        background: bool = True,
        max_refresh_seconds: float = 60.0,
    ) -> None:
        self._store = store
        self._clock = clock
        self._background = background
        self._max_refresh_seconds = max_refresh_seconds
        self._condition = Condition()
        self._records = store.load()
        self._loading: dict[str, float] = {}
        self._generations: dict[str, int] = {}
        self._dirty: set[str] = set()
        self._errors: dict[str, str] = {}
        self._error_at: dict[str, float] = {}

    def invalidate(self, *names: str) -> None:
        with self._condition:
            self._dirty.update(names)

    def read(
        self,
        name: str,
        loader: Callable[[], dict[str, Any]],
        *,
        ttl_seconds: float,
        force: bool = False,
        force_cooldown_seconds: float = 0.0,
    ) -> SurfaceRead:
        start_refresh = False
        with self._condition:
            record = self._records.get(name)
            last_valid_at = (
                float(record["last_valid_at"]) if record is not None else None
            )
            age = (
                max(0.0, self._clock() - last_valid_at)
                if last_valid_at is not None
                else None
            )
            needs_refresh = (
                force
                or name in self._dirty
                or record is None
                or age is None
                or age > ttl_seconds
            )
            if (
                force
                and record is not None
                and age is not None
                and age <= force_cooldown_seconds
                and name not in self._dirty
                and name not in self._errors
                and name not in self._loading
            ):
                needs_refresh = False
            recent_cold_error = (
                record is None
                and name in self._errors
                and not force
                and self._clock() - self._error_at.get(name, 0.0)
                < min(ttl_seconds, 30.0)
            )
            if recent_cold_error:
                needs_refresh = False
            if needs_refresh and name in self._loading:
                started_at = self._loading[name]
                if self._clock() - started_at >= self._max_refresh_seconds:
                    self._errors[name] = (
                        "The canonical GBrain refresh did not complete. Last verified data is kept."
                    )
                    self._error_at[name] = self._clock()
                    self._generations[name] = self._generations.get(name, 0) + 1
                    self._loading.pop(name, None)
                    needs_refresh = force
                else:
                    needs_refresh = False
            recent_refresh_error = (
                record is not None
                and name in self._errors
                and not force
                and self._clock() - self._error_at.get(name, 0.0)
                < ttl_seconds
            )
            if recent_refresh_error:
                needs_refresh = False
            if needs_refresh and name not in self._loading:
                self._loading[name] = self._clock()
                self._generations[name] = self._generations.get(name, 0) + 1
                generation = self._generations[name]
                self._errors.pop(name, None)
                self._error_at.pop(name, None)
                start_refresh = True
            else:
                generation = self._generations.get(name, 0)

        if start_refresh and not self._background:
            self._refresh(name, loader, generation)
        elif start_refresh:
            Thread(
                target=self._refresh,
                args=(name, loader, generation),
                name=f"gtasks-{name}-refresh",
                daemon=True,
            ).start()

        with self._condition:
            record = self._records.get(name)
            loading = name in self._loading
            error = self._errors.get(name)
            if record is None:
                status = "error" if error else "loading"
                return SurfaceRead(
                    payload=None,
                    state={
                        "surface": name,
                        "status": status,
                        "refreshing": loading,
                        "stale": False,
                        "last_valid_at": None,
                        "error": error,
                    },
                )
            age = max(0.0, self._clock() - float(record["last_valid_at"]))
            stale = loading or name in self._dirty or age > ttl_seconds or bool(error)
            return SurfaceRead(
                payload=deepcopy(dict(record["payload"])),
                state={
                    "surface": name,
                    "status": "refreshing" if loading else "stale" if stale else "fresh",
                    "refreshing": loading,
                    "stale": stale,
                    "last_valid_at": float(record["last_valid_at"]),
                    "error": error,
                },
            )

    def wait_for_idle(self, name: str, timeout_seconds: float = 5.0) -> bool:
        deadline = self._clock() + timeout_seconds
        with self._condition:
            while name in self._loading:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(remaining, 0.05))
            return True

    def _refresh(
        self,
        name: str,
        loader: Callable[[], dict[str, Any]],
        generation: int,
    ) -> None:
        try:
            payload = loader()
            if not isinstance(payload, dict):
                raise TypeError("surface loader did not return an object")
            last_valid_at = self._clock()
            with self._condition:
                if self._generations.get(name) != generation:
                    return
                self._records[name] = {
                    "payload": deepcopy(payload),
                    "last_valid_at": last_valid_at,
                }
                self._dirty.discard(name)
                self._errors.pop(name, None)
                persisted = deepcopy(self._records)
            try:
                self._store.save(persisted)
            except OSError:
                # A private performance cache must never take Mission Control down.
                pass
        except Exception:
            with self._condition:
                if self._generations.get(name) != generation:
                    return
                self._errors[name] = (
                    "The canonical GBrain refresh did not complete. Last verified data is kept."
                )
                self._error_at[name] = self._clock()
        finally:
            with self._condition:
                if self._generations.get(name) == generation:
                    self._loading.pop(name, None)
                self._condition.notify_all()
