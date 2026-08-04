from __future__ import annotations

import json
from datetime import datetime, timezone

from .runtime import EVENTS_STREAM, RuntimeLayout
from .store import EventStore


async def redrive(
    event_id: str,
    *,
    layout: RuntimeLayout | None = None,
    confirmed: bool,
) -> dict[str, object]:
    if not confirmed:
        raise ValueError("redrive requires explicit confirmation")
    try:
        import nats
    except ImportError as exc:
        raise RuntimeError("nats-py is required to redrive") from exc
    layout = layout or RuntimeLayout()
    store = EventStore(layout.receipts)
    try:
        terminal = store.get_terminal(event_id)
    finally:
        store.close()
    if terminal is None:
        raise ValueError("terminal event was not found")
    credentials = json.loads(layout.admin_credentials.read_text(encoding="utf-8"))
    nc = await nats.connect(
        credentials["url"],
        user=credentials["user"],
        password=credentials["password"],
        inbox_prefix=credentials["inbox_prefix"].encode("ascii"),
        max_reconnect_attempts=0,
    )
    js = nc.jetstream(timeout=3)
    original = await js.get_msg(
        EVENTS_STREAM,
        seq=int(terminal["original_stream_sequence"]),
    )
    raw = json.loads(original.data)
    if raw.get("event_id") != event_id:
        await nc.close()
        raise RuntimeError("terminal record does not match original stream event")
    redrive_message_id = (
        f"redrive:{event_id}:{terminal['attempts']}:"
        f"{terminal['original_stream_sequence']}"
    )
    try:
        ack = await js.publish(
            original.subject,
            original.data,
            headers={"Nats-Msg-Id": redrive_message_id},
            timeout=3,
        )
    finally:
        await nc.close()
    if ack.stream != EVENTS_STREAM:
        raise RuntimeError("redrive PubAck stream mismatch")
    store = EventStore(layout.receipts)
    try:
        # Keep the terminal receipt durable until the broker has accepted a
        # redelivery. A crash before this transaction leaves the original
        # terminal visible and safely retryable by the operator.
        store.reopen_terminal(event_id, now=datetime.now(timezone.utc))
        store.release(event_id, now=datetime.now(timezone.utc))
    finally:
        store.close()
    return {
        "event_id": event_id,
        "idempotency_key": raw.get("idempotency_key"),
        "stream": ack.stream,
        "stream_sequence": ack.seq,
        "duplicate": ack.duplicate,
    }
