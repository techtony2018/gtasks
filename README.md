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

Mission Control also defines one specialist consultation role:
**GBrain & Memory Stargraph Expert**. It is documented in
[`docs/runbooks/gbrain-memory-stargraph-expert.md`](docs/runbooks/gbrain-memory-stargraph-expert.md)
and registered in the shared documentation config. Developer, QA, System
Tickets Manager, Documentation Manager, Goal Steward, and Agent workers should
consult it when a GBrain, remote MCP, typed-relationship, sync, or Memory
Stargraph blocker prevents canonical readback or safe progress. The role is
consultation-only: it returns evidence and one unblock recommendation, but it
does not take ownership, mutate canonical data, or replace the caller's
authority boundary. Callers must not guess, raw-write, or create replacement
tasks to work around those blockers; in short, do not guess, raw-write, or
create replacement tasks.
Contract phrase: do not guess, raw-write, or create replacement tasks.

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

Latest verified pushed release baseline: V0.0.206 at commit
`ca0ad4665a9b69944bc975f2c0849a6c10062e6c`. Mission Control supports a
controlled Codex-only Goal execution canary through private dashboard-managed
runtime configuration, keeps the default mode at `shadow`, persists a
30-minute local Codex resume timeout for the Tammy supervisor, suppresses
immediate duplicate exact completed Goal review canaries as
`recently_completed`, reconciles stale local abandon-start rows against
authoritative server recovery state, treats `codex_thread_active_writer` as
retryable local backpressure, permits operator recovery of owned
`terminal_delivery_failure` handoffs only when abandoned execution starts prove
the failed launches were unused, and throttles active-writer retries with a
bounded 300-second local backoff. V0.0.145 also clears a local pre-gate wake
inbox by cancelling its unused launch when authoritative Mission Control
recovery proves the same handoff is already completed or suppressed. The
verified Career canary task
`tasks/a6251324-1af6-5005-8a17-0ad0610be4d8` completed with canonical Artifact
`artifacts/32142bd1-8b1b-4ffc-a115-87fd39d7f6d7`; V0.0.149 shows that
recently-completed Career task's title, status, and Agent in Goal execution
status. V0.0.151 projects active or planned goal-derived duplicate decisions
with no verified Agent handoff as `handoff_missing` Needs attention, while
terminal handoff states remain `handoff_needs_repair` and ordinary
duplicate/recently_completed states stay distinct. V0.0.152 also projects the
latest dispatcher handoff status into `/api/agent-work` non-completed task
rows as `dispatcher_handoff` without overwriting the canonical task `handoff`;
completed task rows suppress that projection. V0.0.153 flags active or planned
non-derived Agent goal tasks that have no explicit next action, no handoff, no
blockers/dependencies, and no open TODO as `task_needs_next_action` Needs
attention instead of ordinary duplicate. V0.0.154 excludes those stalled,
non-actionable Agent tasks from Goal execution WIP accounting so another
bounded Goal review can become `auto_eligible`; active Agent tasks with a real
`next_action` still consume WIP. V0.0.155 flags a Goal-derived active task
whose latest handoff is still `queued` after the bounded worker attention
window as `handoff_worker_unavailable` Needs attention, while fresh queued
handoffs remain Delivering. The current Toddy Health readback is an
operational host/private-route remediation item, not a completed Agent
execution. V0.0.156 safely reconciles a selected canary Goal-derived active
Task to completed only after the latest dispatcher handoff is completed and an
exact `produced_for` Artifact readback exists; if the Artifact is missing, the
Task remains active/duplicate. The live Civic task
`tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92` completed at
`2026-08-24T03:25:19.000864-07:00` after exact Artifact readback for
`artifacts/4fb85655-dc13-4050-b3a3-0c56b27acb9f`. V0.0.157 adds a cycle key
to Goal-derived Agent review deterministic fingerprints, so a completed
prior-cycle review no longer permanently suppresses the next bounded review
cycle while same-cycle completed work still suppresses repeats. The
dashboard-managed scheduler created/activated current-cycle Civic/Timmy task
`tasks/44e14ea5-0f81-558b-a761-ec3540f3b4e2` for
`goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10`; its detail includes
`Review cycle starts 2026-08-24`, and dispatcher handoff readback reached
`actively_executing` during the deploy handoff. A later documentation readback
showed the task still active with `dispatcher_handoff.status=suppressed`; no
Artifact completion was present at the bounded handoff check.
V0.0.158/V0.0.159 complete the checkpointed handoff reconciliation path:
`suppressed` plus terminal `checkpointed` state plus exact `produced_for`
Artifact reconciles as `completed_after_verified_handoff`; suppressed without
checkpoint or without exact Artifact remains attention/active. V0.0.159 fixed
the bridge readback root cause by falling back to terminal execution claims so
released checkpointed claims retain `terminal_state=checkpointed` instead of
losing that state after nonterminal-claim filtering. Final live readback shows
current-cycle Civic/Timmy task
`tasks/44e14ea5-0f81-558b-a761-ec3540f3b4e2` completed at
`2026-08-24T04:08:08.447338-07:00` with Artifact
`artifacts/6e6c331e-a181-4d8f-ab16-cda613b8fed9` created by `agents/timmy`
and `produced_for` that task.
V0.0.160 classifies canonical blocked Agent tasks with
`handoff.state=waiting_for_input` and `waiting_on=people/tony-guan` as
`waiting_for_tony` / Blocked rather than `task_needs_next_action` / Needs
attention. Exact task detail now hydrates the active handoff question TODO
from canonical `todo_for` backlinks so the handoff panel shows the real
question; bounded TODO hydration failures surface as a canonical TODO list
unavailable issue while preserving task and handoff visibility. Live readback
shows Family/Toddy task `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`
blocked on `people/tony-guan`, handoff `waiting_for_input`, question TODO
`todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, and Goal
`goals/2c86f86c-c9fb-5f49-96d0-e4d63f489fc8` projecting
`waiting_for_tony`.
V0.0.161 retains accepted dispatcher handoff status for selected duplicate or
recent active Goal tasks, so the Goal execution UI can render Delivering or
Executing instead of falling back to ambiguous Ready or duplicate-only context.
The live canary target was rotated from Toddy Health to Faith/Tammy; the
dashboard-managed scheduler produced and completed Goal-derived task
`tasks/46ba34c2-9ccb-523e-a786-9b70d5673073` for `agents/tammy`, with exact
Artifact `artifacts/d2a45c21-1428-4891-ae98-531a958e1e98` created by
`agents/tammy`, `produced_for` that task, and supporting Faith Goal
`goals/755548a3-d556-513a-900c-45f90da5702e`. This does not mean all Goals are
automated: the current next-owner blockers remain Family/Toddy waiting for
Tony's answer on question TODO
`todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, and Toddy Health waiting for
Toddy host Tailscale login/dispatcher availability before queued task
`tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5` can be leased.
V0.0.162 adds an explicit private `auto` canary target for
dashboard-managed canary mode. `auto` still activates at most one
Goal-derived Agent Task per run; it selects the first currently
`auto_eligible` Goal instead of staying pinned to a fixed completed canary. If
no new Goal is eligible, public status prioritizes an active accepted handoff,
then the newest recently completed canary, then attention/blocker states. Live
readback showed the auto canary completed Finance/Tammy task
`tasks/cc655813-1968-5264-a5ad-454199c1b3cb` with Artifact
`artifacts/9362d402-0f7c-4d65-9222-a8c140f1d9d3`, then Career/Tammy task
`tasks/53264f17-e5d5-5b5d-ad36-af1eadc1a770` with Artifact
`artifacts/fbffd8c1-b04e-420f-8db3-14be7a2b7f8f`; `/api/goal-execution` now
surfaces Career as the newest `recently_completed`, Finance as
`recently_completed`, and Family/Toddy separately as `waiting_for_tony`.
V0.0.163 exposes the exact open question TODO on Goal execution rows and Agent
compact cards for Goal-derived work waiting on Tony. Current Family/Toddy
surfaces now include the copy `Answer: Which family-care scope, outcomes,
constraints, and first action should Toddy use next?` for task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` and question TODO
`todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`. Task lookup also merges richer
Agent-work projections with same-slug snapshot rows so handoff/TODO context is
not hidden by thinner cached rows. V0.0.164 recovers expired owned
`execution_claim` rows at the next authenticated dispatcher claim boundary when
the same registered Agent host returns, preserving verified task authority and
owned-execution fencing so remote host worker outages or lost local claim state
do not leave Goal-derived Agent work permanently queued. The local supervisor
on this Mac remains Tammy/Tammy-OC only; Timmy and Toddy are not local workers
here and must run on their own host machines. Post-release verifier commit
`f5a2aa77d44561a9d7279a185c184388759945ad` adds
`scripts/verify_handoff_worker_runtime.py`, a read-only worker runtime verifier
that checks private worker config, authenticated preflight, optional Git HEAD,
and optional LaunchAgent presence without claiming, waking, acknowledging, or
mutating handoffs. Timmy was verified on its own host at this exact commit with
route `hosts/timmy` and a loaded launch label; Toddy remains unrecovered because
its host/SSH/control plane is still unreachable.
Fleet verifier commit `d7622b7272df3c8979d1db8e6b0c7b396c7a093c` adds
non-secret inventory `config/handoff-dispatcher/remote-workers.json` and
`scripts/verify_handoff_worker_fleet.py` to check remote workers as a fleet.
Latest reported fleet evidence is summary `ok=1 failed=1`: Timmy is verified
at route `hosts/timmy` with preflight verified, launch loaded, and repo HEAD
exactly `d7622b7`; Toddy is still `ok: false` with `ssh_unreachable` for
`toddy@100.117.212.20`. V0.0.165 makes Goal execution `owner_missing` repair
copy actionable: assign exactly one Codex Agent and verify the single
`default_agent_for` link for that Goal. QA verified the copy on desktop
1440x1000 and genuine mobile 390x844, and local workers still read back
Tammy/Tammy-OC only. V0.0.166 keeps Goal execution running past non-visible
malformed Agent work items that are already reported in Inbox, while missing
canonical roots and visible unsafe Agent-work issues still fail closed. Live
readback showed `/api/goal-execution` with 13 last-run decisions and
`last_error: null`; the hidden malformed Tammy task remains an Inbox warning
and stays excluded from Board.
V0.0.167 updates auto Goal execution public selection priority: active or
eligible work and active accepted handoffs still win, but actionable blocker
states now surface before `recently_completed` history. The blocker states are
`waiting_for_tony`, `handoff_needs_repair`, `handoff_missing`,
`task_needs_next_action`, and `handoff_worker_unavailable`. Post-deploy
readback showed `/api/goal-execution` with `public_reason=waiting_for_tony`,
`decision_count=13`, and `last_error=null`.
V0.0.168/V0.0.169 populate waiting-for-Tony Goal task context in the
Goal-execution readback and consume that context during Agents cold load before
Agent Work reconciliation. The selected headline task now carries
`slug`, `title`, `status`, and `agent_slug`, so Family/Toddy can render the
exact blocked Task link immediately. Live readback showed task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, title
`Prepare family-care goal map and weekly review brief`, status `blocked`, and
agent `agents/toddy`.
V0.0.182 adds a compact Goal execution reader summary at both top-level
`/api/goal-execution.summary` and `last_run.summary`. It counts
`total_goals`, `needs_attention`, `waiting_for_tony`, `owner_missing`,
`ready`, `in_flight`, `recently_completed`, includes per-reason counts under
`reasons`, and carries bounded `next_action` guidance for lightweight readers.
Postdeploy readback showed `total_goals=7`, `needs_attention=2`,
`waiting_for_tony=1`, `owner_missing=1`, `in_flight=1`,
`recently_completed=3`, and a present `next_action`.
V0.0.183 renders that verified summary directly in Agents > Goal execution:
the panel shows the `Next action:` line plus visible counts for total Goals,
Needs attention, Waiting for Tony, Missing owner, In flight, and Recently
completed. The rendered summary remains read-only display context; it does
not mutate Goal ownership, answer Tony's blocker, wake a worker, or complete a
task.
V0.0.184 adds `blocking_questions` to the Goal execution summary and renders
the current waiting-for-Tony question in Agents > Goal execution. The live
Family/Toddy blocker now appears as
`Question: Which family-care scope, outcomes, constraints, and first action should Toddy use next?`,
backed by canonical task `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` and
question TODO `todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`. The question is
display/readback context only until Tony answers and hands back the same task.
V0.0.185 adds `missing_owners` to the same Goal execution summary and renders
compact missing-owner rows in Agents > Goal execution. The current
Entrepreneurship owner gap appears as
`Missing owner: Entrepreneurship: create a company and start running business, compound over time — add default_agent_for`,
backed by Goal `goals/d837ac94-36f5-4735-93bb-d84c69b45435`. The repair is
still exactly one verified `default_agent_for` relationship to a Codex Agent;
the summary does not create or infer that owner.
V0.0.186 makes those Goal execution summary action items exact controls:
the Family/Toddy question opens canonical Task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, the Entrepreneurship
missing-owner title opens canonical Goal
`goals/d837ac94-36f5-4735-93bb-d84c69b45435`, and closing either detail
restores focus to the originating summary control. These links are read-only
navigation; they do not answer Tony, create `default_agent_for`, or mutate
canonical state.
V0.0.187 adds explicit Codex-only owner assignment controls beside the
missing-owner summary item: `Assign to Tammy`, `Assign to Timmy`, and
`Assign to Toddy`. Each control uses the verified
`POST /api/agents/<agent>/default-goals` contract with body
`{goal_slug, action: "assign"}` only after explicit user activation. The UI
does not infer or automatically mutate ownership, and it exposes no OpenClaw
assignment controls.
V0.0.188 adds `summary.action_queue` and `last_run.summary.action_queue` so
Goal execution next actions are grouped by owner. The current live queue has
two Tony-owned actions: `answer_question` for the Family/Toddy task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` and TODO
`todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, plus
`assign_goal_owner` for Entrepreneurship Goal
`goals/d837ac94-36f5-4735-93bb-d84c69b45435`. Agents > Goal execution renders
`Action queue:`, `Tony action required`, `Answer Agent question`, and
`Assign Goal owner` while preserving the V0.0.187 explicit assignment buttons.
V0.0.189 makes the `Answer Agent question` action a direct inline control:
activating it opens the exact canonical waiting-for-input Task after readback
and focuses `#task-handoff-answer`, so Tony can answer immediately. Closing
the detail restores focus to the originating `.goal-execution-answer-action`
using immutable `data-goal-execution-origin`; it does not fall back to a
same-slug Agent-card link. This adds no new mutation path: answer submission
still uses the existing verified `/api/todos/<todo>/answer` flow.
V0.0.190 adds an inline answer composer directly in the Goal execution Action
queue for Tony waiting-for-input Agent questions. The backend carries verified
`todo_updated_at`; the UI renders one labeled textarea plus `Submit answer`.
Submission uses the existing canonical
`POST /api/todos/<todo>/answer` contract with `answer`,
`expected_updated_at`, actor `people/tony-guan`, source `mission_control`, a
UUID `idempotency_key`, verified response reconciliation, toast, and bounded
Goal execution/Agent Work refresh. V0.0.189's direct Task-open action remains
available and still restores exact origin focus.
V0.0.191 renders `assign_goal_owner` Action queue entries with the same
Codex-only inline assignment controls in the primary Action queue:
`Assign to Tammy`, `Assign to Toddy`, and `Assign to Timmy`. Each control uses
the existing verified `POST /api/agents/<agent>/default-goals` contract with
`{goal_slug, action: "assign"}`. OpenClaw assignment remains excluded, and the
separate Missing owner detail row keeps its own preserved assignment controls.
V0.0.192 adds verified Codex Agent candidate-owner metadata to missing-owner
summaries and `assign_goal_owner` Action queue entries. The current
Entrepreneurship missing-owner row labels Timmy as
`Assign to Timmy (recommended: lowest verified Codex Goal load)` because Timmy
has 1 verified default Goal, while Toddy has 2 and Tammy has 3. The
recommendation is readback guidance only: no `default_agent_for` relationship
is created until Tony explicitly activates a verified Codex assignment
control, and OpenClaw assignment remains excluded.
V0.0.193 carries the verified waiting-for-Tony question detail into
`answer_question` Action queue entries and renders that detail beside the
inline answer form. The Family/Toddy action now shows the detail beginning
`Based on Artifact artifacts/0e0323e7-f5b8-4833-881a-018507ac7e2a...`, so
Tony can answer from the Action queue without opening the Task first. This is
readback guidance plus explicit user answer submission only; it does not
automatically answer the TODO or mutate GBrain.
V0.0.194 makes `summary.next_action` name the exact Tony-owned work instead of
generic guidance. Current postdeploy readback names the Toddy question
`Which family-care scope, outcomes, constraints, and first action should Toddy use next?`
and the recommended Entrepreneurship owner assignment to Timmy with
`recommended: lowest verified Codex Goal load`, while noting executing or
delivered Agent work can continue. The sentence is still guidance only: it
does not answer, assign, acknowledge, wake, complete, or mutate GBrain without
Tony activating the existing verified controls.
V0.0.195 adds an editable `Insert answer template` button beside the Goal
execution waiting-question inline answer textarea. The verified
`answer_question` Action queue entry carries `answer_template` lines for
Scope categories, Desired outcomes, Constraints, First action, and Notes; the
button inserts that structured draft into the textarea and focuses it. This
does not submit, answer, assign, acknowledge, wake, complete, or mutate GBrain.
V0.0.196 makes that inserted template a concrete editable approval draft
instead of placeholder choices: Scope categories accepted, Desired outcomes
accepted, Constraints accepted, First action approved, and Notes asking to
keep the work bounded to the stated scope, outcomes, constraints, and first
action. The button still only fills and focuses the textarea; Tony must
explicitly submit the answer before any GBrain mutation can occur.
V0.0.197 surfaces the same Tony-owned Goal execution Action queue in Inbox as
a dedicated expanded `Goal execution actions` section, so waiting Agent
questions and missing Goal-owner repairs appear in the central Needs Attention
flow as well as Agents. The section reuses the same explicit answer/template
and Codex-only assignment controls; it does not automatically answer, assign,
acknowledge, wake, complete, or mutate GBrain.
Phase note: this closes the current broad Goal-execution improvement pass.
V0.0.195 added editable answer templates, V0.0.196 made the template concrete,
and V0.0.197 moved the same actions into Inbox for central triage.
V0.0.198 adds an explicit Inbox-only `Run recommended unblock plan` button
when the Goal execution Action queue has both a concrete answer draft and a
recommended Codex owner assignment. One reviewed click sequences the verified
answer POST first, then the recommended owner assignment POST; it never runs
automatically and does not mutate GBrain unless Tony clicks it. Current live
readback now has two `answer_question` actions plus `assign_goal_owner`, so
the next product slice is expanding the recommended plan to cover all
answerable questions.
V0.0.199 detects private credential or token questions in Goal execution,
labels them `Private input required`, suppresses synthetic answer templates
and inline answer forms for those questions, and excludes them from
recommended unblock plans. Safe answer questions with concrete templates and a
recommended Codex owner assignment still use the explicit recommended plan;
Mission Control never generates, prefills, or one-click submits private
credential values.
V0.0.200 includes those private-input blockers in verified
`summary.next_action` copy alongside ordinary answer and owner actions. Inbox
and Agents now show every reason Goal-derived Agent work is blocked, including
that Tony must provide private input for credential/token questions, while
still showing safe answer and recommended owner-assignment work. MC200-001
initially caught Inbox omitting the `Next action:` line; the repaired retest
verified Inbox renders it on desktop and mobile. This is visibility only for
private blockers: Mission Control still does not generate, prefill, or submit
secrets.
V0.0.201 fixes the resulting next-action grammar so ordinary answer,
private-input blocker, and owner-assignment clauses are separated with clear
semicolons. Agents and Inbox keep all three blockers visible without producing
misleading `. and assign` or `?. and` copy; private input remains
non-autofilled and excluded from plan writes, while the safe Family answer
plus recommended Timmy owner plan behavior is preserved.
V0.0.202 groups repeated private Goal blockers for the same Agent question so
duplicate credential prompts do not crowd out owner-assignment controls in
Agents or Inbox. The grouped item keeps `blocked_goal_count` and
`related_questions` visible while preserving the hard boundary: no answer form,
template, generated secret, or recommended-plan write for private input.
V0.0.203 repairs the live grouping shape by grouping same-Agent/same-question
private blockers even when detail text differs, and strips the remaining
`.; assign` punctuation from next-action copy. Postdeploy readback for
V0.0.203 showed one Tammy private action with `blocked_goal_count=3`, the
owner action still present, and `next_action` naming
`3 Tammy private-input blockers`.
V0.0.204 routes Artifact publisher identity mismatch blockers, including
`artifact_identity_mismatch`, to system-owned
`repair_artifact_publisher_identity` Action queue rows instead of Tony
private-input answer rows. The queue keeps safe business questions and owner
assignment visible, and `summary.next_action` can now say:
`Answer ...; repair Tammy Artifact publisher identity for 3 blocked Goals; assign Entrepreneurship...`.
Recommended plans remain limited to the safe answer plus recommended Timmy
owner assignment; there is no system/private auto-write.
V0.0.205/V0.0.206 complete the terminal handoff status repair path for the
Tammy Artifact publisher blocker tasks. Completed Agent handoff tasks with
stale `ready_for_agent` handoff frontmatter now reconcile after terminal
status and exact `produced_for` Artifact evidence, and the status endpoint
tolerates an invalid pre-mutation snapshot so a PATCH to completed/status can
repair canonical state and wake Goal execution instead of failing before the
repair. Post-repair readback restored and completed the Faith, Finance, and
Career Tammy Artifact publisher blocker tasks with one expected Artifact each.
Goal execution returned to three recently completed, one waiting-for-Tony
question, and one missing owner; Timmy and Toddy remain non-local.
Goal execution overhaul is paused after V0.0.206. The canonical pause handoff
is `docs/handoffs/2026-08-26-goal-execution-overhaul-pause.md`: it records
the verified V0.0.206 baseline, the intentionally uncommitted/unshipped
V0.0.207 stash boundary
`stash@{0}: On main: pause goal execution v0.0.207 suppressed handoff WIP`,
and the resume order. Do not apply that stash, continue implementation,
mutate GBrain, or treat the V0.0.207 tests as release evidence unless Tony
explicitly resumes the Goal.
The earlier Finance canary task
`tasks/3d54d11c-db8e-59bf-8039-e050fa763dc9` completed with canonical Artifact
`artifacts/b6acc5bc-4af2-42f2-a829-8c97e3dd0838`. OpenClaw remains excluded
from Goal execution.

