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

- Last verified released baseline date: `2026-08-26`
  (`America/Los_Angeles`)
- Last verified pushed release: `V0.0.200`
- Release commits:
  `5560d8f342674e29a3eac97dec3c3ce3f566027f`
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
- V0.0.163 evidence: dashboard-managed health and releases readback
  `V0.0.163`; release commit
  `c39f726034d7579959337abf759cdb92706dd132`; release evidence file
  `docs/release-evidence/v0.0.163.md`; independent QA PASS at
  `artifacts/qa/v0.0.163-independent/gate-report.md` with frozen aggregate
  `2544cd274a848a35eaf3dd080c8a1814f1730da6e229dcc65074cab3ca657539`;
  focused/static checks reported `263` OK for
  `tests.test_frontend_contract` plus `tests.test_releases`;
  `node --check static/app.js` and
  `python3 -m compileall -q gtasks tests` passed; full suite reported `1360`
  OK with `5` skipped.
- Verified V0.0.163 behavior: Goal execution rows and compact Agent cards
  surface exact open question TODO text for Goal-derived work waiting on Tony.
  Current Family/Toddy surfaces show exactly `Answer: Which family-care scope,
  outcomes, constraints, and first action should Toddy use next?` for task
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` and question TODO
  `todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`. Task lookup merges richer
  Agent-work projections with same-slug snapshot rows so handoff/TODO context
  is not hidden by thinner cached rows. Finance/Tammy and Career/Tammy remain
  completed with exact `produced_for` Artifacts:
  `tasks/cc655813-1968-5264-a5ad-454199c1b3cb` /
  `artifacts/9362d402-0f7c-4d65-9222-a8c140f1d9d3`, and
  `tasks/53264f17-e5d5-5b5d-ad36-af1eadc1a770` /
  `artifacts/fbffd8c1-b04e-420f-8db3-14be7a2b7f8f`.
- V0.0.164 evidence: dashboard-managed health and releases readback
  `V0.0.164`; release commit
  `f9e9e424b6a57df0dc8fc2d5873912aca88efadf`; release evidence file
  `docs/release-evidence/v0.0.164.md`; independent QA PASS at
  `artifacts/qa/v0.0.164-independent/gate-report.md` with frozen aggregate
  `423fd743695dcd53c0ea688620c87409ad0fda75e78973639a3f019a1fd09846`; full
  regression reported `1361` OK with `5` skipped.
- Verified V0.0.164 behavior: the handoff dispatcher recovers expired owned
  `execution_claim` rows at the next authenticated claim boundary for the same
  registered Agent host, after verifying current task authority and preserving
  the owned/nondelegated execution fence. QA readback confirmed this Mac's
  local supervisor has exactly `agents/tammy` and `agents/tammy-oc` worker
  configs and no local Timmy/Toddy worker. Documentation must preserve this
  boundary: Timmy/Toddy worker recovery belongs on their own host machines, not
  by installing Timmy/Toddy local workers on this Mac.
- Post-release verifier evidence: commit
  `f5a2aa77d44561a9d7279a185c184388759945ad` added
  `scripts/verify_handoff_worker_runtime.py`,
  `tests/test_handoff_worker_runtime_verifier.py`, and the read-only remote
  worker verification procedure. Reported checks: full regression `1364` OK
  with `5` skipped before commit; focused verifier/release tests `84` OK;
  Timmy host fast-forwarded and verifier PASS with `ok: true`,
  `agents/timmy`, route `hosts/timmy`, loaded launch label, and repo HEAD
  exactly `f5a2aa77d44561a9d7279a185c184388759945ad`. Toddy host/SSH/control
  plane remains unreachable and must not be documented as recovered.
- Fleet verifier evidence: commit
  `d7622b7272df3c8979d1db8e6b0c7b396c7a093c` added non-secret inventory
  `config/handoff-dispatcher/remote-workers.json`, fleet CLI
  `scripts/verify_handoff_worker_fleet.py`, tests
  `tests/test_handoff_worker_fleet_verifier.py`, and fleet runbook
  instructions. Reported checks: full regression `1367` OK with `5` skipped;
  focused verifier/release tests `87` OK. Final fleet result was `ok=1
  failed=1`: Timmy `ok: true`, route `hosts/timmy`, preflight verified, launch
  loaded, repo head exact `d7622b7`; Toddy `ok: false`, issue
  `ssh_unreachable` for `toddy@100.117.212.20`.
- V0.0.165 evidence: dashboard-managed health and releases readback
  `V0.0.165`; release commit
  `4778793e60bb201393afbafc89a2e81079229d9d`; release evidence file
  `docs/release-evidence/v0.0.165.md`; independent QA PASS at
  `artifacts/qa/v0.0.165-independent/gate-report.md` with frozen aggregate
  `261c5f4e5b9b0d990088ca0a4f56169c614580d0b1f48b351bab85d58b917fb1`;
  focused frontend/release contracts `263` passed; desktop `1440x1000` and
  genuine mobile `390x844` PASS.
- Verified V0.0.165 behavior: Goal execution `owner_missing` copy now names
  the exact repair. Operators must assign exactly one Codex Agent and verify
  the single `default_agent_for` link for the Goal. QA readback confirmed the
  visible copy contains both `Assign exactly one Codex Agent` and
  `default_agent_for`, and the local supervisor still contains exactly
  `agents/tammy` and `agents/tammy-oc`.
- V0.0.166 evidence: dashboard-managed health and releases readback
  `V0.0.166`; release commit
  `7b57e945afa70ed47761d20f62be156bb785ee33`; release evidence file
  `docs/release-evidence/v0.0.166.md`; independent QA PASS at
  `artifacts/qa/v0.0.166-independent/gate-report.md` with frozen aggregate
  `c9fd2484758bde53b55767930659607c75f8d99dc7cea56bdaab73b789e7adaa`;
  desktop `1440x1000` and genuine mobile `390x844` PASS; Developer reported
  focused `141` OK and full regression `1369` OK with `5` skipped.
- Verified V0.0.166 behavior: non-visible malformed Agent work items already
  reported in Inbox no longer abort the whole Goal execution scheduler. Missing
  canonical roots and visible unsafe Agent-work issues still fail closed. QA
  and documentation refresh readback showed `/api/goal-execution` with
  top-level `last_error: null` and a populated 13-decision `last_run`.
  Malformed Tammy task `tasks/78147b5d-7385-431e-ae1a-cf710a160910` remains an
  Inbox data-quality warning with `task_visible: false` and is excluded from
  Board.
- Post-release fleet verifier HEAD-pinning evidence: commit
  `8d4f31b458286ac9750b4b1e3a9f1b375189ff96` changed
  `scripts/verify_handoff_worker_fleet.py` so omitted `--expected-commit`
  defaults to this checkout's local HEAD instead of allowing each remote
  worker to validate against its own stale repo HEAD. Focused verifier tests
  `tests.test_handoff_worker_fleet_verifier` plus
  `tests.test_handoff_worker_runtime_verifier` reported `7` OK. Timmy remote
  host `toddy@100.100.126.85` was fast-forwarded to
  `8d4f31b458286ac9750b4b1e3a9f1b375189ff96`, LaunchAgent restarted, and
  runtime verifier PASS. Fleet verifier now reports Timmy OK against current
  HEAD and Toddy still `ssh_unreachable` at `toddy@100.117.212.20`.
- Post-release fleet verifier diagnostic evidence: commit
  `6984f24c1fe330aca68fd95adc0a80dbcc9b4428` added safe Tailscale diagnostics
  to worker fleet verifier SSH failures, including peer metadata and issues
  such as `tailscale_key_expired` and `tailscale_peer_offline`. Focused
  runtime/fleet verifier tests reported `8` OK. Timmy remote host
  `toddy@100.100.126.85` was fast-forwarded to
  `6984f24c1fe330aca68fd95adc0a80dbcc9b4428` and runtime verifier PASS. Fleet
  verifier now reports Timmy OK and Toddy blocked with `ssh_unreachable` plus
  `tailscale_key_expired`; Toddy peer metadata is `Toddy's Mac Mini-1`, DNS
  `toddys-mac-mini-1.taildb46a7.ts.net.`, and IP `100.117.212.20`.
