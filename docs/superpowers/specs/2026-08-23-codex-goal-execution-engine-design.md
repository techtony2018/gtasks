# Codex Goal Execution Engine Design

**Date:** 2026-08-23

**Status:** Approved direction — authority model A (risk-tiered autonomy)

**Scope:** Mission Control Codex Agents only

## Problem

Mission Control has strong downstream machinery for canonical Agent Tasks,
fixed-thread delivery, acknowledgements, blocker questions, receipts, and
read-only coordination views. It does not yet have a safe upstream system that
turns Goals and Projects into a steady supply of bounded executable work.

The practical result is that most visible Agent work is historical, scheduled,
or blocked infrastructure/canary work. Goal Steward can identify gaps but is
intentionally suggestion-only. The handoff Dispatcher can deliver verified
work but cannot decide what work should exist. These boundaries leave Codex
Agents underused even when canonical Goals and Projects have clear gaps.

## Outcome

Mission Control continuously derives useful, bounded Agent Tasks from canonical
Goals and Projects, routes them to an existing Codex Agent, and activates them
without routine Tony approval when the work is internal, reversible, and
within an explicit authority envelope.

Success is observable when:

- each eligible active Goal has either current Agent work, an explicit reason
  why no work is eligible, or one precise Tony decision request;
- each eligible active Project has a verified Goal relationship or an explicit
  data-quality/ownership issue;
- Codex Agents receive and acknowledge derived tasks in their existing fixed
  threads;
- ordinary internal work does not accumulate in `planned` or `blocked` merely
  because no human performed a second lifecycle transition;
- blocked work contains one actionable blocker, one next owner, and one next
  step, then resumes the same task after resolution;
- creation, activation, delivery, retries, and reconciliation are idempotent;
- no OpenClaw identity or runtime is required for this first rollout.

## Authority model

Tony selected **A: risk-tiered autonomy**.

### Automatically eligible

Mission Control may create and activate derived Agent Tasks when all of these
are true:

- the work is internal to Tony's local systems or canonical knowledge graph;
- it is read-only or reversible;
- it does not make an external promise, publish externally, spend money,
  change credentials/permissions, delete data, or impersonate Tony;
- the Goal, Project, expected artifact/evidence, and completion condition are
  explicit in canonical data;
- exactly one approved Codex Agent can be selected through typed ownership or
  an explicit version-controlled routing rule;
- the Agent's fixed-thread route and delivery health are verified;
- the same work is not already represented by an open or completed Task,
  Proposal, TODO, System Ticket, or recent derived-work fingerprint;
- the Agent is below its configured work-in-progress limit.

Examples include canonical audits, internal summaries, evidence collection,
local code investigation, test creation, review of existing artifacts, and
bounded draft preparation that is not externally sent or published.

### Tony decision required

Mission Control must not auto-activate work that is ambiguous, destructive,
financial, credential/permission changing, externally visible, externally
communicative, legally consequential, or a material strategic commitment. It
records one decision request with the exact unresolved choice and consequence.

The fallback is not a vague `waiting` state. The candidate remains uncreated,
or the same already-created task becomes `blocked` with `blocked_by: Tony`, an
explicit `next_step`, and one canonical question TODO.

### Explicit exclusions

- No title or prose inference for ownership.
- No new Agent, Codex task/thread, OpenClaw session, or background database.
- No automatic mutation of Tony personal Tasks.
- No automatic Goal/Project strategy invention.
- No automatic external send, publish, purchase, application, filing, or
  destructive operation.
- No second lifecycle/status vocabulary.

## Canonical data contract

GBrain remains the only canonical store. A derived work item is a normal
canonical `type: task` page using standard Mission Control statuses:

```text
planned -> active -> completed
             |          ^
             v          |
           blocked -----+

planned | active | blocked -> cancelled
```

Every derived Agent Task must have:

- an immutable opaque `tasks/<uuid>` slug;
- exactly one typed `member_of` edge to the assigned Agent's approved Task
  collection;
- exactly one typed `assigned_to` edge to that Agent;
- exactly one typed `advances_goal` edge to the canonical Goal;
- an optional canonical Project membership/reference when a verified Project
  is the immediate scope;
