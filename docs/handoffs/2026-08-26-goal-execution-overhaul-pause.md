# Goal execution overhaul pause handoff — 2026-08-26

Status: paused by Tony; do not continue this Goal until Tony explicitly resumes it.

Post-handoff update: this document is historical after V0.0.207 and V0.0.208
shipped from `origin/main`. Do not apply the saved V0.0.207 stash over newer
commits. Use release evidence `docs/release-evidence/v0.0.207.md` and
`docs/release-evidence/v0.0.208.md` plus current readbacks as the source of
truth.

Branch: `main`

Last pushed verified commits:

- Product: `ca0ad46` — `Reconcile terminal handoff status repairs`
- Documentation: `91c86b9` — `docs: document v0.0.206 terminal handoff repair`

Verified deployed baseline at pause target: Mission Control `V0.0.206`.

## What shipped in the latest completed slice

- Repaired terminal handoff status reconciliation for completed Agent work.
- Completed canonical repair for Faith, Finance, and Career Agent-produced work:
  - `tasks/3da38b72-4636-5056-b171-568e9ce7c538` with `artifacts/767e630a-58d4-4fbe-9b2f-eb3286be6ca5`
  - `tasks/a58adfb6-3400-56e9-992c-4e65ae87f66f` with `artifacts/70b62868-b612-4c60-80a4-332ab5085309`
  - `tasks/c0ab18d6-dc1a-58da-911e-1bec46d7f21a` with `artifacts/6081e00f-4c84-4057-93d8-dc59116f2f69`
- Each of those three tasks read back `completed`, `handoff=null`, empty blockers/dependencies, and exactly one expected `produced_for` Artifact.
- Goal execution summary after V0.0.206 showed `recently_completed=3`, `waiting_for_tony=1`, `owner_missing=1`, and no Artifact identity repair action.

## Evidence for shipped baseline

- Independent QA PASS: `/Users/tony/work/gtasks/artifacts/qa/v0.0.206-independent/gate-report.md`
- Structured QA: `/Users/tony/work/gtasks/artifacts/qa/v0.0.206-independent/gate-results.json`
- QA aggregate: `dc4944218df13af339cbf51775a903013b0ca25cc7120d43905a1e116d952f19`
- Focused tests: `543 OK`
- Documentation Manager pushed V0.0.206 documentation in `91c86b9`.

## Canonical mutations performed during this slice

All mutations were performed through supported Mission Control HTTP endpoints with readback.

- Answered three Tammy Artifact publisher token question TODOs for Faith, Finance, and Career so Tammy could publish verified Artifacts.
- Assigned Entrepreneurship to Timmy through:
  - `POST /api/agents/agents%2Ftimmy/default-goals`
  - body: `{"goal_slug":"goals/d837ac94-36f5-4735-93bb-d84c69b45435","action":"assign"}`
- Completed stale Timmy Civic task `tasks/fa3efbe2-c650-5802-8721-8aa9bb2db104` after exact Artifact/handoff readback.

Do not repeat these mutations unless fresh readback proves they are still needed.

## Paused uncommitted V0.0.207 WIP

The V0.0.207 attempt is intentionally not committed or deployed as a verified release.

Saved stash:

```text
stash@{0}: On main: pause goal execution v0.0.207 suppressed handoff WIP
```

Files in the stash:

- `gtasks/goal_execution.py`
- `tests/test_goal_execution.py`
- `gtasks/releases.json`

The WIP change adds a regression test and a small branch so an active Goal-derived task with `suppressed` handoff status plus exact `produced_for` Artifact can be treated as a terminal completion candidate.

Focused test result before pause:

```text
python3 -m unittest \
  tests.test_goal_execution.GoalExecutionEngineTests.test_canary_reconciles_suppressed_handoff_with_verified_artifact \
  tests.test_goal_execution.GoalExecutionEngineTests.test_canary_does_not_complete_handoff_without_verified_artifact \
  tests.test_goal_execution.GoalExecutionEngineTests.test_canary_reconciles_checkpointed_suppressed_handoff_with_verified_artifact \
  tests.test_goal_execution.GoalExecutionEngineTests.test_canary_reconciles_completed_handoff_with_verified_artifact

4 OK
```

Broader suite before pause:

```text
python3 -m unittest tests.test_goal_execution tests.test_gbrain tests.test_server tests.test_frontend_contract tests.test_releases tests.test_release_command

789 OK
```

## Why V0.0.207 was not shipped

Runtime verification showed the code path was not sufficient in live scheduling order:

- `/api/goal-execution` still reported public reason `actively_executing` for `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`.
- Civic Goal `goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10` still reported `handoff_needs_repair` for `tasks/106db451-137a-5094-af72-7de3d9332a87`.
- Entrepreneurship Goal `goals/d837ac94-36f5-4735-93bb-d84c69b45435` still reported `wip_full`.

Diagnosis: the suppressed-handoff-with-Artifact reconciliation can pass in an isolated canary test, but live scheduler selection does not prioritize that repair before other public/active Goal execution work. The next implementation must address selection/reconciliation ordering, not only the terminal-signal predicate.

## Current known live blocker to resume later

Timmy active WIP still holding capacity:

- Task: `tasks/106db451-137a-5094-af72-7de3d9332a87`
- Title: `Review Civic: Help California be better through political action progress and publish one bounded next-step brief`
- Owner: `agents/timmy`
- Goal: `goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10`
- Artifact: `artifacts/b95ad28a-eb6f-4b6f-b3a6-9e460642623a`
- Observed handoff history included `execution_claim_released suppressed`.

This should likely be reconciled as completed after exact Artifact readback, then Entrepreneurship should be re-evaluated after Timmy WIP capacity is released.

## Worker boundary

Keep local supervisor scoped to local identities only:

- Allowed local workers: `agents/tammy`, `agents/tammy-oc`
- Timmy and Toddy must remain on other machines unless Tony explicitly changes host placement.

## Resume order

1. Refresh `git status`, `/api/health`, `/api/goal-execution?refresh=1`, `/api/agents?refresh=1`, and `/api/agent-work?refresh=1`.
2. Confirm Mission Control is running the expected committed version before applying WIP.
3. Apply the saved stash into a clean branch or worktree only if Tony resumes this Goal.
4. Add a RED test for scheduler selection ordering:
   - include a `waiting_for_tony` decision,
   - include Timmy active derived WIP with `suppressed` handoff and exact Artifact,
   - assert the engine reconciles the repairable terminal WIP before leaving Entrepreneurship `wip_full`.
5. Implement minimal selection/reconciliation ordering fix.
6. Run focused tests and the broader server/frontend/release suite.
7. Bump version, restart dashboard-managed Mission Control, and obtain independent QA PASS on desktop `1440x1000` and genuine mobile `390x844` before commit.
8. Commit, push, deploy, verify postdeploy readback, then notify Documentation Manager.
