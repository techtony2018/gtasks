# Mission Control System Documentation Runbook

## Purpose and authority

This Runbook is the durable operating contract for the single fixed Mission
Control Documentation Manager task
`019fcb77-2886-7f31-a4d2-0b8bbe7e1477`.

The Documentation Manager maintains evidence-based system documentation. It
does not implement product code, create System Tickets, change task lifecycle,
deploy merely to make documentation agree, or bypass the implementation
owner's QA/release gates.

## Canonical shared registration

- Documentation collection:
  [`collections/mission-control-documentation`](http://127.0.0.1:8788/?slug=collections%2Fmission-control-documentation)
- System Overview:
  [`docs/f2516aa8-89ae-4570-a205-118d5c038ad7`](http://127.0.0.1:8788/?slug=docs%2Ff2516aa8-89ae-4570-a205-118d5c038ad7)
- Machine-readable Agent registration:
  [`config/agent-artifact-protocol/shared-documentation.json`](../../config/agent-artifact-protocol/shared-documentation.json)

The Overview is one canonical page with one typed
`member_of -> collections/mission-control-documentation` relationship. Agent
runtime instructions reference that shared collection; they must not copy the
page into Agent pages, work collections, Artifact collections, or
identity-specific prompts. Documentation membership is read-only context and
never execution authority. The global GBrain `index` is out of scope.

Direct read availability and installed prompt deployment are separate gates.
Verify the collection, Overview hash, and typed edge from every Agent host.
Then inspect installed prompts independently; a pushed template does not prove
that an installed automation has received it. Do not update external
automations from this documentation task without separate authority.

## When to refresh documentation

Refresh after a material Mission Control feature or architecture change is
actually released and verified. Do not refresh the current-state description
from a planned, blocked, uncommitted, QA-failed, or partially deployed
candidate. Keep those items in an explicitly labelled future/blocked section.

The six-Agent OpenClaw delegation candidate is governed by
[`openclaw-agent-delegation.md`](openclaw-agent-delegation.md). Until its
sequential live canaries and deployment are verified, the canonical System
Overview must describe Tammy-OC, Timmy-OC, and Toddy-OC only as an unreleased
candidate. After release, refresh the shared Overview once; do not duplicate it
into six identity pages. The exact identities are `agents/tammy`,
`agents/timmy`, `agents/toddy`, `agents/tammy-oc`, `agents/timmy-oc`, and
`agents/toddy-oc`. The three OpenClaw profiles begin with no default Goal.

For each refresh, inspect:

1. deployed `/api/health` version and the process working directory;
2. exact source commit and remote push state;
3. canonical System Ticket page, typed membership, implementation receipts,
   QA receipts, and final lifecycle readback;
4. automated tests, static checks, and independent desktop 1440x1000 plus
   genuine mobile 390x844 QA for UI-affecting work;
5. the live runtime behavior and affected APIs;
6. README, every affected version-controlled Runbook, Agent instruction source,
   and the canonical Overview for contradictions.

If implementation, deployment, and documentation disagree, report the
mismatch and require the implementation owner to resolve it. Do not modify
product code or canonical lifecycle from this documentation task.

## Refresh procedure

1. Reconcile `HEAD`, `origin/main`, tracked-worktree cleanliness, release
   metadata, and `GET http://127.0.0.1:4179/api/health`.
2. Refresh and deduplicate the canonical documentation collection and Overview
   before any write. Adopt the existing opaque Overview identity above; never
   create a replacement merely because its title or body changes.
3. Refresh System Tickets through
   `GET /api/system-tickets?refresh=1&include_completed=1`, then read the exact
   affected page and its typed links directly from GBrain. A stale projection
   is not release evidence.
4. Update the Overview with provenance for every material claim. Keep deployed,
   active, planned, blocked, and unknown states distinct. Preserve exact paths,
   commands, endpoints, owners, authority boundaries, recovery, rollback, and
   verification instructions where useful.
5. Perform the canonical documentation write sequence: supported page write,
   exact page readback, typed relationship write only when the membership is
   missing, and exact outgoing-link plus collection-backlink readback. The
   steady state must contain exactly one Overview membership edge.
6. Update README, all affected Runbooks, and the shared Agent instruction
   source to the same verified reality. Supersede stale instructions rather
   than adding contradictory notes.
7. Validate JSON, rendered Agent instructions, Markdown links/headings,
   commands, diff scope, GBrain page content hash/readback, discovery
   relationship, and unchanged global `index`.
8. Documentation-only changes may use the non-UI exception. Commit and push
   only the documentation/configuration paths after validation. Any UI change
   remains Developer-owned and requires the normal independent QA gate.

## Verification commands

```bash
curl -fsS http://127.0.0.1:4179/api/health
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
gbrain call get_page '{"slug":"docs/f2516aa8-89ae-4570-a205-118d5c038ad7"}'
gbrain call get_links '{"slug":"docs/f2516aa8-89ae-4570-a205-118d5c038ad7"}'
gbrain call get_backlinks '{"slug":"collections/mission-control-documentation"}'
python3 scripts/verify_agent_artifact_protocol.py tammy daytime
python3 scripts/verify_agent_artifact_protocol.py tammy nighttime
python3 scripts/verify_agent_artifact_protocol.py timmy daytime
python3 scripts/verify_agent_artifact_protocol.py timmy nighttime
python3 scripts/verify_agent_artifact_protocol.py toddy daytime
python3 scripts/verify_agent_artifact_protocol.py toddy nighttime
python3 -m unittest discover -s tests
```

For an Overview body large enough to risk command-line or pipe limits, write
from a reviewed local file through the supported `gbrain put <slug>` stdin
contract, then compare `get_page.compiled_truth`, frontmatter, and content hash
to the reviewed source before updating relationships.

## Failure recovery and rollback

- If a page write succeeds but readback differs, stop before relationship
  mutation. Preserve the returned slug/hash and inspect `gbrain history`.
- If page readback succeeds but membership verification fails, do not delete or
  create another page. Re-read both directions, then repair only the one exact
  typed edge if authorized.
- If a documentation update is factually wrong, restore the last verified
  GBrain page version with the supported history/revert contract, read it back,
  and correct repository documentation in a focused follow-up commit.
- If a product rollback is required, hand it to the implementation owner. After
  rollback, re-run health/version/runtime checks before changing documentation.
- Never expose credentials, raw event payloads, private Calendar content, or
  unredacted logs in documentation or verification output.

## Current verification baseline

- Last verified released baseline date: `2026-08-24`
  (`America/Los_Angeles`)
- Last verified pushed release: `V0.0.162`
- Release commits:
  `351fb99bc6c216e7d3a5558c425c7c176fa85a51`
- Service: `http://127.0.0.1:4179/`
- Health: `http://127.0.0.1:4179/api/health`
- Canonical store: `gbrain`
- V0.0.136 evidence: full suite `1331` OK, independent QA PASS at
  `artifacts/qa/v0.0.136-independent/gate-report.md`, and deployed health
  readback `V0.0.136`.
- V0.0.137 evidence reported by Developer handoff: focused recovery tests `5`
  OK, handoff dispatcher/supervisor `183` OK, full suite `1332` OK,
  release/handoff targeted `208` OK, deployed health readback `V0.0.137`, and
  supervisor restart with `--codex-resume-timeout 1800`.
- Verified V0.0.136/V0.0.137 behavior: exact completed derived Goal review
  tasks suppress immediate duplicate canaries as `recently_completed`; the
  Finance canary task `tasks/3d54d11c-db8e-59bf-8039-e050fa763dc9` completed
  for `agents/tammy`, retained the Finance Goal and Project links, published
  Artifact `artifacts/b6acc5bc-4af2-42f2-a829-8c97e3dd0838`, and its handoff
  events reached `completed` plus `execution_claim_released`; Goal execution
  mode was restored to `shadow`.
- V0.0.141 through V0.0.144 evidence: dashboard-managed health readback
  `V0.0.144`; full suite `1336` OK with `5` skipped and focused
  handoff/local-dispatcher suites passed per Developer release handoff.
  Verified behavior: stale local `abandon_start` rows reconcile through
  authoritative `/recover` completed/suppressed state; `codex_thread_active_writer`
  is retryable local backpressure; owned `terminal_delivery_failure` handoffs
  can be operator-recovered only with abandoned-start proof; active-writer
  retries back off for 300 seconds.
- Live recovery examples: Finance handoff `handoff-3369...` read back
  `completed` for `tasks/3d54d11c-db8e-59bf-8039-e050fa763dc9`; Career
  handoff `handoff-806...` read back `received` with
  `system_dependency_recovered` after operator recovery, and active-writer
  retries no longer dead-letter immediately.
- V0.0.145 evidence: dashboard-managed health readback `V0.0.145`; pushed
  commit `573d56c`; `python3 -m py_compile gtasks/local_handoff_dispatcher.py`
  passed; focused new regression `1/1` OK; handoff local/launch/dispatcher
  targeted suites `222` OK; earlier full suite before commit `1337` OK with
  `5` skipped per Developer handoff.
- Verified V0.0.145 behavior: local Codex handoff recovery cancels an unused
  pre-gate launch and clears the wake inbox when authoritative Mission Control
  recovery proves the same handoff is already completed or suppressed. Live
  readback for Career handoff `handoff-806...` showed local inbox
  `completed` with `last_error=server_completed`, server handoff `completed`
  with reason `system_dependency_recovered`, target task
  `tasks/a6251324-1af6-5005-8a17-0ad0610be4d8` completed, and Artifact
  `artifacts/32142bd1-8b1b-4ffc-a115-87fd39d7f6d7` created by
  `agents/tammy` for the same Career task/Goal.
- V0.0.146 through V0.0.149 evidence: dashboard-managed health readback
  `V0.0.149`; release commits `9931700`, `d895ea3`, `5058a53`, and
  `b25797c`; full suites reported as `1338`, `1339`, `1340`, and `1341` OK
  with `5` skipped after the four releases.
- Verified V0.0.146/V0.0.147 behavior: exact Task readback accepts legacy
  pages that omit optional `detail` as empty-detail tasks, and the GBrain
  adapter falls back from missing legacy `summary` to the canonical page title
  while raw `Task.from_page` domain validation remains strict for missing
  summary.
- Verified V0.0.148/V0.0.149 behavior: `GET /api/goal-execution?refresh=1`
  wakes the bounded scheduler before status readback; live readback showed the
  Career canary remains `recently_completed` for
  `tasks/a6251324-1af6-5005-8a17-0ad0610be4d8` and now includes selected Task
  title, status `completed`, and Agent `agents/tammy`. Agent roster readback
  returned `6` agents with `issues: []`.
- V0.0.150/V0.0.151 evidence: dashboard-managed health readback `V0.0.151`;
  release commits `63e1233` and `30e717d`; full suite reported `1343` OK with
  `5` skipped; independent QA PASS at
  `artifacts/qa/v0.0.151-independent/gate-report.md`.
- Verified V0.0.150/V0.0.151 behavior: active or planned goal-derived
  duplicate decisions with no verified Agent handoff project as
  `handoff_missing` / Needs attention with exact copy `The canonical task is
  active, but no verified Agent handoff is recorded yet.` Terminal handoff
  statuses remain `handoff_needs_repair`; ordinary `duplicate` and
  `recently_completed` remain distinct. Live Civic remains ordinary
  `duplicate` because handoff-store readback showed task
  `tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92` latest status
  `completed`/`system_dependency_recovered`.
- V0.0.152 evidence: dashboard-managed health readback `V0.0.152`; release
  commit `a1bd229`; full suite reported `1344` OK with `5` skipped;
  independent QA PASS at `artifacts/qa/v0.0.152-independent/gate-report.md`.
- Verified V0.0.152 behavior: `/api/agent-work` projects latest dispatcher
  handoff status for non-completed Agent tasks as `dispatcher_handoff` without
  overwriting the canonical `handoff` field. Completed Agent-work rows suppress
  that projection. Live Civic Agent-work readback showed
  `tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92` active with canonical
  `handoff: null` and `dispatcher_handoff: {"status": "completed"}`.
- V0.0.153 evidence: dashboard-managed health readback `V0.0.153`; release
  commit `27900b9`; full suite reported `1345` OK with `5` skipped;
  independent QA PASS at `artifacts/qa/v0.0.153-independent/gate-report.md`.
- Verified V0.0.153 behavior: Goal execution flags active or planned
  non-derived Agent goal tasks with blank `next_action`, no handoff, no
  blockers/dependencies, and no open TODO as `task_needs_next_action` / Needs
  attention with exact copy `The canonical task is active, but it has no
  explicit next action for the assigned Agent.` Actionable duplicate, passive
  scheduled waits, `handoff_missing`, `handoff_needs_repair`, and
  `recently_completed` remain distinct. Live Family/Toddy readback showed
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` projecting
  `task_needs_next_action`.
- V0.0.154 evidence: dashboard-managed health readback `V0.0.154`; release
  commit `3e4e2a5`; full suite reported `1346` OK with `5` skipped;
  independent QA PASS at `artifacts/qa/v0.0.154-independent/gate-report.md`.
- Verified V0.0.154 behavior: Goal execution WIP accounting ignores active
  Agent tasks that are themselves missing explicit `next_action`, handoff,
  blocker, dependency, and open TODO evidence, so one stalled
  `task_needs_next_action` item does not block another bounded Goal review from
  becoming `auto_eligible`. Active Agent work with a real `next_action` still
  consumes WIP. Live readback kept Family/Toddy task
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` at
  `task_needs_next_action`, while Toddy Goal
  `goals/d175890b-6e89-5543-b587-b5df345c1c81` projected `auto_eligible` with
  task `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, not `wip_full`, in the
  release/QA evidence. Documentation validation later read the same Goal as
  `duplicate` for that task, still not `wip_full`.
- V0.0.155 evidence: dashboard-managed health readback `V0.0.155`; release
  commit `05dce491ab298a5c11c05b791e2f4c0a683de4f6`; full suite reported
  `1348` OK with `5` skipped; independent QA PASS at
  `artifacts/qa/v0.0.155-independent/gate-report.md`.
- Verified V0.0.155 behavior: Goal execution surfaces
  `handoff_worker_unavailable` when a Goal-derived active task has latest
  handoff status `queued` and the dispatcher execution claim remains
  nonterminal after the bounded worker attention window. The UI copy is exactly
  `The canonical task is active and queued, but no verified Agent worker has
  leased it yet. Verify the Agent host dispatcher and private route.` Fresh
  queued handoffs remain Delivering. Live Toddy Health readback showed
  `goals/d175890b-6e89-5543-b587-b5df345c1c81` projecting
  `handoff_worker_unavailable` for
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, latest handoff status
  `queued`.
- V0.0.156 evidence: dashboard-managed health readback `V0.0.156`; release
  commit `7f779030695076763f871a166ac67ee2253b95c2`; independent QA PASS at
  `artifacts/qa/v0.0.156-independent/gate-report.md` with frozen aggregate
  `6c36412229ea5a32652168b8a717f032f503b6e3cbf67d04f3163c226181876f`;
  focused Goal execution/frontend contract suite reported `219` passed.
- Verified V0.0.156 behavior: Goal execution reconciles a selected canary
  Goal-derived active Task to completed only after latest dispatcher handoff
  completion plus exact `produced_for` Artifact readback. Missing Artifact
  evidence leaves the Task active/duplicate. Live readback showed Civic task
  `tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92` completed at
  `2026-08-24T03:25:19.000864-07:00`; Artifact
  `artifacts/4fb85655-dc13-4050-b3a3-0c56b27acb9f` still has
  `produced_for` set to that task; `/api/goal-execution?mode=shadow` now
  reports Civic as `recently_completed`.
- V0.0.157 evidence: dashboard-managed health readback `V0.0.157`; release
  commit `5b9dbb1cdfe372e1a3b067230749172ba30ab193`; independent QA PASS at
  `artifacts/qa/v0.0.157-independent/gate-report.md` with frozen aggregate
  `dc485a26f8439327c4276b5b0ca429010291d6e6d5053dbff4f3c6f63ace94cd`;
  focused Goal execution suite reported `39` passed.
- Verified V0.0.157 behavior: Goal-derived Agent review Tasks include a cycle
  key in the deterministic fingerprint. A completed prior-cycle review no
  longer permanently suppresses the next bounded review cycle; same-cycle
  completed work still suppresses repeat creation. QA verified different
  deterministic task slugs for cycles `2026-08-17` and `2026-08-24`.
- Live V0.0.157 activation: dashboard-managed scheduler created and activated
  current-cycle Civic/Timmy task
  `tasks/44e14ea5-0f81-558b-a761-ec3540f3b4e2`, owner `agents/timmy`, Goal
  `goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10`; detail includes
  `Review cycle starts 2026-08-24`; dispatcher handoff readback reached
  `actively_executing` during the deploy handoff. Later documentation readback
  showed the task still active with `dispatcher_handoff.status=suppressed`; no
  Artifact was present at the bounded handoff check.
- V0.0.158 evidence: release commit
  `3fedf27c404c1d5e7c371c546a069c29c74293b0`; independent QA PASS at
  `artifacts/qa/v0.0.158-independent/gate-report.md` with frozen aggregate
  `fe3281d1deb2799278c2e1c54d95c50a1a69d62f19977ce2a02cf76753d10928`;
  focused Goal execution suite reported `40` passed.
- Verified V0.0.158 behavior: checkpointed `suppressed` handoff plus exact
  `produced_for` Artifact reconciles as `completed_after_verified_handoff`;
  ordinary completed handoff plus Artifact still reconciles; suppressed
  without checkpoint or without exact Artifact remains attention/active.
- V0.0.159 evidence: dashboard-managed health readback `V0.0.159`; release
  commit `f070f1af2824958e6655d9ba8552278e72624446`; independent QA PASS at
  `artifacts/qa/v0.0.159-independent/gate-report.md` with frozen aggregate
  `fddfe686453ae1c455f14b7ece5ff324c44d3f76b4618915139477b082a8ce1d`;
  focused Goal execution tests reported `40` passed.
- Verified V0.0.159 behavior: `CanonicalHandoffEventBridge.latest_task_handoff_delivery_state()`
  falls back to terminal execution claims, so released checkpointed claims
  expose `terminal_state=checkpointed` instead of losing the state after
  `include_terminal=False`. Live final readback showed Civic/Timmy task
  `tasks/44e14ea5-0f81-558b-a761-ec3540f3b4e2` completed at
  `2026-08-24T04:08:08.447338-07:00`; exact Artifact
  `artifacts/6e6c331e-a181-4d8f-ab16-cda613b8fed9` was created by
  `agents/timmy` and has `produced_for` set to that task.
- V0.0.160 evidence: dashboard-managed health readback `V0.0.160`; release
  commit `682c28e0b86a1f18357502bfd4e68f7777368536`; independent QA PASS at
  `artifacts/qa/v0.0.160-repair-independent/gate-report.md` with frozen
  aggregate
  `0eefffc8cdae5ced352b50e8c2d74f47cde70d360a3ec1e911caee1979c4a836`; full
  suite reported `1356` OK with `5` skipped; `node --check static/app.js` and
  `python3 -m compileall -q gtasks tests` passed.
- Verified V0.0.160 behavior: Goal execution classifies canonical Agent tasks
  with status `blocked`, canonical handoff state `waiting_for_input`, and
  `waiting_on=people/tony-guan` as `waiting_for_tony` / Blocked rather than
  `task_needs_next_action` / Needs attention. The UI copy is exactly
  `The canonical task is blocked waiting for Tony's answer before the assigned
  Agent can continue.` Exact task detail hydrates the active handoff question
  TODO from canonical `todo_for` backlinks. Bounded TODO hydration issues
  surface as a canonical TODO list unavailable issue, shown as
  `The canonical TODO list is unavailable.`, while preserving task/handoff
  visibility. Live readback showed Family/Toddy task
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` blocked with handoff
  `waiting_for_input`, question TODO
  `todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, blocker
  `people/tony-guan`, and Goal
  `goals/2c86f86c-c9fb-5f49-96d0-e4d63f489fc8` projecting
  `waiting_for_tony`.
- V0.0.161 evidence: dashboard-managed health readback `V0.0.161`; release
  commit `28a9c8de5f3191f261c91adfa04d156f0667c5c6`; independent QA PASS at
  `artifacts/qa/v0.0.161-independent/gate-report.md` with frozen aggregate
  `8b4d97e103e48ea4b61dfc2d2787f1a84ab16ced2cadbd2a740319a7f920e5fd`; full
  suite reported `1357` OK with `5` skipped; `node --check static/app.js` and
  `python3 -m compileall -q gtasks tests` passed.
- Verified V0.0.161 behavior: Goal execution retains accepted dispatcher
  handoff status for selected duplicate or recent active tasks so the UI can
  render Delivering or Executing instead of ambiguous Ready or duplicate-only
  context. Independent QA verified a Faith/Tammy active duplicate fixture with
  last-run `handoff.status=actively_executing` rendered `Executing` on desktop
  1440x1000 and genuine mobile 390x844. Runtime canary target was rotated from
  Toddy Health to Faith/Tammy; live readback showed Goal-derived task
  `tasks/46ba34c2-9ccb-523e-a786-9b70d5673073` completed for `agents/tammy`
  and Artifact `artifacts/d2a45c21-1428-4891-ae98-531a958e1e98` created by
  `agents/tammy` with `produced_for` that task and `supports_goal` pointing to
  `goals/755548a3-d556-513a-900c-45f90da5702e`.
- Current V0.0.161 next-owner blockers: Family/Toddy task
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` remains legitimately blocked
  waiting for Tony via question TODO
  `todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`; Toddy Health task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5` remains infrastructure-blocked
  with dispatcher status `queued` / `handoff_worker_unavailable` until the
  Toddy fixed-thread host is logged into Tailscale and its dispatcher route is
  available. Do not document either as completed Agent execution.
- V0.0.162 evidence: dashboard-managed health and releases readback
  `V0.0.162`; release commit
  `351fb99bc6c216e7d3a5558c425c7c176fa85a51`; release evidence file
  `docs/release-evidence/v0.0.162.md`; independent QA PASS at
  `artifacts/qa/v0.0.162-independent/gate-report.md` with frozen aggregate
  `fbf32c0f28353053daf0d8db38a0ad16222104b1362fbc5ca89291b678f7b847`; full
  suite reported `1360` OK with `5` skipped; focused/static checks passed,
  including `node --check static/app.js` and
  `python3 -m compileall -q gtasks tests`.
- Verified V0.0.162 behavior: dashboard-managed canary mode accepts explicit
  private target `auto`. `auto` still activates at most one Goal-derived Agent
  Task per run, but chooses the first currently `auto_eligible` Goal instead
  of staying pinned to a fixed completed canary. When no new eligible exists,
  public status prioritizes active accepted handoff, then newest recently
  completed canary, then attention/blocker states. Live readback showed
  Finance/Tammy task `tasks/cc655813-1968-5264-a5ad-454199c1b3cb` completed
  with Artifact `artifacts/9362d402-0f7c-4d65-9222-a8c140f1d9d3`, and
  Career/Tammy task `tasks/53264f17-e5d5-5b5d-ad36-af1eadc1a770` completed
  with Artifact `artifacts/fbffd8c1-b04e-420f-8db3-14be7a2b7f8f`.
  Post-deploy `/api/goal-execution` surfaces Career as newest
  `recently_completed`, Finance as `recently_completed`, Family/Toddy as
  separate `waiting_for_tony`, and Toddy Health as separate
  `handoff_worker_unavailable`.
- GBrain documentation readback during this refresh found the canonical
  Overview and exactly one
  `member_of -> collections/mission-control-documentation` discovery edge.

Known documentation-quality issue at this baseline:
`collections/mission-control-system-tickets` still lists legacy `waiting` in
frontmatter while the deployed contract writes only `blocked`. The
Documentation Manager must not mutate that ticket-owned collection merely to
hide the mismatch; its owner should reconcile it through the supported
contract.

Current operational caveat at this baseline: V0.0.142 through V0.0.145
supersede the earlier V0.0.137 active-writer handling. Active writer is local
backpressure with bounded retry/backoff, pre-gate local cleanup is allowed only
after authoritative completed/suppressed recovery, and operator recovery from
`terminal_delivery_failure` remains limited to owned handoffs with verified
abandoned execution starts. The Documentation Manager must not document this
as general dead-letter recovery or permission to duplicate Codex launches.
V0.0.148 manual Goal execution refresh is still bounded scheduler wakeup, not
unbounded autonomous execution. V0.0.151 missing-handoff attention is a
readback/repair signal; it does not fabricate handoff delivery or mutate the
canonical task. V0.0.152 `dispatcher_handoff` is also read-only projection
evidence; it must not be treated as canonical `handoff` content or as
completion of a non-completed task. V0.0.153 `task_needs_next_action` is an
instruction-gap signal; it does not create the missing next action, handoff, or
TODO and must not be merged with duplicate or handoff-repair states. V0.0.154
does not make stalled work complete or actionable; it only prevents that
non-actionable item from consuming the automatic Goal WIP slot. V0.0.155
`handoff_worker_unavailable` is also a worker/route repair signal. The current
Toddy Health case is caused by an unreachable private route while Toddy's host
Tailscale session is logged out; do not document it as a leased handoff,
delivered worker execution, or completed Agent task. V0.0.156
`completed_after_verified_handoff` is narrowly gated by dispatcher completion
plus exact Artifact `produced_for` readback; do not use it to complete tasks
that only have a terminal handoff, only have an Artifact without the exact task
relationship, or still have an unresolved worker/route blocker. V0.0.157
cycle-keyed review fingerprints permit the next bounded cycle but do not imply
unbounded recurring execution; same-cycle completed work still suppresses
repeat creation, and an activated current-cycle task remains live Agent work
until handoff and Artifact evidence prove a later state. Toddy's queued
handoff still requires the previously reported Tailscale host login repair.
V0.0.158/V0.0.159 checkpointed-suppressed reconciliation is also narrowly
gated: terminal checkpoint state and exact Artifact `produced_for` evidence are
required. Suppressed without checkpoint, missing terminal checkpoint readback,
or missing exact Artifact must remain repair attention/active. V0.0.160
`waiting_for_tony` is a blocked user-input state, not missing next action and
not completed Agent execution. A TODO hydration issue preserves task/handoff
visibility and should be treated as readback/data-availability repair; it does
not authorize creating a replacement TODO, bypassing the Tony answer, or
marking the task actionable. V0.0.161 active handoff-status projection is also
readback context only: it can show Delivering or Executing for selected
duplicate/recent active work, but it does not automate all Goals, expand the
single-canary boundary, or satisfy completion without terminal handoff and
exact Artifact evidence. V0.0.162 `auto` is also a private canary target under
the same one-task safety boundary. It is not unlimited multi-Agent automation,
and remaining blockers still require their next owners: Tony for the
Family/Toddy question TODO and Toddy host dispatcher/private route availability
for Toddy Health.