### Codex Goal execution controls

Goal execution is owned by the dashboard-managed Mission Control runtime. Its
supported runtime controls are:

```text
MISSION_CONTROL_GOAL_EXECUTION_MODE=off|shadow|canary
MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL=goals/<uuid>|auto
```

`shadow` is the default and performs no canonical mutation. `canary` requires
one exact Goal slug or the explicit private target `auto`. A fixed Goal slug
may create or adopt at most one automatic Task for that Goal after canonical
eligibility, WIP, identity, and fixed-route checks pass. `auto` applies the
same one-task safety boundary but chooses the first currently `auto_eligible`
Goal for the run, avoiding a stale fixed canary after a prior Goal completes.
Every create, activation, and handoff requires exact canonical readback and a
deterministic derivation receipt. OpenClaw is excluded from this rollout. If a
canary cannot verify its Task or delivery path, switch back to `shadow`, retain
the canonical Task and receipts, and repair the named blocker instead of
creating a replacement.

Completed canonical Goal-derived tasks do not require dispatcher recovery just
because an older local wake or launch timed out. Exact completed derived Goal
review tasks suppress immediate repeats as `recently_completed`; cancelled or
materially changed candidates may still be eligible. Active Goal-derived work
with a suppressed or failed dispatcher state still surfaces repair attention
until the handoff history and canonical task state are reconciled.

