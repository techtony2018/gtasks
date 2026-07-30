# Board Drag-and-Drop and Reciprocal Goal Links Design

## Goal

Make Board status changes direct and safe while making every task-goal
relationship explicitly navigable in both directions. GBrain remains the
canonical store; GTasks does not add local task or relationship state.

## Status model

GBrain continues to accept legacy task pages whose status is `waiting`, but
GTasks exposes five workflow statuses:

1. Planned (`planned`)
2. In Progress (`active`)
3. Blocked (`blocked`)
4. Completed (`completed`)
5. Cancelled (`cancelled`)

The Board has one independent lane for each status in that order. Cancelled is
always the final lane. A legacy `waiting` task renders in Blocked and is not
changed merely by loading the app. Its next explicit status Save or Board drop
writes one of the five current statuses; choosing Blocked normalizes it to
canonical `blocked`.

Today keeps its action-first layout but labels the legacy
`waiting_and_blocked` projection as Blocked. The internal projection name can
remain for data compatibility.

## Drag-and-drop interaction

Each Board lane is a drop target with exactly one canonical destination
status. Cards use native desktop drag events and remain in their source lane
while the GBrain request is pending. After verified write and readback, GTasks
reloads the canonical snapshot and the card appears in its new lane.

If a write or readback fails, the card remains where it was, a Board-level
alert names the failed destination, and a Retry button repeats the same
explicit status request. No local move is treated as canonical.

Task cards remain buttons. Clicking or tapping opens the shared detail panel.
The five-option status selector is the keyboard and touch fallback and uses
the same API and refresh path as drag-and-drop. Native drag-and-drop is not
required on mobile.

## Reciprocal goal relationships

The relationship pair is:

- task to goal: `advances_goal`
- goal to task: `advanced_by`

The active `gbrain-base-v2` schema pack does not enumerate task/goal link
verbs, and the GBrain typed-link API accepts relationship verbs without a
schema mutation. `advanced_by` is therefore a compatible explicit inverse of
the existing `advances_goal`.

Linking, changing, or clearing a task goal updates both edges. GTasks adds and
verifies a new pair before removing an old pair. If a later operation fails,
it attempts a compensating rollback to the pre-mutation edge set and reports
whether rollback readback was verified. Final success requires exact readback
from both the task and goal.

Goal reads load outgoing `advanced_by` edges. Goal detail uses those explicit
edges as its primary task list, then unions legacy tasks whose outgoing
`advances_goal` edge points to the goal. Legacy one-way tasks are marked as
needing reconciliation rather than hidden.

Saving an unchanged goal selection is the idempotent reconciliation action:
GTasks adds a missing `advanced_by` edge and verifies both sides. The goal
detail explains that opening a legacy-linked task and saving its current goal
repairs that pair. Deployment and verification do not bulk-mutate live data.

## Error handling

Status and relationship mutations return a verified receipt only after
canonical readback. Partial writes return the exact task slug, a stable error
code, and rollback verification state when applicable. The UI never retries
automatically. Board Retry and goal Save are explicit user actions.

## Verification

Automated fixtures cover:

- all five current UI statuses and legacy `waiting` parsing;
- direct status destination mapping and retry state;
- completion timestamps and lifecycle behavior through Board requests;
- creation, replacement, clearing, reconciliation, and rollback of both goal
  edges;
- goal progress from explicit reciprocal edges with legacy one-way fallback.

Browser verification uses the dashboard-managed GTasks runtime at
`http://127.0.0.1:4179`. Desktop verifies the five lanes, a safe same-status
drop, and task navigation. Mobile verifies the five-lane scroll layout and
the touch status-selector fallback. No verification step changes Tony's live
task or goal data.

## Performance diagnosis boundary

The performance investigation is reported separately. It found that the
current `/api/tasks` read performs 15 sequential GBrain CLI calls for three
task memberships and six goals, while static rendering is fast. This feature
does not change production query concurrency, caching, or GBrain routing.
