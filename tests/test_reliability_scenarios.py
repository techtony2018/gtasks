"""Reusable isolated failure scenarios; no live canonical adapters or stores."""
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from unittest.mock import patch
import gtasks.server as server_module

from gtasks.diagnostics import GbrainVersionProbe
from gtasks.read_cache import ReadSnapshotStore, ReadSurfaceCache
from gtasks.task_operations import TaskOperationStore
from tests.test_server import FakeAdapter, ServerHarness


REQUIRED = ("tasks", "projects", "proposals", "agent_work", "system_tickets")
PRIVATE = "PRIVATE-title-body-slug-token-/private/path"


class CanonicalFake(FakeAdapter):
    def create_inbox(self, task):
        receipt = super().create_inbox(task)
        self.active = (*self.active, task)
        return receipt


def settled_probe(probe):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        snapshot = probe.snapshot()
        if not snapshot["gbrain_version_state"]["refreshing"]:
            return snapshot
        threading.Event().wait(0.001)
    raise AssertionError("probe did not settle")


class ReliabilityScenarios(unittest.TestCase):
    def test_failed_and_invalid_version_output_is_unavailable_not_arbitrary_text(self):
        for value in (None, "", PRIVATE, "gbrain 1.2.3\nsecret", "x" * 10000):
            with self.subTest(value=str(value)[:20]):
                probe = GbrainVersionProbe(lambda: value)
                result = settled_probe(probe)
                self.assertEqual(result["gbrain_version"], "unavailable")
                self.assertEqual(result["gbrain_version_state"]["status"], "unavailable")
                self.assertNotIn(PRIVATE, json.dumps(result))

    def test_expired_probe_is_bounded_held_then_failed_preserving_verified_version(self):
        now = [1000.0]
        entered, release = threading.Event(), threading.Event()
        calls = []
        def provider():
            calls.append(1)
            if len(calls) == 1:
                return "gbrain 1.2.3"
            entered.set()
            release.wait(2)
            raise RuntimeError(PRIVATE)
        probe = GbrainVersionProbe(provider, clock=lambda: now[0], wall_clock=lambda: now[0])
        with patch.object(server_module, "GbrainVersionProbe", return_value=probe):
            harness = ServerHarness(self, FakeAdapter())
        self.assertEqual(settled_probe(probe)["gbrain_version_state"]["status"], "verified")
        now[0] += 301
        try:
            started = time.monotonic()
            self.assertEqual(probe.snapshot()["gbrain_version_state"]["status"], "stale")
            self.assertTrue(entered.wait(1))
            for _ in range(100):
                self.assertEqual(probe.snapshot()["gbrain_version"], "gbrain 1.2.3")
            status, payload, _ = harness.request("GET", "/api/health")
            self.assertEqual(status, 200)
            self.assertEqual(payload["gbrain_version_state"]["status"], "stale")
            self.assertLess(time.monotonic() - started, 0.2)
            now[0] += 10000  # No timeout-based replacement of the held worker.
            probe.snapshot()
            self.assertEqual(len(calls), 2)
        finally:
            release.set()
        result = settled_probe(probe)
        self.assertEqual(result["gbrain_version_state"]["status"], "stale")
        self.assertEqual(result["gbrain_version_state"]["verified_at"], 1000)
        self.assertEqual(result["gbrain_version_state"]["error_code"], "version_unavailable")
        self.assertNotIn(PRIVATE, json.dumps(result))
        probe.snapshot()
        self.assertEqual(len(calls), 2)
        started = time.monotonic()
        self.assertEqual(harness.request("GET", "/api/health")[0], 200)
        self.assertLess(time.monotonic() - started, 0.2)

    def cache(self, *, background=False, clock=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = ReadSnapshotStore(Path(directory.name) / "reads.json")
        return ReadSurfaceCache(store, background=background, clock=clock or time.time), store

    def seed(self, cache):
        for name in REQUIRED:
            cache.read(name, lambda: {"items": [PRIVATE], "issues": []}, ttl_seconds=300)

    def test_cold_health_is_prompt_and_concurrent_requests_share_held_version_probe(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def held():
            calls.append(1)
            entered.set()
            release.wait(3)
            return "gbrain 1.2.3"
        harness = ServerHarness(self, FakeAdapter(), gbrain_version_provider=held)
        with ThreadPoolExecutor(max_workers=8) as workers:
            try:
                first = workers.submit(harness.request, "GET", "/api/health")
                self.assertTrue(entered.wait(1))
                try:
                    status, payload, _ = first.result(timeout=0.2)
                except TimeoutError:
                    self.fail("liveness waited for the held version provider")
                self.assertEqual(status, 200)
                self.assertEqual(payload["gbrain_version"], "unavailable")
                self.assertEqual(payload["gbrain_version_state"]["status"], "pending")
                requests = [workers.submit(harness.request, "GET", "/api/health") for _ in range(8)]
                for request in requests:
                    self.assertEqual(request.result(timeout=0.3)[0], 200)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
            # Readback waits only in the test; health never waits for the provider.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                _, payload, _ = harness.request("GET", "/api/health")
                if payload.get("gbrain_version_state", {}).get("status") == "verified":
                    break
            self.assertEqual(payload["gbrain_version"], "gbrain 1.2.3")
            self.assertEqual(len(calls), 1)

    def test_readiness_is_metadata_only_required_surfaces_and_private(self):
        cache, _ = self.cache()
        calls = []
        harness = ServerHarness(self, FakeAdapter(), read_cache=cache,
                                gbrain_version_provider=lambda: calls.append(1))
        status, payload, _ = harness.request("GET", "/api/readiness")
        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(calls, [])
        self.seed(cache)
        status, payload, _ = harness.request("GET", "/api/readiness")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(set(payload["surfaces"]), {*REQUIRED, "system_tickets_all"})
        self.assertFalse(payload["surfaces"]["system_tickets_all"]["required"])
        self.assertEqual(payload["surfaces"]["system_tickets_all"]["status"], "missing")
        self.assertNotIn(PRIVATE, json.dumps(payload))
        self.assertEqual(calls, [])
        cache.invalidate("projects")
        self.assertEqual(harness.request("GET", "/api/readiness")[0], 503)

    def test_readiness_fails_closed_promptly_when_cache_publication_lock_is_busy(self):
        cache, _ = self.cache()
        self.seed(cache)
        harness = ServerHarness(self, FakeAdapter(), read_cache=cache)
        entered, release = threading.Event(), threading.Event()
        def held_publication():
            with cache._condition:
                entered.set()
                release.wait(2)
        worker = threading.Thread(target=held_publication)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            with ThreadPoolExecutor(max_workers=1) as callers:
                request = callers.submit(harness.request, "GET", "/api/readiness")
                try:
                    status, payload, _ = request.result(timeout=0.2)
                except TimeoutError:
                    release.set()
                    self.fail("readiness waited on a busy cache publication lock")
                self.assertEqual(status, 503)
                self.assertEqual(payload["surfaces"]["tasks"]["error_code"], "inspection_busy")
        finally:
            release.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())

    def test_expired_auth_is_alive_not_ready_and_retains_verified_age(self):
        now = [1000.0]
        cache, _ = self.cache(clock=lambda: now[0])
        self.seed(cache)
        harness = ServerHarness(self, FakeAdapter(), read_cache=cache)
        now[0] += 10
        def expired_auth():
            raise RuntimeError("401 " + PRIVATE)
        cache.read("tasks", expired_auth, ttl_seconds=300, force=True)
        self.assertEqual(harness.request("GET", "/api/health")[0], 200)
        status, payload, _ = harness.request("GET", "/api/readiness")
        self.assertEqual(status, 503)
        evidence = payload["surfaces"]["tasks"]
        self.assertEqual(evidence["last_valid_at"], 1000)
        self.assertEqual(evidence["age_seconds"], 10)
        self.assertEqual(evidence["last_read_observed_at"], 1010)
        self.assertEqual(evidence["error_code"], "refresh_failed")
        self.assertNotIn(PRIVATE, json.dumps(payload))

    def test_restart_during_recovery_requires_new_process_evidence(self):
        cache, store = self.cache()
        self.seed(cache)
        restarted = ReadSurfaceCache(store, background=True)
        harness = ServerHarness(self, FakeAdapter(), read_cache=restarted)
        status, payload, _ = harness.request("GET", "/api/readiness")
        self.assertEqual(status, 503)
        self.assertEqual(payload["surfaces"]["tasks"]["provenance"], "persisted_snapshot")
        self.assertIsNone(payload["surfaces"]["tasks"]["last_read_observed_at"])
        entered, release = threading.Event(), threading.Event()
        def held():
            entered.set()
            release.wait(2)
            return {"issues": []}
        try:
            restarted.read("tasks", held, ttl_seconds=300, force=True)
            self.assertTrue(entered.wait(1))
            status, payload, _ = harness.request("GET", "/api/readiness")
            self.assertEqual(status, 503)
            self.assertEqual(payload["surfaces"]["tasks"]["status"], "refreshing")
        finally:
            release.set()
            self.assertTrue(restarted.wait_for_idle("tasks"))
        for name in REQUIRED:
            restarted.read(name, lambda: {"issues": []}, ttl_seconds=300, force=True,
                           foreground_refresh=True)
        self.assertEqual(harness.request("GET", "/api/readiness")[0], 200)

    def test_readiness_rejects_expired_invalid_and_issue_bearing_evidence_without_copy(self):
        now = [1000.0]
        cache, _ = self.cache(clock=lambda: now[0])
        self.seed(cache)
        harness = ServerHarness(self, FakeAdapter(), read_cache=cache)
        # Prove endpoint inspection does not deep-copy cached private payloads.
        with patch("gtasks.read_cache.deepcopy", side_effect=AssertionError("payload copied")):
            self.assertEqual(harness.request("GET", "/api/readiness")[0], 200)
        now[0] += 301
        status, payload, _ = harness.request("GET", "/api/readiness")
        self.assertEqual(status, 503)
        self.assertEqual(payload["surfaces"]["tasks"]["status"], "stale")
        cache.read("tasks", lambda: {"issues": [PRIVATE]}, ttl_seconds=300, force=True)
        status, payload, _ = harness.request("GET", "/api/readiness")
        self.assertEqual(payload["surfaces"]["tasks"]["issue_count"], 1)
        self.assertEqual(payload["surfaces"]["tasks"]["error_code"], "canonical_read_issues")
        self.assertNotIn(PRIVATE, json.dumps(payload))
        for bad_time in (float("nan"), float("inf"), True, -1, now[0] + 100):
            with self.subTest(timestamp=bad_time):
                cache._records["tasks"]["last_valid_at"] = bad_time
                status, payload, _ = harness.request("GET", "/api/readiness")
                self.assertEqual(status, 503)
                self.assertIsNone(payload["surfaces"]["tasks"]["last_valid_at"])
                self.assertEqual(payload["surfaces"]["tasks"]["status"], "invalid")

    def test_overlapping_refresh_and_verified_write_never_restore_prewrite_readiness(self):
        cache, _ = self.cache(background=True)
        for name in REQUIRED:
            cache.read(name, lambda: {"issues": []}, ttl_seconds=300, foreground_refresh=True)
        adapter = CanonicalFake()
        harness = ServerHarness(self, adapter, read_cache=cache)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def old_read():
            entered.set()
            release.wait(2)
            finished.set()
            return {"tasks": [], "issues": []}
        try:
            cache.read("tasks", old_read, ttl_seconds=300, force=True)
            self.assertTrue(entered.wait(1))
            status, saved, _ = harness.request("POST", "/api/tasks", {"title": "Overlap QA"},
                                             {"Idempotency-Key": "overlap"})
            self.assertEqual(status, 201)
            self.assertTrue(saved["receipt"]["verified"])
            self.assertEqual(harness.request("GET", "/api/readiness")[0], 503)
            release.set()
            self.assertTrue(finished.wait(1))
            status, payload, _ = harness.request("GET", "/api/readiness")
            self.assertEqual(status, 503)
            self.assertEqual(payload["surfaces"]["tasks"]["status"], "stale")
            # Real HTTP refresh, not a synthetic replacement of the cache record.
            status, current, _ = harness.request("GET", "/api/tasks?refresh=1")
            self.assertEqual(status, 200)
            self.assertIn(saved["task"]["slug"], [task["slug"] for task in current["tasks"]])
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
            cache.wait_for_idle("tasks")

    def test_response_really_dropped_after_save_then_restart_retry_does_not_duplicate(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "operations.sqlite3"
        adapter = CanonicalFake()
        harness = ServerHarness(self, adapter, task_operation_store=TaskOperationStore(path))
        handler = harness.server.RequestHandlerClass
        original_json = handler._json
        dropped = threading.Event()
        def drop_saved_response(request_handler, status, payload):
            if request_handler.command == "POST" and status == 201:
                # Close the real socket before headers/body: the client cannot
                # receive a success response that this test merely ignores.
                request_handler.connection.shutdown(socket.SHUT_RDWR)
                request_handler.connection.close()
                request_handler.close_connection = True
                dropped.set()
                return
            return original_json(request_handler, status, payload)
        body = {"title": "Dropped response QA"}
        headers = {"Idempotency-Key": "actual-drop"}
        connection = http.client.HTTPConnection("127.0.0.1", harness.server.server_address[1], timeout=2)
        try:
            with patch.object(handler, "_json", drop_saved_response):
                connection.request("POST", "/api/tasks", json.dumps(body),
                                   {**headers, "Content-Type": "application/json"})
                with self.assertRaises(http.client.RemoteDisconnected):
                    connection.getresponse()
        finally:
            connection.close()
        self.assertTrue(dropped.wait(1))
        self.assertEqual(len(adapter.created), 1)
        original_slug = adapter.active[0].slug
        harness.close()
        restarted = ServerHarness(self, adapter, task_operation_store=TaskOperationStore(path))
        status, result, _ = restarted.request("POST", "/api/tasks", body, headers)
        self.assertEqual(status, 200)
        self.assertEqual(result["task"]["slug"], original_slug)
        self.assertTrue(result["receipt"]["verified"])
        self.assertEqual(len(adapter.created), 1)


if __name__ == "__main__":
    unittest.main()
