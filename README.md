# GTasks

GTasks is a local task-oriented interface for Tony's GBrain. GBrain remains the canonical store: this repository contains application code, tests, and static assets, but no task database or duplicate task ledger. A private local last-valid read projection may cache already-verified API output solely to keep the interface usable during a slow GBrain refresh; it never accepts writes or becomes canonical state.

## Run

Requirements:

- Python 3.12+
- The `gbrain` command configured for Tony's GBrain

Start the local app:

```bash
python3 -m gtasks.server
```

Open [http://127.0.0.1:4179](http://127.0.0.1:4179).

## Canonical system documentation

Mission Control Agents share one read-only documentation root instead of
copying system instructions into identity-specific pages:

- Collection: [`collections/mission-control-documentation`](http://127.0.0.1:8788/?slug=collections%2Fmission-control-documentation)
- System Overview: [`docs/f2516aa8-89ae-4570-a205-118d5c038ad7`](http://127.0.0.1:8788/?slug=docs%2Ff2516aa8-89ae-4570-a205-118d5c038ad7)

The Overview is a reference, not execution authority. Its shared registration
is version controlled in
[`config/agent-artifact-protocol/shared-documentation.json`](config/agent-artifact-protocol/shared-documentation.json),
and the post-release maintenance and verification procedure is
[`docs/runbooks/mission-control-system-documentation.md`](docs/runbooks/mission-control-system-documentation.md).

## Visual identity

Mission Control uses a distinct North Star and Big Dipper mark within the
Memory Stargraph visual family. The interface follows the same deep-space HUD
language with compact panels, cyan instrumentation, high-contrast text, and a
dark-only canvas. Production assets live in `static/assets`; editable logo,
favicon, artwork sources, guidance, and review previews live in
`design/mission-control-brand`.

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

Latest verified and deployed release: V0.0.87. It gives local Dispatcher
authority mutations a bounded 60-second request budget while retaining
fail-closed acknowledgement ordering, idempotent retries, and canonical
registration/delegation readback.

V0.0.88 is an uncommitted release candidate for unified Task and System Ticket
Markdown. Its catalog entry reserves the next sequential version but is not
evidence of a shipment: independent UI/UX QA, commit/push, dashboard-managed
deployment, runtime health, and bounded canonical readback remain required.

### Independent UI/UX release gate

For every UI-affecting Mission Control change, independent UI/UX QA is a
required pre-commit gate. QA verifies the frozen uncommitted candidate through
the dashboard-managed service at desktop and a genuine 390px-wide mobile
viewport. Restarting that managed process from the checkout is a candidate
gate, not a release deployment. Only a documented QA **PASS** authorizes the
commit. A QA **FAIL** or **INCONCLUSIVE** result requires repair and another
independent retest before any commit; developer self-checks are not a
substitute. The resulting commit must reference the corresponding QA evidence,
be pushed, and then be deployed through the dashboard-managed service with a
clean tracked checkout. This rule also applies to System Ticket
nightly-automation UI work.

### Unified Task and System Ticket Markdown

New Tasks and System Tickets use the shared `gtasks.markdown_policy` formatter,
not prompt-specific Markdown construction. A verified System Ticket reference
uses exactly `#system-ticket/tasks%2F<uuid>` so it opens the Ticket inside
Mission Control; it is never replaced with a Memory Stargraph link. The
formatter only emits verified canonical references, leaves unavailable
references as plain text, and rejects unsafe URL schemes. It applies only to
new writes: historical bodies are neither bulk-migrated nor silently rewritten.
Exact Task reads and System Ticket payloads expose the verified canonical body
only as an optional display projection when the page carries the durable
`markdown_contract: unified-task-ticket-v1` marker and the body exactly
rerenders from current canonical fields and verified Ticket references.
Structured fields and graph edges remain authoritative; marked content edits
rerender and verify the body, while older unmarked pages preserve and display
their existing detail/field fallback.

The [`Task and Ticket Markdown runbook`](docs/runbooks/task-ticket-markdown.md)
defines the templates, canonical readback, active `mc-add-task` skill sync, and
verification gates. The repository skill and helper are updated, but syncing
them to `/Users/tony/.codex/skills/mc-add-task` is currently **PENDING/BLOCKED**:
the attempted `ditto` write returned `Operation not permitted`. Do not report
the active skill as synchronized until both installed-file hashes match the
repository source.

While the page is open, GTasks performs a read-only refresh every 30 minutes.
The interval is shown beside the sync state, requests are coalesced with manual
Refresh, and a hidden tab defers work until it becomes visible again.
The server also coalesces duplicate task and proposal reads, caps aggregate
GBrain command concurrency to prevent multi-tab request stampedes, and stores
the last verified projections in a private `0600` local file. Slow refreshes
run in the background: each surface shows its own explicit refreshing, stale,
or error state while independently available data remains usable. Manual and
automatic Refresh explicitly invalidate the relevant projection; verified
mutations invalidate task and proposal projections together.

Independent UI QA fixtures must never be created in Tony's Tasks or an Agent
work root. Their explicit contract is one typed `member_of` relationship to
`collections/mission-control-qa-fixtures`, `qa_fixture: true`, a non-empty
`qa_owner`, and an optional `qa_release`. Mission Control rejects QA metadata
in any personal or Agent scope and rejects unmarked records in the QA scope;
it never hides a personal task based on title or prose.

Projects are scoped exclusively through the canonical
`collections/tonys-projects` collection. A project appears in GTasks only when
it has a typed `member_of` relationship to that collection; `type: project`
alone, task links, titles, and age never imply ownership. Existing GBrain
projects are not imported. The first explicit New Project action may initialize
the missing Tony's Projects collection, then creates the project and verifies
both page and typed scope membership before reporting success. Merely opening
or refreshing Projects never creates that collection. Every Project card uses
a native keyboard-selectable control to open the shared right sidebar. The
sidebar shows canonical summary Markdown, status, supporting Goals, assigned
Tasks, timestamps, and slug; Edit preserves the same project identity and
relationships and reports success only after verified canonical readback.

The server binds to `127.0.0.1` by default. To choose another local port:

```bash
python3 -m gtasks.server --port 4180
```

## Calendar overlay privacy

Calendar events are a separate, read-only local overlay in Mission Control's
Calendar view. They are never copied to GBrain and cannot be treated as tasks.
Apple EventKit requires **Full Access to Calendar** for an app to read events;
Mission Control explains this before it asks macOS, then lets Tony choose which
calendar identifiers are included. It never calls an EventKit write or delete
API. The selection is stored only in
`~/Library/Application Support/Mission Control/calendar-preferences.json`.
The picker keeps its compact dialog open while saving, reads the selected
identifiers back before reporting success, and leaves an in-context Calendar
confirmation after the dialog closes. A failed save/readback remains in the
dialog with an actionable error and does not claim that the event filter
changed.

The dashboard service invokes the branded `Mission Control Calendar.app`
helper. Build or refresh that local helper after source deployment:

```bash
./scripts/build-mission-control-calendar-helper.sh
```

If the helper is absent or Calendar permission is unavailable, task views and
GBrain remain fully usable and the overlay reports an honest unavailable state.

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

GTasks recognizes six explicitly approved canonical Agent scopes. The existing
Codex identities are:

- `agents/toddy` with `collections/toddys-tasks`
- `agents/timmy` with `collections/timmys-tasks`
- `agents/tammy` with `collections/tammys-tasks`

The independent OpenClaw identities are:

- `agents/tammy-oc` with `collections/tammy-oc-tasks`
- `agents/timmy-oc` with `collections/timmy-oc-tasks`
- `agents/toddy-oc` with `collections/toddy-oc-tasks`

OpenClaw identities start with no default Goal and may later receive their own
Goals and owned tasks. Each host uses a two-worker supervisor: one isolated
Codex worker and one isolated OpenClaw worker. Every OpenClaw worker resumes
one pre-authorized fixed session and never creates, replaces, forks, or guesses
a session. Private credentials and fixed-session identifiers stay under
`~/Library/Application Support/GTasks/handoff-dispatcher`; they are not stored
in Git or rendered by Mission Control.

Tony may explicitly authorize a time-bounded delegation from a Codex Agent to
its paired OpenClaw Agent. The window is selectable from 15 minutes through 7
days, stored in UTC, and displayed in `America/Los_Angeles`. Permanent
`assigned_to` ownership does not change, owned work always outranks delegated
work, and expiry, completion, or revocation hands any unfinished task back to
the permanent owner. See
[`docs/runbooks/openclaw-agent-delegation.md`](docs/runbooks/openclaw-agent-delegation.md)
for dry-run, canary, rollback, and recovery gates.

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
Blocked Agent work is always visible in Today's Blocked section and the
dedicated Blocked view, independent of the Board preference, so work awaiting
an unblock cannot disappear from Tony's action surfaces.
Agent status changes use the same write/readback rules as Tony tasks while
preserving the agent collection and exact assignment. Malformed typed members
become Inbox-only Needs Attention warnings and never hide Tony's tasks.

The Agent Work view is a coordination surface, not another Today list. It
shows profile-to-goal ownership and keeps work in the standard task states
Planned, Active, Blocked, Completed, and Cancelled. Waiting for Tony is a
specific Blocked condition, never a separate status. It does not imply
that unapproved work is permitted to execute.

### Agent question and answer handoff

When an Agent needs information, Mission Control keeps the same canonical task
and changes its task status to `blocked`. The Agent creates one canonical
question TODO, adds the typed `blocked_by: people/tony-guan` relationship, and
records a structured `handoff` projection containing the exact question,
assigned Agent, resume action, timestamps, and question round. Mission Control
never uses `waiting` as a task status.

Tony answers with the single **Answer and Hand Back** action. That verified
operation appends the immutable answer comment, completes the question TODO,
removes only Tony's matching blocker, and returns the same task to `active`
with its explicit Agent resume action. If another blocker remains, the task
stays `blocked`. The assigned Agent acknowledges the handoff before resuming;
an insufficient answer produces one precise follow-up question on the same
task and increments the handoff round.

A verified answer is eligible immediately. During the daytime schedule, each
assigned Agent checks for the oldest unacknowledged handoff before selecting
other work at its next hourly heartbeat, so review occurs within at most 60
minutes. Answers outside the 09:00–19:00 America/Los_Angeles schedule are
reviewed at the next daytime heartbeat unless Tony separately authorizes an
urgent wake. The heartbeat always continues in the Agent's existing fixed
Codex task; it never creates a new task for a question or handoff.

### Event-driven Agent Dispatcher

Verified actionable task changes are recorded idempotently in the durable
handoff outbox only after exact canonical mutation readback. One private local
Dispatcher per host claims only its registered Agent identity and resumes only
that Agent's already-approved fixed Codex thread. It never creates, forks,
replaces, or guesses a thread.

Task Timeline and Agents Handoff History are read-only projections over the same
append-only handoff event table. They share ordering, totals, filters,
correlation, pagination, retention/export metadata, and privacy-safe
`registration_ref` evidence; neither view mutates or repairs GBrain.

The central runtime paths, exact local install/resume contracts, redaction and
retention rules, retry/dead-letter recovery, Guardian boundary, rollback, and
three-host/Tammy-only canary sequence are documented in
[`docs/runbooks/agent-handoff-dispatcher.md`](docs/runbooks/agent-handoff-dispatcher.md).

### Agent Artifact publication

Durable Agent deliverables live only in canonical GBrain Artifact pages under
`collections/mission-control-artifacts` and exactly one producing-Agent child
collection. Authenticated `POST /api/artifacts` is the schema-enforcing write
boundary: it derives the executing Agent from a private local credential,
requires `created_by` and collection to match that identity, stores an
idempotency key, writes the
page plus typed `member_of`, `created_by`, and `produced_for` relationships,
and reports success only after exact page/link readback. `GET /api/artifacts`
and `GET /api/artifacts/<encoded-slug>` are read-only browsing boundaries.

Mission Control does not maintain an Artifact database, file index, or sync
service. Agents must fail closed when the authenticated publication boundary
is unavailable; raw GBrain writes are not an identity-safe substitute.
Attachments must already be verified GBrain-served
`/media/...` references; source code remains in Git and is linked by an HTTPS
commit URL. Secrets, credentials, browser profiles, raw logs, routine scans,
and unchanged status messages are not Artifacts.

The version-controlled automation source is
`config/agent-artifact-protocol`. It provides a generic parameterized identity
template, one isolated instance per Agent, daytime/nighttime sources, and
checked-in rendered prompts. Installed `~/.codex/automations/*/automation.toml`
files are readback state only. `scripts/verify_agent_artifact_protocol.py`
renders, detects drift, and emits validated inputs for the supported Codex
automation update tool without overwriting installed TOML or committing
schedules, target task IDs, hosts, credentials, or other private runtime data.
`scripts/provision_artifact_publisher_credentials.py` atomically provisions
only unique token hashes into the dashboard's private `0600` runtime state;
plaintext publisher tokens remain outside the repository and prompts.

## Agent proposal review

Inbox reads proposals only from typed `member_of
collections/gtasks-proposed-work` backlinks. A valid `type: task_proposal`
page has one typed `proposed_by` link to Toddy, Timmy, or Tammy; a recipient
of `tony` or `agent`; a rationale and concrete next step; and at least one
explicit `serves_goal` or `proposes_for_task` relationship. GTasks never
guesses or fabricates proposals from goals.

The Proposed Tasks section is grouped and filtered by proposing agent. Pending
review and recent decisions remain separately visible, and every decided row
opens a task-detail timeline showing the canonical decision event, actor,
timestamp, note, previous state, and resulting state. The recipient is
secondary and visible on every card as **Proposed for Tony** or **Proposed
agent work**. Editing moves a legacy proposal record into `review` without
creating a task. Approval and rejection are confirmation-bound:

- a canonical proposed task is decided in place with one idempotent decision
  event and exact lifecycle/readback verification;
- a legacy `task_proposal` approval creates exactly one canonical task and adds
  typed `approved_as` readback before reporting success;
- rejection retains the proposal and all linked goal, task, and agent data;
- `proposed`, `review`, `approved`, and `rejected` remain durable audit
  states.

The proposal collection is not created or seeded during application
deployment or verification. Its first canonical producer must create it under
an explicit authorized workflow; an empty or absent scope renders an honest
empty state.

## Task creation contract

Create Task is the sole visible task-creation flow. The same full dialog opens
from the sidebar, the top of Today, and the top of Inbox. Every new task has a
due date:

- If the user chooses a date, GTasks preserves it.
- If the user leaves the date blank, GTasks uses the task creation day in the server's local timezone (Tony's local date).

The full Create Task form adds detail, priority, an optional initial TODO,
project, goal, and optional progress tracking. The TODO is created as its own
canonical child record after the parent task is verified. A count metric has a
user-facing label, positive target, and current value from zero through the
target. Unmetered tasks are unchanged. A manual metric does not complete a task
merely because its initial current value equals its target.

Full Create Task also has an explicit assignee. Tony is the default and keeps
the existing `member_of collections/tonys-tasks` path. Choosing Toddy, Timmy,
or Tammy instead creates one `status: planned` agent work item—shown as
queued, never falsely in progress—with:

- exactly one typed `member_of` relationship to that agent's approved work
  collection;
- exactly one typed task-to-agent `assigned_to` relationship;
- no membership in Tony's Tasks and no proposal-review entry;
- exact page, collection, assignment, and optional goal/project readback
  before success.

The `assigned_to` relation is individual work ownership and is intentionally
different from an agent profile's goal-level `default_agent_for` relation.
Reassignment is a separate later workflow and is not implied by this creation
contract.

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
  baseline_count: 0
  evidence_slugs: []
  receipt_ids: []
```

`label` is display-only and may be absent on rollout-era pages; the Queue
Reader never uses it for selection. `target` may be any positive integer, and
`baseline_count` records progress entered when the task is created. For an
event-bound metric, `current` always equals `baseline_count` plus both unique
evidence and receipt counts. GTasks requires `task_day` to match the task due
day. Every distinct accepted `job_applied` event increments `current` by one;
reaching `target` completes the task using the same verified canonical status
mutation as the UI. Older task pages without `baseline_count` read it as zero.

Task detail exposes a read-only per-task TODO list with All, Not Done, and
Done filters. The Add form remains hidden until the accessible Plus action is
activated; cancelling returns to the unchanged list and an empty task says
`No TODO yet`. Each item can be opened for its detail, append-only comments,
and audit history; text/detail edits and status changes use the item's
last-read timestamp to reject lost updates. Marking an item Done never
completes its parent task.

Select Edit to change the parent task's title, status, priority, due date,
project, associated goal, optional metric, or assignee in one form. TODO
changes stay on the individual item so task edits cannot bypass item history.
Duplicate is a reviewable action in the canonical task workflow. It copies task
intent, priority, the first open TODO as a new initial item, project, goal, and
metric configuration, while resetting
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
next_action_history: []
due_day: YYYY-MM-DD
scheduled_day: null
inbox: true
completed_at: null
links:
  - to: collections/tonys-tasks
    type: member_of
```

`next_action` and `next_action_history` are a bounded read-only compatibility
projection during migration: they are recomputed from canonical TODO records
and have no Mission Control write endpoint. Once initialized, the parent also
records `todo_projection_version: 1`. New data uses three stable Markdown
record types:

```yaml
# todos/<uuid>
type: todo
text: One concrete action
detail: ""
status: not_done # or done
kind: action # or question/blocker
created_at: 2026-08-01T20:00:00-07:00
updated_at: 2026-08-01T20:00:00-07:00
creator: people/tony-guan
source: mission_control
links:
  - to: tasks/<uuid>
    type: todo_for

# todo-comments/<uuid> and todo-events/<uuid> each link to exactly one Todo
# with comment_on and event_for respectively.
```

Comments are append-only. Creation, edits, comments, and status changes create
stable idempotent audit events. Legacy current actions migrate to Not Done;
legacy history migrates to Done with known timestamps and explicit legacy
provenance. Migration and retries derive stable identities and create no
duplicates.

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

A creation is reported as successful only when the task page and exactly one
typed lifecycle membership edge both read back. If the page exists but
relationship verification fails, the API returns a `partial_write` result with
the exact slug and does not delete, retry, or conceal the partial state. A
proposal with a missing or duplicate lifecycle edge is fail-closed before any
approval metadata or status is written, with a safe Memory Stargraph inspection
link rather than an opaque internal error.

Changing a task's goal is also explicit and verified. GTasks proves both nodes
are under the approved roots, writes the task-to-goal `advances_goal` edge and
the reciprocal goal-to-task `advanced_by` edge, then verifies both before
removing any prior pair. Clearing the selection removes both directions.
Saving the current goal selection is an idempotent repair action for legacy
one-way links; deployment never performs a bulk relationship migration.

Changing status is explicit and verified too. The detail editor supports
`planned`, `active`, `blocked`, `completed`, and `cancelled`. Legacy
`waiting` pages remain readable only through compatibility parsing and display
as Blocked, but Mission Control never writes or offers `waiting` as a task
status and no bulk normalization occurs.
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
Create Task, the full Edit save, duplicate, goal lifecycle actions, metric
configuration, or relationship repair mutates GBrain.

## Views

- Today: In Progress (maximum three), Today's Actions, Blocked, Overdue
- Board: Planned, In Progress, Blocked, Completed, Cancelled
- Inbox
- Blocked
- Projects
- Goals
- Completed

Task rows show title, project, priority, open TODO summary, due date, and configured
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

The four action sections remain first on Today. A compact goal-progress strip follows them. The Goals view is populated from GBrain at runtime; goal details show target date, review cadence, active/completed linked tasks, and progress. The full Task Edit form can select a goal; both detail surfaces navigate across the relationship.

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
