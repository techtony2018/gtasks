"""Bounded, privacy-safe local diagnostics; never canonical availability proof."""
from __future__ import annotations

import re
from threading import Lock, Thread
from time import monotonic, time
from typing import Callable


class GbrainVersionProbe:
    """One in-flight provider per server, no queued or abandoned replacements.

    A stuck provider occupies this one slot until it really returns. The default
    subprocess has its own timeout; liveness never waits for either timeout.
    """

    def __init__(self, provider: Callable[[], str | None], *, clock=monotonic,
                 wall_clock=time, ttl_seconds=300.0, retry_seconds=5.0):
        self._provider = provider
        self._clock, self._wall_clock = clock, wall_clock
        self._ttl, self._retry = ttl_seconds, retry_seconds
        self._lock = Lock()
        self._running = False
        self._next_probe = 0.0
        self._value = "unavailable"
        self._verified_at = None
        self._observed_at = None
        self._error = None

    def snapshot(self) -> dict:
        with self._lock:
            if not self._running and self._clock() >= self._next_probe:
                self._running = True
                try:
                    Thread(target=self._run, name="gtasks-version-probe", daemon=True).start()
                except RuntimeError:
                    self._running = False
                    self._error = "version_unavailable"
                    self._next_probe = self._clock() + self._retry
            status = (("pending" if self._running else "unavailable")
                      if self._verified_at is None else
                      "stale" if self._running or self._error else "verified")
            return {
                "gbrain_version": self._value,
                "gbrain_version_state": {
                    "status": status, "refreshing": self._running,
                    "verified_at": self._verified_at, "observed_at": self._observed_at,
                    "provenance": "runtime_version_probe" if self._verified_at is not None else "none",
                    "error_code": self._error,
                },
            }

    def _run(self) -> None:
        try:
            raw = self._provider()
            # Accept only a bounded version grammar, never arbitrary CLI output.
            value = raw.strip() if isinstance(raw, str) else ""
            valid = len(value) <= 96 and re.fullmatch(
                r"(?:gbrain(?:-test)?\s+)?v?\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9.]+)?",
                value, re.IGNORECASE,
            ) is not None
        except Exception:
            valid, value = False, ""
        with self._lock:
            self._observed_at = self._wall_clock()
            if valid:
                self._value = value
                self._verified_at = self._observed_at
            self._error = None if valid else "version_unavailable"
            self._next_probe = self._clock() + (self._ttl if valid else self._retry)
            self._running = False
