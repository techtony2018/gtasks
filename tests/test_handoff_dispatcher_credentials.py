from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gtasks.server import HandoffDispatcherAuth


ROOT = Path(__file__).resolve().parents[1]
PROVISIONER_PATH = ROOT / "scripts" / "provision_handoff_dispatcher_credentials.py"


def load_provisioner():
    spec = importlib.util.spec_from_file_location(
        "provision_handoff_dispatcher_credentials", PROVISIONER_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("credential provisioner module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HandoffDispatcherCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def _write_json(self, path: Path, payload: dict, *, mode: int = 0o600) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(mode)

    def _credential_payload(self) -> dict:
        return {
            "schema_version": 1,
            "identities": [
                {
                    "agent_slug": "agents/tammy",
                    "registration_sha256": hashlib.sha256(
                        b"private-registration-tammy"
                    ).hexdigest(),
                    "token_sha256": hashlib.sha256(b"tammy-private-token").hexdigest(),
                },
                {
                    "agent_slug": "agents/timmy",
                    "registration_sha256": hashlib.sha256(
                        b"private-registration-timmy"
                    ).hexdigest(),
                    "token_sha256": hashlib.sha256(b"timmy-private-token").hexdigest(),
                },
                {
                    "agent_slug": "agents/toddy",
                    "registration_sha256": hashlib.sha256(
                        b"private-registration-toddy"
                    ).hexdigest(),
                    "token_sha256": hashlib.sha256(b"toddy-private-token").hexdigest(),
                },
            ],
        }

    def _write_six_identity_configs(
        self, directory: Path
    ) -> tuple[list[Path], list[Path]]:
        config_paths: list[Path] = []
        token_paths: list[Path] = []
        for agent in (
            "tammy",
            "timmy",
            "toddy",
            "tammy-oc",
            "timmy-oc",
            "toddy-oc",
        ):
            token_path = directory / f"{agent}.token"
            token_path.write_text(f"{agent}-private-token\n", encoding="utf-8")
            token_path.chmod(0o600)
            config_path = directory / f"{agent}.json"
            self._write_json(
                config_path,
                {
                    "schema_version": 1,
                    "agent_slug": f"agents/{agent}",
                    "registration_id": f"private-registration-{agent}",
                    "fixed_thread_id": f"private-thread-{agent}",
                    "mission_control_url": "http://127.0.0.1:4179",
                    "token_file": str(token_path),
                },
            )
            config_paths.append(config_path)
            token_paths.append(token_path)
        return config_paths, token_paths

    def test_auth_loader_requires_exact_private_hashed_schema(self) -> None:
        path = self.root / "credentials.json"
        payload = self._credential_payload()
        self._write_json(path, payload)

        auth = HandoffDispatcherAuth.from_file(path)
        identity = auth.resolve("Bearer tammy-private-token")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.agent_slug, "agents/tammy")
        self.assertEqual(
            identity.registration_id,
            payload["identities"][0]["registration_sha256"],
        )
        self.assertIsNone(auth.resolve(None))
        self.assertIsNone(auth.resolve("Bearer wrong-token"))
        self.assertNotIn("tammy-private-token", repr(auth.__dict__))

        for mutation in (
            lambda value: value.update(extra=True),
            lambda value: value.update(schema_version=2),
            lambda value: value["identities"][0].update(registration_id="raw"),
            lambda value: value["identities"][1].update(
                token_sha256=value["identities"][0]["token_sha256"]
            ),
        ):
            with self.subTest(mutation=mutation):
                invalid = self._credential_payload()
                mutation(invalid)
                self._write_json(path, invalid)
                with self.assertRaises(ValueError):
                    HandoffDispatcherAuth.from_file(path)

        self._write_json(path, self._credential_payload(), mode=0o644)
        with self.assertRaisesRegex(ValueError, "0600"):
            HandoffDispatcherAuth.from_file(path)

    def test_auth_loader_accepts_exactly_all_three_codex_openclaw_pairs(self) -> None:
        path = self.root / "paired-credentials.json"
        payload = self._credential_payload()
        payload["identities"].extend(
            {
                "agent_slug": f"agents/{agent}-oc",
                "registration_sha256": hashlib.sha256(
                    f"private-registration-{agent}-oc".encode()
                ).hexdigest(),
                "token_sha256": hashlib.sha256(
                    f"{agent}-oc-private-token".encode()
                ).hexdigest(),
            }
            for agent in ("tammy", "timmy", "toddy")
        )
        self._write_json(path, payload)

        auth = HandoffDispatcherAuth.from_file(path)

        for agent in ("tammy", "timmy", "toddy"):
            identity = auth.resolve(f"Bearer {agent}-oc-private-token")
            self.assertIsNotNone(identity)
            self.assertEqual(identity.agent_slug, f"agents/{agent}-oc")

        payload["identities"][-1]["agent_slug"] = "agents/unknown-oc"
        self._write_json(path, payload)
        with self.assertRaisesRegex(ValueError, "wrong schema"):
            HandoffDispatcherAuth.from_file(path)

    def test_provisioner_hashes_six_explicit_private_identity_configs(self) -> None:
        config_paths: list[Path] = []
        raw_values: list[str] = []
        agents = ("tammy", "timmy", "toddy", "tammy-oc", "timmy-oc", "toddy-oc")
        for agent in agents:
            token = f"{agent}-private-dispatcher-token"
            registration = f"private-registration-{agent}"
            raw_values.extend((token, registration))
            token_path = self.root / f"{agent}.token"
            token_path.write_text(token + "\n", encoding="utf-8")
            token_path.chmod(0o600)
            config_path = self.root / f"{agent}.json"
            self._write_json(
                config_path,
                {
                    "schema_version": 1,
                    "agent_slug": f"agents/{agent}",
                    "registration_id": registration,
                    "fixed_thread_id": f"private-thread-{agent}",
                    "mission_control_url": "http://127.0.0.1:4179",
                    "token_file": str(token_path),
                },
            )
            config_paths.append(config_path)
        output = self.root / "handoff-dispatcher-credentials.json"
        command = [
            sys.executable,
            "scripts/provision_handoff_dispatcher_credentials.py",
            "--output",
            str(output),
        ]
        for path in config_paths:
            command.extend(("--identity-config", str(path)))
        result = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(
            [entry["agent_slug"] for entry in payload["identities"]],
            [f"agents/{agent}" for agent in agents],
        )
        rendered = output.read_text(encoding="utf-8") + result.stdout + result.stderr
        for raw in raw_values:
            self.assertNotIn(raw, rendered)
        HandoffDispatcherAuth.from_file(output)

        reordered_output = self.root / "handoff-dispatcher-credentials-reordered.json"
        reordered_command = [
            sys.executable,
            "scripts/provision_handoff_dispatcher_credentials.py",
            "--output",
            str(reordered_output),
        ]
        for path in reversed(config_paths):
            reordered_command.extend(("--identity-config", str(path)))
        reordered = subprocess.run(
            reordered_command,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(reordered.returncode, 0, reordered.stderr)
        self.assertEqual(reordered_output.read_bytes(), output.read_bytes())

    def test_provisioner_requires_exactly_six_reviewed_unique_private_inputs(self) -> None:
        token_path = self.root / "token"
        token_path.write_text("private-token\n", encoding="utf-8")
        token_path.chmod(0o600)
        config_path = self.root / "agent.json"
        self._write_json(
            config_path,
            {
                "schema_version": 1,
                "agent_slug": "agents/tammy",
                "registration_id": "private-registration-tammy",
                "fixed_thread_id": "private-thread-tammy",
                "mission_control_url": "http://127.0.0.1:4179",
                "token_file": str(token_path),
            },
        )
        output = self.root / "credentials.json"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/provision_handoff_dispatcher_credentials.py",
                "--output",
                str(output),
                "--identity-config",
                str(config_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertNotIn("private-token", result.stdout + result.stderr)

    def test_provisioner_rejects_symlink_and_nonregular_inputs_at_every_position(self) -> None:
        provisioner = load_provisioner()
        for kind in (
            "config_symlink",
            "config_directory",
            "token_symlink",
            "token_directory",
        ):
            for index in range(6):
                with self.subTest(kind=kind, index=index):
                    case = self.root / f"{kind}-{index}"
                    case.mkdir()
                    configs, tokens = self._write_six_identity_configs(case)
                    if kind == "config_symlink":
                        target = configs[index].with_suffix(".real.json")
                        configs[index].rename(target)
                        configs[index].symlink_to(target)
                    elif kind == "config_directory":
                        configs[index].unlink()
                        configs[index].mkdir()
                    elif kind == "token_symlink":
                        target = tokens[index].with_suffix(".real.token")
                        tokens[index].rename(target)
                        tokens[index].symlink_to(target)
                    else:
                        tokens[index].unlink()
                        tokens[index].mkdir()
                    output = case / "credentials.json"

                    with self.assertRaisesRegex(
                        ValueError, "symbolic link|regular file"
                    ):
                        provisioner.provision(configs, output)

                    self.assertFalse(output.exists())

    def test_provisioner_refuses_duplicate_hashes_or_unreviewed_identity_set(self) -> None:
        configs: list[Path] = []
        for agent in ("tammy", "timmy", "toddy", "tammy-oc", "timmy-oc", "unknown-oc"):
            token_path = self.root / f"{agent}.token"
            token_path.write_text(f"private-token-{agent}\n", encoding="utf-8")
            token_path.chmod(0o600)
            config_path = self.root / f"{agent}.json"
            self._write_json(
                config_path,
                {
                    "schema_version": 1,
                    "agent_slug": f"agents/{agent}",
                    "registration_id": f"private-registration-{agent}",
                    "fixed_thread_id": f"private-binding-{agent}",
                    "mission_control_url": "http://127.0.0.1:4179",
                    "token_file": str(token_path),
                },
            )
            configs.append(config_path)
        output = self.root / "invalid-credentials.json"

        with self.assertRaisesRegex(ValueError, "reviewed"):
            load_provisioner().provision(configs, output)
        self.assertFalse(output.exists())

        replacement = json.loads(configs[-1].read_text(encoding="utf-8"))
        replacement["agent_slug"] = "agents/toddy-oc"
        replacement["registration_id"] = "private-registration-tammy"
        self._write_json(configs[-1], replacement)
        with self.assertRaisesRegex(ValueError, "registration_sha256.*unique"):
            load_provisioner().provision(configs, output)
        self.assertFalse(output.exists())

        replacement["registration_id"] = "private-registration-toddy-oc"
        replacement["token_file"] = str(self.root / "tammy.token")
        self._write_json(configs[-1], replacement)
        with self.assertRaisesRegex(ValueError, "token_sha256.*unique"):
            load_provisioner().provision(configs, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
