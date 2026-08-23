# Codex Goal Execution Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and release a Codex-only engine that derives one safe, canonical Agent Task from an eligible Goal, activates it through the existing fixed-thread handoff path, and exposes an honest Goal-to-Agent execution state.

**Architecture:** Add a pure planning module that consumes already-validated GBrain domain objects, emits deterministic decisions, and never mutates. Add a thin orchestration service that rechecks canonical state, creates one normal Agent Task through `GBrainAdapter.create_agent_task`, activates the same task through `set_task_status`, and hands the verified mutation to the existing `CanonicalHandoffEventBridge`. Start in shadow mode, then enable one explicit Codex Goal canary through dashboard-managed runtime configuration after independent QA and commit.

**Tech Stack:** Python 3.12 stdlib, frozen dataclasses, GBrain remote MCP through `GBrainAdapter`, existing durable handoff Dispatcher/SQLite evidence, `ThreadingHTTPServer`, vanilla JavaScript/CSS, `unittest`.

## Global Constraints

- Tony approved authority model A: risk-tiered autonomy.
- GBrain is the only canonical Task/Goal/Project store; do not add a second database or sync service.
- Derived work is a normal canonical `type: task` using only `planned`, `active`, `blocked`, `completed`, and `cancelled`.
- The first rollout uses existing Codex Agents only: `agents/tammy`, `agents/timmy`, and `agents/toddy`.
- Never create a Codex thread, Agent, OpenClaw session, Tony Task, or Proposed Task.
- Ownership comes only from one verified `default_agent_for` edge or an explicit version-controlled route; never infer from prose.
- Automatically execute only internal, reversible, non-financial, non-destructive, non-external work.
- One automatically derived active task per Agent; existing active manual work counts toward WIP.
- All creation, activation, delivery, and retry paths must be idempotent and adopt an exact partial write rather than creating a successor.
- UI-affecting work remains uncommitted until independent QA explicitly passes desktop 1440x1000 and genuine mobile 390x844.
- Preserve unrelated `.gitignore` and untracked artifacts; stage only plan-owned files.
- Every coherent backend slice is committed and pushed after focused/full verification.

---

## File structure

- Create `gtasks/goal_execution.py`: pure candidate, decision, fingerprint, routing, WIP, and orchestration contracts.
- Create `tests/test_goal_execution.py`: pure planner and orchestration TDD coverage.
- Modify `gtasks/domain.py`: validated optional `GoalDerivationReceipt` on normal Tasks.
- Modify `gtasks/gbrain.py`: render/read derivation metadata, exact derived-task lookup, verified create/adopt helper, and route-health readback.
- Modify `gtasks/server.py`: shadow snapshot API, scheduler lifecycle, canonical mutation bridge, and runtime-mode gate.
- Modify `tests/test_domain.py`, `tests/test_gbrain.py`, `tests/test_server.py`: normalized remote-MCP and HTTP integration contracts.
- Modify `static/index.html`, `static/app.js`, `static/styles.css`, `tests/test_frontend_contract.py`: compact Agents execution strip and reason state.
- Modify `scripts/automation/start_gtasks_dashboard.zsh`, `tests/test_dashboard_integration.py`: dashboard-owned mode/canary configuration validation.
- Modify `gtasks/releases.json`, `tests/test_releases.py`, `README.md`, `docs/runbooks/agent-handoff-dispatcher.md`: release and operator contract.

---

### Task 1: Canonical derivation receipt on normal Tasks

**Files:**
- Modify: `gtasks/domain.py`
- Modify: `gtasks/gbrain.py`
- Test: `tests/test_domain.py`
- Test: `tests/test_gbrain.py`

**Interfaces:**
- Produces: `GoalDerivationReceipt.from_value(value) -> GoalDerivationReceipt`
- Produces: `Task.goal_derivation: GoalDerivationReceipt | None`
- Consumes later: `GoalExecutionPlanner` and `GBrainAdapter.create_or_adopt_derived_agent_task`

- [ ] **Step 1: Write failing domain tests**