Manual `GET /api/goal-execution?refresh=1` wakes the bounded scheduler before
returning status. It does not bypass canary/shadow mode, WIP, identity, or
handoff safety checks. When a canary Goal is already covered by duplicate or
recently completed work, the status includes the selected Task slug, title,
status, and Agent so operators can verify the canonical work item directly.
V0.0.161+ also preserves an accepted dispatcher handoff status on selected
duplicate or recently active task responses, allowing the UI to show live
Delivering or Executing state when the latest verified dispatcher status is
`queued` or `actively_executing`. This is readback context for already-selected
work; it does not broaden canary scope, mark all Goals automated, or replace
the completion requirements below.
V0.0.182+ mirrors the compact Goal execution summary at the response top level
and in `last_run.summary`. Readers can use `summary.needs_attention`,
`summary.waiting_for_tony`, `summary.owner_missing`, `summary.in_flight`,
`summary.recently_completed`, `summary.reasons`, and `summary.next_action` for
status dashboards without walking every decision row. The summary is read-only
projection data; it does not create ownership links, answer Tony-blocked work,
lease a worker, or complete a task.
V0.0.183+ also renders that same verified summary in the Agents view's Goal
execution panel, including `Next action:` guidance and the key counts:
total Goals, Needs attention, Waiting for Tony, Missing owner, In flight, and
Recently completed. The visible panel must match `/api/goal-execution.summary`
after readback and must not expose credentials, fixed-thread ids, or private
worker routes.
V0.0.184+ includes `summary.blocking_questions` and
`last_run.summary.blocking_questions` for waiting-for-Tony decisions when the
canonical question TODO can be read. Each entry carries the Goal, Task, TODO,
Agent, question text, and detail. Agents > Goal execution renders the question
with a `Question:` prefix so Tony can identify the exact blocker; it still
does not answer the question, remove the blocker, or authorize new Agent work.
V0.0.185+ includes `summary.missing_owners` and
`last_run.summary.missing_owners` for `owner_missing` decisions. Each entry
carries `goal_slug`, `goal_title`, `required_relationship:
default_agent_for`, and a repair message. Agents > Goal execution renders
`Missing owner: <Goal title> — add default_agent_for`; this is a visible
repair pointer, not a write, assignment, or ownership inference.
V0.0.186+ renders the blocking-question and missing-owner summary action items
as clickable exact controls. Waiting-for-Tony question controls open canonical
Task detail; missing-owner title controls open canonical Goal detail; closing
the detail restores focus to the exact summary origin. This remains GET-only,
read-only navigation and must not be documented as an answer, owner assignment,
handoff acknowledgement, worker wake, or repair mutation.
V0.0.187+ adds explicit Codex-only assignment controls to missing-owner summary
items. `Assign to Tammy`, `Assign to Timmy`, and `Assign to Toddy` call
`POST /api/agents/<agent>/default-goals` with `{goal_slug, action: "assign"}`
only after the operator activates a button and only for non-OpenClaw Agents.
These controls are a verified mutation path when activated; they are not
automatic owner inference, background repair, or OpenClaw assignment.
V0.0.188+ includes `summary.action_queue` and
`last_run.summary.action_queue` for owner-classified next actions. Queue
entries include `owner`, `kind`, `label`, relevant Goal/Task/TODO/Agent slugs,
and a bounded `summary`. The Agents panel renders the queue with owner labels
such as `Tony action required` so operators can distinguish Tony-owned
unblockers from Agent-active or system-action states without reading decision
rows directly.
V0.0.189+ renders Tony-owned `answer_question` queue entries as direct answer
actions. The `Answer Agent question` button opens the canonical Task, focuses
the existing handoff answer textarea after readback, and restores focus to the
exact immutable summary-origin control on Close. Actual submission remains the
existing verified `/api/todos/<todo>/answer` mutation flow; the action queue
button only navigates and focuses the answer field.
V0.0.190+ also renders an inline answer composer for the same Tony-owned
`answer_question` entries when the queue includes verified `todo_updated_at`.
The composer posts to the existing TODO answer endpoint with
`expected_updated_at` and a UUID idempotency key, then reconciles the verified
Task/TODO response and refreshes Goal execution and Agent Work. It is the same
canonical answer mutation path, surfaced closer to the blocker in the Agents
summary.
V0.0.191+ renders Tony-owned `assign_goal_owner` queue entries with inline
Codex assignment buttons in the Action queue itself. The controls are the same
explicit verified `default-goals` assignment path used by the preserved Missing
owner detail row; they do not infer a default Agent and they never expose
OpenClaw assignment.
V0.0.192+ includes `candidate_owners` metadata on missing-owner summaries and
Action queue `assign_goal_owner` entries. Candidate entries identify each
eligible Codex Agent, verified `default_goal_count`, `recommended`, and the
recommendation copy; the UI labels exactly one recommended owner by lowest
verified Codex Goal load. This recommendation is guidance for Tony's explicit
click, not automatic ownership repair or dispatcher mutation.
V0.0.193+ includes verified question `detail` on Tony-owned `answer_question`
Action queue entries and displays it near the inline answer composer. The
detail is the decision context Tony needs before submitting an answer through
the existing `/api/todos/<todo>/answer` flow; showing it does not itself
answer, acknowledge, wake, complete, or mutate anything.
V0.0.194+ derives `summary.next_action` from the verified Action queue, so it
names the exact waiting Agent question and the recommended missing-owner
assignment when both are present. Readers should treat this as a compact
operator instruction line backed by the existing queue entries, not as a
mutation receipt or proof that Tony already answered or assigned ownership.
V0.0.195+ includes `answer_template` on Tony-owned `answer_question` Action
queue entries and renders `Insert answer template` beside the inline answer
textarea. Activating the button only copies the draft into the editable
textarea; answer submission remains the separate verified
`/api/todos/<todo>/answer` POST path.
V0.0.196+ changes the default `answer_template` from placeholder options to a
concrete approval draft. Readers should still treat template insertion as
local editable text preparation only, not a submitted answer or mutation
receipt.
V0.0.197+ renders `summary.action_queue` in Inbox under `Goal execution
actions` when Tony-owned actions are present. The Inbox section is a central
surface for the same queue entries and controls already used in Agents; its
presence is not a separate mutation path or proof that the actions have been
completed.
V0.0.198+ may render `Run recommended unblock plan` in that Inbox section only
when one answerable question has a concrete `answer_template` and an
`assign_goal_owner` action has a recommended Codex Agent. The plan is an
explicit reviewed mutation shortcut, not automation: it uses the existing
answer and default-goals endpoints in sequence only after Tony activates it.
V0.0.199+ marks credential/token-like `answer_question` actions with
`private_input_required=true`. Those actions display private-input guidance
only, without `answer_template`, inline answer form, template insertion, or
recommended-plan participation; Tony must open the Task and answer directly
through the verified private-input path.
V0.0.200+ derives `summary.next_action` from safe answer actions, private
answer blockers, and recommended owner assignment together. Inbox renders the
same `Next action:` line in `Goal execution actions` as Agents, so central
triage sees private blockers too. Treat that copy as operator guidance only,
not generated private input, a submitted answer, plan eligibility, or a
mutation receipt.
V0.0.201+ formats that combined `summary.next_action` as separate clauses:
ordinary answer guidance, private-input guidance, owner assignment guidance,
then the continuing-work note. The copy must remain readable operator
guidance, without `. and assign` sentence breaks or implied secret autofill.
V0.0.202+ groups repeated private `answer_question` actions when they share
the same Agent and question summary, exposing `blocked_goal_count` plus
`related_questions` instead of rendering one noisy private row per blocked
Goal. V0.0.203+ intentionally ignores differing detail text for that grouping
key because the live Tammy credential blockers share the same question but
carry task-specific detail payloads. Grouping is a display/readback
compression only; it does not make private input answerable by template or
eligible for recommended plans.
V0.0.204+ classifies Artifact publisher identity mismatches as system-owned
`repair_artifact_publisher_identity` actions. These rows should render
`System action required` / `Repair Artifact publisher identity`, carry
`blocked_goal_count`, and remain excluded from Tony answer controls and
recommended-plan writes. They identify dashboard Artifact publisher credential
or identity repair work, not a request for Tony to paste secrets into an Agent
question.
Paused V0.0.207 WIP is not part of the deployed contract. If Tony resumes it,
start from the handoff resume order: refresh git/runtime/Goal execution/Agents
/Agent Work readbacks, confirm the committed version, apply the stash in a
clean branch or worktree, add the RED scheduler-selection-ordering test, then
implement, version, deploy, and get independent desktop/mobile QA before
commit.
In V0.0.167+ auto-canary mode, public status selection is ordered: first
activate the first currently `auto_eligible` Goal, then prefer an existing
duplicate/recent task with an accepted active dispatcher handoff, then surface
verified actionable blockers, then fall back to the newest recently completed
canary. The blocker set is `waiting_for_tony`, `handoff_needs_repair`,
`handoff_missing`, `task_needs_next_action`, and
`handoff_worker_unavailable`. This keeps the dashboard pointed at the next
repairable unblocker instead of foregrounding stale completed history, while
preserving the one-task canary safety boundary.
If a goal-derived duplicate decision points at an active or planned canonical
task without any verified Agent handoff, Goal execution reports
`handoff_missing` with Needs attention copy instead of ordinary duplicate
copy. Terminal handoff states remain `handoff_needs_repair`. If an active or
planned non-derived Agent goal task lacks an explicit next action, handoff,
blocker/dependency, and open TODO, Goal execution reports
`task_needs_next_action` with Needs attention copy so the assigned Agent gets a
repairable instruction gap instead of an actionable duplicate. That stalled
instruction-gap task does not consume the Goal execution WIP slot in
V0.0.154+, but actionable active Agent work with a real next action still
blocks additional automatic Goal review as `wip_full`.

