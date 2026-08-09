"""Fail-closed execution through one configured OpenClaw session.

This module deliberately exposes no operation which can create, fork, select,
or infer a session.  A caller must supply the private, pre-existing session
key and receives only a bounded completion summary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Callable, Mapping


_MAX_STDOUT_BYTES = 65_536
_MAX_ASSISTANT_TEXT_CHARS = 4_096
_MAX_PROMPT_CHARS = 16_384
_MAX_SESSION_KEY_CHARS = 256


class OpenClawContractError(RuntimeError):
    """The configured OpenClaw CLI did not prove a fixed-session completion."""


@dataclass(frozen=True, slots=True)
class OpenClawExecutionResult:
    status: str
    assistant_text: str
    session_key: str


def _require_bounded_string(
    value: object, field: str, *, limit: int, allow_empty: bool = False
) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value) > limit
        or "\0" in value
        or (field.endswith("session_key") and any(character.isspace() for character in value))
    ):
        raise ValueError(f"{field} must be one bounded string")
    return value


def _bounded_assistant_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:_MAX_ASSISTANT_TEXT_CHARS] if text else None


def _structured_output(stdout: str) -> Mapping[str, object]:
    """Read exactly one JSON object after optional bounded warning lines."""
    if not isinstance(stdout, str):
        raise OpenClawContractError("OpenClaw returned no structured output")
    try:
        rendered = stdout.encode("utf-8")
    except UnicodeError as exc:
        raise OpenClawContractError("OpenClaw returned invalid structured output") from exc
    if len(rendered) > _MAX_STDOUT_BYTES:
        raise OpenClawContractError("OpenClaw structured output exceeds the bounded limit")

    decoder = json.JSONDecoder()
    for line_start in (0, *(index + 1 for index, char in enumerate(stdout) if char == "\n")):
        candidate = stdout[line_start:].lstrip()
        if not candidate.startswith("{"):
            continue
        try:
            decoded, position = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if candidate[position:].strip():
            raise OpenClawContractError("OpenClaw returned malformed structured output")
        if isinstance(decoded, dict):
            return decoded
        break
    raise OpenClawContractError("OpenClaw returned no structured output")


def _result_payload(value: Mapping[str, object]) -> Mapping[str, object]:
    nested = value.get("result")
    if nested is None:
        return value
    if not isinstance(nested, dict):
        raise OpenClawContractError("OpenClaw returned malformed structured output")
    return nested


def _reported_session_key(
    envelope: Mapping[str, object], result: Mapping[str, object]
) -> str:
    keys = [value for value in (envelope.get("sessionKey"),) if value is not None]
    if result is not envelope and result.get("sessionKey") is not None:
        keys.append(result["sessionKey"])
    if (
        not keys
        or any(not isinstance(key, str) or not key for key in keys)
        or len(set(keys)) != 1
    ):
        raise OpenClawContractError("OpenClaw completion omitted its fixed session")
    return keys[0]


def _assistant_text(result: Mapping[str, object]) -> str:
    for field in ("finalAssistantVisibleText", "finalAssistantRawText"):
        text = _bounded_assistant_text(result.get(field))
        if text is not None:
            return text
    payloads = result.get("payloads")
    if isinstance(payloads, list):
        for payload in payloads:
            if isinstance(payload, dict):
                text = _bounded_assistant_text(payload.get("text"))
                if text is not None:
                    return text
    raise OpenClawContractError("OpenClaw completion omitted assistant text")


def parse_openclaw_output(
    stdout: str, *, expected_session_key: str
) -> OpenClawExecutionResult:
    """Validate one fixed-session JSON completion without exposing raw output."""
    expected_session_key = _require_bounded_string(
        expected_session_key, "expected_session_key", limit=_MAX_SESSION_KEY_CHARS
    )
    envelope = _structured_output(stdout)
    result = _result_payload(envelope)
    reported_session_key = _reported_session_key(envelope, result)
    if reported_session_key != expected_session_key:
        raise OpenClawContractError("OpenClaw completion belongs to another session")
    statuses = [envelope.get("status")]
    if result is not envelope:
        statuses.append(result.get("status"))
    if not any(status in {"ok", "completed", "success"} for status in statuses) or any(
        status is not None and status not in {"ok", "completed", "success"}
        for status in statuses
    ):
        raise OpenClawContractError("OpenClaw did not report a successful completion")
    return OpenClawExecutionResult(
        status="completed",
        assistant_text=_assistant_text(result),
        session_key=expected_session_key,
    )


class OpenClawSessionAdapter:
    """Execute a message in exactly one configured existing OpenClaw session."""

    def __init__(
        self,
        *,
        executable: str,
        session_key: str,
        timeout_seconds: int,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.executable = _require_bounded_string(
            executable, "executable", limit=4_096
        )
        self.session_key = _require_bounded_string(
            session_key, "session_key", limit=_MAX_SESSION_KEY_CHARS
        )
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 86_400
        ):
            raise ValueError("timeout_seconds must be between 1 and 86400")
        self.timeout_seconds = timeout_seconds
        self._run = run

    def command(self, prompt: str) -> list[str]:
        """Build the sole supported command form, without invoking a shell."""
        prompt = _require_bounded_string(
            prompt, "prompt", limit=_MAX_PROMPT_CHARS
        )
        return [
            self.executable,
            "agent",
            "--local",
            "--json",
            "--timeout",
            str(self.timeout_seconds),
            "--session-key",
            self.session_key,
            "--message",
            prompt,
        ]

    def execute(self, prompt: str) -> OpenClawExecutionResult:
        """Run one pre-existing session and fail closed on any uncertain result."""
        command = self.command(prompt)
        try:
            completed = self._run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenClawContractError("OpenClaw fixed-session execution timed out") from exc
        except OSError as exc:
            raise OpenClawContractError("OpenClaw fixed-session execution could not start") from exc
        if completed.returncode != 0:
            raise OpenClawContractError("OpenClaw fixed-session execution failed")
        return parse_openclaw_output(
            completed.stdout, expected_session_key=self.session_key
        )

    def verify_contract(self) -> str:
        """Prove the installed binary advertises the required fixed-session flags."""
        try:
            version = self._run(
                [self.executable, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            help_result = self._run(
                [self.executable, "agent", "--help"],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OpenClawContractError("OpenClaw CLI contract could not be verified") from exc
        if version.returncode != 0 or not version.stdout.strip():
            raise OpenClawContractError("openclaw --version failed")
        help_text = f"{help_result.stdout}\n{help_result.stderr}".lower()
        if (
            help_result.returncode != 0
            or "--local" not in help_text
            or "--json" not in help_text
            or "--session-key" not in help_text
            or "--message" not in help_text
        ):
            raise OpenClawContractError("openclaw agent --help failed")
        return version.stdout.strip()[:_MAX_ASSISTANT_TEXT_CHARS]
