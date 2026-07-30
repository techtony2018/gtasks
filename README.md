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

The server binds to `127.0.0.1` by default. To choose another local port:

```bash
python3 -m gtasks.server --port 4180
```

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

Changing a task's goal is also explicit and verified. GTasks proves both nodes are under the approved roots, writes an `advances_goal` edge, verifies it, removes the prior goal edge when needed, and performs final readback. Clearing the selection removes only `advances_goal`.

Loading the app, refreshing, running the test suite, and browser verification are read-only. Only an explicit Quick Add submit mutates GBrain.

## Views

- Today: In Progress (maximum three), Today's Actions, Waiting and Blocked, Overdue
- Inbox
- Upcoming
- Blocked
- Projects
- Goals
- Completed

Task rows show title, project, priority, next action, and due date. Selecting a row opens its detail panel and links to the same GBrain slug in Memory Stargraph.

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