If a Goal-derived active task has latest handoff status `queued` and the
dispatcher execution claim is still nonterminal after the bounded worker
attention window, Goal execution reports `handoff_worker_unavailable` with
Needs attention copy: `The canonical task is active and queued, but no
verified Agent worker has leased it yet. Verify the Agent host dispatcher and
private route.` Fresh queued handoffs remain Delivering. Treat this as
operator remediation for the Agent host, dispatcher, or private route; do not
claim Agent execution completion until a verified lease/delivery/completion
readback exists.

If a selected canary Goal-derived active task has latest dispatcher handoff
status `completed`, Goal execution may reconcile the canonical Task to
completed only after exact Artifact readback verifies at least one Artifact
with `produced_for` equal to that Task slug. The public reason is
`completed_after_verified_handoff`, with UI copy:
`Mission Control completed the canonical task after verified Agent handoff and
Artifact readback.` Missing Artifact evidence keeps the task active and the
Goal decision at ordinary duplicate/executing rather than fabricating
completion.

Goal-derived Agent review deterministic fingerprints include the review cycle
key in V0.0.157+. A completed prior-cycle review can suppress same-cycle
repeats, but it does not permanently suppress the next bounded review cycle.
The new cycle creates a different deterministic Task slug and includes the
cycle marker in the task detail, for example `Review cycle starts
2026-08-24`. Treat the newly activated task as live Agent work until its
handoff and Artifact evidence prove a later state.

