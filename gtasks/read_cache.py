from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Lock
from time import monotonic, time
from typing import Any, Callable, Mapping

from .read_budget import (
    BoundedReadExecutor, ReadBudget, ReadCapacityExceeded, ReadDeadlineExceeded,
    current_budget, remaining_seconds, use_budget,
)


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
        monotonic_clock: Callable[[], float] = monotonic,
        max_workers: int = 4,
        max_queued: int = 8,
        max_waiters: int = 16,
    ) -> None:
        self._store = store
        self._clock = clock
        self._background = background
        self._max_refresh_seconds = max_refresh_seconds
        self._monotonic = monotonic_clock
        self._executor = BoundedReadExecutor(max_workers, max_queued, name="gtasks-cache-refresh")
        if max_waiters < 1:
            raise ValueError("Read waiter limit must be positive")
        self._max_waiters = max_waiters
        self._waiters = 0
        self._condition = Condition()
        self._persistence_lock = Lock()
        self._records = store.load()
        self._loading: dict[str, ReadBudget] = {}
        self._futures: dict[str, Any] = {}
        self._generations: dict[str, int] = {}
        self._dirty: set[str] = set()
        self._errors: dict[str, str] = {}
        self._error_at: dict[str, float] = {}
        self._error_codes: dict[str, str] = {}
        # Deliberately process-local: disk snapshots are not new canonical reads.
        self._verified_here: set[str] = set()
        self._last_read_observed_at: dict[str, float] = {}

    def _stop_loading(self, name: str) -> None:
        budget = self._loading.pop(name, None)
        if budget is not None:
            budget.cancelled.set()
        future = self._futures.pop(name, None)
        if future is not None:
            future.cancel()

    def _fail(self, name: str, code: str) -> None:
        self._errors[name] = (
            "The canonical GBrain refresh did not complete. Last verified data is kept."
        )
        self._error_codes[name] = code
        self._error_at[name] = self._clock()
        self._last_read_observed_at[name] = self._error_at[name]
        self._generations[name] = self._generations.get(name, 0) + 1
        self._stop_loading(name)
        self._condition.notify_all()

    def _expire(self, name: str) -> None:
        budget = self._loading.get(name)
        if budget is not None:
            try:
                budget.remaining()
            except ReadDeadlineExceeded:
                self._fail(name, "refresh_deadline")

    def invalidate(self, *names: str) -> None:
        with self._condition:
            self._dirty.update(names)
            for name in set(names):
                # A read started before a verified mutation cannot publish or
                # clear the replacement worker's loading/error state.
                self._generations[name] = self._generations.get(name, 0) + 1
                self._stop_loading(name)
            self._condition.notify_all()

    def read(
        self,
        name: str,
        loader: Callable[[], dict[str, Any]],
        *,
        ttl_seconds: float,
        force: bool = False,
        force_cooldown_seconds: float = 0.0,
        foreground_refresh: bool = False,
    ) -> SurfaceRead:
        start_refresh = False
        wait_deferred = False
        with self._condition:
            remaining_seconds()
            self._expire(name)
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
                seconds = remaining_seconds(self._max_refresh_seconds)
                budget = ReadBudget(seconds, clock=self._monotonic)
                self._loading[name] = budget
                self._generations[name] = self._generations.get(name, 0) + 1
                generation = self._generations[name]
                self._errors.pop(name, None)
                self._error_at.pop(name, None)
                self._error_codes.pop(name, None)
                start_refresh = True
            else:
                generation = self._generations.get(name, 0)

            if start_refresh:
                try:
                    with use_budget(budget):
                        self._futures[name] = self._executor.submit(
                            self._refresh, name, loader, generation,
                            wait_for_capacity=False,
                        )
                except (ReadCapacityExceeded, ReadDeadlineExceeded) as exc:
                    self._fail(name, "refresh_capacity" if isinstance(exc, ReadCapacityExceeded)
                               else "refresh_deadline")
            # Coalesced foreground readers share the admitted generation's
            # remaining deadline; they never grant it another full budget.
            if foreground_refresh or (not self._background and (start_refresh or force)):
                if name in self._loading and self._waiters >= self._max_waiters:
                    wait_deferred = True
                else:
                    self._waiters += 1
                    try:
                        while name in self._loading and self._generations.get(name) == generation:
                            # A joining caller may have less time than this
                            # useful shared refresh. End only its own wait;
                            # never fail/cancel the admitted job on its behalf.
                            remaining_seconds()
                            self._expire(name)
                            if name not in self._loading:
                                break
                            budget = self._loading[name]
                            remaining = max(0.0, budget.deadline - budget.clock())
                            self._condition.wait(timeout=remaining_seconds(min(remaining, 0.05)))
                        remaining_seconds()
                    finally:
                        self._waiters -= 1

        with self._condition:
            self._expire(name)
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
                        "age_seconds": None,
                        "error_code": self._error_codes.get(name),
                        "retryable": bool(error),
                        "wait_deferred": wait_deferred,
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
                    "age_seconds": age,
                    "error_code": self._error_codes.get(name),
                    "retryable": bool(error),
                    "wait_deferred": wait_deferred,
                    "error": error,
                },
            )

    def wait_for_idle(self, name: str, timeout_seconds: float = 5.0) -> bool:
        deadline = self._monotonic() + remaining_seconds(timeout_seconds)
        with self._condition:
            while name in self._loading:
                self._expire(name)
                if name not in self._loading:
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(remaining, 0.05))
            remaining_seconds()
            return True

    def inspect(self, surfaces: Mapping[str, float], *, optional: frozenset[str] = frozenset()) -> dict:
        """Inspect fixed-size metadata only; no loader, disk read or payload copy.

        Readiness describes recent observed evidence, not a live connectivity
        guarantee. Archive diagnostics can be optional without weakening the
        required active surfaces. Restored payloads must be reverified here.
        """
        if not self._condition.acquire(timeout=0.05):
            # Publication can copy a large payload under the existing cache
            # lock. Diagnostics must not inherit that unbounded wait.
            return {
                "status": "not_ready", "ready": False, "observed_at": self._clock(),
                "evidence": "recent_reads_not_live_probe",
                "surfaces": {name: {
                    "required": name not in optional, "status": "unavailable",
                    "refreshing": None, "last_valid_at": None, "age_seconds": None,
                    "max_age_seconds": ttl, "last_read_observed_at": None,
                    "provenance": "none", "issue_count": None, "error_code": "inspection_busy",
                } for name, ttl in surfaces.items()},
            }
        try:
            now = self._clock()
            result = {}
            for name, ttl in surfaces.items():
                record = self._records.get(name)
                timestamp = record.get("last_valid_at") if record else None
                valid_time = (type(timestamp) in (int, float) and math.isfinite(timestamp)
                              and 0 < timestamp <= now)
                age = now - timestamp if valid_time else None
                payload = record.get("payload") if record else None
                issues = payload.get("issues") if isinstance(payload, dict) else None
                count = len(issues) if isinstance(issues, list) else None
                loading = name in self._loading
                code = self._error_codes.get(name)
                if loading:
                    try:
                        self._loading[name].remaining()
                    except ReadDeadlineExceeded:
                        code = "refresh_deadline"
                if code:
                    status = "error"
                elif loading:
                    status = "refreshing"
                elif record is None:
                    status = "missing"
                elif not valid_time or count is None:
                    status, code = "invalid", "invalid_evidence"
                elif name not in self._verified_here:
                    status, code = "unverified", "restart_unverified"
                elif name in self._dirty or age > ttl:
                    status = "stale"
                elif count:
                    status, code = "error", "canonical_read_issues"
                else:
                    status = "fresh"
                result[name] = {
                    "required": name not in optional, "status": status,
                    "refreshing": loading, "last_valid_at": timestamp if valid_time else None,
                    "age_seconds": age, "max_age_seconds": ttl,
                    "last_read_observed_at": self._last_read_observed_at.get(name),
                    "provenance": ("current_process_read" if name in self._verified_here else
                                   "persisted_snapshot" if record else "none"),
                    "issue_count": count, "error_code": code,
                }
            ready = all(item["status"] == "fresh" for item in result.values() if item["required"])
            return {"status": "ready" if ready else "not_ready", "ready": ready,
                    "observed_at": now, "evidence": "recent_reads_not_live_probe", "surfaces": result}
        finally:
            self._condition.release()

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
                current_budget().remaining()
                self._records[name] = {
                    "payload": deepcopy(payload),
                    "last_valid_at": last_valid_at,
                }
                self._verified_here.add(name)
                self._last_read_observed_at[name] = last_valid_at
                self._dirty.discard(name)
                self._errors.pop(name, None)
                self._error_codes.pop(name, None)
            try:
                # Capture after acquiring the save lane so a delayed writer
                # cannot overwrite a newer snapshot already persisted by another
                # surface or generation. Disk I/O never holds the read lock.
                with self._persistence_lock:
                    with self._condition:
                        persisted = deepcopy(self._records)
                    self._store.save(persisted)
            except OSError:
                # A private performance cache must never take Mission Control down.
                pass
        except Exception as exc:
            with self._condition:
                if self._generations.get(name) != generation:
                    return
                self._fail(name, "refresh_deadline" if isinstance(exc, ReadDeadlineExceeded)
                           else "refresh_failed")
        finally:
            with self._condition:
                if self._generations.get(name) == generation:
                    self._loading.pop(name, None)
                    self._futures.pop(name, None)
                self._condition.notify_all()
