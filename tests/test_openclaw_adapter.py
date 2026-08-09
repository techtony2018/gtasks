from __future__ import annotations

import subprocess
import unittest

from gtasks.openclaw_adapter import (
    OpenClawContractError,
    OpenClawSessionAdapter,
    parse_openclaw_output,
)


SESSION_KEY = "agent:tammy-oc:fixed"


class OpenClawOutputParserTests(unittest.TestCase):
    def test_accepts_warning_prefixed_json_and_prefers_visible_text(self) -> None:
        result = parse_openclaw_output(
            "warning: local profile reload\n"
            '{"status":"ok","sessionKey":"agent:tammy-oc:fixed",'
            '"finalAssistantVisibleText":"Visible completion",'
            '"finalAssistantRawText":"Raw completion",'
            '"payloads":[{"text":"Payload completion"}]}',
            expected_session_key=SESSION_KEY,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.assistant_text, "Visible completion")
        self.assertEqual(result.session_key, SESSION_KEY)

    def test_uses_raw_text_then_payload_text_when_visible_text_is_absent(self) -> None:
        raw = parse_openclaw_output(
            '{"status":"ok","sessionKey":"agent:tammy-oc:fixed",'
            '"finalAssistantRawText":"Raw completion"}',
            expected_session_key=SESSION_KEY,
        )
        payload = parse_openclaw_output(
            '{"status":"ok","sessionKey":"agent:tammy-oc:fixed",'
            '"payloads":[{"type":"tool"},{"text":"Payload completion"}]}',
            expected_session_key=SESSION_KEY,
        )

        self.assertEqual(raw.assistant_text, "Raw completion")
        self.assertEqual(payload.assistant_text, "Payload completion")

    def test_accepts_a_matching_envelope_and_nested_result_session(self) -> None:
        result = parse_openclaw_output(
            '{"status":"ok","sessionKey":"agent:tammy-oc:fixed","result":'
            '{"sessionKey":"agent:tammy-oc:fixed",'
            '"payloads":[{"text":"Nested completion"}]}}',
            expected_session_key=SESSION_KEY,
        )

        self.assertEqual(result.assistant_text, "Nested completion")

    def test_rejects_wrong_or_missing_session_and_malformed_output(self) -> None:
        for stdout in (
            '{"status":"ok","sessionKey":"agent:other","finalAssistantVisibleText":"x"}',
            '{"status":"ok","finalAssistantVisibleText":"x"}',
            "warning only",
            '{"status":"ok","sessionKey":"agent:tammy-oc:fixed"}',
            '{"sessionKey":"agent:tammy-oc:fixed","finalAssistantVisibleText":"x"}',
        ):
            with self.subTest(stdout=stdout):
                with self.assertRaisesRegex(OpenClawContractError, "session|structured|assistant|completion"):
                    parse_openclaw_output(stdout, expected_session_key=SESSION_KEY)

        with self.assertRaises(ValueError):
            parse_openclaw_output(
                '{"status":"ok","sessionKey":"agent:tammy-oc:fixed",'
                '"finalAssistantVisibleText":"x"}',
                expected_session_key="agent:tammy-oc:fixed\n",
            )

    def test_rejects_unbounded_stdout_and_bounds_returned_text(self) -> None:
        huge = "x" * 65_537
        with self.assertRaisesRegex(OpenClawContractError, "bounded"):
            parse_openclaw_output(huge, expected_session_key=SESSION_KEY)

        result = parse_openclaw_output(
            '{"status":"ok","sessionKey":"agent:tammy-oc:fixed",'
            f'"finalAssistantVisibleText":"{"x" * 5000}"}}',
            expected_session_key=SESSION_KEY,
        )

        self.assertEqual(len(result.assistant_text), 4096)


class OpenClawSessionAdapterTests(unittest.TestCase):
    def test_verifies_the_installed_cli_contract_with_argument_arrays(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def run(arguments, **kwargs):
            calls.append((arguments, kwargs))
            stdout = "openclaw 2026.8.8" if arguments[-1] == "--version" else (
                "Usage: openclaw agent --local --json --session-key --message"
            )
            return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

        adapter = OpenClawSessionAdapter(
            executable="/opt/bin/openclaw",
            session_key=SESSION_KEY,
            timeout_seconds=41,
            run=run,
        )

        self.assertEqual(adapter.verify_contract(), "openclaw 2026.8.8")
        self.assertEqual(
            [call[0] for call in calls],
            [
                ["/opt/bin/openclaw", "--version"],
                ["/opt/bin/openclaw", "agent", "--help"],
            ],
        )
        self.assertTrue(all("shell" not in kwargs for _, kwargs in calls))

    def test_executes_only_the_fixed_session_with_an_argument_array(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def run(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=(
                    '{"status":"ok","sessionKey":"agent:tammy-oc:fixed",'
                    '"finalAssistantVisibleText":"Completed"}'
                ),
                stderr="",
            )

        adapter = OpenClawSessionAdapter(
            executable="/opt/bin/openclaw",
            session_key=SESSION_KEY,
            timeout_seconds=41,
            run=run,
        )

        result = adapter.execute("handoff summary; $(not-a-shell-command)")

        self.assertEqual(result.status, "completed")
        self.assertEqual(calls, [(
            [
                "/opt/bin/openclaw", "agent", "--local", "--json",
                "--timeout", "41", "--session-key", SESSION_KEY,
                "--message", "handoff summary; $(not-a-shell-command)",
            ],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "timeout": 41,
            },
        )])
        self.assertNotIn("shell", calls[0][1])
        self.assertFalse(any("new" in call[0] or "create" in call[0] or "fork" in call[0] for call in calls))

    def test_fails_closed_on_timeout_or_nonzero_exit_without_leaking_output(self) -> None:
        def timeout_run(arguments, **kwargs):
            raise subprocess.TimeoutExpired(arguments, kwargs["timeout"], output="secret prompt")

        with self.assertRaisesRegex(OpenClawContractError, "timed out") as timeout_error:
            OpenClawSessionAdapter(
                executable="openclaw", session_key=SESSION_KEY, timeout_seconds=3, run=timeout_run
            ).execute("secret prompt")
        self.assertNotIn("secret prompt", str(timeout_error.exception))

        def failed_run(arguments, **kwargs):
            return subprocess.CompletedProcess(arguments, 9, stdout="secret output", stderr="secret error")

        with self.assertRaisesRegex(OpenClawContractError, "failed") as exit_error:
            OpenClawSessionAdapter(
                executable="openclaw", session_key=SESSION_KEY, timeout_seconds=3, run=failed_run
            ).execute("secret prompt")
        self.assertNotIn("secret output", str(exit_error.exception))
        self.assertNotIn("secret error", str(exit_error.exception))

    def test_rejects_invalid_private_execution_inputs_before_running(self) -> None:
        for executable, session_key, timeout_seconds in (
            ("", SESSION_KEY, 1),
            ("openclaw", "", 1),
            ("openclaw", "bad\nkey", 1),
            ("openclaw", SESSION_KEY, 0),
        ):
            with self.subTest(executable=executable, session_key=session_key, timeout_seconds=timeout_seconds):
                with self.assertRaises(ValueError):
                    OpenClawSessionAdapter(
                        executable=executable,
                        session_key=session_key,
                        timeout_seconds=timeout_seconds,
                    )

    def test_source_never_contains_session_creation_commands(self) -> None:
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "gtasks" / "openclaw_adapter.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("shell=True", source)
        self.assertNotIn('"session", "new"', source)
        self.assertNotIn('"session", "create"', source)
        self.assertNotIn('"session", "fork"', source)
