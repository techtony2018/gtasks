from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gtasks.local_handoff_supervisor as supervisor_module
from gtasks.local_handoff_supervisor import (
    SupervisorConfig,
    WorkerFailure,
    claim_store_path_for,
    load_isolated_workers,
    run_supervisor,
    worker_route,
    worker_runtime,
)


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "scripts" / "install_local_handoff_supervisor.py"
TEMPLATE_PATH = (
    ROOT / "config" / "openclaw-agents" / "dispatcher-supervisor.plist.template"
)


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "install_local_handoff_supervisor", INSTALLER_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("supervisor installer module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SupervisorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.codex_token = self._private_text("codex.token", "codex-secret-token\n")
        self.openclaw_token = self._private_text(
            "openclaw.token", "openclaw-secret-token\n"
        )
        self.codex_config = self._worker_config(
            "codex.json",
            agent_slug="agents/tammy",
            registration_id="private-registration-tammy",
            fixed_thread_id="fixed-codex-thread",
            token_file=self.codex_token,
        )
        self.openclaw_config = self._worker_config(
            "openclaw.json",
            agent_slug="agents/tammy-oc",
            registration_id="private-registration-tammy-oc",
            fixed_thread_id="agent:tammy-oc:fixed",
            token_file=self.openclaw_token,
        )
        self.supervisor_path = self.root / "supervisor.json"
        self.write_supervisor(
            [self.codex_config.name, self.openclaw_config.name]
        )

    def _private_text(self, name: str, value: str) -> Path:
        path = self.root / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        return path

    def _worker_config(
        self,
        name: str,
        *,
        agent_slug: str,
        registration_id: str,
        fixed_thread_id: str,
        token_file: Path,
        mission_control_url: str = "https://mission-control.test",
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "agent_slug": agent_slug,
                    "registration_id": registration_id,
                    "fixed_thread_id": fixed_thread_id,
                    "mission_control_url": mission_control_url,
                    "token_file": str(token_file),
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def write_supervisor(
        self, paths: list[str | Path], *, mode: int = 0o600, extra: bool = False
    ) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "worker_config_paths": [str(path) for path in paths],
        }
        if extra:
            payload["token"] = "must-not-be-accepted"
        self.supervisor_path.write_text(json.dumps(payload), encoding="utf-8")
        self.supervisor_path.chmod(mode)


class SupervisorConfigTests(SupervisorFixture):
    def test_loads_exactly_one_codex_and_paired_openclaw_worker_in_isolation(self) -> None:
        supervisor = SupervisorConfig.from_file(self.supervisor_path)

        workers = load_isolated_workers(supervisor)

        self.assertEqual(
            supervisor.worker_config_paths,
            (self.codex_config.resolve(), self.openclaw_config.resolve()),
        )
        self.assertEqual(tuple(config.agent_slug for config in workers), (
            "agents/tammy",
            "agents/tammy-oc",
        ))
        self.assertEqual(tuple(worker_runtime(config) for config in workers), (
            "codex",
            "openclaw",
        ))
        self.assertEqual(tuple(worker_route(config) for config in workers), (
            "hosts/tammy",
            "hosts/tammy",
        ))
        self.assertNotEqual(workers[0].registration_id, workers[1].registration_id)
        self.assertNotEqual(workers[0].token_file, workers[1].token_file)
        self.assertNotEqual(workers[0].read_token(), workers[1].read_token())
        self.assertEqual(
            tuple(claim_store_path_for(path).name for path in supervisor.worker_config_paths),
            ("codex.active-claim.json", "openclaw.active-claim.json"),
        )
        self.assertNotEqual(
            claim_store_path_for(supervisor.worker_config_paths[0]),
            claim_store_path_for(supervisor.worker_config_paths[1]),
        )

    def test_rejects_non_private_or_non_exact_supervisor_schema(self) -> None:
        for paths, mode, extra, message in (
            ([self.codex_config], 0o600, False, "exactly two"),
            (
                [self.codex_config, self.openclaw_config, self.codex_config],
                0o600,
                False,
                "exactly two",
            ),
            ([self.codex_config, self.openclaw_config], 0o644, False, "0600"),
            ([self.codex_config, self.openclaw_config], 0o600, True, "exactly"),
        ):
            with self.subTest(paths=paths, mode=mode, extra=extra):
                self.write_supervisor(paths, mode=mode, extra=extra)
                with self.assertRaisesRegex(ValueError, message):
                    SupervisorConfig.from_file(self.supervisor_path)

    def test_refuses_duplicate_paths_identities_registrations_and_tokens(self) -> None:
        self.write_supervisor([self.codex_config, self.codex_config])
        with self.assertRaisesRegex(ValueError, "distinct"):
            load_isolated_workers(SupervisorConfig.from_file(self.supervisor_path))

        duplicate_identity = self._worker_config(
            "duplicate.json",
            agent_slug="agents/tammy",
            registration_id="private-registration-other",
            fixed_thread_id="other-thread",
            token_file=self.openclaw_token,
        )
        self.write_supervisor([self.codex_config, duplicate_identity])
        with self.assertRaisesRegex(ValueError, "distinct Agent|Codex.*OpenClaw"):
            load_isolated_workers(SupervisorConfig.from_file(self.supervisor_path))

        duplicate_registration = self._worker_config(
            "duplicate-registration.json",
            agent_slug="agents/tammy-oc",
            registration_id="private-registration-tammy",
            fixed_thread_id="agent:tammy-oc:fixed",
            token_file=self.openclaw_token,
        )
        self.write_supervisor([self.codex_config, duplicate_registration])
        with self.assertRaisesRegex(ValueError, "registrations"):
            load_isolated_workers(SupervisorConfig.from_file(self.supervisor_path))

        duplicate_token = self._worker_config(
            "duplicate-token.json",
            agent_slug="agents/tammy-oc",
            registration_id="private-registration-tammy-oc",
            fixed_thread_id="agent:tammy-oc:fixed",
            token_file=self.codex_token,
        )
        self.write_supervisor([self.codex_config, duplicate_token])
        with self.assertRaisesRegex(ValueError, "tokens|credential"):
            load_isolated_workers(SupervisorConfig.from_file(self.supervisor_path))

    def test_refuses_a_symbolic_link_worker_config_even_when_constructed_directly(self) -> None:
        linked_config = self.root / "linked-codex.json"
        linked_config.symlink_to(self.codex_config)
        supervisor = SupervisorConfig(
            schema_version=1,
            worker_config_paths=(linked_config, self.openclaw_config),
        )

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            load_isolated_workers(supervisor)

    def test_refuses_cross_host_or_unapproved_identity_pairs(self) -> None:
        for agent_slug in ("agents/timmy-oc", "agents/unreviewed-oc"):
            with self.subTest(agent_slug=agent_slug):
                other = self._worker_config(
                    "other.json",
                    agent_slug=agent_slug,
                    registration_id="private-registration-other",
                    fixed_thread_id="agent:other-oc:fixed",
                    token_file=self.openclaw_token,
                )
                self.write_supervisor([self.codex_config, other])
                with self.assertRaisesRegex(ValueError, "approved.*host|reviewed"):
                    load_isolated_workers(SupervisorConfig.from_file(self.supervisor_path))

    def test_refuses_workers_pointing_at_different_mission_control_origins(self) -> None:
        other = self._worker_config(
            "different-origin.json",
            agent_slug="agents/tammy-oc",
            registration_id="private-registration-tammy-oc",
            fixed_thread_id="agent:tammy-oc:fixed",
            token_file=self.openclaw_token,
            mission_control_url="https://other-mission-control.test",
        )
        self.write_supervisor([self.codex_config, other])

        with self.assertRaisesRegex(ValueError, "Mission Control"):
            load_isolated_workers(SupervisorConfig.from_file(self.supervisor_path))


class SupervisorLifecycleTests(SupervisorFixture):
    def test_passes_each_worker_its_canonical_config_path(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        config = SupervisorConfig(
            schema_version=1,
            worker_config_paths=(
                nested / ".." / self.codex_config.name,
                nested / ".." / self.openclaw_config.name,
            ),
        )
        seen: list[Path] = []
        terminate = threading.Event()

        def worker_runner(path, _worker, stop_requested):
            seen.append(path)
            if len(seen) == 2:
                terminate.set()
            while not stop_requested():
                time.sleep(0.001)

        events = run_supervisor(
            config,
            worker_runner=worker_runner,
            stop_requested=terminate.is_set,
            poll_interval=0.001,
        )

        self.assertEqual(
            set(seen),
            {self.codex_config.resolve(), self.openclaw_config.resolve()},
        )
        self.assertEqual(events, ())

    def test_starts_both_workers_and_stops_both_on_one_termination_signal(self) -> None:
        config = SupervisorConfig.from_file(self.supervisor_path)
        started = {"codex": threading.Event(), "openclaw": threading.Event()}
        stopped = {"codex": threading.Event(), "openclaw": threading.Event()}
        terminate = threading.Event()
        seen: list[tuple[Path, str, str]] = []

        def worker_runner(path, worker, stop_requested):
            runtime = worker_runtime(worker)
            seen.append((path, worker.agent_slug, worker.read_token()))
            started[runtime].set()
            while not stop_requested():
                time.sleep(0.001)
            stopped[runtime].set()

        result: list[WorkerFailure] = []

        def supervise() -> None:
            result.extend(
                run_supervisor(
                    config,
                    worker_runner=worker_runner,
                    stop_requested=terminate.is_set,
                    poll_interval=0.001,
                )
            )

        supervisor_thread = threading.Thread(target=supervise)
        supervisor_thread.start()
        self.assertTrue(started["codex"].wait(1))
        self.assertTrue(started["openclaw"].wait(1))

        terminate.set()
        supervisor_thread.join(1)

        self.assertFalse(supervisor_thread.is_alive())
        self.assertTrue(stopped["codex"].is_set())
        self.assertTrue(stopped["openclaw"].is_set())
        self.assertEqual({entry[1] for entry in seen}, {"agents/tammy", "agents/tammy-oc"})
        self.assertEqual({entry[2] for entry in seen}, {
            "codex-secret-token",
            "openclaw-secret-token",
        })
        self.assertEqual(len({entry[0] for entry in seen}), 2)
        self.assertEqual(result, [])

    def test_restarts_after_unexpected_normal_return_with_bounded_backoff(self) -> None:
        config = SupervisorConfig.from_file(self.supervisor_path)
        restarted = threading.Event()
        sibling_started = threading.Event()
        sibling_stopped = threading.Event()
        terminate = threading.Event()
        attempts = 0
        reports: list[WorkerFailure] = []
        delays: list[float] = []

        def worker_runner(_path, worker, stop_requested):
            nonlocal attempts
            if worker_runtime(worker) == "codex":
                attempts += 1
                if attempts == 1:
                    return
                restarted.set()
                while not stop_requested():
                    time.sleep(0.001)
                return
            sibling_started.set()
            while not stop_requested():
                time.sleep(0.001)
            sibling_stopped.set()

        result: list[WorkerFailure] = []

        def supervise() -> None:
            result.extend(
                run_supervisor(
                    config,
                    worker_runner=worker_runner,
                    stop_requested=terminate.is_set,
                    report_failure=reports.append,
                    poll_interval=0.001,
                    max_consecutive_failures=3,
                    restart_backoff_initial=2,
                    restart_backoff_max=5,
                    restart_wait=lambda delay: delays.append(delay) or False,
                )
            )

        supervisor_thread = threading.Thread(target=supervise)
        supervisor_thread.start()
        self.assertTrue(restarted.wait(1))
        self.assertTrue(sibling_started.wait(1))
        self.assertFalse(sibling_stopped.is_set())

        terminate.set()
        supervisor_thread.join(1)

        self.assertEqual(result, reports)
        self.assertEqual(len(reports), 1)
        rendered = json.dumps(asdict(reports[0]), sort_keys=True)
        self.assertIn("agents/tammy", rendered)
        self.assertEqual(reports[0].outcome, "unexpected_return")
        self.assertEqual(reports[0].error_type, "UnexpectedReturn")
        self.assertEqual(reports[0].consecutive_failures, 1)
        self.assertFalse(reports[0].fatal)
        self.assertEqual(delays, [2])
        self.assertEqual(attempts, 2)
        self.assertTrue(sibling_stopped.is_set())

    def test_recovers_a_transient_exception_without_leaking_or_stopping_sibling(self) -> None:
        config = SupervisorConfig.from_file(self.supervisor_path)
        restarted = threading.Event()
        sibling_started = threading.Event()
        sibling_stopped = threading.Event()
        terminate = threading.Event()
        attempts = 0
        reports: list[WorkerFailure] = []

        def worker_runner(_path, worker, stop_requested):
            nonlocal attempts
            if worker_runtime(worker) == "codex":
                attempts += 1
                if attempts == 1:
                    raise RuntimeError(
                        "codex-secret-token private-registration-tammy fixed-codex-thread"
                    )
                restarted.set()
                while not stop_requested():
                    time.sleep(0.001)
                return
            sibling_started.set()
            while not stop_requested():
                time.sleep(0.001)
            sibling_stopped.set()

        result: list[WorkerFailure] = []

        def supervise() -> None:
            result.extend(
                run_supervisor(
                    config,
                    worker_runner=worker_runner,
                    stop_requested=terminate.is_set,
                    report_failure=reports.append,
                    poll_interval=0.001,
                    max_consecutive_failures=3,
                    restart_backoff_initial=0,
                    restart_backoff_max=0,
                )
            )

        supervisor_thread = threading.Thread(target=supervise)
        supervisor_thread.start()
        self.assertTrue(restarted.wait(1))
        self.assertTrue(sibling_started.wait(1))
        self.assertFalse(sibling_stopped.is_set())

        terminate.set()
        supervisor_thread.join(1)

        self.assertFalse(supervisor_thread.is_alive())
        self.assertEqual(result, reports)
        self.assertEqual(len(reports), 1)
        rendered = json.dumps(asdict(reports[0]), sort_keys=True)
        self.assertEqual(reports[0].outcome, "exception")
        self.assertIn("RuntimeError", rendered)
        self.assertFalse(reports[0].fatal)
        for secret in (
            "codex-secret-token",
            "private-registration-tammy",
            "fixed-codex-thread",
        ):
            self.assertNotIn(secret, rendered)
            self.assertNotIn(secret, repr(reports[0]))
        self.assertTrue(sibling_stopped.is_set())

    def test_persistent_failure_marks_fatal_and_stops_the_whole_pair(self) -> None:
        config = SupervisorConfig.from_file(self.supervisor_path)
        sibling_started = threading.Event()
        sibling_stopped = threading.Event()
        attempts = 0
        reports: list[WorkerFailure] = []
        delays: list[float] = []

        def worker_runner(_path, worker, stop_requested):
            nonlocal attempts
            if worker_runtime(worker) == "codex":
                attempts += 1
                if attempts == 1:
                    self.assertTrue(sibling_started.wait(1))
                raise RuntimeError("private persistent failure must not leak")
            sibling_started.set()
            while not stop_requested():
                time.sleep(0.001)
            sibling_stopped.set()

        events = run_supervisor(
            config,
            worker_runner=worker_runner,
            report_failure=reports.append,
            poll_interval=0.001,
            max_consecutive_failures=5,
            restart_backoff_initial=2,
            restart_backoff_max=3,
            restart_wait=lambda delay: delays.append(delay) or False,
        )

        self.assertTrue(sibling_started.is_set())
        self.assertTrue(sibling_stopped.is_set())
        self.assertEqual(attempts, 5)
        self.assertEqual(delays, [2, 3, 3, 3])
        self.assertEqual(events, tuple(reports))
        self.assertEqual(
            [event.consecutive_failures for event in events], [1, 2, 3, 4, 5]
        )
        self.assertEqual(
            [event.fatal for event in events], [False, False, False, False, True]
        )
        self.assertNotIn("private persistent failure", repr(events))

        with (
            patch.object(
                supervisor_module.SupervisorConfig,
                "from_file",
                return_value=config,
            ),
            patch.object(supervisor_module, "install_signal_handlers", return_value=lambda: False),
            patch.object(
                supervisor_module, "run_supervisor", return_value=events
            ) as run_mock,
        ):
            self.assertEqual(
                supervisor_module.main(["--config", str(self.supervisor_path)]),
                1,
            )
            run_mock.return_value = events[:-1]
            self.assertEqual(
                supervisor_module.main(["--config", str(self.supervisor_path)]),
                0,
            )


class SupervisorInstallerTests(SupervisorFixture):
    def setUp(self) -> None:
        super().setUp()
        self.installer = load_installer()
        self.home = self.root / "home"
        self.home.mkdir()
        self.paths = self.installer.canonical_install_paths(self.home)
        self.python_path = str(Path(sys.executable).resolve())
        self.codex = self._executable("codex")
        self.openclaw = self._executable("openclaw")
        self.legacy_config, self.legacy_plist = (
            self.installer.canonical_single_worker_install_paths(self.home)
        )
        launch_domain = f"gui/{os.getuid()}"
        self.supervisor_ref = f"{launch_domain}/{self.installer.DEFAULT_LABEL}"
        self.legacy_ref = (
            f"{launch_domain}/{self.installer.LEGACY_LABEL}"
        )
        self.supervisor_loaded = False
        self.legacy_loaded = False
        self.legacy_disabled = False
        self.fail_supervisor_bootstrap = False
        self.bad_supervisor_readback = False
        self.ignore_legacy_disable = False
        self.ignore_legacy_bootout = False
        self.concurrent_active_seen = False
        self.last_calls: list[tuple[list[str], dict[str, object]]] = []

    def _executable(self, name: str) -> Path:
        path = self.root / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def expected_arguments(self) -> list[str]:
        return [
            self.python_path,
            "-m",
            "gtasks.local_handoff_supervisor",
            "--config",
            str(self.paths.supervisor_config.resolve()),
            "--codex-path",
            str(self.codex.resolve()),
            "--openclaw-path",
            str(self.openclaw.resolve()),
            "--working-directory",
            str(ROOT.resolve()),
        ]

    def launchctl_output(
        self,
        *,
        arguments: list[str] | None = None,
        label: str | None = None,
    ) -> str:
        rendered_arguments = "\n".join(
            f"\t\t{value}" for value in (arguments or self.expected_arguments())
        )
        return (
            "service = {\n"
            "\targuments = {\n"
            f"{rendered_arguments}\n"
            "\t}\n"
            f"\tworking directory = {ROOT.resolve()}\n"
            "\tenvironment = {\n"
            f"\t\tXPC_SERVICE_NAME => {label or self.installer.DEFAULT_LABEL}\n"
            f"\t\tPYTHONPATH => {ROOT.resolve()}\n"
            "\t}\n"
            "}\n"
        )

    def legacy_arguments(self) -> list[str]:
        return [
            self.python_path,
            "-m",
            "gtasks.local_handoff_dispatcher",
            "--config",
            str(self.legacy_config.resolve()),
            "--codex-path",
            str(self.codex.resolve()),
            "--working-directory",
            str(ROOT.resolve()),
        ]

    def write_legacy_install(self, *, loaded: bool, disabled: bool = False) -> None:
        self.legacy_config.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_config.write_bytes(self.codex_config.read_bytes())
        self.legacy_config.chmod(0o600)
        self.legacy_plist.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_plist.write_bytes(
            plistlib.dumps(
                {
                    "Label": self.installer.LEGACY_LABEL,
                    "ProgramArguments": self.legacy_arguments(),
                    "WorkingDirectory": str(ROOT.resolve()),
                    "EnvironmentVariables": {"PYTHONPATH": str(ROOT.resolve())},
                    "RunAtLoad": True,
                    "KeepAlive": True,
                    "ProcessType": "Background",
                },
                sort_keys=False,
            )
        )
        self.legacy_plist.chmod(0o644)
        self.legacy_loaded = loaded
        self.legacy_disabled = disabled

    def fake_run(self, calls):
        def run(arguments, **kwargs):
            calls.append((list(arguments), kwargs))
            if arguments[0] == self.python_path and "-c" in arguments:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=f"{(ROOT / 'gtasks' / 'local_handoff_supervisor.py').resolve()}\n",
                    stderr="",
                )
            if arguments == [str(self.codex.resolve()), "--version"]:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout="codex-cli 1.2.3\n", stderr=""
                )
            if arguments == [str(self.codex.resolve()), "exec", "resume", "--help"]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout="codex exec resume --skip-git-repo-check\n",
                    stderr="",
                )
            if arguments == [str(self.openclaw.resolve()), "--version"]:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout="openclaw 4.5.6\n", stderr=""
                )
            if arguments == [str(self.openclaw.resolve()), "agent", "--help"]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout="agent --local --json --timeout --session-key --message\n",
                    stderr="",
                )
            if arguments[:2] == ["/bin/launchctl", "print-disabled"]:
                disabled = "true" if self.legacy_disabled else "false"
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=(
                        "disabled services = {\n"
                        f'\t"{self.installer.LEGACY_LABEL}" => {disabled}\n'
                        "}\n"
                    ),
                    stderr="",
                )
            if arguments[:2] == ["/bin/launchctl", "print"]:
                if arguments[2] == self.supervisor_ref and self.supervisor_loaded:
                    output = self.launchctl_output()
                    if self.bad_supervisor_readback:
                        output = output.replace(
                            "gtasks.local_handoff_supervisor",
                            "gtasks.local_handoff_dispatcher",
                        )
                    return subprocess.CompletedProcess(
                        arguments, 0, stdout=output, stderr=""
                    )
                if arguments[2] == self.legacy_ref and self.legacy_loaded:
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout=self.launchctl_output(
                            arguments=self.legacy_arguments(),
                            label=self.installer.LEGACY_LABEL,
                        ),
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    arguments, 3, stdout="", stderr="not loaded"
                )
            if arguments[:2] == ["/bin/launchctl", "disable"]:
                if not self.ignore_legacy_disable:
                    self.legacy_disabled = True
            elif arguments[:2] == ["/bin/launchctl", "enable"]:
                self.legacy_disabled = False
            elif arguments[:2] == ["/bin/launchctl", "bootout"]:
                if arguments[2] == self.supervisor_ref:
                    self.supervisor_loaded = False
                elif arguments[2] == self.legacy_ref:
                    if not self.ignore_legacy_bootout:
                        self.legacy_loaded = False
            elif arguments[:2] == ["/bin/launchctl", "bootstrap"]:
                if arguments[3] == str(self.paths.plist):
                    if self.fail_supervisor_bootstrap:
                        return subprocess.CompletedProcess(
                            arguments, 5, stdout="", stderr="bootstrap failed"
                        )
                    self.supervisor_loaded = True
                elif arguments[3] == str(self.legacy_plist):
                    self.legacy_loaded = True
            self.concurrent_active_seen = self.concurrent_active_seen or (
                self.supervisor_loaded and self.legacy_loaded
            )
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        return run

    def install(self, *, dry_run: bool, replace_legacy: bool = False):
        calls: list[tuple[list[str], dict[str, object]]] = []
        self.last_calls = calls
        receipt = self.installer.install(
            source_worker_configs=(self.codex_config, self.openclaw_config),
            plist_template=TEMPLATE_PATH,
            python_path=self.python_path,
            module_root=ROOT,
            runner_path=ROOT / "gtasks" / "local_handoff_supervisor.py",
            codex_path=str(self.codex),
            openclaw_path=str(self.openclaw),
            working_directory=ROOT,
            home_directory=self.home,
            run=self.fake_run(calls),
            dry_run=dry_run,
            replace_legacy=replace_legacy,
        )
        return receipt, calls

    def test_uses_canonical_nonlegacy_paths(self) -> None:
        base = (
            self.home.resolve()
            / "Library"
            / "Application Support"
            / "GTasks"
            / "handoff-dispatcher"
        )
        self.assertEqual(self.paths.supervisor_config, base / "supervisor.json")
        self.assertEqual(self.paths.codex_worker_config, base / "workers" / "codex.json")
        self.assertEqual(
            self.paths.openclaw_worker_config, base / "workers" / "openclaw.json"
        )
        self.assertEqual(
            self.paths.plist,
            self.home.resolve()
            / "Library"
            / "LaunchAgents"
            / "com.tony.gtasks-handoff-dispatcher-supervisor.plist",
        )
        legacy_config, legacy_plist = self.installer.canonical_single_worker_install_paths(
            self.home
        )
        self.assertNotIn(legacy_config, self.paths)
        self.assertNotIn(legacy_plist, self.paths)

    def test_dry_run_receipt_is_deterministic_redacted_and_writes_nothing(self) -> None:
        first, first_calls = self.install(dry_run=True)
        second, second_calls = self.install(dry_run=True)

        self.assertEqual(first, second)
        self.assertFalse(self.paths.supervisor_config.exists())
        self.assertFalse(self.paths.codex_worker_config.exists())
        self.assertFalse(self.paths.openclaw_worker_config.exists())
        self.assertFalse(self.paths.plist.exists())
        self.assertFalse(first.activated)
        self.assertEqual(
            {
                (worker.agent_slug, worker.runtime, worker.runtime_version)
                for worker in first.workers
            },
            {
                ("agents/tammy", "codex", "codex-cli 1.2.3"),
                ("agents/tammy-oc", "openclaw", "openclaw 4.5.6"),
            },
        )
        rendered = json.dumps(asdict(first), sort_keys=True)
        for secret in (
            "codex-secret-token",
            "openclaw-secret-token",
            "private-registration-tammy",
            "private-registration-tammy-oc",
            "fixed-codex-thread",
            "agent:tammy-oc:fixed",
        ):
            self.assertNotIn(secret, rendered)
        self.assertEqual(first_calls, second_calls)
        self.assertFalse(
            any(
                call[0][:2]
                in (
                    ["/bin/launchctl", "bootstrap"],
                    ["/bin/launchctl", "bootout"],
                    ["/bin/launchctl", "disable"],
                    ["/bin/launchctl", "enable"],
                )
                for call in first_calls
            )
        )

    def test_dry_run_import_probe_disables_bytecode_and_changes_no_files(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        fake = self.fake_run(calls)

        def run(arguments, **kwargs):
            if arguments[0] == self.python_path and "-c" in arguments:
                calls.append((list(arguments), kwargs))
                return subprocess.run(arguments, **kwargs)
            return fake(arguments, **kwargs)

        def snapshot(path: Path) -> dict[str, tuple[int, int]]:
            return {
                str(candidate.relative_to(path)): (
                    candidate.stat().st_size,
                    candidate.stat().st_mtime_ns,
                )
                for candidate in path.rglob("*")
                if candidate.is_file()
            }

        root_before = snapshot(ROOT)
        home_before = snapshot(self.home)

        receipt = self.installer.install(
            source_worker_configs=(self.codex_config, self.openclaw_config),
            plist_template=TEMPLATE_PATH,
            python_path=self.python_path,
            module_root=ROOT,
            runner_path=ROOT / "gtasks" / "local_handoff_supervisor.py",
            codex_path=str(self.codex),
            openclaw_path=str(self.openclaw),
            working_directory=ROOT,
            home_directory=self.home,
            run=run,
            dry_run=True,
        )

        self.assertFalse(receipt.activated)
        self.assertEqual(snapshot(ROOT), root_before)
        self.assertEqual(snapshot(self.home), home_before)
        import_calls = [
            call for call in calls if call[0][0] == self.python_path and "-c" in call[0]
        ]
        self.assertEqual(len(import_calls), 1)
        self.assertEqual(import_calls[0][0][:3], [self.python_path, "-B", "-c"])
        self.assertEqual(
            import_calls[0][1]["env"],
            {
                "PYTHONPATH": str(ROOT.resolve()),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.assertFalse(any(self.home.rglob("__pycache__")))

    def test_requires_the_exact_plist_dictionary_for_templates_and_existing_files(self) -> None:
        template_value = plistlib.loads(TEMPLATE_PATH.read_bytes())
        mutations = (
            ("missing KeepAlive", lambda value: value.pop("KeepAlive")),
            ("false RunAtLoad", lambda value: value.update(RunAtLoad=False)),
            ("integer KeepAlive", lambda value: value.update(KeepAlive=1)),
            ("wrong ProcessType", lambda value: value.update(ProcessType="Interactive")),
            ("extra key", lambda value: value.update(ThrottleInterval=10)),
        )
        for name, mutate in mutations:
            with self.subTest(source="template", mutation=name):
                value = dict(template_value)
                mutate(value)
                mutated_template = self.root / f"{name.replace(' ', '-')}.plist"
                mutated_template.write_bytes(plistlib.dumps(value, sort_keys=False))
                with self.assertRaisesRegex(ValueError, "exact canonical contract"):
                    self.installer.install(
                        source_worker_configs=(self.codex_config, self.openclaw_config),
                        plist_template=mutated_template,
                        python_path=self.python_path,
                        module_root=ROOT,
                        runner_path=ROOT / "gtasks" / "local_handoff_supervisor.py",
                        codex_path=str(self.codex),
                        openclaw_path=str(self.openclaw),
                        working_directory=ROOT,
                        home_directory=self.home,
                        run=self.fake_run([]),
                        dry_run=True,
                    )

        self.install(dry_run=False)
        canonical_plist = plistlib.loads(self.paths.plist.read_bytes())
        for name, mutate in mutations:
            with self.subTest(source="existing", mutation=name):
                value = dict(canonical_plist)
                mutate(value)
                self.paths.plist.write_bytes(plistlib.dumps(value, sort_keys=False))
                with self.assertRaisesRegex(ValueError, "exact canonical contract"):
                    self.install(dry_run=True)
                self.paths.plist.write_bytes(
                    plistlib.dumps(canonical_plist, sort_keys=False)
                )

    def test_dry_run_reports_an_active_legacy_fence_without_mutating_it(self) -> None:
        self.write_legacy_install(loaded=True)

        receipt, calls = self.install(dry_run=True)

        self.assertFalse(receipt.activated)
        self.assertEqual(receipt.legacy_state, "loaded")
        self.assertEqual(receipt.transition_state, "blocked_legacy_loaded")
        self.assertTrue(self.legacy_loaded)
        self.assertFalse(self.supervisor_loaded)
        self.assertFalse(
            any(
                arguments[:2]
                in (
                    ["/bin/launchctl", "bootstrap"],
                    ["/bin/launchctl", "bootout"],
                    ["/bin/launchctl", "disable"],
                    ["/bin/launchctl", "enable"],
                )
                for arguments, _kwargs in calls
            )
        )

    def test_refuses_loaded_or_enabled_legacy_without_explicit_replacement(self) -> None:
        for loaded, expected_state in ((True, "loaded"), (False, "enabled")):
            with self.subTest(expected_state=expected_state):
                self.write_legacy_install(loaded=loaded)
                with self.assertRaisesRegex(ValueError, "legacy.*--replace-legacy"):
                    self.install(dry_run=False)
                self.assertEqual(self.legacy_loaded, loaded)
                self.assertFalse(self.supervisor_loaded)
                self.assertFalse(
                    any(
                        arguments[1] in {"bootstrap", "bootout", "disable", "enable"}
                        for arguments, _kwargs in self.last_calls
                        if arguments[0] == "/bin/launchctl"
                    )
                )
                self.legacy_plist.unlink()
                self.legacy_config.unlink()

    def test_replace_legacy_stops_it_before_starting_the_supervisor(self) -> None:
        self.write_legacy_install(loaded=True)

        receipt, calls = self.install(dry_run=False, replace_legacy=True)

        self.assertTrue(receipt.activated)
        self.assertEqual(receipt.legacy_state, "loaded")
        self.assertEqual(receipt.transition_state, "legacy_replaced")
        self.assertTrue(self.supervisor_loaded)
        self.assertFalse(self.legacy_loaded)
        self.assertTrue(self.legacy_disabled)
        self.assertFalse(self.concurrent_active_seen)
        mutations = [
            arguments
            for arguments, _kwargs in calls
            if arguments[0] == "/bin/launchctl"
            and arguments[1] in {"bootstrap", "bootout", "disable", "enable"}
        ]
        self.assertLess(
            mutations.index(["/bin/launchctl", "disable", self.legacy_ref]),
            mutations.index(["/bin/launchctl", "bootout", self.legacy_ref]),
        )
        self.assertLess(
            mutations.index(["/bin/launchctl", "bootout", self.legacy_ref]),
            mutations.index(
                [
                    "/bin/launchctl",
                    "bootstrap",
                    f"gui/{os.getuid()}",
                    str(self.paths.plist),
                ]
            ),
        )

    def test_replace_legacy_rolls_back_bootstrap_or_readback_failure(self) -> None:
        for failure_attribute in (
            "fail_supervisor_bootstrap",
            "bad_supervisor_readback",
        ):
            with self.subTest(failure_attribute=failure_attribute):
                self.write_legacy_install(loaded=True)
                setattr(self, failure_attribute, True)

                with self.assertRaisesRegex(RuntimeError, "rolled back"):
                    self.install(dry_run=False, replace_legacy=True)

                self.assertTrue(self.legacy_loaded)
                self.assertFalse(self.legacy_disabled)
                self.assertFalse(self.supervisor_loaded)
                self.assertFalse(self.concurrent_active_seen)
                mutations = [
                    arguments
                    for arguments, _kwargs in self.last_calls
                    if arguments[0] == "/bin/launchctl"
                    and arguments[1] in {"bootstrap", "bootout", "disable", "enable"}
                ]
                self.assertIn(
                    ["/bin/launchctl", "bootout", self.supervisor_ref],
                    mutations,
                )
                self.assertIn(
                    ["/bin/launchctl", "enable", self.legacy_ref],
                    mutations,
                )
                self.assertIn(
                    [
                        "/bin/launchctl",
                        "bootstrap",
                        f"gui/{os.getuid()}",
                        str(self.legacy_plist),
                    ],
                    mutations,
                )
                setattr(self, failure_attribute, False)
                self.legacy_loaded = False
                self.legacy_plist.unlink()
                self.legacy_config.unlink()

    def test_replace_legacy_requires_disable_and_stop_readback_before_bootstrap(self) -> None:
        for failure_attribute in (
            "ignore_legacy_disable",
            "ignore_legacy_bootout",
        ):
            with self.subTest(failure_attribute=failure_attribute):
                self.write_legacy_install(loaded=True)
                setattr(self, failure_attribute, True)

                with self.assertRaisesRegex(RuntimeError, "rolled back"):
                    self.install(dry_run=False, replace_legacy=True)

                self.assertTrue(self.legacy_loaded)
                self.assertFalse(self.legacy_disabled)
                self.assertFalse(self.supervisor_loaded)
                self.assertFalse(
                    any(
                        arguments[:2] == ["/bin/launchctl", "bootstrap"]
                        and arguments[-1] == str(self.paths.plist)
                        for arguments, _kwargs in self.last_calls
                    )
                )
                setattr(self, failure_attribute, False)
                self.legacy_loaded = False
                self.legacy_plist.unlink()
                self.legacy_config.unlink()

    def test_refuses_an_already_concurrent_legacy_and_supervisor_state(self) -> None:
        self.install(dry_run=False)
        self.write_legacy_install(loaded=True)

        with self.assertRaisesRegex(ValueError, "both.*loaded|concurrent"):
            self.install(dry_run=False, replace_legacy=True)

        self.assertFalse(
            any(
                arguments[1] in {"bootstrap", "bootout", "disable", "enable"}
                for arguments, _kwargs in self.last_calls
                if arguments[0] == "/bin/launchctl"
            )
        )

    def test_dry_run_refuses_existing_identity_drift_without_launchctl(self) -> None:
        self.paths.codex_worker_config.parent.mkdir(parents=True)
        drifted = json.loads(self.codex_config.read_text(encoding="utf-8"))
        drifted["agent_slug"] = "agents/timmy"
        self.paths.codex_worker_config.write_text(
            json.dumps(drifted), encoding="utf-8"
        )
        self.paths.codex_worker_config.chmod(0o600)
        calls = []

        with self.assertRaisesRegex(ValueError, "another identity"):
            self.installer.install(
                source_worker_configs=(self.codex_config, self.openclaw_config),
                plist_template=TEMPLATE_PATH,
                python_path=self.python_path,
                module_root=ROOT,
                runner_path=ROOT / "gtasks" / "local_handoff_supervisor.py",
                codex_path=str(self.codex),
                openclaw_path=str(self.openclaw),
                working_directory=ROOT,
                home_directory=self.home,
                run=self.fake_run(calls),
                dry_run=True,
            )
        self.assertFalse(any(call[0][0] == "/bin/launchctl" for call in calls))

    def test_installs_two_private_configs_and_one_secret_free_plist(self) -> None:
        receipt, calls = self.install(dry_run=False)

        self.assertTrue(receipt.activated)
        self.assertEqual(self.paths.supervisor_config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.paths.codex_worker_config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            self.paths.openclaw_worker_config.stat().st_mode & 0o777, 0o600
        )
        self.assertEqual(self.paths.plist.stat().st_mode & 0o777, 0o644)
        installed_supervisor = SupervisorConfig.from_file(self.paths.supervisor_config)
        installed_workers = load_isolated_workers(installed_supervisor)
        self.assertEqual(
            tuple(worker_runtime(worker) for worker in installed_workers),
            ("codex", "openclaw"),
        )
        self.assertEqual(
            tuple(installed_supervisor.worker_config_paths),
            (
                self.paths.codex_worker_config.resolve(),
                self.paths.openclaw_worker_config.resolve(),
            ),
        )
        for worker_receipt in receipt.workers:
            worker_bytes = Path(worker_receipt.config_path).read_bytes()
            self.assertEqual(
                worker_receipt.config_sha256,
                hashlib.sha256(worker_bytes).hexdigest(),
            )
        self.assertEqual(
            receipt.supervisor_config_sha256,
            hashlib.sha256(self.paths.supervisor_config.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            receipt.plist_sha256,
            hashlib.sha256(self.paths.plist.read_bytes()).hexdigest(),
        )
        rendered_plist = self.paths.plist.read_text(encoding="utf-8")
        parsed_plist = plistlib.loads(self.paths.plist.read_bytes())
        self.assertEqual(parsed_plist["ProgramArguments"], self.expected_arguments())
        self.assertEqual(parsed_plist["EnvironmentVariables"], {
            "PYTHONPATH": str(ROOT.resolve())
        })
        self.assertEqual(rendered_plist.count("<key>Label</key>"), 1)
        for secret in (
            "codex-secret-token",
            "openclaw-secret-token",
            "private-registration-tammy",
            "private-registration-tammy-oc",
            "fixed-codex-thread",
            "agent:tammy-oc:fixed",
        ):
            self.assertNotIn(secret, rendered_plist)
            self.assertNotIn(secret, json.dumps(asdict(receipt), sort_keys=True))
        launch_ref = (
            f"gui/{os.getuid()}/com.tony.gtasks-handoff-dispatcher-supervisor"
        )
        self.assertIn(
            (["/bin/launchctl", "print", launch_ref]),
            [call[0] for call in calls],
        )
        self.assertIn(
            ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(self.paths.plist)],
            [call[0] for call in calls],
        )
        self.assertTrue(all("shell" not in kwargs for _args, kwargs in calls))

    def test_refuses_cross_host_pair_before_any_subprocess(self) -> None:
        self._worker_config(
            self.openclaw_config.name,
            agent_slug="agents/timmy-oc",
            registration_id="private-registration-timmy-oc",
            fixed_thread_id="agent:timmy-oc:fixed",
            token_file=self.openclaw_token,
        )
        calls = []

        with self.assertRaisesRegex(ValueError, "approved.*host"):
            self.installer.install(
                source_worker_configs=(self.codex_config, self.openclaw_config),
                plist_template=TEMPLATE_PATH,
                python_path=self.python_path,
                module_root=ROOT,
                runner_path=ROOT / "gtasks" / "local_handoff_supervisor.py",
                codex_path=str(self.codex),
                openclaw_path=str(self.openclaw),
                working_directory=ROOT,
                home_directory=self.home,
                run=lambda *args, **kwargs: calls.append((args, kwargs)),
                dry_run=True,
            )
        self.assertEqual(calls, [])

    def test_refuses_a_symbolic_link_source_before_any_subprocess(self) -> None:
        linked_config = self.root / "linked-codex.json"
        linked_config.symlink_to(self.codex_config)
        calls = []

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.installer.install(
                source_worker_configs=(linked_config, self.openclaw_config),
                plist_template=TEMPLATE_PATH,
                python_path=self.python_path,
                module_root=ROOT,
                runner_path=ROOT / "gtasks" / "local_handoff_supervisor.py",
                codex_path=str(self.codex),
                openclaw_path=str(self.openclaw),
                working_directory=ROOT,
                home_directory=self.home,
                run=lambda *args, **kwargs: calls.append((args, kwargs)),
                dry_run=True,
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