Add tests proving a valid receipt round-trips and malformed receipts fail closed:

```python
def test_task_parses_verified_goal_derivation_receipt(self) -> None:
    page, edges = stored_task_page()
    page["frontmatter"]["goal_derivation"] = {
        "planner_version": "goal-execution-v1",
        "fingerprint": "a" * 64,
        "action_kind": "goal_progress_review",
        "authority_class": "auto_eligible",
        "goal_slug": "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10",
        "project_slug": None,
        "expected_evidence": "One internal progress brief with one bounded next step.",
    }
    task = Task.from_page(page, edges)
    self.assertEqual(task.goal_derivation.fingerprint, "a" * 64)
    self.assertEqual(task.to_dict()["goal_derivation"], page["frontmatter"]["goal_derivation"])

def test_task_rejects_goal_derivation_without_exact_goal(self) -> None:
    page, edges = stored_task_page()
    page["frontmatter"]["goal_derivation"] = {
        "planner_version": "goal-execution-v1",
        "fingerprint": "a" * 64,
        "action_kind": "goal_progress_review",
        "authority_class": "auto_eligible",
        "goal_slug": "goals/other",
        "project_slug": None,
        "expected_evidence": "One brief.",
    }
    with self.assertRaisesRegex(DomainValidationError, "derivation goal"):
        Task.from_page(page, edges)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_domain.DomainContractTests.test_task_parses_verified_goal_derivation_receipt \
  tests.test_domain.DomainContractTests.test_task_rejects_goal_derivation_without_exact_goal
```

Expected: errors because `GoalDerivationReceipt` and `Task.goal_derivation` do not exist.

- [ ] **Step 3: Implement the minimal domain contract**

Add this frozen value object near `Task`:

```python
@dataclass(frozen=True, slots=True)
class GoalDerivationReceipt:
    planner_version: str
    fingerprint: str
    action_kind: str
    authority_class: str
    goal_slug: str
    project_slug: str | None
    expected_evidence: str

    @classmethod
    def from_value(cls, value: object) -> "GoalDerivationReceipt":
        if not isinstance(value, Mapping):
            raise DomainValidationError("goal_derivation must be an object")
        planner_version = value.get("planner_version")
        fingerprint = value.get("fingerprint")
        action_kind = value.get("action_kind")
        authority_class = value.get("authority_class")
        goal_slug = value.get("goal_slug")
        project_slug = value.get("project_slug")
        expected_evidence = value.get("expected_evidence")
        if planner_version != "goal-execution-v1":
            raise DomainValidationError("goal_derivation planner_version is unsupported")
        if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise DomainValidationError("goal_derivation fingerprint must be sha256")
        if action_kind != "goal_progress_review":
            raise DomainValidationError("goal_derivation action_kind is unsupported")
        if authority_class != "auto_eligible":
            raise DomainValidationError("goal_derivation authority_class is unsupported")
        if not isinstance(goal_slug, str) or not goal_slug.startswith("goals/"):
            raise DomainValidationError("goal_derivation goal_slug is invalid")
        if project_slug is not None and (
            not isinstance(project_slug, str) or not project_slug.startswith("projects/")
        ):
            raise DomainValidationError("goal_derivation project_slug is invalid")
        if not isinstance(expected_evidence, str) or not expected_evidence.strip():
            raise DomainValidationError("goal_derivation expected_evidence is required")
        return cls(
            planner_version=planner_version,
            fingerprint=fingerprint,
            action_kind=action_kind,
            authority_class=authority_class,
            goal_slug=goal_slug,
            project_slug=project_slug,
            expected_evidence=expected_evidence.strip(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "planner_version": self.planner_version,
            "fingerprint": self.fingerprint,
            "action_kind": self.action_kind,
            "authority_class": self.authority_class,
            "goal_slug": self.goal_slug,
            "project_slug": self.project_slug,
            "expected_evidence": self.expected_evidence,
        }
```

Parse `frontmatter.get("goal_derivation")`, require it to match `Task.goal` and
`Task.project`, add it to `Task`, `Task.to_dict()`, task YAML rendering, preserved
task edits, and equality/readback tuples.