Checkpointed suppressed handoffs are a separate terminal-success path in
V0.0.158+. A selected Goal-derived task can reconcile to
`completed_after_verified_handoff` when the latest handoff is `suppressed`,
the terminal execution claim readback says `terminal_state=checkpointed`, and
an exact Artifact with `produced_for` equal to that Task exists. V0.0.159 makes
that terminal checkpoint state readable after the active claim is released by
falling back to terminal execution claims. Suppressed handoffs without the
checkpoint state or without exact Artifact evidence remain
`handoff_needs_repair` / active.

Blocked Agent work waiting on Tony is not an instruction-gap duplicate. In
V0.0.160+, when a canonical Agent task is `blocked`, its canonical handoff is
`waiting_for_input`, and `waiting_on` is `people/tony-guan`, Goal execution
reports `waiting_for_tony` with Blocked copy:
`The canonical task is blocked waiting for Tony's answer before the assigned
Agent can continue.` The exact task detail API also hydrates the active
handoff question TODO from canonical `todo_for` backlinks so the handoff panel
can render the real question and answer flow. If bounded TODO hydration fails,
the response exposes a canonical TODO list unavailable issue, shown as
`The canonical TODO list is unavailable.`, but keeps the task and handoff
visible; operators should repair readback/data availability rather than
fabricate a new TODO or mark the task actionable.

