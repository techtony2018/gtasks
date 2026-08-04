from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Protocol

from .contract import ContractError, parse_event


class PublishUnavailable(RuntimeError):
    pass


class PublishRejected(RuntimeError):
    pass


class EnqueueStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    status: EnqueueStatus
    event_id: str
    duplicate: bool = False
    stream_sequence: int | None = None
    error_code: str | None = None
    retry_same_ids: bool = False

    def safe_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "event_id": self.event_id,
            "duplicate": self.duplicate,
            "stream_sequence": self.stream_sequence,
            "error_code": self.error_code,
            "retry_same_ids": self.retry_same_ids,
        }


class Publisher(Protocol):
    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> object: ...


def _read_binding(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishRejected("binding_unavailable") from exc
    required = {
        "version",
        "broker_url",
        "subject",
        "stream",
        "credential_file",
        "acceptance",
        "message_id_header",
        "publish_timeout_seconds",
        "result_semantics",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("version") != 1
        or value.get("subject") != "gtasks.events.job_applied.v1"
        or value.get("stream") != "GTASKS_EVENTS"
        or value.get("acceptance") != "jetstream_puback"
        or value.get("message_id_header") != "Nats-Msg-Id"
        or value.get("result_semantics") != "nonblocking_enqueue_only"
    ):
        raise PublishRejected("binding_invalid")
    return value


class NatsJetStreamPublisher:
    def __init__(self, binding: Mapping[str, Any]) -> None:
        self.binding = binding

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> object:
        try:
            from nats.aio.client import Client as NATS
            from nats.errors import (
                AuthorizationError,
                Error as NatsError,
                NoServersError,
                TimeoutError as NatsTimeoutError,
            )
            from nats.js.errors import APIError
        except ImportError as exc:
            raise PublishUnavailable("nats_client_unavailable") from exc
        try:
            credentials = json.loads(
                Path(self.binding["credential_file"]).read_text(encoding="utf-8")
            )

            async def ignore_safe_client_error(_error: Exception) -> None:
                return

            nc = NATS()
            try:
                await nc.connect(
                    servers=[self.binding["broker_url"]],
                    user=credentials["user"],
                    password=credentials["password"],
                    inbox_prefix=credentials["inbox_prefix"].encode("ascii"),
                    connect_timeout=timeout,
                    allow_reconnect=False,
                    max_reconnect_attempts=0,
                    error_cb=ignore_safe_client_error,
                )
            except Exception:
                await nc.close()
                raise
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise PublishRejected("credential_invalid") from exc
        except AuthorizationError as exc:
            raise PublishRejected("authorization_rejected") from exc
        except NatsError as exc:
            if "Authorization Violation" in str(exc):
                raise PublishRejected("authorization_rejected") from exc
            raise PublishUnavailable("broker_connection_failed") from exc
        except (NoServersError, NatsTimeoutError, TimeoutError) as exc:
            raise PublishUnavailable("broker_unavailable") from exc
        try:
            return await nc.jetstream(timeout=timeout).publish(
                subject,
                payload,
                headers=headers,
                timeout=timeout,
            )
        except AuthorizationError as exc:
            raise PublishRejected("authorization_rejected") from exc
        except APIError as exc:
            if exc.err_code in {10014, 10039} or exc.code in {400, 403}:
                raise PublishRejected("broker_rejected") from exc
            raise PublishUnavailable("broker_api_unavailable") from exc
        except (NatsTimeoutError, TimeoutError) as exc:
            raise PublishUnavailable("publish_timeout") from exc
        finally:
            await nc.close()


async def enqueue_once(
    envelope: Mapping[str, Any],
    *,
    binding_path: Path,
    publisher: Publisher | None = None,
) -> EnqueueResult:
    event_id = (
        envelope.get("event_id")
        if isinstance(envelope.get("event_id"), str)
        else "invalid"
    )
    try:
        binding = _read_binding(binding_path)
        payload = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        event = parse_event(payload, binding["subject"])
    except (ContractError, PublishRejected):
        return EnqueueResult(
            status=EnqueueStatus.REJECTED,
            event_id=event_id,
            error_code="invalid_event_or_binding",
            retry_same_ids=True,
        )
    client = publisher or NatsJetStreamPublisher(binding)
    try:
        ack = await client.publish(
            binding["subject"],
            payload,
            headers={binding["message_id_header"]: event.event_id},
            timeout=float(binding["publish_timeout_seconds"]),
        )
    except PublishRejected:
        return EnqueueResult(
            status=EnqueueStatus.REJECTED,
            event_id=event.event_id,
            error_code="publish_rejected",
            retry_same_ids=True,
        )
    except PublishUnavailable:
        return EnqueueResult(
            status=EnqueueStatus.UNAVAILABLE,
            event_id=event.event_id,
            error_code="publish_unavailable",
            retry_same_ids=True,
        )
    if getattr(ack, "stream", None) != binding["stream"]:
        return EnqueueResult(
            status=EnqueueStatus.UNAVAILABLE,
            event_id=event.event_id,
            error_code="puback_stream_mismatch",
            retry_same_ids=True,
        )
    return EnqueueResult(
        status=EnqueueStatus.ACCEPTED,
        event_id=event.event_id,
        duplicate=bool(getattr(ack, "duplicate", False)),
        stream_sequence=int(getattr(ack, "seq")),
        retry_same_ids=False,
    )