- V0.0.167 evidence: dashboard-managed health and releases readback
  `V0.0.167`; release commit
  `5a3a51c81196cfcfdcbce3722802b90e58271d25`; release evidence file
  `docs/release-evidence/v0.0.167.md`; independent QA PASS at
  `artifacts/qa/v0.0.167-independent/gate-report.md` with frozen aggregate
  `8b44466616156a9491f18db25da846641ecd70bb795f8fb0e5a0d34525a97df6`;
  desktop `1440x1000` and genuine mobile `390x844` PASS; Developer reported
  focused `126` OK and full regression `1371` OK with `5` skipped.
- Verified V0.0.167 behavior: auto Goal execution still selects active or
  eligible work first, but actionable blocker states now surface before
  `recently_completed` history. The blocker states are `waiting_for_tony`,
  `handoff_needs_repair`, `handoff_missing`, `task_needs_next_action`, and
  `handoff_worker_unavailable`. Post-deploy `/api/goal-execution` readback
  reported `public_reason=waiting_for_tony`, `decision_count=13`, and
  `last_error=null`.
- V0.0.169 evidence: dashboard-managed health and releases readback
  `V0.0.169`; release commit
  `066bca00433deb313b79b2383b0156677c38c2e6`; release evidence file
  `docs/release-evidence/v0.0.169.md`; independent QA PASS at
  `artifacts/qa/v0.0.169-independent/gate-report.md` with structured results
  `artifacts/qa/v0.0.169-independent/gate-results.json`; desktop `1440x1000`
  and genuine mobile `390x844` PASS. Developer verification reported focused
  `128` OK, full regression `1372` OK with `5` skipped, and JS syntax OK.
