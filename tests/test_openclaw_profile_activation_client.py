import os
import unittest
from unittest.mock import patch

from scripts.provision_openclaw_agent_profiles import provision
from gtasks.gbrain import GBrainAdapter, MemoryStargraphOpenClawProfileClient


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

    def test_execute_delegates_the_exact_batch_to_memory_stargraph(self):
        class Client:
            def __init__(self):
                self.calls = []

            def provision(self, declarations, *, owner, operation_id):
                self.calls.append((tuple(declarations), owner, operation_id))
                return {
                    "generation": 4,
                    "manifest_slug": "system/openclaw-profile-manifests/g000004-op",
                    "default_goal_link_count": 0,
                }

        client = Client()
        result = provision(DECLARATIONS, execute=True, client=client)

        self.assertEqual(client.calls[0][0], DECLARATIONS)
        self.assertTrue(client.calls[0][1].startswith("gtasks-provisioner-"))
        self.assertTrue(client.calls[0][2])
        self.assertTrue(result["verified"])
        self.assertEqual(result["default_goal_link_count"], 0)

    def test_dry_run_never_constructs_or_calls_the_client(self):
        result = provision(DECLARATIONS, execute=False)

        self.assertFalse(result["mutated"])
        self.assertFalse(result["verified"])
        self.assertIsNone(result["activation"])
