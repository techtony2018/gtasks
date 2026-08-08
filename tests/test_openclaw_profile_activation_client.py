import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.provision_openclaw_agent_profiles as provision_module
from scripts.provision_openclaw_agent_profiles import provision
from gtasks.gbrain import (
    GBrainAdapter,
    GBrainCommandError,
    GBrainProtocolError,
    MemoryStargraphOpenClawProfileClient,
)


DECLARATIONS = (
    {
        "slug": "agents/tammy-oc",
        "name": "Tammy-OC",
        "runtime": "openclaw",
        "route": "hosts/tammy",
        "task_collection": "collections/tammy-oc-tasks",
        "artifact_collection": "collections/tammy-oc-artifacts",
    },
    {
        "slug": "agents/timmy-oc",
        "name": "Timmy-OC",
        "runtime": "openclaw",
        "route": "hosts/timmy",
        "task_collection": "collections/timmy-oc-tasks",
        "artifact_collection": "collections/timmy-oc-artifacts",
    },
    {
        "slug": "agents/toddy-oc",
        "name": "Toddy-OC",
        "runtime": "openclaw",
        "route": "hosts/toddy",
        "task_collection": "collections/toddy-oc-tasks",
        "artifact_collection": "collections/toddy-oc-artifacts",
    },
)