- Verified V0.0.169 behavior: waiting-for-Tony Goal execution headline now
  carries populated task context (`slug`, `title`, `status`, `agent_slug`).
  Agents cold-load UI uses `goal-execution.last_run.task` before Agent Work
  reconciliation, so Family/Toddy shows the exact Task link immediately. Live
  readback showed `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, title
  `Prepare family-care goal map and weekly review brief`, status `blocked`,
  and agent `agents/toddy`.
- Local worker boundary reaffirmed for V0.0.169: this Mac has only
  `agents/tammy` and `agents/tammy-oc` local configs. Documentation must not
  describe Timmy/Toddy as local workers; their workers must be verified or
  repaired on their own remote hosts.
- V0.0.182 evidence: dashboard-managed health and releases readback
  `V0.0.182`; release commit
  `94e383595b0e7d6f991fa333a87596fc5c8d02d0`; release evidence file
  `docs/release-evidence/v0.0.182.md`; independent QA PASS at
  `artifacts/qa/v0.0.182-independent/gate-report.md` with frozen aggregate
  `3c6aa4eaa08f3e88ba682437e6ca11697d94c2401e62764e861808640995c335`;
  desktop `1440x1000` and genuine mobile `390x844` PASS. Developer
  verification reported `python3 -m unittest discover -s tests` as `1393` OK
  with `5` skipped.
- Verified V0.0.182 behavior: `/api/goal-execution` exposes a compact reader
  summary at both top level and `last_run.summary` with `total_goals`,
  `needs_attention`, `waiting_for_tony`, `owner_missing`, `ready`,
  `in_flight`, `recently_completed`, per-reason `reasons`, and bounded
  `next_action` guidance. Postdeploy readback reported `total_goals=7`,
  `needs_attention=2`, `waiting_for_tony=1`, `owner_missing=1`,
  `in_flight=1`, `recently_completed=3`, and `next_action` present.
- V0.0.183 evidence: dashboard-managed health and releases readback
  `V0.0.183`; release commit
  `fc3e5296a263a1a29dc00b4c86e82a8178550cf0`; release evidence file
  `docs/release-evidence/v0.0.183.md`; independent repair QA PASS at
  `artifacts/qa/v0.0.183-repair-independent/gate-report.md` with frozen
  aggregate
  `7d4d2b1e0f4423c6cac621fcffccfdfd8e1627c205ca36fecfd54dc1915a2715`;
  desktop `1440x1000` and genuine mobile `390x844` PASS. Developer
  verification reported focused/release `136` OK and
  `python3 -m unittest discover -s tests` as `1393` OK with `5` skipped.
- Verified V0.0.183 behavior: Agents > Goal execution visibly renders the
  verified Goal execution summary next action and counts for total Goals,
  Needs attention, Waiting for Tony, Missing owner, In flight, and Recently
  completed. Repair QA closed MC183-001 by verifying the full visible summary
  text on desktop and mobile. Postdeploy `/api/goal-execution` summary
  readback still reported `total_goals=7`, `needs_attention=2`,
  `waiting_for_tony=1`, `owner_missing=1`, `in_flight=1`, and
  `recently_completed=3`.
- V0.0.184 evidence: dashboard-managed health readback `V0.0.184`; release
  commit `525e23119c4484f97c9bd816ea8b0af03729d3ac`; release evidence file
  `docs/release-evidence/v0.0.184.md`; independent QA PASS at
  `artifacts/qa/v0.0.184-independent/gate-report.md` with frozen aggregate
  `c2f7c7769335285fa4dd5ac6f1b19cb7d58d6767b9d1e4a8279a2e39bc14c1b8`;
  desktop `1440x1000` and genuine mobile `390x844` PASS. Developer
  verification reported focused `3` OK and
  `python3 -m unittest discover -s tests` as `1394` OK with `5` skipped.
- Verified V0.0.184 behavior: `/api/goal-execution` summary and
  `last_run.summary` include `blocking_questions` for waiting-for-Tony
  decisions when the exact canonical question TODO is available. Postdeploy
  readback had `last_error=null` and one blocking question: task
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, TODO
  `todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, Agent `agents/toddy`,
  question `Which family-care scope, outcomes, constraints, and first action should Toddy use next?`.
  Agents > Goal execution visibly rendered the same question with the
  `Question:` prefix while preserving the V0.0.183 next action and counts.
- V0.0.185 evidence: dashboard-managed health readback `V0.0.185`; release
  commit `67fae3692e97b6c6a5b644686f1a2d9697c06996`; release evidence file
  `docs/release-evidence/v0.0.185.md`; independent QA PASS at
  `artifacts/qa/v0.0.185-independent/gate-report.md` with frozen aggregate
  `0c54b279ea130cec6198e383d2f83b840d25de1a38dcc97da32980fd53b56616`;
  desktop `1440x1000` and genuine mobile `390x844` PASS. Developer
  verification reported focused post-QA `2` OK and
  `python3 -m unittest discover -s tests` as `1394` OK with `5` skipped.
- Verified V0.0.185 behavior: `/api/goal-execution` summary and
  `last_run.summary` include `missing_owners` for `owner_missing` decisions.
  Postdeploy readback had `last_error=null`; `missing_owners[0]` named Goal
  `goals/d837ac94-36f5-4735-93bb-d84c69b45435`, title
  `Entrepreneurship: create a company and start running business, compound over time`,
  required relationship `default_agent_for`, and the exact repair message
  `Assign exactly one Codex Agent with a verified default_agent_for link before Mission Control can derive work from this Goal.`
  Agents > Goal execution visibly rendered a compact
  `Missing owner: Entrepreneurship: create a company and start running business, compound over time — add default_agent_for`
  line. V0.0.184 `blocking_questions[0]` still matched the Family/Toddy
  canonical question.
- V0.0.186 evidence: dashboard-managed health readback `V0.0.186`; release
  commit `84e624464d302f4f7cdc503f81c1e93dab22ec76`; release evidence file
  `docs/release-evidence/v0.0.186.md`; independent QA PASS at
  `artifacts/qa/v0.0.186-independent/gate-report.md` with frozen aggregate
  `c9a0bb8fd4f84ea45e6fd5c1e1214690f77ebe022f13a6accf435cb79d878f18`.
  Developer verification reported the focused frontend contract OK and
  `python3 -m unittest discover -s tests` as `1394` OK with `5` skipped.
- Verified V0.0.186 behavior: Agents > Goal execution summary action items are
  exact read-only controls. The Family/Toddy question opens Task detail for
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`; the Entrepreneurship
  missing-owner title opens Goal detail for
  `goals/d837ac94-36f5-4735-93bb-d84c69b45435`; Close restores focus to the
  exact summary origin. Postdeploy `/api/goal-execution` had
  `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, `last_error=null`, counts
  `total_goals=7`, `needs_attention=2`, `waiting_for_tony=1`,
  `owner_missing=1`, `in_flight=1`, `recently_completed=3`, the same
  `default_agent_for` missing owner, and the same Family/Toddy blocking
  question task.
