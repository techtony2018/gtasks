from __future__ import annotations

import unittest
from pathlib import Path

from gtasks import domain


class CodexOnlyAgentContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_supported_agent_roster_is_exactly_three_codex_agents(self) -> None:
        expected = {
            "agents/tammy": "collections/tammys-tasks",
            "agents/timmy": "collections/timmys-tasks",
            "agents/toddy": "collections/toddys-tasks",
        }

        self.assertEqual(dict(domain.AGENT_SCOPES), expected)
        self.assertEqual(
            domain.AGENT_RUNTIME_BY_SLUG,
            {agent_slug: "codex" for agent_slug in expected},
        )
        self.assertEqual(domain.APPROVED_AGENT_RUNTIMES, frozenset({"codex"}))

    def test_supported_artifact_roster_is_exactly_three_codex_agents(self) -> None:
        self.assertEqual(
            dict(domain.ARTIFACT_AGENT_SCOPES),
            {
                "agents/tammy": "collections/tammys-artifacts",
                "agents/timmy": "collections/timmys-artifacts",
                "agents/toddy": "collections/toddys-artifacts",
            },
        )

    def test_retired_openclaw_slugs_have_no_supported_scope(self) -> None:
        retired = {
            "agents/tammy-oc",
            "agents/timmy-oc",
            "agents/toddy-oc",
        }

        self.assertTrue(retired.isdisjoint(domain.AGENT_RUNTIME_BY_SLUG))
        self.assertTrue(retired.isdisjoint(dict(domain.AGENT_SCOPES)))
        self.assertTrue(retired.isdisjoint(dict(domain.ARTIFACT_AGENT_SCOPES)))

    def test_task_creation_skill_has_only_codex_agent_aliases(self) -> None:
        implementation = (
            self.ROOT / "skills" / "mc-add-task" / "scripts" / "mc_add_task.py"
        ).read_text(encoding="utf-8")
        instructions = (
            self.ROOT / "skills" / "mc-add-task" / "SKILL.md"
        ).read_text(encoding="utf-8")

        for retired_name in ("tammy-oc", "timmy-oc", "toddy-oc", "openclaw"):
            self.assertNotIn(retired_name, implementation.lower())
            self.assertNotIn(retired_name, instructions.lower())
        for supported_name in ("tammy", "timmy", "toddy"):
            self.assertIn(f'"{supported_name}"', implementation)
            self.assertIn(f"`{supported_name.title()}`", instructions)

    def test_dashboard_startup_has_no_openclaw_activation_dependency(self) -> None:
        startup = (
            self.ROOT / "scripts" / "automation" / "start_gtasks_dashboard.zsh"
        ).read_text(encoding="utf-8")

        self.assertNotIn("openclaw", startup.lower())
        self.assertNotIn("MEMORY_STARGRAPH_OC_PROVISION_TOKEN", startup)

    def test_frontend_has_no_openclaw_agent_or_delegation_controls(self) -> None:
        frontend = (self.ROOT / "static" / "app.js").read_text(encoding="utf-8")

        for retired_token in (
            "OPENCLAW_PAIR_BY_SOURCE",
            "SOURCE_BY_OPENCLAW_PAIR",
            'runtime === "openclaw"',
            'runtime !== "openclaw"',
            "/api/agent-delegations",
        ):
            self.assertNotIn(retired_token, frontend)

    def test_server_has_no_openclaw_delegation_route(self) -> None:
        server = (self.ROOT / "gtasks" / "server.py").read_text(encoding="utf-8")

        self.assertNotIn('path == "/api/agent-delegations"', server)
        self.assertNotIn('delegation_prefix = "/api/agent-delegations/"', server)

    def test_retired_openclaw_runtime_modules_and_config_are_absent(self) -> None:
        retired_paths = (
            "gtasks/delegation.py",
            "gtasks/openclaw_adapter.py",
            "gtasks/local_handoff_supervisor.py",
            "scripts/install_local_handoff_supervisor.py",
            "scripts/provision_openclaw_agent_profiles.py",
            "config/openclaw-agents",
        )

        self.assertEqual(
            [path for path in retired_paths if (self.ROOT / path).exists()],
            [],
        )

    def test_live_gbrain_and_dispatcher_code_has_no_openclaw_route(self) -> None:
        for relative_path in (
            "gtasks/gbrain.py",
            "gtasks/handoff_dispatcher.py",
            "gtasks/server.py",
        ):
            content = (self.ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("openclaw", content.lower(), relative_path)
            self.assertNotIn("agent-delegations", content, relative_path)


if __name__ == "__main__":
    unittest.main()
