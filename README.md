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
The server also coalesces duplicate initial snapshots for 30 seconds and caps
aggregate GBrain command concurrency to prevent multi-tab request stampedes.
Manual and automatic Refresh explicitly bypass that short cache, and every
verified mutation invalidates it.

Projects are scoped exclusively through the canonical
`collections/tonys-projects` collection. A project appears in GTasks only when
it has a typed `member_of` relationship to that collection; `type: project`
alone, task links, titles, and age never imply ownership. Existing GBrain
projects are not imported. The first explicit New Project action may initialize
the missing Tony's Projects collection, then creates the project and verifies
both page and typed scope membership before reporting success. Merely opening
or refreshing Projects never creates that collection.

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

## Agent profiles and work visibility

GTasks recognizes only the three explicitly approved canonical agent scopes:

- `agents/toddy` with `collections/toddys-tasks`
- `agents/timmy` with `collections/timmys-tasks`
- `agents/tammy` with `collections/tammys-tasks`

Agent profiles are read from `type: agent` GBrain pages. Goal ownership comes
from the single typed agent-to-goal `default_agent_for` edge; Goal detail reads
that edge in reverse without requiring or creating a redundant reciprocal
edge. A profile may later provide an explicit safe `chat_url` and avatar
configuration. Until then, GTasks links to the canonical Memory Stargraph
profile and renders a stable initials placeholder—never an invented photo or
external image.

Today and Board remain Tony-only by default. Board's client-side **Show agent
tasks** preference performs no GBrain mutation. When enabled, GTasks reads only
typed `member_of` backlinks from the three approved agent collections and
shows each valid work item in its canonical status lane with a visible agent
name and avatar placeholder. It never imports those items into Tony's Tasks.
Agent work is currently read-only in GTasks because Tony-task lifecycle
mutation rules cannot safely be applied to an agent collection. Malformed
typed members become Inbox-only Needs Attention warnings and never hide
Tony's tasks.

The Agent Work view is a coordination surface, not another Today list. It
shows profile-to-goal ownership and reserves the future work states Queued,
Working, Waiting for Tony, Blocked, Completed, and Failed. It does not imply
that unapproved work is permitted to execute.

## Agent proposal review

Inbox reads proposals only from typed `member_of
collections/gtasks-proposed-work` backlinks. A valid `type: task_proposal`
page has one typed `proposed_by` link to Toddy, Timmy, or Tammy; a recipient
of `tony` or `agent`; a rationale and concrete next step; and at least one
explicit `serves_goal` or `proposes_for_task` relationship. GTasks never
guesses or fabricates proposals from goals.

The Proposed Tasks section is grouped and filtered by proposing agent. The
recipient is secondary and visible on every card as **Proposed for Tony** or
**Proposed agent work**. Editing moves a proposal into `review` without
creating a task. Approval and rejection are confirmation-bound:

- approval creates exactly one canonical task and adds typed `approved_as`
  readback before reporting success;
- rejection retains the proposal and all linked goal, task, and agent data;
- `proposed`, `review`, `approved`, and `rejected` remain durable audit
  states.

The proposal collection is not created or seeded during application
deployment or verification. Its first canonical producer must create it under
an explicit authorized workflow; an empty or absent scope renders an honest
empty state.

## Task creation contract

Quick Add is title-first and creates a real Inbox task only after the user submits the form. Every new task has a due date:

- If the user chooses a date, GTasks preserves it.
- If the user leaves the date blank, GTasks uses the task creation day in the server's local timezone (Tony's local date).

The separate full Create Task form adds detail, priority, Next Action,
project, goal, and optional progress tracking. A count metric has a
user-facing label, positive target, and current value from zero through the
target. Unmetered tasks are unchanged. A manual metric does not complete a task
merely because its initial current value equals its target.

The first automatic binding is the daily job-application quota. Its canonical
contract is intentionally exact:

```yaml
progress_metric:
  kind: count
  label: Job applications
  unit: job_application
  target: 5
  current: 0
  event_binding: job_applied
  auto_complete: true
  task_day: YYYY-MM-DD
  timezone: America/Los_Angeles
event_progress:
  evidence_slugs: []
  receipt_ids: []
```