V0.0.163+ surfaces the open question TODO text directly in waiting-for-Tony
Goal execution surfaces, including full Goal execution rows and compact Agent
cards. The current Family/Toddy copy is exactly `Answer: Which family-care
scope, outcomes, constraints, and first action should Toddy use next?`. This
question text comes from the canonical open TODO, not from generated copy, and
must remain tied to the same blocked task until Tony answers and hands it back.
V0.0.168/V0.0.169 also populate the selected waiting-for-Tony task context
directly in Goal execution status. Agents cold-load rendering may use
`last_run.task` before the separate Agent Work cache has reconciled, so the
headline can show the exact Task link/title/status/Agent immediately. This is
readback context only; the task remains blocked until Tony answers the
canonical question TODO and the Agent resumes through the normal handoff path.

V0.0.164+ recovers an expired owned execution claim only at an authenticated
claim boundary for the same registered Agent host, after Mission Control
verifies current task authority and preserves the owned/nondelegated execution
fence. The recovery sequence refreshes the execution claim before leasing the
handoff again; it is not a manual deletion path and does not apply to delegated
execution, arbitrary dead letters, mismatched Agent routes, or unverified
tasks. This repair makes stale queued Goal-derived Agent work recoverable after
the correct remote worker host returns, while preserving the distinction
between remote Agent hosts and this Mac's local worker set.

