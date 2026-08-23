import json
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_RUNTIME_ROOT = Path("/Users/tony/work/gtasks")
HANDOFF_STATE_ROOT = Path(
    "/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks"
)
BUZZ_ENV_FILE = HANDOFF_STATE_ROOT / "buzz.env"
BUZZ_OUTBOX_ROOT = HANDOFF_STATE_ROOT / "buzz-outbox"
REMOTE_GBRAIN_ROOT = Path(
    "/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-remote"
)


class DashboardIntegrationTests(unittest.TestCase):
    def test_contract_manages_the_existing_gbrain_backed_app(self) -> None:
        contract = json.loads(
            (PROJECT_ROOT / "dashboard-integration.json").read_text(encoding="utf-8")
        )

        self.assertEqual(contract["service_id"], "gtasks")
        self.assertEqual(contract["service_url"], "http://127.0.0.1:4179/")
        self.assertEqual(contract["health_path"], "/api/health")
        self.assertEqual(contract["logs_path"], "/api/logs")
        self.assertEqual(contract["logs_mode"], "privacy_safe_read_only")
        self.assertEqual(
            contract["queue_reader_observability_url"],
            "http://127.0.0.1:4181/api/observability",
        )
        self.assertTrue(
            contract["queue_reader_observability_artifact"].endswith(
                "gtasks-events/reader-observability.json"
            )
        )
        self.assertEqual(contract["working_directory"], str(CANONICAL_RUNTIME_ROOT))
        self.assertEqual(
            contract["command"],
            [
                str(PROJECT_ROOT / "scripts/automation/start_gtasks_dashboard.zsh"),
                "-m",
                "gtasks.server",
                "--host",
                "127.0.0.1",
                "--port",
                "4179",
                "--artifact-publisher-credentials-file",
                "/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/artifact-publisher-credentials.json",
                "--handoff-store",
                str(HANDOFF_STATE_ROOT / "handoff-dispatcher.sqlite3"),
                "--handoff-dispatcher-credentials-file",
                str(HANDOFF_STATE_ROOT / "handoff-dispatcher-credentials.json"),
            ],
        )
        self.assertEqual(contract["canonical_store"], "gbrain")
        self.assertEqual(
            contract["remote_mcp"],
            {
                "transport": "oauth_client_credentials",
                "gbrain_home": str(REMOTE_GBRAIN_ROOT),
                "config": str(REMOTE_GBRAIN_ROOT / ".gbrain/config.json"),
                "credentials": str(REMOTE_GBRAIN_ROOT / "credentials.env"),
                "credentials_mode": "0600",
                "credential_contents": "private_runtime_only",
            },
        )
        self.assertFalse(contract["vendored_copy"])
        self.assertEqual(contract["managed_actions"], ["start", "stop", "restart"])

    def test_dashboard_launcher_requires_remote_mcp_runtime_without_secrets(self) -> None:
        launcher = (
            PROJECT_ROOT / "scripts/automation/start_gtasks_dashboard.zsh"
        ).read_text(encoding="utf-8")

        self.assertIn(f"export GBRAIN_HOME={REMOTE_GBRAIN_ROOT}", launcher)
        self.assertIn(
            f"GBRAIN_CREDENTIALS_FILE={REMOTE_GBRAIN_ROOT / 'credentials.env'}",
            launcher,
        )
        self.assertIn("GBRAIN_REMOTE_CLIENT_SECRET", launcher)
        self.assertNotIn('local path="$1"', launcher)
        self.assertIn("/usr/bin/stat -f '%Lp'", launcher)
        self.assertIn("exec /opt/homebrew/opt/python@3.12/libexec/bin/python3", launcher)
        self.assertNotRegex(launcher, r"gbrain_cl_[0-9a-f]+")
        self.assertNotIn("oauth_client_secret", launcher)

    def test_dashboard_launcher_requires_private_buzz_runtime_and_outbox(self) -> None:
        launcher = (
            PROJECT_ROOT / "scripts/automation/start_gtasks_dashboard.zsh"
        ).read_text(encoding="utf-8")

        self.assertIn(f"BUZZ_ENV_FILE={BUZZ_ENV_FILE}", launcher)
        self.assertIn('require_owner_file "$BUZZ_ENV_FILE"', launcher)
        self.assertIn('source "$BUZZ_ENV_FILE"', launcher)
        self.assertIn('"${BUZZ_PRIVATE_KEY:-}"', launcher)
        self.assertIn('"${BUZZ_RELAY_URL:-}"', launcher)
        self.assertIn('"${MISSION_CONTROL_BUZZ_OUTBOX_DIR:-}"', launcher)
        self.assertIn('/Users/tony/.local/bin', launcher)
        self.assertNotIn("BUZZ_AUTH_TAG:-", launcher)

    def test_dashboard_launcher_defaults_goal_execution_to_shadow_and_fails_closed(self) -> None:
        launcher = (
            PROJECT_ROOT / "scripts/automation/start_gtasks_dashboard.zsh"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'MISSION_CONTROL_GOAL_EXECUTION_MODE="${MISSION_CONTROL_GOAL_EXECUTION_MODE:-shadow}"',
            launcher,
        )
        self.assertIn("off|shadow|canary", launcher)
        self.assertIn("MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL", launcher)
        self.assertIn("canary mode requires one canonical Goal slug", launcher)
        completed = subprocess.run(
            ["zsh", "-n", str(PROJECT_ROOT / "scripts/automation/start_gtasks_dashboard.zsh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_contract_declares_private_buzz_runtime_without_secrets(self) -> None:
        contract = json.loads(
            (PROJECT_ROOT / "dashboard-integration.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            contract["buzz_coordination"],
            {
                "env": str(BUZZ_ENV_FILE),
                "env_mode": "0600",
                "credential_contents": "private_runtime_only",
                "outbox": str(BUZZ_OUTBOX_ROOT),
                "outbox_mode": "0700",
                "required_env": [
                    "BUZZ_RELAY_URL",
                    "BUZZ_PRIVATE_KEY",
                    "MISSION_CONTROL_BUZZ_OUTBOX_DIR",
                ],
                "optional_env": ["BUZZ_AUTH_TAG"],
            },
        )
        rendered = json.dumps(contract)
        self.assertNotIn("BUZZ_PRIVATE_KEY=", rendered)
        self.assertNotIn("BUZZ_AUTH_TAG=", rendered)

    def test_contract_declares_private_handoff_runtime_paths_without_secrets(self) -> None:
        contract = json.loads(
            (PROJECT_ROOT / "dashboard-integration.json").read_text(encoding="utf-8")
        )

        handoff = contract["handoff_dispatcher"]
        self.assertEqual(
            handoff,
            {
                "store": str(HANDOFF_STATE_ROOT / "handoff-dispatcher.sqlite3"),
                "credentials": str(
                    HANDOFF_STATE_ROOT / "handoff-dispatcher-credentials.json"
                ),
                "credentials_mode": "0600",
                "credential_contents": "private_runtime_only",
            },
        )
        rendered = json.dumps(contract)
        self.assertNotIn("fixed_thread_id", rendered)
        self.assertNotIn("bearer_token", rendered)

    def test_contract_registers_independent_broker_and_consumer_services(self) -> None:
        contract = json.loads(
            (PROJECT_ROOT / "dashboard-integration.json").read_text(encoding="utf-8")
        )

        queue = contract["event_queue"]
        self.assertEqual(queue["broker_service_id"], "gtasks-events")
        self.assertEqual(queue["broker_health_url"], "http://127.0.0.1:8222/healthz?js-enabled-only=true")
        self.assertEqual(
            queue["consumer_service_id"],
            "gtasks-event-consumer",
        )
        self.assertEqual(
            queue["consumer_health_url"],
            "http://127.0.0.1:4181/api/health",
        )
        self.assertEqual(
            queue["runtime_root"],
            "/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-events",
        )
        self.assertEqual(
            queue["consumer_observability_url"],
            "http://127.0.0.1:4181/api/observability",
        )
        self.assertEqual(
            queue["consumer_observability_artifact"],
            (
                "/Users/tony/.codex/services/all-things-codex-dashboard/state/"
                "gtasks-events/reader-observability.json"
            ),
        )


if __name__ == "__main__":
    unittest.main()
