import json
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from gtasks.read_cache import ReadSnapshotStore, ReadSurfaceCache


class ReadSnapshotStoreTests(unittest.TestCase):
    def test_ignores_pre_codex_only_schema_to_avoid_retired_agent_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "surfaces": {
                            "agent_work": {
                                "payload": {
                                    "roots": ["collections/tammy-oc-tasks"],
                                    "tasks": [],
                                },
                                "last_valid_at": 42.0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(ReadSnapshotStore(path).load(), {})

    def test_persists_only_last_valid_surface_payload_privately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state" / "read-snapshots.json"
            store = ReadSnapshotStore(path)

            store.save(
                {
                    "tasks": {
                        "payload": {"tasks": [{"slug": "tasks/example"}]},
                        "last_valid_at": 42.0,
                    }
                }
            )

            self.assertEqual(
                store.load()["tasks"]["payload"]["tasks"][0]["slug"],
                "tasks/example",
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"],
                2,
            )

    def test_ignores_corrupt_or_unknown_cache_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            path.write_text('{"schema_version":99,"surfaces":{}}', encoding="utf-8")
            self.assertEqual(ReadSnapshotStore(path).load(), {})
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(ReadSnapshotStore(path).load(), {})


class ReadSurfaceCacheTests(unittest.TestCase):
    def test_cold_failure_is_reported_without_immediate_retry_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls = 0

            def loader() -> dict:
                nonlocal calls
                calls += 1
                raise RuntimeError("private")

            cache = ReadSurfaceCache(
                ReadSnapshotStore(Path(temporary) / "reads.json"),
                background=False,
            )
            first = cache.read("proposals", loader, ttl_seconds=30)
            second = cache.read("proposals", loader, ttl_seconds=30)

            self.assertEqual(first.state["status"], "error")
            self.assertEqual(second.state["status"], "error")
            self.assertEqual(calls, 1)

    def test_returns_last_valid_immediately_while_one_refresh_is_coalesced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "proposals": {
                        "payload": {"proposals": [{"slug": "tasks/old"}]},
                        "last_valid_at": 10.0,
                    }
                }
            )
            entered = threading.Event()
            release = threading.Event()
            reads = 0

            def loader() -> dict:
                nonlocal reads
                reads += 1
                entered.set()
                release.wait(timeout=2)
                return {"proposals": [{"slug": "tasks/new"}]}

            cache = ReadSurfaceCache(store, clock=lambda: 100.0)
            first = cache.read("proposals", loader, ttl_seconds=30)
            self.assertTrue(entered.wait(timeout=1))
            second = cache.read("proposals", loader, ttl_seconds=30, force=True)

            self.assertEqual(first.payload["proposals"][0]["slug"], "tasks/old")
            self.assertEqual(second.payload["proposals"][0]["slug"], "tasks/old")
            self.assertEqual(first.state["status"], "refreshing")
            self.assertEqual(reads, 1)

            release.set()
            self.assertTrue(cache.wait_for_idle("proposals"))
            refreshed = cache.read("proposals", loader, ttl_seconds=30)
            self.assertEqual(refreshed.payload["proposals"][0]["slug"], "tasks/new")
            self.assertEqual(refreshed.state["status"], "fresh")

    def test_failed_refresh_keeps_last_valid_and_labels_it_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "tasks": {
                        "payload": {"tasks": [{"slug": "tasks/kept"}]},
                        "last_valid_at": 1.0,
                    }
                }
            )
            cache = ReadSurfaceCache(store, clock=lambda: 100.0, background=False)

            result = cache.read(
                "tasks",
                lambda: (_ for _ in ()).throw(RuntimeError("private detail")),
                ttl_seconds=30,
                force=True,
            )

            self.assertEqual(result.payload["tasks"][0]["slug"], "tasks/kept")
            self.assertEqual(result.state["status"], "stale")
            self.assertNotIn("private detail", result.state["error"])

    def test_cold_background_read_is_non_blocking_and_becomes_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            entered = threading.Event()
            release = threading.Event()

            def loader() -> dict:
                entered.set()
                release.wait(timeout=2)
                return {"tasks": []}

            cache = ReadSurfaceCache(ReadSnapshotStore(path))
            initial = cache.read("tasks", loader, ttl_seconds=30)
            self.assertIsNone(initial.payload)
            self.assertEqual(initial.state["status"], "loading")
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            self.assertTrue(cache.wait_for_idle("tasks"))
            self.assertEqual(
                cache.read("tasks", loader, ttl_seconds=30).payload,
                {"tasks": []},
            )

    def test_expired_background_refresh_force_starts_bounded_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "tasks": {
                        "payload": {"tasks": [{"slug": "tasks/old"}]},
                        "last_valid_at": 1.0,
                    }
                }
            )
            now = 100.0
            stalled_entered = threading.Event()
            release_stalled = threading.Event()
            replacement_entered = threading.Event()
            release_replacement = threading.Event()
            calls = 0

            def clock() -> float:
                return now

            def loader() -> dict:
                nonlocal calls
                calls += 1
                stalled_entered.set()
                if calls == 1:
                    release_stalled.wait(timeout=2)
                    return {"tasks": [{"slug": "tasks/stalled"}]}
                replacement_entered.set()
                release_replacement.wait(timeout=2)
                return {"tasks": [{"slug": "tasks/new"}]}

            cache = ReadSurfaceCache(
                store,
                clock=clock,
                max_refresh_seconds=5,
            )
            try:
                first = cache.read("tasks", loader, ttl_seconds=30, force=True)
                self.assertEqual(first.payload["tasks"][0]["slug"], "tasks/old")
                self.assertEqual(first.state["status"], "refreshing")
                self.assertTrue(stalled_entered.wait(timeout=1))

                now = 106.0
                expired = cache.read("tasks", loader, ttl_seconds=30, force=True)
                self.assertEqual(expired.payload["tasks"][0]["slug"], "tasks/old")
                self.assertEqual(expired.state["status"], "refreshing")
                self.assertTrue(expired.state["refreshing"])
                self.assertTrue(expired.state["stale"])
                self.assertTrue(replacement_entered.wait(timeout=1))
                self.assertEqual(calls, 2)
                release_replacement.set()
                self.assertTrue(cache.wait_for_idle("tasks"))

                repeated_force = cache.read("tasks", loader, ttl_seconds=30, force=True)
                self.assertEqual(repeated_force.state["status"], "fresh")
                self.assertFalse(repeated_force.state["refreshing"])
                self.assertEqual(repeated_force.payload["tasks"][0]["slug"], "tasks/new")
                self.assertEqual(calls, 2)
            finally:
                release_stalled.set()
                release_replacement.set()
                self.assertTrue(cache.wait_for_idle("tasks"))

    def test_non_force_read_respects_error_cooldown_after_expired_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "tasks": {
                        "payload": {"tasks": [{"slug": "tasks/old"}]},
                        "last_valid_at": 1.0,
                    }
                }
            )
            now = 100.0
            stalled_entered = threading.Event()
            release_stalled = threading.Event()
            calls = 0

            def clock() -> float:
                return now

            def loader() -> dict:
                nonlocal calls
                calls += 1
                stalled_entered.set()
                release_stalled.wait(timeout=2)
                return {"tasks": [{"slug": "tasks/stalled"}]}

            cache = ReadSurfaceCache(
                store,
                clock=clock,
                max_refresh_seconds=5,
            )
            try:
                first = cache.read("tasks", loader, ttl_seconds=30, force=True)
                self.assertEqual(first.state["status"], "refreshing")
                self.assertTrue(stalled_entered.wait(timeout=1))

                now = 106.0
                expired = cache.read("tasks", loader, ttl_seconds=30)
                self.assertEqual(expired.payload["tasks"][0]["slug"], "tasks/old")
                self.assertEqual(expired.state["status"], "stale")
                self.assertFalse(expired.state["refreshing"])
                self.assertEqual(calls, 1)
            finally:
                release_stalled.set()
                self.assertTrue(cache.wait_for_idle("tasks"))

    def test_expired_refresh_force_retries_without_waiting_for_error_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "tasks": {
                        "payload": {"tasks": [{"slug": "tasks/old"}]},
                        "last_valid_at": 1.0,
                    }
                }
            )
            now = 100.0
            stalled_entered = threading.Event()
            release_stalled = threading.Event()
            replacement_entered = threading.Event()
            release_replacement = threading.Event()
            calls = 0

            def clock() -> float:
                return now

            def loader() -> dict:
                nonlocal calls
                calls += 1
                if calls == 1:
                    stalled_entered.set()
                    release_stalled.wait(timeout=2)
                    return {"tasks": [{"slug": "tasks/stalled"}]}
                replacement_entered.set()
                release_replacement.wait(timeout=2)
                return {"tasks": [{"slug": "tasks/new"}]}

            cache = ReadSurfaceCache(
                store,
                clock=clock,
                max_refresh_seconds=5,
            )
            try:
                first = cache.read("tasks", loader, ttl_seconds=30, force=True)
                self.assertEqual(first.state["status"], "refreshing")
                self.assertTrue(stalled_entered.wait(timeout=1))

                now = 106.0
                expired = cache.read("tasks", loader, ttl_seconds=30, force=True)
                self.assertEqual(expired.state["status"], "refreshing")
                self.assertTrue(expired.state["refreshing"])
                self.assertTrue(replacement_entered.wait(timeout=1))
                release_replacement.set()
                self.assertTrue(cache.wait_for_idle("tasks"))

                refreshed = cache.read("tasks", loader, ttl_seconds=30)
                self.assertEqual(refreshed.payload["tasks"][0]["slug"], "tasks/new")
                self.assertEqual(refreshed.state["status"], "fresh")
            finally:
                release_stalled.set()
                release_replacement.set()
                self.assertTrue(cache.wait_for_idle("tasks"))

    def test_force_refresh_respects_cooldown_after_verified_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "tasks": {
                        "payload": {"tasks": [{"slug": "tasks/fresh"}]},
                        "last_valid_at": 100.0,
                    }
                }
            )
            calls = 0

            def loader() -> dict:
                nonlocal calls
                calls += 1
                return {"tasks": [{"slug": "tasks/reloaded"}]}

            cache = ReadSurfaceCache(store, clock=lambda: 120.0, background=False)
            result = cache.read(
                "tasks",
                loader,
                ttl_seconds=300,
                force=True,
                force_cooldown_seconds=120,
            )

            self.assertEqual(result.payload["tasks"][0]["slug"], "tasks/fresh")
            self.assertEqual(result.state["status"], "fresh")
            self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