class OpenClawProfileActivationClientTests(unittest.TestCase):
    def test_adapter_enables_the_client_only_when_both_private_settings_exist(self):
        with patch.dict(os.environ, {"MEMORY_STARGRAPH_URL": "http://127.0.0.1:8788", "MEMORY_STARGRAPH_OC_PROVISION_TOKEN": "unit-token"}):
            adapter = GBrainAdapter(openclaw_profiles=None)

        self.assertIsInstance(adapter.openclaw_profiles, MemoryStargraphOpenClawProfileClient)

    def test_client_accepts_only_an_exact_loopback_http_origin(self):
        for valid in (
            "http://127.0.0.1:8788",
            "http://localhost:8788/",
            "http://[::1]:8788",
        ):
            with self.subTest(valid=valid):
                MemoryStargraphOpenClawProfileClient(valid, "unit-token")

        for invalid in (
            "https://127.0.0.1:8788",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "http://127.0.0.1.evil.example:8788",
            "http://127.0.0.1:8788@evil.example:8788",
            "http://localhost:8788/internal",
            "http://localhost:8788?next=http://evil.example",
            "http://localhost:8788/#fragment",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    MemoryStargraphOpenClawProfileClient(invalid, "unit-token")

    def test_client_submits_then_polls_the_same_operation_to_completion(self):
        requests = []
        responses = [
            {
                "ok": True,
                "operation_id": "op-fixed",
                "status": "accepted",
                "fence_generation": None,
                "receipt": None,
                "error": None,
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
            {
                "ok": True,
                "operation_id": "op-fixed",
                "status": "running",
                "fence_generation": 4,
                "receipt": None,
                "error": None,
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
            {
                "ok": True,
                "operation_id": "op-fixed",
                "status": "completed",
                "fence_generation": 4,
                "receipt": {
                    "generation": 4,
                    "manifest_slug": "system/openclaw-profile-manifests/g000004-op-fixed",
                    "manifest_digest": "a" * 64,
                    "default_goal_link_count": 0,
                },
                "error": None,
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
        ]

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def open_request(request, timeout):
            requests.append((request.get_method(), request.full_url, timeout))
            return Response(responses.pop(0))

        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788",
            "unit-token",
            submit_timeout_seconds=10,
            status_timeout_seconds=5,
            poll_timeout_seconds=60,
            poll_interval_seconds=0.01,
            sleeper=lambda _seconds: None,
            clock=lambda: 0,
        )
        with patch("gtasks.gbrain.urlopen", side_effect=open_request):
            accepted = client.submit(
                DECLARATIONS, owner="worker-session", operation_id="op-fixed"
            )
            completed = client.wait("op-fixed", initial=accepted)

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["receipt"]["generation"], 4)
        self.assertEqual(
            [request[0] for request in requests], ["POST", "GET", "GET"]
        )
        self.assertTrue(requests[0][1].endswith("/api/internal/openclaw-profiles/provision"))
        self.assertTrue(requests[1][1].endswith("/api/internal/openclaw-profiles/operations/op-fixed"))
        self.assertEqual([request[2] for request in requests], [10, 5, 5])

    def test_default_client_timeouts_match_documented_endpoint_budgets(self):
        observed = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def open_request(request, timeout):
            observed.append((request.get_method(), request.full_url, timeout))
            if request.full_url.endswith("/provision"):
                return Response(
                    {
                        "ok": True,
                        "operation_id": "op-budget",
                        "status": "accepted",
                        "fence_generation": None,
                        "receipt": None,
                        "error": None,
                        "recovery_request_generation": 0,
                        "recovery_processed_generation": 0,
                    }
                )
            if request.full_url.endswith("/recover"):
                return Response(
                    {
                        "ok": True,
                        "operation_id": "op-budget",
                        "status": "recovery_required",
                        "fence_generation": 2,
                        "receipt": None,
                        "error": "queued",
                        "recovery_request_generation": 1,
                        "recovery_processed_generation": 0,
                    }
                )
            if request.full_url.endswith("/active"):
                return Response(
                    {
                        "ok": True,
                        "status": "ready",
                        "control_revision": 4,
                        "validated_at": 1000.0,
                        "generation": 2,
                        "active_manifest": "system/openclaw-profile-manifests/g000002-op-budget",
                        "manifest_digest": "a" * 64,
                        "profiles": [],
                    }
                )
            return Response(
                {
                    "ok": True,
                    "operation_id": "op-budget",
                    "status": "accepted",
                    "fence_generation": None,
                    "receipt": None,
                    "error": None,
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                }
            )

        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788", "unit-token"
        )
        with patch("gtasks.gbrain.urlopen", side_effect=open_request):
            client.submit(
                DECLARATIONS, owner="worker-a", operation_id="op-budget"
            )
            client.status("op-budget")
            client.recover("op-budget")
            client.active_projection()

        self.assertEqual(
            [timeout for _method, _url, timeout in observed],
            [8.0, 4.0, 8.0, 4.0],
        )

    def test_active_projection_reports_validation_pending_truthfully(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(
                    {
                        "ok": True,
                        "status": "validation_pending",
                        "control_revision": 5,
                        "generation": 2,
                        "active_manifest": "system/openclaw-profile-manifests/g000002-op",
                        "manifest_digest": "b" * 64,
                    }
                ).encode("utf-8")

        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788", "unit-token"
        )
        with (
            patch("gtasks.gbrain.urlopen", return_value=Response()),
            self.assertRaisesRegex(GBrainCommandError, "validation is pending"),
        ):
            client.active_projection()

    def test_active_projection_rejects_invalid_generation_positive_manifest_identity(self):
        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788", "unit-token"
        )
        valid = {
            "ok": True,
            "status": "ready",
            "control_revision": 5,
            "validated_at": 1000.0,
            "generation": 3,
            "active_manifest": (
                "system/openclaw-profile-manifests/"
                "g000003-op-active-shape"
            ),
            "manifest_digest": "a" * 64,
            "profiles": [],
        }
        invalid = (
            {**valid, "active_manifest": None},
            {
                **valid,
                "active_manifest": (
                    "system/openclaw-profile-manifests/"
                    "g000004-op-active-shape"
                ),
            },
            {
                **valid,
                "active_manifest": (
                    "system/openclaw-profile-manifests/"
                    "g000003-bad/operation"
                ),
            },
            {**valid, "manifest_digest": "A" * 64},
        )

        for payload in invalid:
            with self.subTest(payload=payload):
                with (
                    patch.object(client, "_request", return_value=payload),
                    self.assertRaisesRegex(GBrainProtocolError, "projection"),
                ):
                    client.active_projection()

    def test_active_projection_accepts_generation_zero_without_a_manifest(self):
        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788", "unit-token"
        )
        payload = {
            "ok": True,
            "status": "ready",
            "control_revision": 1,
            "validated_at": 1000.0,
            "generation": 0,
            "active_manifest": None,
            "manifest_digest": None,
            "profiles": [],
        }

        with patch.object(client, "_request", return_value=payload):
            projection = client.active_projection()

        self.assertEqual(projection["generation"], 0)
        self.assertIsNone(projection["active_manifest"])

    def test_client_recovers_the_same_id_and_times_out_with_the_id_visible(self):
        class Client(MemoryStargraphOpenClawProfileClient):
            def __init__(self):
                self.status_calls = []
                self.recover_calls = []
                self.now = 0.0
                super().__init__(
                    "http://127.0.0.1:8788",
                    "unit-token",
                    submit_timeout_seconds=0.5,
                    status_timeout_seconds=0.5,
                    poll_timeout_seconds=1,
                    poll_interval_seconds=0.5,
                    sleeper=self.advance,
                    clock=lambda: self.now,
                )

            def advance(self, seconds):
                self.now += seconds

            def status(self, operation_id, *, timeout_seconds=None):
                self.status_calls.append(operation_id)
                return {
                    "operation_id": operation_id,
                    "status": "running",
                    "fence_generation": 7,
                    "receipt": None,
                    "error": None,
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                }

            def recover(self, operation_id, *, timeout_seconds=None):
                self.recover_calls.append(operation_id)
                return {
                    "operation_id": operation_id,
                    "status": "completed",
                    "fence_generation": 7,
                    "receipt": {
                        "generation": 7,
                        "manifest_slug": "system/openclaw-profile-manifests/g000007-op-recover",
                        "manifest_digest": "b" * 64,
                        "default_goal_link_count": 0,
                    },
                    "error": None,
                    "recovery_request_generation": 1,
                    "recovery_processed_generation": 1,
                }

        client = Client()
        recovered = client.wait(
            "op-recover",
            initial={
                "operation_id": "op-recover",
                "status": "recovery_required",
                "fence_generation": 7,
                "receipt": None,
                "error": "journal interrupted",
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
        )

        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(client.recover_calls, ["op-recover"])

        with self.assertRaisesRegex(GBrainCommandError, "op-timeout"):
            client.wait(
                "op-timeout",
                initial={
                    "operation_id": "op-timeout",
                    "status": "running",
                    "fence_generation": 8,
                    "receipt": None,
                    "error": None,
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                },
            )

    def test_repeated_recovery_required_responses_obey_poll_deadline_and_backoff(self):
        class Client(MemoryStargraphOpenClawProfileClient):
            def __init__(self):
                self.now = 0.0
                self.recover_calls = 0
                self.status_calls = 0
                super().__init__(
                    "http://127.0.0.1:8788",
                    "unit-token",
                    submit_timeout_seconds=0.5,
                    status_timeout_seconds=0.5,
                    poll_timeout_seconds=1,
                    poll_interval_seconds=0.5,
                    sleeper=self.advance,
                    clock=lambda: self.now,
                )

            def advance(self, seconds):
                self.now += seconds

            def recover(self, operation_id, *, timeout_seconds=None):
                self.recover_calls += 1
                if self.recover_calls > 1:
                    raise AssertionError("pending recovery generation was reposted")
                return {
                    "operation_id": operation_id,
                    "status": "recovery_required",
                    "fence_generation": 7,
                    "receipt": None,
                    "error": "still interrupted",
                    "recovery_request_generation": 1,
                    "recovery_processed_generation": 0,
                }

            def status(self, operation_id, *, timeout_seconds=None):
                self.status_calls += 1
                return {
                    "operation_id": operation_id,
                    "status": "recovery_required",
                    "fence_generation": 7,
                    "receipt": None,
                    "error": "still interrupted",
                    "recovery_request_generation": 1,
                    "recovery_processed_generation": 0,
                }

        client = Client()

        with self.assertRaisesRegex(GBrainCommandError, "op-bounded-recovery"):
            client.wait(
                "op-bounded-recovery",
                initial={
                    "operation_id": "op-bounded-recovery",
                    "status": "recovery_required",
                    "fence_generation": 7,
                    "receipt": None,
                    "error": "interrupted",
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                },
            )

        self.assertEqual(client.recover_calls, 1)
        self.assertGreaterEqual(client.status_calls, 1)

    def test_recovery_generation_is_posted_once_then_get_polled_to_exact_completion(self):
        class Client(MemoryStargraphOpenClawProfileClient):
            def __init__(self):
                self.now = 0.0
                self.recover_calls = 0
                self.status_calls = 0
                super().__init__(
                    "http://127.0.0.1:8788",
                    "unit-token",
                    poll_timeout_seconds=10,
                    poll_interval_seconds=0.25,
                    sleeper=self.advance,
                    clock=lambda: self.now,
                )

            def advance(self, seconds):
                self.now += seconds

            def recover(self, operation_id, *, timeout_seconds=None):
                self.recover_calls += 1
                if self.recover_calls != 1:
                    raise AssertionError("pending recovery generation was reposted")
                return {
                    "operation_id": operation_id,
                    "status": "recovery_required",
                    "fence_generation": 7,
                    "receipt": None,
                    "error": "canonical verification is running",
                    "recovery_request_generation": 1,
                    "recovery_processed_generation": 0,
                }

            def status(self, operation_id, *, timeout_seconds=None):
                self.status_calls += 1
                if self.status_calls < 3:
                    return {
                        "operation_id": operation_id,
                        "status": "recovery_required",
                        "fence_generation": 7,
                        "receipt": None,
                        "error": "canonical verification is running",
                        "recovery_request_generation": 1,
                        "recovery_processed_generation": 0,
                    }
                return {
                    "operation_id": operation_id,
                    "status": "completed",
                    "fence_generation": 7,
                    "receipt": {
                        "generation": 7,
                        "manifest_slug": (
                            "system/openclaw-profile-manifests/"
                            "g000007-op-generation-race"
                        ),
                        "manifest_digest": "a" * 64,
                        "default_goal_link_count": 0,
                    },
                    "error": None,
                    "recovery_request_generation": 1,
                    "recovery_processed_generation": 1,
                }

        client = Client()
        completed = client.wait(
            "op-generation-race",
            initial={
                "operation_id": "op-generation-race",
                "status": "recovery_required",
                "fence_generation": 7,
                "receipt": None,
                "error": "recovery is required",
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
        )

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["recovery_processed_generation"], 1)
        self.assertEqual(client.recover_calls, 1)
        self.assertEqual(client.status_calls, 3)

    def test_poll_requests_are_capped_by_the_remaining_total_deadline(self):
        now = [0.0]
        observed_timeouts = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def open_request(request, timeout):
            observed_timeouts.append((request.get_method(), timeout))
            operation_id = request.full_url.split("/operations/", 1)[1].split(
                "/", 1
            )[0]
            if (
                operation_id == "op-deadline-recover"
                and request.get_method() == "POST"
                and len(
                [item for item in observed_timeouts if item[0] == "POST"]
                )
                == 1
            ):
                return Response(
                    {
                        "ok": True,
                        "operation_id": operation_id,
                        "status": "recovery_required",
                        "fence_generation": 3,
                        "receipt": None,
                        "error": "recover again",
                        "recovery_request_generation": 1,
                        "recovery_processed_generation": 0,
                    }
                )
            return Response(
                {
                    "ok": True,
                    "operation_id": operation_id,
                    "status": "completed",
                    "fence_generation": 3,
                    "receipt": {
                        "generation": 3,
                        "manifest_slug": "system/openclaw-profile-manifests/"
                        f"g000003-{operation_id}",
                        "manifest_digest": "f" * 64,
                        "default_goal_link_count": 0,
                    },
                    "error": None,
                    "recovery_request_generation": (
                        1 if operation_id == "op-deadline-recover" else 0
                    ),
                    "recovery_processed_generation": (
                        1 if operation_id == "op-deadline-recover" else 0
                    ),
                }
            )

        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788",
            "unit-token",
            submit_timeout_seconds=10,
            status_timeout_seconds=5,
            poll_timeout_seconds=10,
            poll_interval_seconds=9.75,
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
            clock=lambda: now[0],
        )
        with patch("gtasks.gbrain.urlopen", side_effect=open_request):
            client.wait(
                "op-deadline-status",
                initial={
                    "operation_id": "op-deadline-status",
                    "status": "running",
                    "fence_generation": 3,
                    "receipt": None,
                    "error": None,
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                },
            )
            now[0] = 0
            client.wait(
                "op-deadline-recover",
                initial={
                    "operation_id": "op-deadline-recover",
                    "status": "recovery_required",
                    "fence_generation": 3,
                    "receipt": None,
                    "error": "recover",
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                },
            )

        self.assertEqual(observed_timeouts[0], ("GET", 0.25))
        self.assertEqual(observed_timeouts[1], ("POST", 10.0))
        self.assertEqual(observed_timeouts[2], ("GET", 0.25))

    def test_client_rejects_an_incomplete_terminal_receipt(self):
        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788", "unit-token"
        )

        with self.assertRaisesRegex(GBrainProtocolError, "receipt"):
            client.wait(
                "op-incomplete",
                initial={
                    "operation_id": "op-incomplete",
                    "status": "completed",
                    "fence_generation": 3,
                    "receipt": {
                        "generation": 3,
                        "default_goal_link_count": 0,
                    },
                    "error": None,
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                },
            )

    def test_client_rejects_non_exact_completed_terminal_semantics(self):
        client = MemoryStargraphOpenClawProfileClient(
            "http://127.0.0.1:8788", "unit-token"
        )
        valid_receipt = {
            "generation": 3,
            "manifest_slug": "system/openclaw-profile-manifests/g000003-op-terminal",
            "manifest_digest": "e" * 64,
            "default_goal_link_count": 0,
        }
        invalid_operations = (
            {
                "operation_id": "op-terminal",
                "status": "completed",
                "fence_generation": 3,
                "receipt": valid_receipt,
                "error": "completed with an error",
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
            {
                "operation_id": "op-terminal",
                "status": "completed",
                "fence_generation": 3,
                "receipt": {
                    **valid_receipt,
                    "manifest_slug": "system/openclaw-profile-manifests/g000003-op-other",
                },
                "error": None,
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
            {
                "operation_id": "op-terminal",
                "status": "completed",
                "fence_generation": 3,
                "receipt": {
                    **valid_receipt,
                    "default_goal_link_count": False,
                },
                "error": None,
                "recovery_request_generation": 0,
                "recovery_processed_generation": 0,
            },
        )

        for operation in invalid_operations:
            with self.subTest(operation=operation):
                with self.assertRaises(GBrainProtocolError):
                    client.wait("op-terminal", initial=operation)

        with self.assertRaises(GBrainProtocolError):
            client.wait(
                "bad/op",
                initial={
                    "operation_id": "bad/op",
                    "status": "completed",
                    "fence_generation": 3,
                    "receipt": {
                        **valid_receipt,
                        "manifest_slug": "system/openclaw-profile-manifests/"
                        "g000003-bad/op",
                    },
                    "error": None,
                    "recovery_request_generation": 0,
                    "recovery_processed_generation": 0,
                },
            )

    def test_execute_persists_private_operation_identity_before_submission(self):
        class Client:
            def __init__(self):
                self.calls = []
                self.operation_file = None

            def submit(self, declarations, *, owner, operation_id):
                persisted = json.loads(self.operation_file.read_text(encoding="utf-8"))
                self.calls.append((tuple(declarations), owner, operation_id, persisted))
                return {
                    "operation_id": operation_id,
                    "status": "accepted",
                    "fence_generation": None,
                    "receipt": None,
                    "error": None,
                }

            def wait(self, operation_id, *, initial, on_status):
                completed = {
                    "operation_id": operation_id,
                    "status": "completed",
                    "fence_generation": 4,
                    "receipt": {
                        "generation": 4,
                        "manifest_slug": "system/openclaw-profile-manifests/g000004-op-fixed",
                        "manifest_digest": "c" * 64,
                        "default_goal_link_count": 0,
                    },
                    "error": None,
                }
                on_status(completed)
                return completed

        client = Client()
        with tempfile.TemporaryDirectory() as temp_dir:
            operation_file = Path(temp_dir) / "operation.json"
            client.operation_file = operation_file
            result = provision(
                DECLARATIONS,
                execute=True,
                client=client,
                operation_file=operation_file,
                operation_id_factory=lambda: "op-fixed",
                owner_factory=lambda: "gtasks-provisioner-session-fixed",
            )
            persisted = json.loads(operation_file.read_text(encoding="utf-8"))
            mode = stat.S_IMODE(operation_file.stat().st_mode)

        self.assertEqual(client.calls[0][0], DECLARATIONS)
        self.assertEqual(client.calls[0][1], "gtasks-provisioner-session-fixed")
        self.assertEqual(client.calls[0][2], "op-fixed")
        self.assertEqual(client.calls[0][3]["operation_id"], "op-fixed")
        self.assertEqual(persisted["status"], "completed")
        self.assertEqual(mode, 0o600)
        self.assertTrue(result["verified"])
        self.assertEqual(result["default_goal_link_count"], 0)
        self.assertEqual(result["operation_id"], "op-fixed")

    def test_concurrent_load_or_create_reuses_one_locked_operation_identity(self):
        load_or_create = getattr(
            provision_module, "_load_or_create_operation", None
        )
        self.assertIsNotNone(load_or_create)
        barrier = threading.Barrier(2)
        generated = []
        results = []
        errors = []
        guard = threading.Lock()

        def operation_id_factory():
            with guard:
                operation_id = f"op-{len(generated) + 1}"
                generated.append(operation_id)
                return operation_id

        def worker(path):
            try:
                barrier.wait(timeout=2)
                state = load_or_create(
                    path,
                    "a" * 64,
                    operation_id_factory=operation_id_factory,
                    owner_factory=lambda: "owner-locked",
                )
                with guard:
                    results.append(state)
            except Exception as error:
                with guard:
                    errors.append(error)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "operation.json"
            threads = [threading.Thread(target=worker, args=(path,)) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)

        self.assertEqual(errors, [])
        self.assertEqual(generated, ["op-1"])
        self.assertEqual(
            {state["operation_id"] for state in results}, {"op-1"}
        )

    def test_atomic_operation_state_rename_fsyncs_file_and_parent_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state" / "operation.json"
            with patch.object(os, "fsync", wraps=os.fsync) as fsync:
                provision_module._write_operation_state(
                    path,
                    {
                        "schema_version": 1,
                        "operation_id": "op-fsync",
                        "owner": "owner-fsync",
                        "declarations_digest": "b" * 64,
                        "status": "created",
                        "fence_generation": None,
                        "receipt": None,
                        "error": None,
                    },
                )

        self.assertGreaterEqual(fsync.call_count, 2)

    def test_locked_status_update_cannot_overwrite_a_terminal_operation(self):
        digest = "c" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "operation.json"
            provision_module._load_or_create_operation(
                path,
                digest,
                operation_id_factory=lambda: "op-terminal-lock",
                owner_factory=lambda: "owner-terminal-lock",
            )
            completed = {
                "operation_id": "op-terminal-lock",
                "status": "completed",
                "fence_generation": 3,
                "receipt": {
                    "generation": 3,
                    "manifest_slug": "system/openclaw-profile-manifests/"
                    "g000003-op-terminal-lock",
                    "manifest_digest": "d" * 64,
                    "default_goal_link_count": 0,
                },
                "error": None,
            }
            provision_module._persist_operation_status(
                path, digest, "op-terminal-lock", completed
            )
            persisted = provision_module._persist_operation_status(
                path,
                digest,
                "op-terminal-lock",
                {
                    "operation_id": "op-terminal-lock",
                    "status": "accepted",
                    "fence_generation": None,
                    "receipt": None,
                    "error": None,
                },
            )

        self.assertEqual(persisted["status"], "completed")
        self.assertEqual(persisted["receipt"], completed["receipt"])

    def test_load_or_create_reuses_a_completed_identity_for_idempotent_retry(self):
        digest = "e" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "operation.json"
            provision_module._load_or_create_operation(
                path,
                digest,
                operation_id_factory=lambda: "op-original-terminal",
                owner_factory=lambda: "owner-original-terminal",
            )
            provision_module._persist_operation_status(
                path,
                digest,
                "op-original-terminal",
                {
                    "operation_id": "op-original-terminal",
                    "status": "completed",
                    "fence_generation": 4,
                    "receipt": {
                        "generation": 4,
                        "manifest_slug": "system/openclaw-profile-manifests/"
                        "g000004-op-original-terminal",
                        "manifest_digest": "f" * 64,
                        "default_goal_link_count": 0,
                    },
                    "error": None,
                },
            )

            retried = provision_module._load_or_create_operation(
                path,
                digest,
                operation_id_factory=lambda: "op-must-not-replace-terminal",
                owner_factory=lambda: "owner-must-not-replace-terminal",
            )

        self.assertEqual(retried["operation_id"], "op-original-terminal")
        self.assertEqual(retried["status"], "completed")

    def test_retry_after_submit_failure_reuses_the_persisted_operation_id_and_owner(self):
        class Client:
            def __init__(self, fail):
                self.fail = fail
                self.identities = []

            def submit(self, _declarations, *, owner, operation_id):
                self.identities.append((owner, operation_id))
                if self.fail:
                    raise GBrainCommandError("simulated submit timeout")
                return {
                    "operation_id": operation_id,
                    "status": "accepted",
                    "fence_generation": None,
                    "receipt": None,
                    "error": None,
                }

            def wait(self, operation_id, *, initial, on_status):
                completed = {
                    "operation_id": operation_id,
                    "status": "completed",
                    "fence_generation": 1,
                    "receipt": {
                        "generation": 1,
                        "manifest_slug": f"system/openclaw-profile-manifests/g000001-{operation_id}",
                        "manifest_digest": "d" * 64,
                        "default_goal_link_count": 0,
                    },
                    "error": None,
                }
                on_status(completed)
                return completed

        with tempfile.TemporaryDirectory() as temp_dir:
            operation_file = Path(temp_dir) / "operation.json"
            first = Client(fail=True)
            with self.assertRaisesRegex(GBrainCommandError, "op-original"):
                provision(
                    DECLARATIONS,
                    execute=True,
                    client=first,
                    operation_file=operation_file,
                    operation_id_factory=lambda: "op-original",
                    owner_factory=lambda: "owner-original",
                )
            second = Client(fail=False)
            result = provision(
                DECLARATIONS,
                execute=True,
                client=second,
                operation_file=operation_file,
                operation_id_factory=lambda: "op-should-not-be-used",
                owner_factory=lambda: "owner-should-not-be-used",
            )

        self.assertEqual(first.identities, [("owner-original", "op-original")])
        self.assertEqual(second.identities, [("owner-original", "op-original")])
        self.assertEqual(result["operation_id"], "op-original")

    def test_dry_run_never_constructs_or_calls_the_client(self):
        result = provision(DECLARATIONS, execute=False)

        self.assertFalse(result["mutated"])
        self.assertFalse(result["verified"])
        self.assertIsNone(result["activation"])