- [ ] **Step 4: Add failing normalized-render tests, then implement rendering**

Test that `render_task_page()` emits `goal_derivation` and that normalized
remote-MCP readback still reconstructs it. Run the focused test once before the
renderer change and confirm the field is absent, then add the exact mapping to
the rendered frontmatter.

- [ ] **Step 5: Verify focused and full domain tests**

Run:

```bash
python3 -m unittest tests.test_domain tests.test_gbrain
```

Expected: all tests pass.

- [ ] **Step 6: Commit and push the backend contract**

```bash
git add gtasks/domain.py gtasks/gbrain.py tests/test_domain.py tests/test_gbrain.py
git commit -m "feat: add canonical goal derivation receipts"
git push origin HEAD
```

---

### Task 2: Pure deterministic planner

**Files:**
- Create: `gtasks/goal_execution.py`
- Create: `tests/test_goal_execution.py`

**Interfaces:**
- Produces: `GoalExecutionCandidate`, `GoalExecutionDecision`, `GoalExecutionPlan`
- Produces: `GoalExecutionPlanner.plan(snapshot) -> GoalExecutionPlan`
- Produces: `derived_task_slug(fingerprint: str) -> str`
- Consumes: validated `Goal`, `Project`, `AgentProfile`, `Task`

- [ ] **Step 1: Write RED tests for the exact first candidate kind**

Cover:

```python
def test_plans_one_internal_review_for_owned_goal_without_open_goal_work(self):
    plan = GoalExecutionPlanner().plan(snapshot(
        goals=(goal(status="planned"),),
        agents=(agent(default_goal_slugs=(GOAL,)),),
        tasks=(),
        route_health={AGENT: True},
    ))
    self.assertEqual(len(plan.decisions), 1)
    decision = plan.decisions[0]
    self.assertEqual(decision.reason, "auto_eligible")
    self.assertEqual(decision.candidate.goal_slug, GOAL)
    self.assertEqual(decision.candidate.agent_slug, AGENT)
    self.assertEqual(decision.candidate.action_kind, "goal_progress_review")

def test_existing_open_task_for_goal_suppresses_duplicate(self):
    plan = GoalExecutionPlanner().plan(snapshot(tasks=(agent_task(goal=GOAL, status="active"),)))
    self.assertEqual(plan.decisions[0].reason, "duplicate")

def test_active_agent_wip_suppresses_new_activation(self):
    plan = GoalExecutionPlanner().plan(snapshot(tasks=(agent_task(goal=OTHER, status="active"),)))
    self.assertEqual(plan.decisions[0].reason, "wip_full")

def test_missing_or_duplicate_owner_never_infers_agent(self):
    self.assertEqual(GoalExecutionPlanner().plan(snapshot(agents=())).decisions[0].reason, "owner_missing")

def test_openclaw_owner_is_not_eligible(self):
    plan = GoalExecutionPlanner().plan(snapshot(agents=(agent(runtime="openclaw"),)))
    self.assertEqual(plan.decisions[0].reason, "runtime_not_allowed")

def test_unhealthy_fixed_route_is_system_repair(self):
    plan = GoalExecutionPlanner().plan(snapshot(route_health={AGENT: False}))
    self.assertEqual(plan.decisions[0].reason, "route_unavailable")
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_goal_execution.GoalExecutionPlannerTests
```

Expected: import failure because `gtasks.goal_execution` does not exist.

- [ ] **Step 3: Implement the pure planner**

Create these exact contracts:

```python
PLANNER_VERSION = "goal-execution-v1"
AUTOMATIC_ACTION_KIND = "goal_progress_review"
AUTO_WIP_LIMIT = 1
DERIVED_TASK_NAMESPACE = uuid.UUID("d90827ae-4529-44c4-9c4c-e86eeb19764a")

@dataclass(frozen=True, slots=True)
class GoalExecutionCandidate:
    goal_slug: str
    project_slug: str | None
    agent_slug: str
    action_kind: str
    title: str
    detail: str
    expected_evidence: str
    fingerprint: str

@dataclass(frozen=True, slots=True)
class GoalExecutionDecision:
    goal_slug: str
    reason: str
    candidate: GoalExecutionCandidate | None = None
    existing_task_slug: str | None = None

@dataclass(frozen=True, slots=True)
class GoalExecutionSnapshot:
    goals: tuple[Goal, ...]
    projects: tuple[Project, ...]
    agents: tuple[AgentProfile, ...]
    tasks: tuple[Task, ...]
    route_health: Mapping[str, bool]

@dataclass(frozen=True, slots=True)
class GoalExecutionPlan:
    planner_version: str
    decisions: tuple[GoalExecutionDecision, ...]

def derived_task_slug(fingerprint: str) -> str:
    return f"tasks/{uuid.uuid5(DERIVED_TASK_NAMESPACE, fingerprint)}"
```

`GoalExecutionPlanner.plan()` must sort by canonical Goal slug, accept Goal
status `planned` or `active`, choose exactly one Codex owner, suppress any open
task with the same Goal or derivation fingerprint, enforce one active task per
Agent, and emit at most one `auto_eligible` decision per Agent per cycle.

The only first-rollout template is:

```python
title = f"Review {goal.title} progress and publish one bounded next-step brief"
expected = "One internal progress brief with evidence, one bounded next step, and no external action."
detail = (
    f"Review canonical Goal {goal.slug}. Compare its outcome, success criteria, "
    "strategy, current linked work, and available evidence. Publish one Agent "
    "Artifact containing verified progress, gaps, and one bounded next step. "
    "Do not send, publish, purchase, delete, change permissions, or mutate Tony Tasks."
)
```

Fingerprint the canonical JSON object containing planner version, Goal,
Project, action kind, title, and expected evidence using SHA-256.

- [ ] **Step 4: Verify property-style edge cases**

Add tests for ordering independence, duplicate agents, legacy alias Goals,
completed/cancelled tasks, blocked tasks, planned unrelated work, repeated
planner calls, and Unicode titles. Run:

```bash
python3 -m unittest tests.test_goal_execution
```

Expected: all tests pass with no network calls.

- [ ] **Step 5: Commit and push**

```bash
git add gtasks/goal_execution.py tests/test_goal_execution.py
git commit -m "feat: plan safe Goal-derived Agent work"
git push origin HEAD
```

---

### Task 3: Remote-MCP snapshot and idempotent create/adopt

**Files:**
- Modify: `gtasks/gbrain.py`
- Modify: `gtasks/goal_execution.py`
- Test: `tests/test_gbrain.py`
- Test: `tests/test_goal_execution.py`

**Interfaces:**
- Produces: `GBrainAdapter.read_goal_execution_snapshot(route_health) -> GoalExecutionSnapshot`
- Produces: `GBrainAdapter.create_or_adopt_derived_agent_task(candidate, now) -> TaskEditReceipt`
- Consumes: `derived_task_slug()`, `GoalDerivationReceipt`, existing `create_agent_task()`

- [ ] **Step 1: Write failing adapter tests**

Tests must model real normalized remote-MCP page shapes and include these exact
methods and assertions:

- `test_snapshot_reads_only_verified_canonical_roots_and_codex_profiles`:
  assert the snapshot contains the fixture Goal/Project, three Codex profiles,
  zero OpenClaw profiles, and the exact open Agent Tasks.
- `test_snapshot_fails_closed_when_goals_root_is_missing`: delete the Goal root
  page from the fake runner and assert `CanonicalRootError` names only
  `collections/tonys-goals` and that `put_page` was never called.
- `test_create_derived_task_writes_planned_then_verifies_all_edges`: call the
  helper once and assert stored status `planned`, exact receipt equality, and
  the required typed-edge set below.
- `test_create_derived_task_adopts_exact_same_slug_after_partial_write`: seed
  the exact page and a subset of expected edges, call the helper, and assert it
  uses the same slug and adds only missing expected edges.
