# Board Drag-and-Drop and Reciprocal Goal Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe five-lane Board status dragging and explicit reciprocal task-goal relationships without duplicating or bulk-mutating GBrain data.

**Architecture:** Keep canonical mutations behind the existing verified GBrain adapter and PATCH APIs. Normalize only the GTasks UI vocabulary, preserve legacy `waiting` parsing, load `advanced_by` edges for goal detail, and share one status request path between detail Save and Board drop.

**Tech Stack:** Python 3.12 standard library, vanilla JavaScript, HTML5 drag events, CSS, unittest, dashboard-managed local runtime.

## Global Constraints

- GBrain remains the only canonical task, goal, and relationship store.
- UI statuses are exactly `planned`, `active`, `blocked`, `completed`, and `cancelled`.
- Legacy `waiting` pages remain readable and are not changed without an explicit status action.
- Board order is Planned, In Progress, Blocked, Completed, Cancelled.
- A card stays in its source lane until write and readback succeed.
- `advances_goal` and `advanced_by` must be written, removed, and verified together.
- Verification must not change Tony's live task or goal data.
- Performance findings remain diagnosis-only in this implementation.

---

### Task 1: Current status vocabulary

**Files:**
- Modify: `gtasks/domain.py`
- Modify: `gtasks/gbrain.py`
- Modify: `gtasks/server.py`
- Modify: `tests/test_domain.py`
- Modify: `tests/test_gbrain.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Produces: `EDITABLE_TASK_STATUSES`, the five values accepted by GTasks mutations.
- Preserves: `TASK_STATUSES`, including legacy `waiting` for page parsing.

- [ ] **Step 1: Write failing compatibility and API tests**

Add assertions that `Task.from_page` still parses `waiting`, that
`set_task_status(..., "waiting", ...)` is rejected, and that the status PATCH
returns `invalid_status` for `waiting`.

- [ ] **Step 2: Run focused tests and verify the expected failures**

Run:

```bash
python3 -m unittest tests.test_domain tests.test_gbrain.TaskStatusMutationTests tests.test_server.TaskStatusApiTests -v
```

Expected: failures because the mutation path still accepts `waiting`.

- [ ] **Step 3: Add the current editable set**

Define:

```python
EDITABLE_TASK_STATUSES = frozenset(
    {"planned", "active", "blocked", "completed", "cancelled"}
)
```

Use it in adapter and HTTP mutation validation. Leave `TASK_STATUSES`
unchanged for compatibility reads.

- [ ] **Step 4: Run focused tests**

Expected: all focused status tests pass.

### Task 2: Reciprocal goal data model and projection

**Files:**
- Modify: `gtasks/domain.py`
- Modify: `gtasks/gbrain.py`
- Modify: `gtasks/server.py`
- Modify: `tests/test_domain.py`
- Modify: `tests/test_gbrain.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Produces: `Goal.advanced_by: tuple[str, ...]`.
- Produces: goal snapshot fields `legacy_one_way_tasks` and
  `relationship_warning`.

- [ ] **Step 1: Write failing parsing and projection tests**

Construct a goal with outgoing `advanced_by` edges and assert that explicit
reciprocal tasks are primary. Add a legacy task carrying only
`advances_goal` and assert it remains visible and is flagged for
reconciliation.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_domain.GoalTests tests.test_gbrain.GoalReadTests tests.test_server.TasksApiTests -v
```

Expected: failures because goals do not yet parse outgoing task edges.

- [ ] **Step 3: Parse goal edges and build the union**

Change `Goal.from_page` to accept `edges=()` and collect unique
`advanced_by` task slugs. Add a detail-only relationship read so `list_goals`
does not add one outgoing-link call per goal to the already-slow home snapshot.
In `build_task_snapshot`, union explicit reciprocal matches with legacy
forward matches and expose the legacy subset separately.

- [ ] **Step 4: Run focused tests**

Expected: explicit and legacy goal projection tests pass.

### Task 3: Verified bidirectional goal mutation

**Files:**
- Modify: `gtasks/gbrain.py`
- Modify: `tests/test_gbrain.py`

**Interfaces:**
- Consumes: `GBrainAdapter.set_task_goal(task_slug, goal_slug)`.
- Produces: paired `advances_goal` / `advanced_by` writes with final readback.
- Produces: idempotent repair when the forward edge already exists.

- [ ] **Step 1: Replace one-way fixture expectations with failing pair tests**

Cover:

- new pair creation;
- unchanged selection repairing only `advanced_by`;
- pair replacement;
- pair clearing;
- add failure rollback to the pre-mutation set;
- removal failure rollback to the pre-mutation set.

- [ ] **Step 2: Run the goal mutation tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_gbrain.GoalLinkMutationTests -v
```