- a derivation receipt containing source Goal, optional Project, planner
  version, authority class, deterministic fingerprint, acceptance criteria,
  and readback evidence;
- no membership in Tony Tasks or Proposed Work.

Existing task creation, validation, handoff, Timeline, Artifact, and readback
contracts remain authoritative. The engine composes them; it does not bypass
or duplicate them.

## Architecture

### 1. Canonical work-supply reader

Read active Goals, active Projects, current Tony/Agent Tasks, Proposals, TODOs,
completed history, Agent profiles, fixed-thread registrations, and relevant
Artifacts through the existing remote-MCP adapter.

Fail closed when required roots are missing, stale without canonical readback,
type-corrupt, or relationship-ambiguous. A stale dashboard projection is never
authority to create work.

### 2. Goal and Project gap analyzer

For each eligible Goal, produce a small set of evidence-backed gaps:

- active Project has no current bounded next work;
- Goal has no active Project and canonical data contains a sufficiently
  explicit next action;
- current task is blocked and has a resolvable system-repair step;
- expected recurring review is due and no equivalent current work exists;
- a completed Artifact/receipt reveals one explicit follow-up already stated
  in canonical evidence.

The analyzer must not invent strategy, deadlines, requirements, or success
criteria. A gap with insufficient evidence becomes an informational reason,
not a task.

### 3. Deterministic dedupe and authority classifier

Normalize each candidate into a stable fingerprint over:

```text
goal + project + action kind + bounded scope + expected evidence + planner version
```

Compare that fingerprint and exact canonical references against open Agent
Tasks, Tony Tasks, Proposals, TODOs, completed history, and recent derivation
receipts. Duplicate and same-effect candidates are suppressed.

The classifier emits one of:

- `auto_eligible`: safe under authority model A;
- `tony_decision_required`: one exact decision is missing;
- `system_repair_required`: Mission Control or canonical infrastructure must be
  repaired before work can run;
- `not_actionable`: no bounded next action exists;
- `duplicate`: equivalent work already exists.

These are planner decisions and UI explanations, not task statuses.

### 4. Typed Agent allocator

Select exactly one existing Codex Agent by this order:

1. the Goal's single verified `default_agent_for` edge;
2. an explicit version-controlled Goal/Project routing rule;
3. no allocation.

Never infer from names, descriptions, prior executor, or task ownership. A
missing or duplicate owner becomes a compact system-attention issue.

Each Agent starts with a conservative WIP limit of one automatically derived
`active` task. Existing manually assigned active work counts toward the limit.
Planned work may be queued only when it is visible with the exact reason it is
not active; the first rollout should prefer not creating surplus queue items.

### 5. Verified creation and activation transaction

For an `auto_eligible` candidate:

1. acquire a per-fingerprint lease;
2. repeat canonical dedupe under the lease;
3. create the normal Agent Task as `planned` through the existing verified
   mutation contract;
4. read back page, exact collection membership, `assigned_to`, Goal, Project,
   and derivation receipt;
5. confirm the existing fixed-thread route is healthy and the Agent has WIP;
6. transition the same task to `active` through the existing task mutation
   contract;
7. let the existing durable handoff outbox/Dispatcher deliver it;
8. require delivery and Agent acknowledgement receipts before presenting the
   Agent as executing.

Any partial write remains discoverable by fingerprint and slug. A retry adopts
and verifies it; it never creates a successor task.

### 6. Blocker resolver and same-task resume

The engine distinguishes:

- **Tony input:** one canonical question TODO, `blocked_by: Tony`, exact
  `next_step`; an answer produces the existing answer/handoff event and resumes
  the same task.
- **System repair:** exact failing subsystem, observed error/evidence, repair
  owner, and retry condition; it is not counted as Tony waiting.
- **Agent execution blocker:** exact missing input/dependency plus the Agent's
  next action once resolved.

Same blocker text, timestamps, formatting changes, or presentation-only changes
do not redeliver. A materially changed answer or blocker does.

Stale canary and infrastructure records remain visible in audit/history but do
not count as a Goal's current execution path unless they are canonical current
work for that Goal.

### 7. Progress reconciler

The Goal Execution Engine is the primary planner/sender. Progress Guardian is a
fallback auditor that detects:

