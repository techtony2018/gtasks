import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            },
            {
                "ok": True,
                "operation_id": "op-fixed",
                "status": "running",
                "fence_generation": 4,
                "receipt": None,
                "error": None,
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

            def status(self, operation_id):
                self.status_calls.append(operation_id)
                return {
                    "operation_id": operation_id,
                    "status": "running",
                    "fence_generation": 7,
                    "receipt": None,
                    "error": None,
                }

            def recover(self, operation_id):
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
                },
            )

    def test_repeated_recovery_required_responses_obey_poll_deadline_and_backoff(self):
        class Client(MemoryStargraphOpenClawProfileClient):
            def __init__(self):
                self.now = 0.0
                self.recover_calls = 0
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

            def recover(self, operation_id):
                self.recover_calls += 1
                if self.recover_calls > 2:
                    raise AssertionError("recovery retry was not bounded")
                return {
                    "operation_id": operation_id,
                    "status": "recovery_required",
                    "fence_generation": 7,
                    "receipt": None,
                    "error": "still interrupted",
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
                },
            )

        self.assertEqual(client.recover_calls, 2)

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