Expected: failures because only `advances_goal` is currently managed.

- [ ] **Step 3: Implement paired mutation helpers**

Use exact edge descriptors:

```python
("advances_goal", task_slug, goal_slug)
("advanced_by", goal_slug, task_slug)
```

Snapshot both sides, apply additions before removals, verify exact final
selection, and compensate back to the snapshot on failure. Raise
`PartialMutationError` with rollback verification in the message if success
cannot be proven.

- [ ] **Step 4: Run goal mutation tests**

Expected: all pair, reconciliation, and rollback tests pass.

### Task 4: Five-lane Board and shared status requests

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces: five direct drop targets whose `data-status` is canonical.
- Produces: `requestTaskStatus(taskSlug, status)` shared by Save and drop.
- Produces: Board retry state without optimistic canonical movement.

- [ ] **Step 1: Write failing frontend contract tests**

Require:

- no Waiting option;
- five independent Board lane titles in order;
- `draggable = true`;
- `dragstart`, `dragover`, `drop`, and `dragend` listeners;
- a Board alert and explicit Retry control;
- legacy `waiting` mapped to the Blocked lane.

- [ ] **Step 2: Run the contract test and verify failure**

Run:

```bash
python3 -m unittest tests.test_frontend_contract -v
```

Expected: failures on the old grouped columns and absent drag listeners.

- [ ] **Step 3: Implement the minimal UI behavior**

Create one lane per status. On drop, keep the snapshot unchanged, mark the
request pending, call the existing PATCH API, then reload. On error, clear
pending state and render a Retry button for the same task/status pair.
Selecting a legacy `waiting` task sets the editor to Blocked and leaves Save
enabled so the user can explicitly normalize it.

- [ ] **Step 4: Run the frontend contract test and JavaScript syntax check**

Run:

```bash
python3 -m unittest tests.test_frontend_contract -v
node --check static/app.js
```

Expected: pass.

### Task 5: User-facing relationship repair and documentation

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `README.md`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces: goal-detail legacy warning with navigation to each affected task.
- Documents: saving the current goal as the idempotent reciprocal repair action.

- [ ] **Step 1: Write failing warning-copy tests**

Require UI and documentation to name both typed edges and the explicit repair
action.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python3 -m unittest tests.test_frontend_contract tests.test_dashboard_integration -v
```

- [ ] **Step 3: Add warning and documentation**

Goal detail should say that a legacy link remains visible and that opening
the task and saving its selected goal repairs both directions. Do not add an
automatic migration.

- [ ] **Step 4: Run focused tests**

Expected: pass.

### Task 6: Managed-runtime verification

**Files:**
- No production file changes.

**Interfaces:**
- Consumes: All Things Codex Dashboard service controls.
- Produces: live verification evidence without task or goal data changes.

- [ ] **Step 1: Run complete automated verification**

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q gtasks tests
node --check static/app.js
```

- [ ] **Step 2: Restart GTasks through All Things Codex Dashboard**

Confirm `/api/health` still declares `canonical_store: gbrain`.

- [ ] **Step 3: Verify desktop Board**

Confirm all five lanes in order. Drag a planned card back to Planned; this
uses the adapter's verified same-status no-op and must leave canonical data
unchanged.

- [ ] **Step 4: Verify mobile fallback**

At 390 px width, tap a card and confirm the five-option status selector is
usable. Do not click Save.

- [ ] **Step 5: Verify goal detail**

Confirm explicit or legacy linked tasks are visible and navigable. Do not
invoke the repair action against live data.

- [ ] **Step 6: Read back live data and browser diagnostics**

Confirm task statuses and relationship edges match the pre-verification
snapshot and that browser logs contain no errors.