- V0.0.187 evidence: dashboard-managed health readback `V0.0.187`; release
  commit `a68b8febbd7874999ea5f61d3439a5d34bd69fc5`; release evidence file
  `docs/release-evidence/v0.0.187.md`; independent QA PASS at
  `artifacts/qa/v0.0.187-independent/gate-report.md` with frozen aggregate
  `ae715146d8fc04662fd54ded5fc85c089172ae58684c121fcea5b6f65eabc09d`.
  Developer verification reported the focused frontend contract OK and
  `python3 -m unittest discover -s tests` as `1394` OK with `5` skipped.
- Verified V0.0.187 behavior: Agents > Goal execution missing-owner action
  items expose explicit Codex-only `Assign to Tammy`, `Assign to Timmy`, and
  `Assign to Toddy` controls. On activation they use
  `POST /api/agents/<agent>/default-goals` with body
  `{goal_slug, action: "assign"}` and canonical readback. QA intercepted the
  Tammy request before network at `/api/agents/agents%2Ftammy/default-goals`
  with body
  `{"goal_slug":"goals/d837ac94-36f5-4735-93bb-d84c69b45435","action":"assign"}`;
  live readback remained missing-owner, proving no live GBrain mutation during
  QA. No OpenClaw assignment controls appeared. V0.0.186 Task/Goal action links
  and exact-origin focus restoration remained intact.
- V0.0.188 evidence: dashboard-managed health readback `V0.0.188`; release
  commit `7484f86ae95fd0dbc496f0e41f9b4376397af202`; release evidence file
  `docs/release-evidence/v0.0.188.md`; independent QA PASS at
  `artifacts/qa/v0.0.188-independent/gate-report.md` with frozen aggregate
  `7be6883d45ada59030d0db7c9509244a339828f96cca1a7ee32a005196958697`.
  Developer verification reported focused `238` OK and
  `python3 -m unittest discover -s tests` as `1394` OK with `5` skipped.
- Verified V0.0.188 behavior: `/api/goal-execution.summary` and
  `last_run.summary` include structured `action_queue` entries grouped by
  owner. Postdeploy readback was terminal with `last_run` present,
  `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, `last_error=null`, and
  `summary.action_queue` length `2`: Tony-owned `answer_question` for Family
  task `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` / TODO
  `todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, and Tony-owned
  `assign_goal_owner` for Entrepreneurship Goal
  `goals/d837ac94-36f5-4735-93bb-d84c69b45435`. Agents > Goal execution
  visibly rendered `Action queue:`, `Tony action required`,
  `Answer Agent question`, and `Assign Goal owner`.
- V0.0.189 evidence: dashboard-managed `/api/health` and `/api/releases`
  readback `V0.0.189`; release commit
  `748fa8a0bfbd0ec0fa648a1be4f181658d62c609`; release evidence file
  `docs/release-evidence/v0.0.189.md`; repaired independent QA PASS at
  `artifacts/qa/v0.0.189-repair-independent/gate-report.md` with frozen
  aggregate
  `4a51b29c345b9043a3eee7cbe7945d2050dfb446c775043bc56c392222f13f97`.
  Developer verification reported focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.189 behavior: `Answer Agent question` is a direct inline
  action. Activating it opens canonical Task
  `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` after readback and focuses
  existing `#task-handoff-answer`. Close restores focus to the exact
  originating `.goal-execution-answer-action` with immutable origin
  `summary:action:answer_question:tasks/561640dd-8e34-43e1-a03e-e3f3f270033d:todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`,
  not to a same-slug Agent-card link. Postdeploy `/api/goal-execution`
  retained `last_run`, `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, Tony `answer_question` and
  `assign_goal_owner` action queue entries, and `last_error=null`.
- V0.0.190 evidence: dashboard-managed `/api/health` and `/api/releases`
  readback `V0.0.190`; release commit
  `f7fd08dc17cd48ad80edfcc166ce3b147e4e7c28`; release evidence file
  `docs/release-evidence/v0.0.190.md`; independent QA PASS at
  `artifacts/qa/v0.0.190-independent/gate-report.md` with frozen aggregate
  `734641816975e6ce1d6be48c826a639422e25c0f57bf87dfb4e50ffa2d928765`.
  Developer verification reported focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.190 behavior: Goal execution Action queue entries for Tony
  waiting-for-input Agent questions now carry verified `todo_updated_at` and
  render one inline textarea plus `Submit answer`. The form posts to the
  existing canonical `/api/todos/<todo>/answer` endpoint with `answer`,
  `expected_updated_at`, actor `people/tony-guan`, source `mission_control`,
  and a UUID `idempotency_key`; verified response reconciliation refreshes Goal
  execution and Agent Work. QA intercepted the POST before live network at
  `/api/todos/todos%2F99b64fec-aebe-57de-bf79-cc9d640a2db2/answer`; canonical
  readback after QA remained unchanged, proving no live mutation during the
  independent check. Postdeploy `/api/goal-execution` retained `last_run`,
  `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, and `last_error=null`.