For remote worker host checks, use `scripts/verify_handoff_worker_runtime.py`
from the Agent's own host checkout. A passing report must include `ok: true`,
the expected `agent_slug`, expected `hosts/<agent>` route, exact repo HEAD, and
loaded LaunchAgent when a launch label is supplied. This is a read-only
verifier: it redacts tokens/registration IDs/fixed thread IDs and must not be
used as a substitute for installing Timmy or Toddy locally. Current readback:
Timmy passed on the Timmy host at commit
`f5a2aa77d44561a9d7279a185c184388759945ad`; Toddy remains blocked on host/SSH
control-plane reachability and must not be documented as recovered.
For fleet checks, use `scripts/verify_handoff_worker_fleet.py` with
`config/handoff-dispatcher/remote-workers.json`. A mixed result is meaningful:
the current `ok=1 failed=1` report verifies Timmy and preserves Toddy as a
host-access blocker, not as a recovered worker and not as permission to install
Toddy locally on this Mac.

V0.0.166+ preserves Goal execution availability around hidden malformed Agent
work only when that item is already surfaced as an Inbox data-quality issue and
excluded from Board. This is not a general ignore-errors mode: missing
canonical roots and visible unsafe Agent-work issues still fail closed so
operators must repair canonical state before automatic Goal planning proceeds.

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
The server also coalesces duplicate task, proposal, Project, System Ticket,
and Agent Work reads, caps aggregate GBrain command concurrency to prevent
multi-tab request stampedes, and stores the last verified projections in a
private `0600` local file. Slow refreshes run in the background: each surface
shows its own explicit refreshing, stale, or error state while independently
available data remains usable. Manual and automatic Refresh explicitly
invalidate the relevant projection; verified task mutations invalidate task,
proposal, and Agent Work projections together.

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