- `test_create_derived_task_rejects_existing_same_slug_with_other_receipt`:
  seed a different fingerprint and assert `PartialMutationError` plus zero
  `put_page`, `add_link`, and `remove_link` calls.
- `test_create_derived_task_never_links_tony_or_proposed_roots`: assert neither
  forbidden root appears in the resulting page links or graph edges.

The verified edge set must include exactly:

```python
{
    (task_slug, agent.work_root, "member_of"),
    (task_slug, agent.slug, "assigned_to"),
    (task_slug, goal.slug, "advances_goal"),
}
```

and the optional project `member_of` edge.

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest \
  tests.test_gbrain.GoalExecutionAdapterTests \
  tests.test_goal_execution.GoalExecutionAdapterContractTests
```

Expected: missing-method failures.

- [ ] **Step 3: Implement canonical snapshot reads**

Reuse `list_goals()`, `list_projects()`, `list_agent_profiles()`, and
`list_agent_work()`. Reject any `CollectionIssue` in required Goal/Agent roots;
retain unrelated legacy malformed Goal issues as explicit planner attention,
not as authority to guess. Filter Agent profiles to `runtime == "codex"`.

- [ ] **Step 4: Implement exact create/adopt**

Build a `Task` with deterministic slug, `status="planned"`, `inbox=True`, the
Agent work root, Goal, optional Project, and `GoalDerivationReceipt`. Under a
per-fingerprint in-process lock:

1. read the deterministic slug;
2. if absent, call existing `create_agent_task()`;
3. if present, parse and require exact receipt, Goal, Project, owner, status in
   `planned|active|blocked|completed`, and exact typed edges;
4. reject mismatches with `PartialMutationError` and no write;
5. return the verified stored Task.

- [ ] **Step 5: Verify focused/full adapter suites**

```bash
python3 -m unittest tests.test_goal_execution tests.test_gbrain
```

Expected: all pass.

- [ ] **Step 6: Commit and push**

```bash
git add gtasks/gbrain.py gtasks/goal_execution.py tests/test_gbrain.py tests/test_goal_execution.py
git commit -m "feat: create or adopt derived Agent tasks"
git push origin HEAD
```

---

### Task 4: Verified activation and fixed-thread handoff

**Files:**
- Modify: `gtasks/goal_execution.py`
- Modify: `gtasks/gbrain.py`
- Modify: `gtasks/server.py`
- Test: `tests/test_goal_execution.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `GoalExecutionEngine.run_once(now) -> GoalExecutionRun`
- Produces: `GoalExecutionRun.to_dict()`
- Consumes: `CanonicalHandoffEventBridge.after_verified_mutation()`

- [ ] **Step 1: Write RED orchestration tests**

Cover exactly:

- `test_run_once_creates_planned_reads_back_activates_and_dispatches`: assert
  one create/adopt call, one planned-to-active mutation, one bridge call, and
  one returned Task slug.
- `test_run_once_does_not_activate_when_route_is_unhealthy`: assert reason
  `route_unavailable` and zero adapter mutation calls.
- `test_run_once_does_not_create_when_wip_is_full`: assert reason `wip_full`
  and zero adapter mutation calls.
- `test_repeat_run_adopts_same_task_and_does_not_redeliver`: run twice and
  assert one Task slug, one activation, and one handoff id.
- `test_activation_partial_write_returns_attention_without_success`: raise
  `PartialMutationError` from status update and assert the run reports
  `system_repair_required` with the same Task slug.
- `test_dispatch_failure_keeps_verified_active_task_and_reports_recovery`:
  return a dead-letter handoff, assert the active Task is not rolled back, and
  assert the public reason is `handoff_needs_repair`.

