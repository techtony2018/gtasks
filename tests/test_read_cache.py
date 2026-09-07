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
    def test_overlapping_refresh_persistence_keeps_newest_snapshot_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            old_saving, new_saved = threading.Event(), threading.Event()

            class SlowStore(ReadSnapshotStore):
                def save(self, records) -> None:
                    revision = records["tasks"]["payload"]["revision"]
                    if revision == "old":
                        old_saving.set()
                        # Without serialization, the newer snapshot finishes
                        # first and the delayed save overwrites it afterward.
                        new_saved.wait(timeout=0.2)
                    super().save(records)
                    if revision == "new":
                        new_saved.set()

            store = SlowStore(Path(temporary) / "reads.json")
            cache = ReadSurfaceCache(store, background=False)
            old = threading.Thread(target=lambda: cache.read(
                "tasks", lambda: {"revision": "old"}, ttl_seconds=30,
            ))
            old.start()
            try:
                self.assertTrue(old_saving.wait(timeout=1))
                cache.invalidate("tasks")
                current = cache.read(
                    "tasks", lambda: {"revision": "new"}, ttl_seconds=30,
                )
                old.join(timeout=2)
                self.assertFalse(old.is_alive())
                self.assertEqual(current.payload, {"revision": "new"})
                self.assertEqual(store.load()["tasks"]["payload"], {"revision": "new"})
            finally:
                new_saved.set()
                old.join(timeout=2)

    def test_invalidated_refresh_cannot_replace_new_value_or_persist_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ReadSnapshotStore(Path(temporary) / "reads.json")
            cache = ReadSurfaceCache(store, background=False)
            cache.read("tasks", lambda: {"revision": "seed"}, ttl_seconds=30)
            entered, release = threading.Event(), threading.Event()

            def old_loader() -> dict:
                entered.set()
                release.wait(timeout=3)
                return {"revision": "before-edit"}

            old = threading.Thread(target=lambda: cache.read(
                "tasks", old_loader, ttl_seconds=30, force=True,
            ))
            old.start()
            try:
                self.assertTrue(entered.wait(timeout=1))
                cache.invalidate("tasks")
                current = cache.read(
                    "tasks", lambda: {"revision": "after-edit"}, ttl_seconds=30,
                    force=True, force_cooldown_seconds=300,
                )
                self.assertEqual(current.payload, {"revision": "after-edit"})
                release.set()
                old.join(timeout=2)
                self.assertFalse(old.is_alive())
                final = cache.read("tasks", old_loader, ttl_seconds=30)
                self.assertEqual(final.payload, {"revision": "after-edit"})
                self.assertEqual(final.state["status"], "fresh")
                self.assertEqual(store.load()["tasks"]["payload"], final.payload)
            finally:
                release.set()
                old.join(timeout=2)

    def test_old_refresh_finishing_after_invalidation_requires_new_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ReadSnapshotStore(Path(temporary) / "reads.json")
            cache = ReadSurfaceCache(store, background=False)
            cache.read("tasks", lambda: {"revision": "seed"}, ttl_seconds=30)

            def invalidated_loader() -> dict:
                cache.invalidate("tasks")
                return {"revision": "obsolete"}

            invalidated = cache.read("tasks", invalidated_loader, ttl_seconds=30, force=True)
            self.assertTrue(invalidated.state["stale"])
            self.assertEqual(invalidated.payload, {"revision": "seed"})
            current = cache.read(
                "tasks", lambda: {"revision": "current"}, ttl_seconds=30,
                force=True, force_cooldown_seconds=300,
            )
            self.assertEqual(current.payload, {"revision": "current"})
            self.assertEqual(current.state["status"], "fresh")

    def test_invalidated_failure_cannot_clear_or_poison_replacement_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ReadSnapshotStore(Path(temporary) / "reads.json")
            cache = ReadSurfaceCache(store, background=False)
            cache.read("tasks", lambda: {"revision": "seed"}, ttl_seconds=30)
            old_entered, old_release = threading.Event(), threading.Event()
            new_entered, new_release = threading.Event(), threading.Event()

            def failed_old_loader() -> dict:
                old_entered.set()
                old_release.wait(timeout=3)
                raise RuntimeError("obsolete failure")

            def replacement_loader() -> dict:
                new_entered.set()
                new_release.wait(timeout=3)
                return {"revision": "current"}

            old = threading.Thread(target=lambda: cache.read(
                "tasks", failed_old_loader, ttl_seconds=30, force=True,
            ))
            new = threading.Thread(target=lambda: cache.read(
                "tasks", replacement_loader, ttl_seconds=30, force=True,
            ))
            old.start()
            try:
                self.assertTrue(old_entered.wait(timeout=1))
                cache.invalidate("tasks")
                new.start()
                self.assertTrue(new_entered.wait(timeout=1))
                old_release.set()
                old.join(timeout=2)
                held = cache.read("tasks", replacement_loader, ttl_seconds=30)
                self.assertTrue(held.state["refreshing"])
                self.assertIsNone(held.state["error"])
                new_release.set()
                new.join(timeout=2)
                self.assertFalse(new.is_alive())
                self.assertEqual(cache.read("tasks", replacement_loader, ttl_seconds=30).payload,
                                 {"revision": "current"})
            finally:
                old_release.set()
                new_release.set()
                old.join(timeout=2)
                if new.ident is not None:
                    new.join(timeout=2)

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

    def test_force_cooldown_does_not_hide_expired_in_flight_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "proposals": {
                        "payload": {"proposals": [{"slug": "tasks/recent"}]},
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
                    return {"proposals": [{"slug": "tasks/stalled"}]}
                replacement_entered.set()
                release_replacement.wait(timeout=2)
                return {"proposals": [{"slug": "tasks/recovered"}]}

            cache = ReadSurfaceCache(
                store,
                clock=clock,
                max_refresh_seconds=5,
            )
            try:
                first = cache.read(
                    "proposals",
                    loader,
                    ttl_seconds=300,
                    force=True,
                    force_cooldown_seconds=0,
                )
                self.assertEqual(first.state["status"], "refreshing")
                self.assertTrue(stalled_entered.wait(timeout=1))

                now = 106.0
                retrying = cache.read(
                    "proposals",
                    loader,
                    ttl_seconds=300,
                    force=True,
                    force_cooldown_seconds=120,
                )
                self.assertEqual(retrying.state["status"], "refreshing")
                self.assertTrue(retrying.state["refreshing"])
                self.assertEqual(retrying.payload["proposals"][0]["slug"], "tasks/recent")
                self.assertTrue(replacement_entered.wait(timeout=1))
                release_replacement.set()
                self.assertTrue(cache.wait_for_idle("proposals"))

                refreshed = cache.read("proposals", loader, ttl_seconds=300)
                self.assertEqual(refreshed.state["status"], "fresh")
                self.assertEqual(refreshed.payload["proposals"][0]["slug"], "tasks/recovered")
                self.assertEqual(calls, 2)
            finally:
                release_stalled.set()
                release_replacement.set()
                self.assertTrue(cache.wait_for_idle("proposals"))

    def test_force_refresh_can_run_foreground_and_return_verified_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "read-snapshots.json"
            store = ReadSnapshotStore(path)
            store.save(
                {
                    "agent_work": {
                        "payload": {"tasks": [{"slug": "tasks/old"}]},
                        "last_valid_at": 1.0,
                    }
                }
            )
            calls = 0

            def loader() -> dict:
                nonlocal calls
                calls += 1
                return {"tasks": [{"slug": "tasks/fresh"}]}

            cache = ReadSurfaceCache(
                store,
                clock=lambda: 100.0,
            )

            result = cache.read(
                "agent_work",
                loader,
                ttl_seconds=300,
                force=True,
                foreground_refresh=True,
            )

            self.assertEqual(result.state["status"], "fresh")
            self.assertFalse(result.state["refreshing"])
            self.assertFalse(result.state["stale"])
            self.assertEqual(result.payload["tasks"][0]["slug"], "tasks/fresh")
            self.assertEqual(calls, 1)

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
