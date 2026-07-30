# Task Status Editor and Board Design

## Status mutation

Task detail exposes one labeled status select with the six collection-supported
values: `planned`, `active`, `waiting`, `blocked`, `completed`, and
`cancelled`. Saving is explicit. GTasks reads the exact approved task and its
links, updates the canonical page, reads the page and links back, and refreshes
every view before reporting success.

Completing an active task sets `completed_at` in Tony's local time but keeps
the task attached to `collections/tonys-tasks`, matching that collection's
next-Monday archive rule. Reopening a task that is already in
`collections/tonys-completed-tasks` moves the same identity back to the active
root. No task is copied or deleted.

## Board

Board is a first-class sidebar view after Today. It renders four calm,
status-based columns from the existing canonical task snapshot:

- Planned
- In Progress
- Waiting / Blocked
- Completed / Cancelled

Cards open the same task detail panel. Status changes happen through the
tested editor, not drag-and-drop. The list rows keep their current action-first
content and do not gain another compact control.

## Errors and verification

Invalid statuses are rejected before a write. Any write/readback mismatch is
reported as a partial mutation with the exact task slug and is not silently
retried. Automated adapter and API tests cover status validation, completed
timestamps, lifecycle preservation, archived-task reopening, and readback
failure. Browser verification covers status options, Board navigation, card
detail navigation, refresh behavior, and desktop/mobile layout.