Assert ordered calls: snapshot, create/adopt, before snapshot, status mutation,
exact after readback, bridge dispatch. Assert no second task slug.

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_goal_execution.GoalExecutionEngineTests
```

Expected: `GoalExecutionEngine` missing.

- [ ] **Step 3: Implement the engine**

Use constructor injection:

```python
class GoalExecutionEngine:
    def __init__(
        self,
        *,
        adapter: GBrainAdapter,
        bridge: CanonicalHandoffEventBridge,
        planner: GoalExecutionPlanner | None = None,
        mode: str = "shadow",
        canary_goal_slug: str | None = None,
    ) -> None:
        if mode not in {"off", "shadow", "canary"}:
            raise ValueError("goal execution mode must be off, shadow, or canary")
        if mode == "canary" and (
            not isinstance(canary_goal_slug, str)
            or not canary_goal_slug.startswith("goals/")
        ):
            raise ValueError("canary mode requires one canonical Goal slug")
        self.adapter = adapter
        self.bridge = bridge
        self.planner = planner or GoalExecutionPlanner()
        self.mode = mode
        self.canary_goal_slug = canary_goal_slug

    def run_once(self, now: datetime) -> GoalExecutionRun:
        route_health = self.route_health()
        snapshot = self.adapter.read_goal_execution_snapshot(route_health)
        plan = self.planner.plan(snapshot)
        if self.mode != "canary":
            return GoalExecutionRun.from_plan(plan, mode=self.mode, ran_at=now)
        eligible = next(
            (
                value
                for value in plan.decisions
                if value.reason == "auto_eligible"
                and value.goal_slug == self.canary_goal_slug
            ),
            None,
        )
        if eligible is None or eligible.candidate is None:
            return GoalExecutionRun.from_plan(plan, mode=self.mode, ran_at=now)
        planned = self.adapter.create_or_adopt_derived_agent_task(
            eligible.candidate,
            now,
        ).task
        if planned.status == "planned":
            activated = self.adapter.set_task_status(planned.slug, "active", now)
            handoff = self.bridge.after_verified_mutation(
                planned.to_dict(),
                activated.task.to_dict(),
                {
                    **activated.to_dict(),
                    "mutation_kind": "task_status",
                    "verified": True,
                },
                now,
            )
            return GoalExecutionRun.activated(
                plan=plan,
                task=activated.task,
                handoff=handoff,
                mode=self.mode,
                ran_at=now,
            )
        return GoalExecutionRun.adopted(
            plan=plan,
            task=planned,
            mode=self.mode,
            ran_at=now,
        )
```

Modes are `off`, `shadow`, and `canary`; they are runtime controls, not task
statuses. `shadow` returns decisions with zero writes. `canary` allows only the
exact configured Goal and at most one mutation sequence per run.

For activation, call existing `set_task_status(task_slug, "active", now)`, then
pass before/after/verified receipt to the existing bridge. Require the returned
handoff record to be `queued`, `leased`, `received`, `execution_started`, or
`active`; suppressed/dead-letter becomes actionable system attention.

- [ ] **Step 4: Add route-health verification**

Compute health from `bridge.dispatcher.registrations`: exactly one verified
registration for the Agent, one route, and no foreign Agent ambiguity. Do not
expose route ids or fixed thread ids in API payloads.

- [ ] **Step 5: Verify orchestration and dispatcher regressions**

```bash
python3 -m unittest \
  tests.test_goal_execution \
  tests.test_handoff_dispatcher \
  tests.test_gbrain \
  tests.test_server
```

Expected: all pass.

- [ ] **Step 6: Commit and push**

```bash
git add gtasks/goal_execution.py gtasks/gbrain.py gtasks/server.py \
  tests/test_goal_execution.py tests/test_server.py
