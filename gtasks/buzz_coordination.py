"""Privacy-safe Buzz coordination receipts for Mission Control Agent handoffs.

This module deliberately stops at a durable coordination outbox and inbound
proposal boundary.  Buzz messages never directly mutate canonical GBrain data.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from typing import Any, Callable, Mapping, Sequence


BUZZ_COORDINATION_CHANNEL = "40145e7c-254d-420d-85c4-c7d7a2cdf08d"
AGENT_BUZZ_IDENTITIES = {
    "agents/tammy": "3ad96d9f8a1ddb233905ac86f582d47006dabbf248f27264d5b041f50d5eb827",
    "agents/timmy": "64f1c766c8fbb16391f7cc27efc0ea0b807a4a842e64c99259ccc16bc30c3dda",
    "agents/toddy": "066a89e9f7bccff197c5ca2156284e3fe069fc41689021bbc0e2cc8aac042f8e",
}
INBOUND_INTENTS = frozenset(
    {"progress", "blocked", "question", "ready_for_review", "completed_request"}
)
_SLUG = re.compile(r"(?:tasks|events|receipts)/[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_TEXT_LIMIT = 600


class BuzzDeliveryError(RuntimeError):
    """Buzz did not return a positive durable acceptance receipt."""


def _bounded_text(value: object, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be bounded text")
    rendered = value.strip()
    if required and not rendered:
        raise ValueError(f"{field} must be bounded text")
    if len(rendered) > _TEXT_LIMIT or "\x00" in rendered:
        raise ValueError(f"{field} must be bounded text")
    return rendered


def _structured_slug(value: object, field: str) -> str:
    if not isinstance(value, str) or _SLUG.fullmatch(value) is None:
        raise ValueError(f"{field} must be one canonical reference")
    return value


@dataclass(frozen=True, slots=True)
class BuzzCoordinationMessage:
    task_slug: str
    canonical_event_id: str
    canonical_version: str
    owner: str
    agent: str
    state: str
    next_action: str
    evidence: tuple[str, ...]
    needs: str

    def __post_init__(self) -> None:
        _structured_slug(self.task_slug, "mc_task")
        _structured_slug(self.canonical_event_id, "canonical_event_id")
        if not isinstance(self.canonical_version, str) or _VERSION.fullmatch(self.canonical_version) is None:
            raise ValueError("canonical_version must be bounded")
        if self.owner not in AGENT_BUZZ_IDENTITIES or self.agent != self.owner:
            raise ValueError("agent must use one verified Buzz identity")
        _bounded_text(self.state, "state")
        _bounded_text(self.next_action, "next_action")
        _bounded_text(self.needs, "needs", required=False)
        if not isinstance(self.evidence, tuple) or len(self.evidence) > 20:
            raise ValueError("evidence must be a bounded tuple")
        for receipt in self.evidence:
            _structured_slug(receipt, "evidence")

    @property
    def idempotency_key(self) -> str:
        source = f"{self.task_slug}\0{self.canonical_event_id}\0{self.canonical_version}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mc_task": self.task_slug,
            "owner": self.owner,
            "agent": self.agent,
            "state": self.state,
            "next_action": self.next_action,
            "evidence": list(self.evidence),
            "needs": self.needs,
            "canonical_event_id": self.canonical_event_id,
            "canonical_version": self.canonical_version,
            "idempotency_key": self.idempotency_key,
        }


BuzzSender = Callable[[Sequence[str]], Mapping[str, Any]]


def _default_sender(command: Sequence[str], *, input_text: str) -> Mapping[str, Any]:
    completed = subprocess.run(
        list(command),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise BuzzDeliveryError("Buzz coordination delivery failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BuzzDeliveryError("Buzz coordination receipt was invalid") from exc
    if not isinstance(result, Mapping):
        raise BuzzDeliveryError("Buzz coordination receipt was invalid")
    return result


class BuzzCoordinationOutbox:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def deliver(
        self,
        message: BuzzCoordinationMessage,
        *,
        sender: Callable[..., Mapping[str, Any]] = _default_sender,
        reply_to: str | None = None,
        direct: bool = False,
    ) -> dict[str, Any]:
        if direct and reply_to is not None:
            raise ValueError("Buzz delivery cannot be both a DM and a thread reply")
        if reply_to is not None:
            reply_to = _bounded_text(reply_to, "reply_to")
        key = message.idempotency_key
        path = self._path(key)
        with self._lock:
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("delivery_status") == "accepted":
                    return existing
            else:
                existing = {
                    **message.to_dict(),
                    "delivery_status": "pending",
                    "attempt": 0,
                    "buzz_event_id": None,
                }
                self._atomic_write(path, existing)

            channel = BUZZ_COORDINATION_CHANNEL
            if direct:
                dm_receipt = sender(
                    [
                        "buzz", "dms", "open", "--pubkey",
                        AGENT_BUZZ_IDENTITIES[message.agent],
                    ],
                    input_text="",
                )
                channel_value = dm_receipt.get("channel_id") if isinstance(dm_receipt, Mapping) else None
                if not isinstance(channel_value, str) or not channel_value.strip():
                    raise BuzzDeliveryError("Buzz DM open did not return a verified channel")
                channel = channel_value.strip()
            command = [
                "buzz", "messages", "send",
                "--channel", channel,
                "--mention", AGENT_BUZZ_IDENTITIES[message.agent],
            ]
            if reply_to is not None:
                command.extend(["--reply-to", reply_to])
            command.extend(["--content", "-"])
            body = json.dumps(message.to_dict(), sort_keys=True, separators=(",", ":"))
            try:
                receipt = sender(command, input_text=body)
            except Exception as exc:
                failed = {**existing, "delivery_status": "retrying", "attempt": int(existing.get("attempt", 0)) + 1, "last_error": "Buzz coordination delivery failed"}
                self._atomic_write(path, failed)
                if isinstance(exc, BuzzDeliveryError):
                    raise
                raise BuzzDeliveryError("Buzz coordination delivery failed") from exc
            event_id = receipt.get("event_id") if isinstance(receipt, Mapping) else None
            if receipt.get("accepted") is not True or not isinstance(event_id, str) or not event_id.strip():
                failed = {**existing, "delivery_status": "retrying", "attempt": int(existing.get("attempt", 0)) + 1, "last_error": "Buzz did not accept the coordination event"}
                self._atomic_write(path, failed)
                raise BuzzDeliveryError("Buzz did not return accepted: true")
            accepted = {
                **existing,
                "delivery_status": "accepted",
                "attempt": int(existing.get("attempt", 0)) + 1,
                "buzz_event_id": event_id.strip(),
            }
            self._atomic_write(path, accepted)
            return accepted


def classify_inbound_coordination(
    *, sender_pubkey: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    reverse = {pubkey: agent for agent, pubkey in AGENT_BUZZ_IDENTITIES.items()}
    agent = reverse.get(sender_pubkey)
    if agent is None:
        raise ValueError("sender is not a verified Buzz identity")
    intent = payload.get("intent")
    if intent not in INBOUND_INTENTS:
        raise ValueError("coordination intent is not allowlisted")
    task_slug = _structured_slug(payload.get("mc_task"), "mc_task")
    state = _bounded_text(payload.get("state", intent), "state")
    next_action = _bounded_text(payload.get("next_action", ""), "next_action", required=False)
    needs = _bounded_text(payload.get("needs", ""), "needs", required=False)
    raw_evidence = payload.get("evidence", [])
    if not isinstance(raw_evidence, list) or len(raw_evidence) > 20:
        raise ValueError("evidence must be a bounded list")
    evidence = [_structured_slug(item, "evidence") for item in raw_evidence]
    return {
        "record_kind": "coordination_proposal",
        "agent": agent,
        "intent": intent,
        "mc_task": task_slug,
        "state": state,
        "next_action": next_action,
        "evidence": evidence,
        "needs": needs,
        "canonical_mutation_authorized": False,
    }


def build_handoff_coordination_sink(
    outbox: BuzzCoordinationOutbox,
    *,
    sender: Callable[..., Mapping[str, Any]] = _default_sender,
) -> Callable[[Any, Any], None]:
    """Adapt verified Dispatcher changes to the structured Buzz outbox."""

    def notify(change: Any, record: Any) -> None:
        if len(change.assigned_to) != 1 or change.assigned_to[0] not in AGENT_BUZZ_IDENTITIES:
            raise ValueError("handoff recipient is not a verified Buzz identity")
        outbox.deliver(
            BuzzCoordinationMessage(
                task_slug=change.task_slug,
                canonical_event_id=change.canonical_event_id,
                canonical_version=change.canonical_version,
                owner=change.assigned_to[0],
                agent=change.assigned_to[0],
                state=record.status,
                next_action=change.summary,
                evidence=(change.canonical_event_id,),
                needs=change.blocker or "",
            ),
            sender=sender,
        )

    return notify
