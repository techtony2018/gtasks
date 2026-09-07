"""Synthetic-only retry identity tests for the next reliability iteration."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


class TaskOperationStoreTests(unittest.TestCase):
    def test_restart_preserves_identity_and_original_time(self) -> None:
        from gtasks.task_operations import TaskOperationStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operations.sqlite3"
            now = datetime(2026, 9, 7, 23, 59, tzinfo=timezone.utc)
            first = TaskOperationStore(path).reserve("create-1", {"title": "QA task"}, now)
            retried = TaskOperationStore(path).reserve(
                "create-1", {"title": "QA task"},
                datetime(2026, 9, 8, 0, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(first.identity, retried.identity)
            self.assertEqual(first.created_at, retried.created_at)
            self.assertTrue(first.is_new)
            self.assertFalse(retried.is_new)

    def test_changed_request_cannot_reuse_operation_identity(self) -> None:
        from gtasks.task_operations import TaskOperationConflict, TaskOperationStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskOperationStore(Path(temporary) / "operations.sqlite3")
            now = datetime.now(timezone.utc)
            store.reserve("create-1", {"title": "Original"}, now)
            with self.assertRaises(TaskOperationConflict):
                store.reserve("create-1", {"title": "Changed"}, now)

    def test_journal_does_not_store_task_text_or_become_canonical(self) -> None:
        from gtasks.task_operations import TaskOperationStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operations.sqlite3"
            TaskOperationStore(path).reserve(
                "create-1", {"title": "Private synthetic title never stored"},
                datetime.now(timezone.utc),
            )
            self.assertNotIn(b"Private synthetic title never stored", path.read_bytes())

    def test_parallel_reservations_allocate_only_one_new_attempt(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from gtasks.task_operations import TaskOperationStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskOperationStore(Path(temporary) / "operations.sqlite3")
            with ThreadPoolExecutor(max_workers=4) as workers:
                results = list(workers.map(
                    lambda _: store.reserve("same-click", {"title": "QA"}, datetime.now(timezone.utc)),
                    range(4),
                ))
            self.assertEqual(sum(item.is_new for item in results), 1)
            self.assertEqual(len({item.identity for item in results}), 1)

    def test_verified_slug_survives_restart(self) -> None:
        from gtasks.task_operations import TaskOperationStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operations.sqlite3"
            store = TaskOperationStore(path)
            operation = store.reserve("first", {"title": "QA"}, datetime.now(timezone.utc))
            store.mark_verified(operation)
            retried = TaskOperationStore(path).reserve("first", {"title": "QA"}, datetime.now(timezone.utc))
            self.assertTrue(retried.verified)
            self.assertEqual(retried.slug, operation.slug)


class TaskCreationRetryApiTests(unittest.TestCase):
    def harness(self, adapter):
        from tests.test_server import ServerHarness
        return ServerHarness(self, adapter)

    def test_lost_response_retry_reads_current_task_without_second_write(self):
        from dataclasses import replace
        from tests.test_server import FakeAdapter

        class CanonicalFake(FakeAdapter):
            def create_inbox(self, task):
                receipt = super().create_inbox(task)
                self.active = (*self.active, task)
                return receipt

        adapter = CanonicalFake()
        harness = self.harness(adapter)
        request = {"title": "Synthetic retry task"}
        headers = {"Idempotency-Key": "lost-response"}
        first_status, first, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(first_status, 201)
        # The browser did not receive the first response. Another writer enriches
        # the object before the same request is retried.
        adapter.active = (replace(adapter.active[0], detail="Later enrichment"),)
        status, recovered, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(status, 200)
        self.assertEqual(recovered["task"]["slug"], first["task"]["slug"])
        self.assertEqual(recovered["task"]["detail"], "Later enrichment")
        self.assertTrue(recovered["receipt"]["verified"])
        self.assertEqual(len(adapter.created), 1)

    def test_changed_payload_is_conflict_and_never_creates_again(self):
        from tests.test_server import FakeAdapter
        adapter = FakeAdapter()
        harness = self.harness(adapter)
        headers = {"Idempotency-Key": "same-click"}
        harness.request("POST", "/api/tasks", {"title": "First"}, headers)
        status, result, _ = harness.request("POST", "/api/tasks", {"title": "Changed"}, headers)
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "task_operation_conflict")
        self.assertEqual(len(adapter.created), 1)

    def test_uncertain_write_is_not_replayed(self):
        from tests.test_server import FakeAdapter
        from gtasks.gbrain import GBrainError

        class UncertainFake(FakeAdapter):
            def create_inbox(self, task):
                super().create_inbox(task)
                raise GBrainError("Synthetic response lost after remote write")

        adapter = UncertainFake()
        harness = self.harness(adapter)
        headers = {"Idempotency-Key": "uncertain"}
        request = {"title": "Ambiguous result"}
        status, first, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(status, 502)
        self.assertEqual(first["code"], "task_operation_unconfirmed")
        self.assertTrue(first["slug"].startswith("tasks/"))
        status, result, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "task_operation_unconfirmed")
        self.assertTrue(result["slug"].startswith("tasks/"))
        self.assertEqual(len(adapter.created), 1)

    def test_initial_todo_transport_failure_reports_parent_as_unconfirmed_without_replay(self):
        from tests.test_server import FakeAdapter
        from gtasks.gbrain import GBrainError

        class UncertainTodoFake(FakeAdapter):
            def create_todo(self, task_slug, **payload):
                super().create_todo(task_slug, **payload)
                raise GBrainError("Synthetic TODO response lost")

        adapter = UncertainTodoFake()
        harness = self.harness(adapter)
        request = {"title": "Parent may exist", "initial_todo": "Child may exist"}
        headers = {"Idempotency-Key": "uncertain-todo"}
        status, first, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(status, 502)
        self.assertEqual(first["code"], "task_operation_unconfirmed")
        self.assertTrue(first["slug"].startswith("tasks/"))
        status, retry, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(status, 409)
        self.assertEqual(retry["code"], "task_operation_unconfirmed")
        self.assertEqual(len(adapter.created), 1)
        self.assertEqual(len(adapter.todo_creates), 1)

    def test_parallel_http_requests_attempt_one_remote_creation(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from tests.test_server import FakeAdapter

        started, release = threading.Event(), threading.Event()

        class HeldFake(FakeAdapter):
            def create_inbox(self, task):
                receipt = super().create_inbox(task)
                started.set()
                release.wait(2)
                self.active = (*self.active, task)
                return receipt

        adapter = HeldFake()
        harness = self.harness(adapter)
        request, headers = {"title": "Parallel synthetic"}, {"Idempotency-Key": "parallel"}
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(harness.request, "POST", "/api/tasks", request, headers)
            self.assertTrue(started.wait(1))
            try:
                status, result, _ = harness.request("POST", "/api/tasks", request, headers)
                self.assertEqual(status, 409)
                self.assertEqual(result["code"], "task_operation_unconfirmed")
            finally:
                release.set()
            self.assertEqual(first.result()[0], 201)
        self.assertEqual(len(adapter.created), 1)

    def test_invalid_request_key_causes_no_remote_write(self):
        from tests.test_server import FakeAdapter
        adapter = FakeAdapter()
        harness = self.harness(adapter)
        status, _, _ = harness.request("POST", "/api/tasks", {"title": "QA"}, {"Idempotency-Key": " "})
        self.assertEqual(status, 422)
        self.assertEqual(adapter.created, [])

    def test_unverified_initial_todo_does_not_finish_operation(self):
        from tests.test_server import FakeAdapter

        class UnverifiedTodoFake(FakeAdapter):
            def create_todo(self, task_slug, **payload):
                receipt = super().create_todo(task_slug, **payload)
                receipt.verified = False
                return receipt

        adapter = UnverifiedTodoFake()
        harness = self.harness(adapter)
        request = {"title": "Full synthetic task", "initial_todo": "First action"}
        headers = {"Idempotency-Key": "todo-unverified"}
        status, result, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(status, 502)
        self.assertEqual(result["code"], "partial_write")
        status, result, _ = harness.request("POST", "/api/tasks", request, headers)
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "task_operation_unconfirmed")
        self.assertEqual(len(adapter.created), 1)
        self.assertEqual(len(adapter.todo_creates), 1)

    def test_validation_error_after_parent_write_is_partial_not_safe_to_restart(self):
        from tests.test_server import FakeAdapter
        from gtasks.domain import DomainValidationError

        class InvalidTodoFake(FakeAdapter):
            def create_todo(self, task_slug, **payload):
                raise DomainValidationError("Synthetic TODO rejected after parent write")

        adapter = InvalidTodoFake()
        harness = self.harness(adapter)
        status, result, _ = harness.request("POST", "/api/tasks", {
            "title": "Parent created", "initial_todo": "Rejected child",
        }, {"Idempotency-Key": "partial-validation"})
        self.assertEqual(status, 502)
        self.assertEqual(result["code"], "partial_write")
        self.assertEqual(len(adapter.created), 1)

    def test_restart_after_midnight_recovers_original_slug_and_date(self):
        from tests.test_server import FakeAdapter, ServerHarness
        from gtasks.task_operations import TaskOperationStore

        class CanonicalFake(FakeAdapter):
            def create_inbox(self, task):
                receipt = super().create_inbox(task)
                self.active = (*self.active, task)
                return receipt

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "task-operations.sqlite3"
            adapter = CanonicalFake()
            first_server = ServerHarness(self, adapter,
                clock=lambda: datetime(2026, 9, 7, 23, 59, tzinfo=timezone.utc),
                task_operation_store=TaskOperationStore(path))
            request, headers = {"title": "Cross-midnight QA"}, {"Idempotency-Key": "restart"}
            status, first, _ = first_server.request("POST", "/api/tasks", request, headers)
            self.assertEqual(status, 201)
            first_server.close()
            second_server = ServerHarness(self, adapter,
                clock=lambda: datetime(2026, 9, 8, 0, 1, tzinfo=timezone.utc),
                task_operation_store=TaskOperationStore(path))
            status, recovered, _ = second_server.request("POST", "/api/tasks", request, headers)
            self.assertEqual(status, 200)
            self.assertEqual(recovered["task"]["slug"], first["task"]["slug"])
            self.assertEqual(recovered["task"]["due_day"], "2026-09-07")
            self.assertEqual(len(adapter.created), 1)
            second_server.close()


if __name__ == "__main__":
    unittest.main()