- V0.0.191 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.191`; release commit
  `21aeac225d4b344a3a3c0ba1f984c1f5b6769668`; release evidence file
  `docs/release-evidence/v0.0.191.md`; independent QA PASS at
  `artifacts/qa/v0.0.191-independent/gate-report.md` with frozen aggregate
  `9e2486934841d4a59b1c0789f1c718b3945624372e2334081847d313ee71c299`.
  Developer verification reported focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.191 behavior: Tony-owned `assign_goal_owner` Action queue
  entries render Codex-only inline assignment controls directly in the primary
  Action queue: `Assign to Tammy`, `Assign to Toddy`, and `Assign to Timmy`.
  Each uses the existing verified
  `POST /api/agents/<agent>/default-goals` contract with
  `{goal_slug, action: "assign"}`. QA intercepted assignment POSTs before live
  network and confirmed desktop Tammy endpoint
  `/api/agents/agents%2Ftammy/default-goals`, mobile Timmy endpoint
  `/api/agents/agents%2Ftimmy/default-goals`, and the exact Entrepreneurship
  Goal body. Direct live readback after QA still showed no owner, proving no
  live GBrain mutation. Postdeploy `/api/goal-execution` retained `last_run`,
  `last_error=null`, `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, `summary.total_goals=7`, and
  action queue entries for `answer_question` and Entrepreneurship
  `assign_goal_owner`.
- V0.0.192 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.192`; release commit
  `3ad6ea396c26a2c4073eb27e782991313ccbc19f`; release evidence file
  `docs/release-evidence/v0.0.192.md`; independent QA PASS at
  `artifacts/qa/v0.0.192-independent/gate-report.md` with frozen aggregate
  `f96790cc11fcda7278bbac2bba4f2344e2baec96691f86b71ab93e7806f8715f`.
  Developer verification reported focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.192 behavior: Goal execution missing-owner summaries and
  Tony-owned `assign_goal_owner` Action queue entries now include verified
  Codex Agent `candidate_owners` metadata. Postdeploy `/api/goal-execution`
  retained `last_run=true`, `last_error=null`,
  `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, and
  `summary.total_goals=7`. The Entrepreneurship `assign_goal_owner` row for
  Goal `goals/d837ac94-36f5-4735-93bb-d84c69b45435` included Timmy with
  verified `default_goal_count=1`, `recommended=true`, and recommendation
  `recommended: lowest verified Codex Goal load`, plus Toddy count 2 and Tammy
  count 3. Desktop 1440 and genuine mobile 390 QA showed the recommended label
  in the primary Action queue and Missing owner detail controls, no OpenClaw
  assignment controls, no automatic `default_agent_for` mutation, no-write
  intercepted assignment POST, V0.0.190 answer regression preserved, and clean
  console/network checks.
- V0.0.193 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.193`; release commit
  `bdf197b7000eec23d783f04756d3d17da7d81345`; release evidence file
  `docs/release-evidence/v0.0.193.md`; independent QA PASS at
  `artifacts/qa/v0.0.193-independent/gate-report.md` with frozen aggregate
  `c46acb37d259f543397a81ff5592a7c0233bc43cebe4951bfb62bf34f399b7eb`.
  Developer verification reported focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.193 behavior: Tony-owned `answer_question` Action queue
  entries now carry verified question `detail` and render it beside the inline
  answer form. Postdeploy `/api/goal-execution` retained `last_run=true`,
  `last_error=null`, `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, and
  `summary.total_goals=7`; the Family/Toddy `answer_question` detail began
  `Based on Artifact artifacts/0e0323e7-f5b8-4833-881a-018507ac7e2a`. Desktop
  1440 and genuine mobile 390 QA showed exactly one labeled inline answer form
  with the short question and full detail visible, preserved the V0.0.192
  Timmy recommendation and no-OpenClaw assignment boundary, intercepted the
  TODO answer POST before live network, and confirmed canonical readback
  remained blocked/not_done with no live GBrain mutation.
- V0.0.194 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.194`; release commit
  `06446ef40abba67eac8b8f9732fb0408a2643b04`; release evidence file
  `docs/release-evidence/v0.0.194.md`; independent QA PASS at
  `artifacts/qa/v0.0.194-independent/gate-report.md` with frozen aggregate
  `c83433e7bf9169b453651f948d9d83ef3b7738f38978e61a250a0db782ae3e0e`.
  Developer verification reported focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.194 behavior: Goal execution `summary.next_action` now names
  exact Tony-owned Action queue work instead of generic guidance. Current
  postdeploy `/api/goal-execution` retained `last_run=true`,
  `last_error=null`, `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, and
  `summary.total_goals=7`; `summary.next_action` read back exactly
  `Answer the Toddy question for Which family-care scope, outcomes, constraints, and first action should Toddy use next? and assign Entrepreneurship: create a company and start running business, compound over time to Timmy (recommended: lowest verified Codex Goal load); executing or delivered Agent work can continue.`
  Independent desktop 1440 and genuine mobile 390 QA rendered the same text,
  retained the V0.0.193 question detail beside the single inline answer form,
  preserved Timmy/Toddy/Tammy owner controls with no OpenClaw assignment,
  intercepted answer POST before live network, and confirmed no live GBrain
  mutation.
