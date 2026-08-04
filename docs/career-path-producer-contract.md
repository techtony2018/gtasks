# Career Path `job_applied` v1 producer contract

Career Path keeps its current Applied-first boundary:

1. Commit `Applied` in its primary local store.
2. Persist the complete event and stable IDs in its durable local outbox.
3. Attempt one bounded JetStream enqueue.
4. Mark the outbox item enqueued only after a `PubAck` for `GTASKS_EVENTS`.
5. Return the already-successful user operation without waiting for GTasks.

A broker timeout, rejection, unavailable connection, or unknown outcome never
rolls back, blocks, or fails the local Applied operation. Career Path preserves
the outbox row, safe-logs a delayed-sync warning, and retries the identical
`event_id`, `idempotency_key`, and body later. It has no direct GTasks fallback.

## Deployed binding

Read the generated non-secret binding at:

```text
/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-events/career-path-producer-binding.json
```

The tested current values are:

- NATS URL: `nats://127.0.0.1:4222`
- subject: `gtasks.events.job_applied.v1`
- expected PubAck stream: `GTASKS_EVENTS`
- header: `Nats-Msg-Id: <event_id>`
- publish timeout: 2 seconds
- client dependency: `nats-py==2.15.0`
- credential location:
  `/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-events/career-path.credentials.json`

Never copy the password into source, URLs, events, or logs. Read the generated
credential JSON at runtime. Set its `inbox_prefix` on the NATS connection.

## Enqueue-only result

Return one of these transport results to the outbox dispatcher:

| Result | Durable acceptance known? | Outbox action |
| --- | --- | --- |
| `accepted` | Yes; PubAck stream is `GTASKS_EVENTS`. | Mark enqueued. |
| `accepted`, `duplicate: true` | Yes; the identical `event_id` was already stored during the broker dedup window. | Mark enqueued. |
| `rejected` | No. | Keep pending, warn safely, retry the same IDs after configuration or payload correction. |
| `unavailable` | Unknown. | Keep pending, warn safely, retry the same IDs with bounded backoff. |

The result must not contain or imply `processed`, `GBrain updated`, or `quota
incremented`. Queue acceptance is only durable enqueue acceptance.

## Mission Control consumer binding

Mission Control consumes asynchronously and targets exactly
`tasks/562466ac-3569-4013-b105-746a64816cc6`. It never selects a task from a
title, due date, application date, or retry-processing day. The task's saved
target and manual baseline remain canonical; each distinct verified event adds
one receipt/evidence pair and one progress unit only after GBrain readback.
Broker redelivery, process restart, or a replay of the same event identity adds
zero. If the explicit task is unavailable or invalid, the event remains
recoverable and the Queue Reader exposes a fixed privacy-safe warning without
logging job payload fields.

The consumer's durable activity receipts cover verified increments, duplicate
no-ops, retrying and binding failures, and terminal outcomes. Each receipt
keeps only the stable event fingerprint/source identity, explicit task and
scope, progress breakdown, target, timestamp, disposition, and safe error code;
credentials and raw job payloads are never stored there.

## Exact envelope

The strict version-1 envelope and payload fields are:

```json
{
  "event_id": "stable globally unique ID",
  "idempotency_key": "stable logical Applied-operation ID",
  "event_type": "job_applied",
  "schema_version": 1,
  "source": {
    "client_id": "career-path",
    "instance_id": "stable local installation ID"
  },
  "occurred_at": "2026-07-30T09:42:00-07:00",
  "timezone": "America/Los_Angeles",
  "payload": {
    "application_identity": {
      "job_source": "linkedin",
      "job_id": "source job ID"
    },
    "job_snapshot": {
      "title": "Engineering Manager",
      "company": "Example",
      "location": "San Francisco, CA",
      "url": "https://example.com/jobs/42"
    },
    "applied_local_date": "2026-07-30",
    "status_evidence": {
      "status": "applied",
      "committed_at": "2026-07-30T09:41:58-07:00",
      "source": "career-path-local-store"
    }
  }
}
```

Unknown fields, naive timestamps, non-IANA timezones, source/subject mismatch,
invalid URLs, and a local date that disagrees with `committed_at` in the named
timezone are rejected.

## Tested reference binding

`gtasks.event_queue.producer.enqueue_once` is the tested reference adapter. It:

- validates the strict event before connection;
- uses JetStream publish, never Core NATS fire-and-forget;
- sets `Nats-Msg-Id` to `event_id`;
- accepts only a `PubAck` naming `GTASKS_EVENTS`;
- treats duplicate PubAck as accepted;
- converts rejection and timeout into non-throwing enqueue-only results.

Career Path may implement the same transport contract in its own outbox worker;
it does not need to import GTasks or wait for the consumer.

The consumer is an independent Dashboard-managed background service. Its
initialization, connection, binding, receive, processing, or recovery state is
never a producer readiness check and never changes an enqueue-only result.
Career Path waits only for its bounded JetStream `PubAck` attempt. It must not
poll the consumer, GTasks, or GBrain before completing its already-committed
Applied operation.
