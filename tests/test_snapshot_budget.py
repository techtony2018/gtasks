import threading
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from gtasks.domain import ACTIVE_ROOT, COMPLETED_ROOT, new_task
from gtasks.gbrain import GBrainAdapter
from gtasks.read_budget import ReadBudget, check_budget, use_budget
from gtasks.read_cache import ReadSnapshotStore, ReadSurfaceCache
from gtasks.server import build_task_snapshot
from tests.test_server import FakeAdapter, ServerHarness, sample_goal


class SnapshotBudgetTests(unittest.TestCase):
    def test_repeated_snapshot_timeouts_bound_actual_inner_workers(self):
        release = threading.Event()
        condition = threading.Condition()
        calls = 0
        active = 0
        class HeldAdapter(FakeAdapter):
            def list_collection_tasks(self, root):
                nonlocal calls, active
                with condition:
                    calls += 1
                    active += 1
                try:
                    release.wait(2)
                    check_budget()
                    return super().list_collection_tasks(root)
                finally:
                    with condition:
                        active -= 1
                        condition.notify_all()
        try:
            for _ in range(8):
                with use_budget(ReadBudget(0.025)):
                    with self.assertRaises(TimeoutError):
                        build_task_snapshot(HeldAdapter(), date(2026, 9, 7))
            self.assertLessEqual(calls, 4)
        finally:
            release.set()
            with condition:
                self.assertTrue(condition.wait_for(lambda: active == 0, timeout=1))
        with use_budget(ReadBudget(1)):
            self.assertEqual(build_task_snapshot(FakeAdapter(), date(2026, 9, 7))["tasks"], [])

    def test_repeated_hydration_timeouts_bound_actual_workers_and_nested_work_converges(self):
        release = threading.Event()
        condition = threading.Condition()
        calls = 0
        active = 0
        adapter = GBrainAdapter(object())
        def held(value):
            nonlocal calls, active
            with condition:
                calls += 1
                active += 1
            try:
                release.wait(2)
                check_budget()
                return value
            finally:
                with condition:
                    active -= 1
                    condition.notify_all()
        try:
            for _ in range(8):
                with use_budget(ReadBudget(0.025)):
                    with self.assertRaises(TimeoutError):
                        adapter._bounded_map(held, list(range(16)))
            self.assertLessEqual(calls, 8)
        finally:
            release.set()
            with condition:
                self.assertTrue(condition.wait_for(lambda: active == 0, timeout=1))
        with use_budget(ReadBudget(1)):
            result = adapter._bounded_map(
                lambda value: adapter._bounded_map(lambda item: item + value, [1, 2]), list(range(8)))
        self.assertEqual(result, [[value + 1, value + 2] for value in range(8)])

    def test_proposal_dependency_wait_consumes_budget_before_canonical_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = ReadSurfaceCache(ReadSnapshotStore(Path(temporary) / "reads.json"),
                                     max_refresh_seconds=0.08)
            release, entered = threading.Event(), threading.Event()
            canonical_calls = []
            class Adapter(FakeAdapter):
                def list_proposals(self):
                    canonical_calls.append("proposals")
                    return super().list_proposals()
            def held():
                entered.set()
                release.wait(2)
                return {"ok": True}
            harness = ServerHarness(self, Adapter(), read_cache=cache)
            try:
                # A task job is admitted slightly later than the proposal
                # budget used below, so the dependency outlives that budget.
                cache._max_refresh_seconds = 1
                cache.read("tasks", held, ttl_seconds=30)
                self.assertTrue(entered.wait(1))
                cache._max_refresh_seconds = 0.05
                started = time.monotonic()
                status, payload, _ = harness.request("GET", "/api/proposals?refresh=1")
                self.assertLess(time.monotonic() - started, 0.2)
                self.assertFalse(payload["read_state"]["refreshing"])
                self.assertEqual(payload["read_state"]["error_code"], "refresh_deadline")
                self.assertEqual(canonical_calls, [])
            finally:
                release.set()
                cache.wait_for_idle("tasks")
                cache.wait_for_idle("proposals")
                harness.close()

    def test_snapshot_deadline_does_not_join_uncooperative_inner_workers(self):
        release = threading.Event()
        class HeldAdapter(FakeAdapter):
            def list_collection_tasks(self, root):
                release.wait(0.35)
                return super().list_collection_tasks(root)
        try:
            started = time.monotonic()
            with use_budget(ReadBudget(0.05)):
                with self.assertRaises(TimeoutError):
                    build_task_snapshot(HeldAdapter(), date(2026, 9, 7))
            self.assertLess(time.monotonic() - started, 0.2)
        finally:
            release.set()

    def test_hydration_deadline_does_not_join_uncooperative_inner_workers(self):
        release = threading.Event()
        adapter = GBrainAdapter(object())
        try:
            started = time.monotonic()
            with use_budget(ReadBudget(0.05)):
                with self.assertRaises(TimeoutError):
                    adapter._bounded_map(lambda value: (release.wait(0.35), value)[1], list(range(16)))
            self.assertLess(time.monotonic() - started, 0.2)
        finally:
            release.set()

    def test_active_snapshot_enriches_active_todos_only_and_keeps_archived_summary(self):
        now = datetime(2026, 9, 7, tzinfo=timezone.utc)
        goal = sample_goal()
        active = new_task(title="Active", now=now, identity="active-budget", goal=goal.slug)
        archived = replace(new_task(title="Archived", now=now, identity="archived-budget"),
                           lifecycle_root=COMPLETED_ROOT, status="completed", completed_at=now, goal=goal.slug)
        class RecordingAdapter(FakeAdapter):
            def __init__(self):
                super().__init__(active=(active,), completed=(archived,), goals=(goal,))
                self.enriched = []
            def enrich_tasks_with_todos(self, tasks):
                self.enriched.extend(task.slug for task in tasks)
                return tuple(tasks), ()
        adapter = RecordingAdapter()
        snapshot = build_task_snapshot(adapter, now.date())
        self.assertEqual(adapter.enriched, [active.slug])
        self.assertEqual({task["slug"] for task in snapshot["tasks"]}, {active.slug, archived.slug})
        self.assertEqual(snapshot["goals"][0]["progress"]["linked"], 2)
        self.assertEqual(snapshot["goals"][0]["completed_tasks"][0]["slug"], archived.slug)
        archived_payloads = [
            next(task for task in snapshot["tasks"] if task["slug"] == archived.slug),
            snapshot["views"]["completed"][0],
            snapshot["goals"][0]["completed_tasks"][0],
        ]
        self.assertTrue(all(task.get("todos_deferred") is True for task in archived_payloads))
        active_payloads = [
            next(task for task in snapshot["tasks"] if task["slug"] == active.slug),
            snapshot["goals"][0]["active_tasks"][0],
        ]
        self.assertTrue(all(not task.get("todos_deferred") for task in active_payloads))


if __name__ == "__main__":
    unittest.main()