- V0.0.195 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.195`; release commit
  `05de4cb5eb1db55bda0ec5b263f0d7956244323e`; release evidence file
  `docs/release-evidence/v0.0.195.md`; independent QA PASS at
  `artifacts/qa/v0.0.195-independent/gate-report.md` with frozen aggregate
  `e85953c50dac2e00848550bd4fb616a2c53ae4a3747b316ba8dccb16ab1f3310`.
  Developer verification reported the focused Goal execution/frontend
  contract as `2` OK, the combined focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK, and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.195 behavior: Tony-owned waiting-question Action queue entries
  now include `answer_template`, and Agents renders an editable
  `Insert answer template` button beside the inline answer textarea.
  Postdeploy `/api/goal-execution` retained `last_run=true`,
  `last_error=null`, `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`; the `answer_question` action
  included template lines for Scope categories, Desired outcomes, Constraints,
  First action, and Notes. Desktop 1440 and genuine mobile 390 QA showed one
  accessible template button per view; clicking inserted the exact draft,
  focused the textarea, generated zero non-GET requests, did not submit, and
  did not mutate GBrain. Intercepted answer-submit regression still used the
  verified TODO answer endpoint with `expected_updated_at`,
  `actor=people/tony-guan`, source `mission_control`, and UUID
  idempotency key; canonical readback remained blocked/not_done with answer
  null.
- V0.0.196 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.196`; release commit
  `78e57d2313690544a2f34957f947bea08640c310`; release evidence file
  `docs/release-evidence/v0.0.196.md`; independent QA PASS at
  `artifacts/qa/v0.0.196-independent/gate-report.md` with frozen aggregate
  `c2aab1808f0290eba4973b3452affda7c81d588637c5c3cbd6cdf8707fbf49dc`.
  Developer verification reported the focused Goal execution/frontend
  contract as `2` OK, the combined focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK, and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.196 behavior: the waiting-question Action queue
  `answer_template` now inserts a concrete editable approval draft instead of
  placeholders: `Scope categories: accepted`, `Desired outcomes: accepted`,
  `Constraints: accepted`, `First action: approved`, and
  `Notes: Keep the work bounded to the stated scope, outcomes, constraints, and first action.`
  Postdeploy `/api/goal-execution` retained `last_run=true`,
  `last_error=null`, `public_reason=actively_executing`, selected task
  `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`, and concrete
  `answer_template` readback. Desktop 1440 and genuine mobile 390 QA verified
  no placeholder strings remained, template click focused the textarea,
  emitted zero POST/non-GET requests, did not submit, and did not mutate data.
  Answer-submit, Timmy recommendation, Codex-only owner controls, direct-answer
  focus restoration, and clean console/network regressions remained intact.
- V0.0.197 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.197`; release commit
  `2a6de54f1f4892d1ea096ddae3007642907fad5b`; release evidence file
  `docs/release-evidence/v0.0.197.md`; independent QA PASS at
  `artifacts/qa/v0.0.197-independent/gate-report.md` with frozen aggregate
  `3a10f27b6b651d1a5ae3484ec7eeb15959134918b1a44905e0edb090df96d562`.
  Developer verification reported the focused frontend contract as `1` OK,
  the combined focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK, and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.197 behavior: Inbox now renders a dedicated expanded
  `Goal execution actions` Needs Attention section for Tony-owned Goal
  execution actions, reusing the same `answer_question` and `assign_goal_owner`
  controls from Agents. Postdeploy `/api/goal-execution` after bounded
  scheduler warmup retained `last_run=true`,
  `public_reason=actively_executing`, `last_error=null`, and Action queue
  kinds `answer_question` plus `assign_goal_owner`. Independent desktop 1440
  and genuine mobile 390 QA showed one Inbox section with two actions, the
  full Artifact-backed question detail, one inline answer form, the concrete
  `Insert answer template`, Timmy/Toddy/Tammy Codex-only assignment controls,
  exactly one Timmy recommendation, no OpenClaw assignment, intercepted answer
  and assignment POSTs only, unchanged live canonical readback, and clean
  console/network checks.
- Phase summary: V0.0.195 added editable answer templates, V0.0.196 made the
  template concrete, and V0.0.197 moved Goal execution actions into Inbox.
  This stops the current broad Goal-execution improvement pass; future work
  should be scoped as a new pass instead of continuing this one by default.
- V0.0.198 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.198`; release commit
  `3dfe196b0a0dd2688ca94561268c236fc7c86814`; release evidence file
  `docs/release-evidence/v0.0.198.md`; independent QA PASS at
  `artifacts/qa/v0.0.198-independent/gate-report.md` with frozen aggregate
  `328fdf1ffa8622b075dca0cfceb93ae4c453c88628300cee0b25e29017798a4d`.
  Developer verification reported the focused frontend contract as `1` OK,
  the combined focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `238` OK, and `python3 -m unittest discover -s tests` as `1394` OK with
  `5` skipped.
- Verified V0.0.198 behavior: Inbox Goal execution actions now include an
  explicit `Run recommended unblock plan` button when a concrete answer draft
  and recommended Codex owner assignment exist. The control sequences the
  existing answer POST first and the recommended owner assignment POST second
  from one reviewed user click; it does not run automatically and does not
  appear in Agents. Independent desktop 1440 and genuine mobile 390 QA verified
  the button is suppressed for blank template, no recommended owner, and
  OpenClaw-only recommendation fixtures; the successful plan produced exactly
  two intercepted writes in order, with exact answer and Timmy assignment
  bodies, and no live GBrain mutation. Postdeploy `/api/goal-execution` after
  bounded warmup retained `last_run=true`, `last_error=null`, and
  `public_reason=actively_executing`.
- Next slice note: current live `action_queue` now contains two
  `answer_question` actions plus one `assign_goal_owner`, so the next product
  slice is expanding the recommended plan to cover all answerable questions.
