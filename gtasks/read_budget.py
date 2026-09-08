"""One cooperative read deadline and bounded, non-joining read worker lanes.

Timeout ends the caller's wait, not an uncooperative Python/remote operation.
Such operations keep their occupied slot until they actually return.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from threading import Condition, Event, Thread
from time import monotonic
from typing import Callable


class ReadDeadlineExceeded(TimeoutError):
    pass


class ReadCapacityExceeded(RuntimeError):
    pass


class ReadBudget:
    def __init__(self, seconds: float, *, clock: Callable[[], float] = monotonic):
        self.clock = clock
        self.deadline = clock() + seconds
        self.cancelled = Event()
        self.parent = current_budget()

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if self.cancelled.is_set() or remaining <= 0:
            raise ReadDeadlineExceeded("Read deadline exhausted")
        return min(remaining, self.parent.remaining()) if self.parent is not None else remaining


@dataclass(frozen=True, slots=True)
class _ReadBudgetIntersection:
    """An entry-local view; never reparent or mutate reusable budget objects."""
    supplied: ReadBudget | _ReadBudgetIntersection
    active: ReadBudget | _ReadBudgetIntersection

    def remaining(self) -> float:
        return min(self.supplied.remaining(), self.active.remaining())


_current: ContextVar[ReadBudget | _ReadBudgetIntersection | None] = ContextVar("gtasks_read_budget", default=None)
_worker_lane: ContextVar[object] = ContextVar("gtasks_read_worker_lane", default=None)


def current_budget() -> ReadBudget | _ReadBudgetIntersection | None:
    return _current.get()


def remaining_seconds(default: float | None = None) -> float | None:
    budget = current_budget()
    if budget is None:
        return default
    remaining = budget.remaining()
    return remaining if default is None else min(default, remaining)


def check_budget() -> None:
    remaining_seconds()


@contextmanager
def use_budget(budget: ReadBudget | _ReadBudgetIntersection):
    parent = current_budget()
    effective = _ReadBudgetIntersection(budget, parent) if parent is not None and parent is not budget else budget
    token = _current.set(effective)
    try:
        check_budget()
        yield
    finally:
        _current.reset(token)


@contextmanager
def budget_lock(lock):
    timeout = remaining_seconds()
    acquired = lock.acquire() if timeout is None else lock.acquire(timeout=timeout)
    if not acquired:
        raise ReadDeadlineExceeded("Read deadline exhausted waiting for lock")
    try:
        check_budget()
        yield
    finally:
        lock.release()


def read_result(future: Future):
    try:
        result = future.result(timeout=remaining_seconds())
    except TimeoutError as exc:
        future.cancel()
        raise ReadDeadlineExceeded("Read deadline exhausted waiting for worker") from exc
    check_budget()
    return result


class BoundedReadExecutor:
    """FIFO daemon workers; queued cancellations are pruned before admission.

    Idle workers exit instead of leaving a permanent pool per cache instance.
    The queue and *actual executing* workers stay bounded even after timeouts.
    """
    def __init__(self, max_workers: int, max_queued: int, *, name: str):
        if max_workers < 1 or max_queued < 1:
            raise ValueError("Read worker and queue limits must be positive")
        self.max_workers = max_workers
        self.max_queued = max_queued
        self.name = name
        self._condition = Condition()
        self._pending = deque()
        self._workers = 0

    def submit(self, function, *args, wait_for_capacity: bool = True) -> Future:
        context = copy_context()
        future = Future()
        with self._condition:
            while True:
                self._pending = deque(job for job in self._pending if not job[0].cancelled())
                check_budget()
                if len(self._pending) < self.max_queued:
                    break
                if not wait_for_capacity:
                    raise ReadCapacityExceeded("Read queue is full")
                self._condition.wait(timeout=remaining_seconds(0.05))
            self._pending.append((future, context, function, args))
            if self._workers < self.max_workers:
                self._workers += 1
                Thread(target=self._work, name=self.name, daemon=True).start()
            self._condition.notify_all()
        return future

    def _work(self):
        while True:
            with self._condition:
                if not self._pending:
                    self._workers -= 1
                    self._condition.notify_all()
                    return
                future, context, function, args = self._pending.popleft()
                self._condition.notify_all()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                def invoke():
                    check_budget()
                    token = _worker_lane.set(self)
                    try:
                        return function(*args)
                    finally:
                        _worker_lane.reset(token)
                future.set_result(context.run(invoke))
            except BaseException as exc:
                future.set_exception(exc)

    def map(self, function, values):
        # Hydration can nest (task -> TODO -> comments). Nested work runs in
        # its existing lane so all workers cannot deadlock waiting on children.
        if _worker_lane.get() is self:
            result = []
            for value in values:
                check_budget()
                result.append(function(value))
                check_budget()
            return result
        pending = deque()
        result = []
        iterator = iter(values)
        try:
            for _ in range(self.max_workers):
                try:
                    value = next(iterator)
                except StopIteration:
                    break
                pending.append(self.submit(function, value))
            while pending:
                result.append(read_result(pending.popleft()))
                try:
                    value = next(iterator)
                except StopIteration:
                    continue
                pending.append(self.submit(function, value))
            return result
        finally:
            for future in pending:
                future.cancel()
