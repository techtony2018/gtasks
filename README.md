# GTasks

GTasks is a local task-oriented interface for Tony's GBrain. GBrain remains the canonical store: this repository contains application code, tests, and static assets, but no task database, task cache file, or duplicate task ledger.

## Run

Requirements:

- Python 3.12+
- The `gbrain` command configured for Tony's GBrain

Start the local app:

```bash
python3 -m gtasks.server
```

Open [http://127.0.0.1:4179](http://127.0.0.1:4179).

## Versioning and releases

GTasks starts at `V0.0.1`. The canonical current version and complete
user-facing release history live together in `gtasks/releases.json`; runtime
health, the sidebar About control, and the About dialog all read that catalog.

Every user-visible deployment increments only the final patch segment by one:
`V0.0.1` → `V0.0.2` → `V0.0.3`. Release authors do not choose a major or
minor number for normal updates. Run the release command with a dated,
plain-language title and summary; it computes the next patch, appends the
history entry, and moves `current_version` atomically:

```bash
python3 -m gtasks.release \
  --title "Concise user-facing title" \
  --summary "What changed for the user"
```

This is a deliberate release step, not a git hook and not a restart-time bump.
Tests and server startup reject skipped, repeated, major, or minor version
drift.

While the page is open, GTasks performs a read-only refresh every 30 minutes.
The interval is shown beside the sync state, requests are coalesced with manual
Refresh, and a hidden tab defers work until it becomes visible again.

The server binds to `127.0.0.1` by default. To choose another local port:

```bash
python3 -m gtasks.server --port 4180
```

## All Things Codex Dashboard

GTasks is registered with All Things Codex Dashboard at
[http://127.0.0.1:4188](http://127.0.0.1:4188). Its service card can start,
stop, restart, observe, and open the existing GTasks process on port `4179`.

The manager runs this checkout directly from `/Users/tony/work/gtasks`; it
does not vendor another copy of GTasks or introduce a task database. The
machine-readable registration contract is
[`dashboard-integration.json`](dashboard-integration.json).

## Canonical task scope

GTasks reads direct typed memberships from exactly two roots:

- Active lifecycle: `collections/tonys-tasks`
- Completed archive: `collections/tonys-completed-tasks`

Goals are discovered dynamically from direct backlinks to:

- Goals: `collections/tonys-goals`

It does not use a global `type: task` query. GBrain contains other task nodes for unrelated product and automation backlogs; treating all of them as personal tasks would be incorrect.

The app never reads from or writes to the global GBrain `index` node.

## Task creation contract

Quick Add is title-first and creates a real Inbox task only after the user submits the form. Every new task has a due date:

- If the user chooses a date, GTasks preserves it.
- If the user leaves the date blank, GTasks uses the task creation day in the server's local timezone (Tony's local date).

New GTasks task pages use these fields:

```yaml
type: task
status: planned
summary: One-line task summary
detail: ""
priority: normal
next_action: ""
due_day: YYYY-MM-DD
scheduled_day: null
inbox: true
completed_at: null
links:
  - to: collections/tonys-tasks
    type: member_of
```

The domain model also validates these typed relationships:

- `member_of`: lifecycle root or project membership
- `child_of`: parent/child task structure
- `depends_on`: prerequisite task
- `blocked_by`: the task or page creating a block
- `advances_goal`: one task to one validated goal under Tony's Goals

Goal pages use the live GBrain contract: outcome, success criteria, strategy, constraints, status, review cadence, and target day. When a goal target is omitted, the visible system default is the final calendar day of the goal's creation quarter.

## Mutation safety

The server invokes documented GBrain tools through argument arrays, never through shell-interpolated commands:

1. `put_page`
2. `get_page` readback
3. `add_link` for active `member_of`
4. `get_links` readback

A creation is reported as successful only when the task page and membership edge both read back. If the page exists but relationship verification fails, the API returns a `partial_write` result with the exact slug and does not delete, retry, or conceal the partial state.

Changing a task's goal is also explicit and verified. GTasks proves both nodes
are under the approved roots, writes the task-to-goal `advances_goal` edge and
the reciprocal goal-to-task `advanced_by` edge, then verifies both before
removing any prior pair. Clearing the selection removes both directions.
Saving the current goal selection is an idempotent repair action for legacy
one-way links; deployment never performs a bulk relationship migration.

Changing status is explicit and verified too. The detail editor supports
`planned`, `active`, `blocked`, `completed`, and `cancelled`. Existing
`waiting` pages remain readable and display as Blocked, but `waiting` is not
offered as a new workflow status and no bulk normalization occurs.
Completion records Tony's local completion time and keeps the active collection
edge until Tony's Tasks applies its next-Monday archive rule. Reopening an
already archived task moves the same task identity back to the active root.

Loading the app, Board, refreshing, running the test suite, and browser verification are read-only. Only an explicit Quick Add, goal Save, status Save, Next Action Save, or relationship repair mutates GBrain.

## Views

- Today: In Progress (maximum three), Today's Actions, Blocked, Overdue
- Board: Planned, In Progress, Blocked, Completed, Cancelled
- Inbox
- Upcoming
- Blocked
- Projects
- Goals
- Completed

Task rows show title, project, priority, next action, and due date. Board cards
show the same canonical tasks by status. Selecting either opens the shared
detail panel, where status can be changed without leaving GTasks.

The four action sections remain first on Today. A compact goal-progress strip follows them. The Goals view is populated from GBrain at runtime; goal details show target date, review cadence, active/completed linked tasks, and progress. Task details can select a goal, and both detail surfaces navigate across the relationship.

All views support an honest empty state. The app does not generate sample tasks.

## Verify

Run the automated suite:

```bash
python3 -m unittest discover -s tests -v
```

Check Python and browser JavaScript syntax:

```bash
python3 -m compileall -q gtasks
node --check static/app.js
```

The HTTP tests bind an ephemeral `127.0.0.1` port. In a restricted sandbox, run the suite in a context that permits local loopback binding.