- V0.0.199 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.199`; release commit
  `f7682905ea5dd5fdb104c558e1e74e9928101b9e`; release evidence file
  `docs/release-evidence/v0.0.199.md`; independent QA PASS at
  `artifacts/qa/v0.0.199-independent/gate-report.md` with frozen aggregate
  `5d03d498d2834bcc8706145ce71d3a1c4f976464234cd1b16cbd021326065f7c`.
  Developer verification reported focused private/frontend coverage as `2` OK,
  the combined focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `239` OK, and `python3 -m unittest discover -s tests` as `1395` OK with
  `5` skipped.
- Verified V0.0.199 behavior: Goal execution detects credential/token/private
  input questions, labels them `Private input required`, sets
  `private_input_required=true`, suppresses synthetic `answer_template`
  content and inline answer/template controls, and excludes those questions
  from recommended unblock plans. Postdeploy `/api/goal-execution` after
  bounded warmup retained `last_run=true`, `last_error=null`,
  `public_reason=actively_executing`, and an Action queue with one safe
  `answer_question` with template, one private `answer_question` without
  template and with `private_input_required=true`, and one
  `assign_goal_owner`. Independent desktop 1440 and genuine mobile 390 QA
  verified the private guidance copy, zero private TODO POST attempts, safe
  recommended-plan answer plus Timmy assignment only, all-private and
  OpenClaw-only plan suppression, no generated secret/token copy, no live
  GBrain mutation, and clean console/network checks.
- V0.0.200 evidence: dashboard-managed `/api/health`, `/api/releases`, and
  About readback `V0.0.200`; release commit
  `5560d8f342674e29a3eac97dec3c3ce3f566027f`; release evidence file
  `docs/release-evidence/v0.0.200.md`; independent QA initially failed
  MC200-001 because Inbox omitted `next_action`, then repaired retest passed
  with frozen aggregate
  `8672a12899cf49ce341a6e0caff1ce082604328bfb46ff812da080557042b3d3` at
  `artifacts/qa/v0.0.200-independent/gate-report-retest.md`. Developer
  verification reported focused repair coverage as `2` OK, the combined
  focused
  `python3 -m unittest tests.test_goal_execution tests.test_frontend_contract`
  as `240` OK, and `python3 -m unittest discover -s tests` as `1396` OK with
  `5` skipped.
- Verified V0.0.200 behavior: Goal execution `summary.next_action` now names
  private-input blockers alongside ordinary safe answer and owner-assignment
  actions, so Inbox and Agents show every reason Goal-derived Agent work is
  blocked without generating or autofilling secrets. Postdeploy
  `/api/goal-execution` after bounded warmup retained `last_run=true`,
  `last_error=null`, `public_reason=actively_executing`, and
  `next_action_has_private=true`. Repaired independent desktop 1440 and
  genuine mobile 390 QA verified both surfaces render the exact `Next action:`
  copy including the ordinary Family/Toddy question, `provide private input for
  the Tammy question`, and the private summary
  `Provide the production API access token and OAuth client secret.` Private
  rows still had no answer form, template button, raw TODO version, synthetic
  credential value, or recommended-plan participation; the explicit
  recommended plan still produced only the safe Family TODO answer and Timmy
  Goal-owner assignment, with zero private TODO POST attempts and no live
  GBrain mutation.
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
for Toddy Health. V0.0.163 question surfacing is display/readback behavior:
showing the exact `Answer: ...` TODO text does not answer the question, remove
the Tony blocker, create a replacement TODO, or complete the task.

V0.0.164 expired owned execution claim recovery is also narrowly scoped:
authenticated same-host polling may refresh and lease an expired owned claim
after task-authority readback, but delegated execution, mismatched routes,
arbitrary dead letters, and local installation of the wrong Agent worker remain
invalid. This Mac remains Tammy/Tammy-OC only; do not document Timmy or Toddy
as local workers here.
The read-only runtime verifier is a diagnostic/readback tool only. A Timmy
PASS on Timmy's host does not imply Toddy recovery, and a Toddy outage must not
be bypassed by installing or running Toddy locally on this Mac.
The fleet verifier preserves partial success: Timmy verified plus Toddy
`ssh_unreachable` means the fleet is not fully healthy. Do not collapse the
summary to all-green, do not document Toddy as recovered, and do not suggest
local Toddy installation as a workaround.
V0.0.165 `owner_missing` remains a repair instruction, not a documentation
authority to mutate Goal ownership. Do not create or infer ownership links in
docs; the actual repair is exactly one verified Codex Agent
`default_agent_for` relationship on the Goal.
V0.0.166 hidden-item tolerance is also narrow. It applies only when malformed
Agent work is non-visible and already reported in Inbox. Missing roots, visible
unsafe Agent-work issues, and malformed work appearing on Board remain
fail-closed conditions and must not be documented as safe to ignore.
The fleet verifier's default-to-local-HEAD behavior is a verifier consistency
guard. It does not recover a remote worker by itself: Timmy is verified at
`6984f24c1fe330aca68fd95adc0a80dbcc9b4428`, while Toddy remains
`ssh_unreachable` with `tailscale_key_expired` and must not be documented as
recovered.
V0.0.167 selection priority is an ordering change only. It does not bypass the
one-task canary boundary, does not turn `recently_completed` into active work,
and does not repair `waiting_for_tony`, handoff, missing-next-action, or worker
blockers without the owner action named by that state.
V0.0.169 waiting-task context is also readback/display behavior. It gives the
headline and Agents cold-load UI enough canonical task context to render the
right link immediately, but it does not answer Tony's question, unblock the
task, or prove any Timmy/Toddy local worker exists on this Mac.
V0.0.170 through V0.0.173 were Mission Control read-path/UI consistency
repairs: mention-only legacy Goal concepts are excluded from typed Goal reads,
Projects uses a bounded last-verified cache, System Tickets avoids completed
ticket hydration fan-out while preserving page readback, and Board/Goal views
now lock the 3-day default window, one-week preset, actionable undated
visibility, and slug-level Goal deduplication. These releases do not authorize
creating replacement Goal pages, hiding valid System Tickets, or merging
distinct canonical Goals by title.
V0.0.174 recoverable Goal handoff recovery is narrowly scoped to the same
active/planned Goal-derived task and the same existing owned handoff when the
latest delivery state is a suppressed checkpoint/expiry. It requeues the
fixed-route handoff as `retrying` and renders it as Delivering; it does not
create a new task, infer a new Agent, bypass missing next actions, or recover
arbitrary dead letters.
V0.0.175 selection priority is also narrow. A verified recoverable handoff
repair can outrank an unrelated `waiting_for_tony` blocker so system-actionable
work is retried, while the waiting task remains blocked and visible. This does
not answer Tony's question, remove `owner_missing`, install Timmy/Toddy locally,
or complete the Agent task without later terminal handoff and exact Artifact
readback.
V0.0.182 compact Goal execution summary is also read-only projection data for
readers. Its counts and `next_action` do not create missing Goal owners,
answer Tony-blocked work, prove Timmy/Toddy local worker availability, or
complete any Agent task without the existing canonical handoff and Artifact
evidence.
V0.0.183 visible summary rendering keeps the same boundary. Seeing the
`Next action:` line and counts in Agents helps operators choose the next owner
to inspect, but it does not perform that owner action, dispatch a worker, or
replace canonical Task/handoff/Artifact readback.
V0.0.184 blocking-question rendering is also readback/display behavior. Showing
the canonical `Question:` text in Agents does not answer it, remove
`people/tony-guan` as blocker, acknowledge the handoff, wake Toddy, or create
replacement family-care work; the same canonical task must be answered and
handed back through the verified answer flow.
V0.0.185 missing-owner rendering is also readback/display behavior. Showing a
`Missing owner:` line does not create the `default_agent_for` edge, choose an
Agent, or authorize derived work for that Goal. The repair remains exactly one
verified Codex Agent `default_agent_for` relationship on the named Goal.
V0.0.186 linked summary controls are navigation only. Opening the canonical
Task or Goal detail from the summary does not answer Tony, create the
`default_agent_for` edge, acknowledge or wake a worker, or mutate state; use
the verified answer and Goal ownership flows for those repairs.
V0.0.187 assignment buttons are an explicit user-activated Codex Agent owner
repair path, not background automation. They must not be exposed for OpenClaw,
must not infer an owner from the summary alone, and must not be treated as
completed until the verified `default_goals` API readback confirms the
`default_agent_for` relationship.
V0.0.188 action queues are owner-classification readback. A Tony-owned
`answer_question` or `assign_goal_owner` item means Tony or a verified
user-activated repair path must act; it does not make the dispatcher, a local
worker, or OpenClaw responsible for that repair.
V0.0.189 answer actions are navigation and focus helpers only. They do not
submit text, answer the TODO, clear `waiting_for_input`, or mutate GBrain until
Tony uses the existing verified `/api/todos/<todo>/answer` submission flow.
V0.0.190 inline composers expose that existing answer submission flow in the
Goal execution summary. They require the verified TODO update timestamp and a
human-entered answer; stale or unverified answer attempts must fail rather than
silently clear a blocker.
V0.0.191 action-queue assignment buttons expose the existing verified Goal
owner assignment flow closer to the primary Tony queue item. They remain
explicit Codex-only controls and must not be treated as automatic owner
selection, OpenClaw assignment, or completed repair without verified
`default-goals` readback.
V0.0.192 recommended-owner labels are readback guidance, not assignment.
Lowest-load candidate metadata can help Tony choose an explicit Codex owner,
but it must not create `default_agent_for`, infer a default Agent, expose
OpenClaw assignment, or mark the missing-owner repair complete without
verified `default-goals` readback after Tony activates a control.
V0.0.193 Action queue question detail is also readback guidance. Rendering the
full TODO detail near the inline composer helps Tony answer without opening
the Task first, but it must not be treated as an automatic answer, handoff
acknowledgement, worker wake, task completion, or GBrain mutation before Tony
submits through the verified `/api/todos/<todo>/answer` flow.
V0.0.194 exact `next_action` copy is also guidance, not execution. It may name
the exact question and recommended owner assignment to reduce operator
ambiguity, but documentation and readers must not treat the sentence as proof
of a submitted answer, completed ownership repair, dispatcher wake, handoff
acknowledgement, task completion, or GBrain mutation.
V0.0.195 answer templates are editable drafts only. Inserting the template
into the inline textarea does not submit the answer, clear `waiting_for_input`,
acknowledge a handoff, wake a worker, assign a Goal owner, complete a task, or
mutate GBrain; only the separate verified answer submission flow may do that
after Tony reviews and submits the text.
V0.0.196 concrete approval templates keep the same boundary. Replacing
placeholder choices with accepted/approved draft language reduces typing but
does not reduce the required human review and explicit Submit answer gate.
V0.0.197 Inbox action surfacing keeps the same boundary. Showing Goal
execution actions in Inbox centralizes triage but does not create a second
mutation path, automatically submit answers, assign owners, acknowledge
handoffs, wake workers, complete tasks, or mutate GBrain.
V0.0.198 recommended unblock plans are explicit reviewed mutation shortcuts,
not automation. The button may sequence existing verified answer and
assignment endpoints only after Tony clicks it, and the current implementation
handles the first answerable question plus recommended owner assignment rather
than every answerable question in the queue.
V0.0.199 private-input handling is a hard safety boundary. Documentation must
not describe credential/token/private questions as eligible for generated
templates, inline synthetic answer forms, recommended unblock plans, or any
prefilled secret value; Tony must open the Task and answer private prompts
directly.
V0.0.200 next-action private blocker copy is visibility only. It can tell Tony
to provide private input, but must not be treated as a generated secret,
answer submission, recommended-plan eligibility, dispatcher wake, task
completion, ownership repair, or GBrain mutation.