The dashboard launcher uses a dedicated remote-MCP thin-client home at
`/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-remote`.
Its checked-in launcher validates the owner-only runtime config and credential
files before starting Mission Control; secrets remain outside the repository.
The canonical `~/.gbrain` local-engine configuration is not reused or
overwritten.

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
Goals and owned tasks. Each Agent host uses a two-worker supervisor: one
isolated Codex worker and one isolated OpenClaw worker for that host's pair.
This Mac's local supervisor is currently Tammy/Tammy-OC only; Timmy and Toddy
are not local workers on this Mac and must be installed or recovered only on
their own host machines. Every OpenClaw worker resumes one pre-authorized fixed
session and never creates, replaces, forks, or guesses a session. Private
credentials and fixed-session identifiers stay under
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
edge. In V0.0.165+, a Goal execution `owner_missing` Needs attention state
names the exact repair: assign exactly one Codex Agent and verify the
`default_agent_for` link. Do not infer Goal ownership from prose, an Agent
profile, a task assignment, or a local worker install. A profile may later
provide an explicit safe `chat_url` and avatar
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
that unapproved work is permitted to execute. V0.0.152+ includes a read-only
`dispatcher_handoff` projection for non-completed Agent work when the handoff
store has a latest dispatcher status but the canonical task `handoff` field is
empty. The projection helps operators see recovery evidence in Agents and
fallback Task details; it never overwrites canonical handoff data, and
completed rows suppress it.

V0.0.178 keeps `/api/agent-work` on the same bounded last-verified cache model
as the other slow read surfaces: cold Agent Work reads return `202`/`loading`,
warm reads keep labeled verified data while refresh runs, and task mutations
invalidate the Agent Work projection. Completed and cancelled Agent-owned
history remains visible, but terminal history rows skip TODO backlink
hydration so old work does not dominate refresh latency; current
non-terminal Agent work still hydrates open TODOs.

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
