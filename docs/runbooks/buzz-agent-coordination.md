# Buzz-backed Mission Control coordination

Mission Control remains canonical in GBrain. Buzz is a notification and
coordination transport only; it is not an alternate task database and inbound
free text never mutates a Task.

## Public identity contract

- Public channel: `40145e7c-254d-420d-85c4-c7d7a2cdf08d`
- Local Mission Control maps only the three versioned Agent slugs and public
  pubkeys in `gtasks/buzz_coordination.py`.
- `BUZZ_PRIVATE_KEY`, relay credentials, and auth tags remain in the host
  service environment. Never pass them as command arguments or store them in
  this repository.

## Outbound delivery

Set `MISSION_CONTROL_BUZZ_OUTBOX_DIR` in the dashboard-managed Mission Control
service only after its `buzz` identity environment is installed. The Dispatcher
then writes an owner-only outbox record before invoking:

```text
buzz messages send --channel 40145e7c-254d-420d-85c4-c7d7a2cdf08d \
  --mention <verified-agent-pubkey> --content -
```

The structured JSON is provided through stdin and includes `mc_task`, owner,
agent, state, next action, evidence, needs, canonical event/version, and the
idempotency key. Delivery is complete only with `accepted: true` plus a Buzz
event id. Replays of the same Task/event/version reuse the accepted receipt.
Failed attempts remain `retrying` in the outbox for reconciliation.

For a thread reply, append `--reply-to <verified-event-id>` while retaining the
explicit `--mention`. When private delivery is required, first run
`buzz dms open --pubkey <verified-agent-pubkey>`, verify the returned DM channel,
then use the same `messages send --channel <dm-channel> --mention ... --content -`
contract. Do not infer a recipient from prose or a display name.

## Inbound boundary

`scripts/automation/mission_control_buzz_bridge.py record-inbound` accepts only
the allowlisted intents `progress`, `blocked`, `question`, `ready_for_review`,
and `completed_request` from a verified public key. It writes a private
`coordination_proposal` receipt. A supported Mission Control API/readback flow
must separately validate and apply any canonical Task update; this bridge never
does so. Ownership reassignment and unknown intents fail closed.