git commit -m "feat: activate derived work through fixed Agent handoffs"
git push origin HEAD
```

---

### Task 5: Event wake, bounded reconciliation, and dashboard runtime gate

**Files:**
- Modify: `gtasks/goal_execution.py`
- Modify: `gtasks/server.py`
- Modify: `scripts/automation/start_gtasks_dashboard.zsh`
- Test: `tests/test_goal_execution.py`
- Test: `tests/test_server.py`
- Test: `tests/test_dashboard_integration.py`

**Interfaces:**
- Produces: `GoalExecutionScheduler.start()`, `.wake(reason)`, `.stop()`
- Produces: `GET /api/goal-execution`
- Consumes: verified engine `run_once()`

- [ ] **Step 1: Write RED scheduler tests**

Prove event bursts coalesce, interval is bounded, only one run executes at a
time, shutdown joins the thread, exceptions produce honest last-run state, and
no zero-delay loop is possible.

Use a fake clock/event and assert:

```python
self.assertEqual(engine.run_count, 1)
self.assertGreaterEqual(scheduler.minimum_interval_seconds, 30)
self.assertLessEqual(scheduler.reconcile_interval_seconds, 1800)
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_goal_execution.GoalExecutionSchedulerTests
```

- [ ] **Step 3: Implement scheduler and API**

Use one daemon `Thread`, one `Event`, one `Lock`, a 30-second minimum interval,
and a 30-minute reconciliation interval. `GET /api/goal-execution` returns only
planner version, mode, last verified run time, per-Goal decisions, current task
slug/title/status, Agent display name, and public reason copy.

Call `scheduler.wake()` after verified Goal, Project, Agent Task, To Do answer,
Artifact, and Agent acknowledgement mutations. Never run the planner inside an
HTTP request thread.

- [ ] **Step 4: Add dashboard-owned runtime validation**

`start_gtasks_dashboard.zsh` must accept:

```text
MISSION_CONTROL_GOAL_EXECUTION_MODE=off|shadow|canary
MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL=goals/<uuid>
```

Default missing mode to `shadow`. Fail closed if `canary` lacks a canonical
Goal slug. Do not store credentials, thread ids, or private routes in the
repository.

- [ ] **Step 5: Verify server/runtime contracts**

```bash
python3 -m unittest \
  tests.test_goal_execution \
  tests.test_server \
  tests.test_dashboard_integration
```

- [ ] **Step 6: Commit and push**

```bash
git add gtasks/goal_execution.py gtasks/server.py \
  scripts/automation/start_gtasks_dashboard.zsh \
  tests/test_goal_execution.py tests/test_server.py tests/test_dashboard_integration.py
git commit -m "feat: schedule bounded Goal execution planning"
git push origin HEAD
```

---

### Task 6: Agents and Goal/Project execution UI

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/project_browser_fixture.py`

**Interfaces:**
- Consumes: `GET /api/goal-execution`
- Produces: compact execution strip in existing Agents route and Goal/Project details

- [ ] **Step 1: Write RED frontend contracts**

Assert exact accessible structure:

```html
<section id="agent-goal-execution" aria-labelledby="agent-goal-execution-heading">
  <h3 id="agent-goal-execution-heading">Goal execution</h3>
  <p id="agent-goal-execution-state" role="status" aria-live="polite"></p>
  <ol id="agent-goal-execution-list"></ol>
</section>
```

Tests must require compact states `Ready`, `Delivering`, `Executing`, `Blocked`,
and `Needs attention`; exact Goal/Project/Task links; no raw route/fixed-thread
identity; honest loading/error/empty copy; and no standalone navigation route.

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTests
```

- [ ] **Step 3: Implement minimal HTML/JS/CSS**

Fetch `/api/goal-execution` independently from slow Agent work. Retain the last
valid payload during bounded refresh. Render one scan-first row per Agent:

```text
Timmy — Civic — Executing — Review Civic progress…
```

Only Agent, Goal, Project, and Task text is linked. Use existing detail-opening
functions so keyboard open focuses the detail H2 and close restores exact
origin after refresh rerenders. On 390px, keep the row wrapping inside the
central pane/full sheet with document width exactly 390px.

- [ ] **Step 4: Add browser fixtures**

Add GET-only fixtures for shadow eligible, WIP full, route unavailable,
duplicate, active delivery, acknowledged execution, root loss, and slow Agent
work. Assert the Goal execution surface remains independently available.

- [ ] **Step 5: Run static/frontend tests**

```bash
node --check static/app.js
python3 -m unittest tests.test_frontend_contract tests.test_server
git diff --check
```

- [ ] **Step 6: Freeze uncommitted UI candidate and obtain independent QA**

Create a precise manifest of every changed source/test/static file. Restart the
unchanged dashboard-managed candidate in `shadow` mode. Independent QA must
explicitly PASS desktop 1440x1000 and genuine mobile 390x844 for:

- Agents starts with Agent cards and compact execution state;
- Goal/Project/Task exact links and focus return;
- shadow, WIP, duplicate, route failure, loading, error, and root-loss copy;
- no raw route/thread identity;
- independent bounded loading;
- containment and accessibility;
- GET-only browser traffic and zero GBrain writes.

Do not commit on FAIL or INCONCLUSIVE. Repair, freeze a new manifest, and rerun.

- [ ] **Step 7: Commit and push only after PASS**

```bash
git add static/index.html static/app.js static/styles.css \
  tests/test_frontend_contract.py tests/project_browser_fixture.py