`label` is display-only and may be absent on rollout-era pages; the Queue
Reader never uses it for selection. For an event-bound metric, `current`
always equals both unique evidence and receipt counts. GTasks requires
`task_day` to match the task due day. Only the fifth distinct accepted
`job_applied` event completes the task, using the same verified canonical
status mutation as the UI.

Duplicate is a reviewable action in task details. It copies task intent,
priority, Next Action, project, goal, and metric configuration, while resetting
status to Planned, current progress to zero, event evidence and receipts to
empty, and completion time to none. The user reviews and chooses the new due
day before the new GBrain task is created and read back.

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
Board cards provide the same five-status selector as a keyboard and touch
alternative to drag and drop. Dropping or selecting the task's current
canonical status is a silent no-op: it performs no GBrain call, loading state,
readback, or notification. A real change is displayed only from the canonical
task returned by verified post-write readback.
Completion records Tony's local completion time and keeps the active collection
edge until Tony's Tasks applies its next-Monday archive rule. Reopening an
already archived task moves the same task identity back to the active root.

Loading the app, Board, refreshing, opening Create or Duplicate, running the
test suite, and browser verification are read-only. Only an explicit submitted
creation/duplicate, Quick Add, goal Save, status Save, Next Action Save, metric
configuration, or relationship repair mutates GBrain.

## Views

- Today: In Progress (maximum three), Today's Actions, Blocked, Overdue
- Board: Planned, In Progress, Blocked, Completed, Cancelled
- Inbox
- Upcoming
- Blocked
- Projects
- Goals
- Completed

Task rows show title, project, priority, next action, due date, and configured
metric progress such as `Job applications: 3 / 5`. Board cards show the same
canonical tasks by status and progress. Selecting either opens the shared
detail panel, where status and progress context remain visible without leaving
GTasks.

All task, project, goal, and relationship warnings are centralized in Inbox's
Needs Attention area. Other views and detail panels do not repeat them.
Warnings never hide core-valid data. A user may deliberately dismiss one exact
warning fingerprint after confirmation; the preference survives refresh and
service restart, while a meaningfully changed warning receives a new
fingerprint and appears again. Dismissed warnings remain recoverable from
Inbox. These user-scoped presentation preferences are stored separately from
canonical GBrain data at
`~/Library/Application Support/GTasks/warning-dismissals.json` by default
(`GTASKS_WARNING_STATE_FILE` overrides the path for an isolated service or
test).

The Logs control beside About opens a separate, read-only operational history.
It never reads or exposes raw NATS, GBrain, HTTP, or consumer process logs.
GTasks records only fixed, concise application messages, while the Event Queue
Reader supplies its independent schema-version-1 observability snapshot at
`state/gtasks-events/reader-observability.json` and
`http://127.0.0.1:4181/api/observability`. That snapshot contains only
timestamp, fixed component, severity, and a fixed privacy-reviewed message;
raw event envelopes, task/job fields, credentials, tokens, and exception text
are excluded. GTasks rejects queue messages outside the approved fixed set.

The combined `/api/logs` view is newest-first, filtered by severity or
component, paginated in pages of 25 (maximum 50), and bounded to 500 combined
events, drawing from GTasks history and the Queue Reader's atomic 100-event
history. Queue Reader health is
shown when available; an unavailable reader degrades only the Logs status and
never task, project, goal, Board, or mutation paths. Inbox warning dismissal is
a separate presentation preference and does not remove operational history.
GTasks operational history defaults to
`~/Library/Application Support/GTasks/operational-events.jsonl`;
`GTASKS_OPERATION_LOG_FILE` and `GTASKS_QUEUE_LOG_FILE` provide isolated test
overrides.

The four action sections remain first on Today. A compact goal-progress strip follows them. The Goals view is populated from GBrain at runtime; goal details show target date, review cadence, active/completed linked tasks, and progress. Task details can select a goal, and both detail surfaces navigate across the relationship.

Goals can be created from complete user-entered outcome, success criteria,
strategy, constraints, cadence, and optional target date. Pause requires a
confirmation and retains the goal plus every relationship while removing it
from active-goal workflows. Delete requires a separate confirmation: GTasks
removes paired goal relationships from linked task pages without deleting or
changing those tasks, then soft-deletes the goal with GBrain's 72-hour recovery
window. Page, relationship, and deletion readback must verify before success.

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
