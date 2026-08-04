from __future__ import annotations

import json

from .runtime import (
    CONSUMER_NAME,
    DLQ_STREAM,
    EVENTS_STREAM,
    RuntimeLayout,
)


async def provision(layout: RuntimeLayout | None = None) -> dict[str, object]:
    """Create/update only the bounded GTasks JetStream resources."""
    try:
        import nats
        from nats.js.api import (
            AckPolicy,
            ConsumerConfig,
            DeliverPolicy,
            DiscardPolicy,
            ReplayPolicy,
            RetentionPolicy,
            StorageType,
            StreamConfig,
        )
        from nats.js.errors import NotFoundError
    except ImportError as exc:
        raise RuntimeError("nats-py is required to provision JetStream") from exc

    layout = layout or RuntimeLayout()
    resources = json.loads(layout.resources.read_text(encoding="utf-8"))
    credentials = json.loads(layout.admin_credentials.read_text(encoding="utf-8"))
    nc = await nats.connect(
        servers=[credentials["url"]],
        user=credentials["user"],
        password=credentials["password"],
        inbox_prefix=credentials["inbox_prefix"].encode("ascii"),
        max_reconnect_attempts=0,
        connect_timeout=2,
    )
    js = nc.jetstream(timeout=3)

    async def upsert_stream(config: StreamConfig) -> None:
        try:
            await js.stream_info(config.name)
        except NotFoundError:
            await js.add_stream(config=config)
        else:
            await js.update_stream(config=config)

    events = resources["events_stream"]
    dlq = resources["dead_letter_stream"]
    await upsert_stream(
        StreamConfig(
            name=events["name"],
            description="Durable ingress for versioned GTasks events.",
            subjects=events["subjects"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            discard=DiscardPolicy.OLD,
            max_age=events["max_age_seconds"],
            max_bytes=events["max_bytes"],
            max_msg_size=events["max_message_bytes"],
            duplicate_window=events["duplicate_window_seconds"],
            num_replicas=1,
            deny_delete=True,
            deny_purge=True,
        )
    )
    await upsert_stream(
        StreamConfig(
            name=dlq["name"],
            description="Privacy-safe terminal GTasks event dispositions.",
            subjects=dlq["subjects"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            discard=DiscardPolicy.OLD,
            max_age=dlq["max_age_seconds"],
            max_bytes=dlq["max_bytes"],
            max_msg_size=dlq["max_message_bytes"],
            num_replicas=1,
            deny_delete=True,
            deny_purge=True,
        )
    )
    consumer = resources["consumer"]
    info = await js.add_consumer(
        EVENTS_STREAM,
        config=ConsumerConfig(
            durable_name=CONSUMER_NAME,
            name=CONSUMER_NAME,
            description="Durable single-flight GTasks job event consumer.",
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=consumer["ack_wait_seconds"],
            max_deliver=consumer["max_deliver"],
            backoff=consumer["backoff_seconds"],
            filter_subject=consumer["filter_subject"],
            replay_policy=ReplayPolicy.INSTANT,
            max_ack_pending=consumer["max_ack_pending"],
            max_waiting=1,
            num_replicas=1,
        ),
    )
    event_info = await js.stream_info(EVENTS_STREAM)
    dlq_info = await js.stream_info(DLQ_STREAM)
    await nc.close()
    return {
        "events_stream": event_info.config.name,
        "dead_letter_stream": dlq_info.config.name,
        "consumer": info.name,
        "max_deliver": info.config.max_deliver,
        "ack_wait_seconds": info.config.ack_wait,
        "backoff_seconds": info.config.backoff,
    }