git commit -m "feat: show Goal-derived Agent execution state"
git push origin HEAD
```

---

### Task 7: Release, one-Goal canary, and terminal readback

**Files:**
- Modify: `gtasks/releases.json`
- Modify: `tests/test_releases.py`
- Modify: `README.md`
- Modify: `docs/runbooks/agent-handoff-dispatcher.md`

**Interfaces:**
- Consumes: all previous tasks
- Produces: deployed release and canonical canary evidence

- [ ] **Step 1: Write RED release/docs tests**

Require a new version entry describing Codex-only risk-tiered Goal execution,
shadow/canary controls, one-Goal WIP, exact readback, and rollback. Require the
runbook to state that OpenClaw is excluded and that canary mutation runs only
through the dashboard-managed Mission Control runtime.

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_releases
```

- [ ] **Step 3: Update release/docs and run full verification**

```bash
python3 -m unittest discover -s tests
node --check static/app.js
python3 -m compileall -q gtasks
git diff --check
```

Expected: full suite passes with zero failures; record exact count.

- [ ] **Step 4: Obtain a fresh final independent pre-commit QA PASS**

Freeze the complete uncommitted release aggregate and rerun desktop 1440x1000
and genuine mobile 390x844. This final gate supersedes earlier partial UI gates.

- [ ] **Step 5: Commit and push the release**

```bash
git add gtasks/releases.json tests/test_releases.py README.md \
  docs/runbooks/agent-handoff-dispatcher.md
git commit -m "release: ship Codex Goal execution canary"
git push origin HEAD
```

- [ ] **Step 6: Deploy through All Things Codex Dashboard**

Set the dashboard-managed Mission Control runtime to:

```text
MISSION_CONTROL_GOAL_EXECUTION_MODE=canary
MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL=goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10
```

Use the supported Dashboard service update/restart boundary. Do not edit a
private generated service file directly. Verify `/api/health` and About show the
new version and the runtime cwd/commit matches pushed `origin/main`.

- [ ] **Step 7: Verify the live canary end to end**

Poll canonical state with bounded waits and require:

1. exactly one deterministic derived Task for the Civic Goal;
2. `type: task`, `status: active`, exact Timmy work root, one `assigned_to`, one
   `advances_goal`, exact derivation receipt, and no Tony/Proposal membership;
3. one durable handoff/outbox event for the exact canonical version;
4. delivery to Timmy's existing fixed Codex thread, never a new thread;
5. Agent acknowledgement and Agents UI state `Executing`;
6. repeated planner runs create no additional Task or handoff;
7. no prolonged planned/pending/blocked state;
8. no Tony Task or OpenClaw mutation.

If the canary cannot execute, switch the dashboard mode back to `shadow`, keep
the same Task and evidence, classify the exact system blocker, and repair/retest
without creating another task.

- [ ] **Step 8: Verify completion evidence and tracked cleanliness**

Record commit, push, deployed version, QA report path, exact Task/Goal/Agent
slugs, handoff id, acknowledgement, and canonical readbacks. Confirm:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
curl -sS http://127.0.0.1:4179/api/health
curl -sS http://127.0.0.1:4179/api/goal-execution
```

Only pre-existing user-owned `.gitignore` and untracked artifacts may remain.