- created task without verified activation;
- activation without handoff outbox evidence;
- delivery without acknowledgement;
- completed work without Goal/Project/Artifact receipt;
- resolvable blocker left blocked after its answer or dependency changed;
- expired lease or WIP drift.

Reconciliation repairs only idempotent system state. It does not invent work or
repeat external actions.

## User experience

The existing Agents route remains the single user-facing collaboration surface.
Each Agent card should compactly show:

- verified profile and owned Goal(s);
- current derived task and immediate next action;
- state: `Ready`, `Delivering`, `Executing`, `Blocked`, or `Needs attention`;
- exact reason when no task is running;
- Goal, Project, Task, and latest Artifact links;
- WIP usage and last verified acknowledgement.

Goal and Project detail surfaces should show a compact execution strip:

```text
Goal -> Project -> Agent Task -> Delivery -> Acknowledged -> Evidence
```

Only canonical stages are links. Planner explanations are read-only. There is
no new standalone coordination page and no raw internal error text.

## Scheduling and pacing

- Run the planner after a relevant canonical Goal/Project/Task/TODO/Artifact
  event and on a low-frequency reconciliation interval.
- Coalesce bursts by canonical version and fingerprint.
- Never perform unbounded full-graph fan-out in a request handler.
- Limit one automatic activation per Agent per planner cycle.
- Apply a cooldown after acknowledgement or completion before deriving another
  task for the same Goal, unless the completed receipt contains an explicit
  immediate follow-up.

## Privacy and security

- Persist no fixed-thread id, credential, token, raw prompt, or private host
  state in GBrain, UI payloads, logs, or repository files.
- Use existing private Dispatcher registration and authenticated identity
  boundaries.
- Derived task details contain only the minimum canonical context needed by the
  assigned Agent.
- Agent-specific installed prompts remain identity-isolated.
- Error copy exposed to an Agent is generic where identity enumeration would
  leak another Agent.

## Rollout

### Phase 1: shadow planning

Run the analyzer/classifier/dedupe pipeline read-only. Show candidate decisions
and exact suppression reasons in developer evidence. Require zero mutations.
Compare results with current Goals, Projects, tasks, and proposal history.

### Phase 2: one-Goal canary

Enable automatic creation/activation for one Goal with one verified Codex
owner and only internal reversible work. WIP is one. Verify page, all typed
edges, handoff, acknowledgement, Artifact/evidence, completion, and subsequent
derivation behavior.

### Phase 3: Codex Goal rollout

Enable remaining eligible Goals, still one automatic active task per Agent.
Observe blocked ratio, time-to-acknowledgement, duplicate suppression, and
completion/evidence quality.

### Phase 4: tuning

Only after live evidence, tune WIP, cooldown, supported candidate kinds, and
project coverage. OpenClaw remains excluded unless Tony separately authorizes a
new design change.

## Verification

Implementation is not complete until all of the following are proven:

- unit/domain tests for classification, authority, routing, WIP, dedupe,
  partial-write adoption, and same-task resume;
- adapter/API tests with real normalized remote-MCP shapes and fail-closed root
  loss/type mismatch fixtures;
- race/concurrency tests for duplicate planner cycles and concurrent Task
  changes;
- synthetic no-side-effect delivery/acknowledgement fixtures;
- full regression suite and static checks;
- independent pre-commit UI/UX PASS at desktop 1440x1000 and genuine 390x844
  for all UI-affecting candidates;
- commit, push, dashboard-managed deployment, version/health readback, and clean
  tracked worktree;
- canonical canary readback proving at least one Goal-derived Agent Task was
  created, activated, delivered to the existing fixed Codex thread,
  acknowledged, and visible on Agents without prolonged planned/pending/blocked
  state;
- no Tony personal task mutation, no duplicate task, and no OpenClaw execution.

## Non-goals

- Replacing the existing handoff Dispatcher, Timeline, Artifact, or Agent
  identity contracts.
- General-purpose autonomous strategy generation.
- Automatic Tony personal-task creation or editing.
- Creating new Codex threads or Agents.
- Enabling OpenClaw execution.
- Solving unrelated malformed legacy Goal fields.
