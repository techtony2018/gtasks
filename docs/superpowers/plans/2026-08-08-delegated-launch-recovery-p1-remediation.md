# Delegated Launch Recovery P1 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five remaining Task 5 launch-recovery races without ever retrying a command that may have started.

**Architecture:** Keep the execution-start row as immutable proof that one launch crossed the server fence, while the lease row remains the mutable current authorization credential. Add an audited CAS that archives and abandons only a start proven unused, restores the handoff to `received`, and permits a fresh launch. Propagate recovered credentials into the private inbox independently of launch evidence, and treat post-revocation checkpoints as terminal reconciliation for the same immutable start fence.

**Tech Stack:** Python 3 standard library, SQLite transactions, `unittest`, private filesystem launch evidence.

## Global Constraints

- Write reviewer reproductions before production changes and observe each fail for the intended reason.
- Never execute a real Codex or OpenClaw command; command-path tests use fake Python targets only.
- A timeout, nonzero exit, missing result, malformed result, or dead runner after possible command start remains ambiguous and is never retried.
- Only `command_not_started` or a dead runner before gate creation is safely retryable.
- Preserve immutable launch id, start-grant reference, and launch-event evidence when rotating current lease credentials.
- Do not push, merge, deploy, or mutate live services.

---

### Task 1: Recover `execution_started` and refresh current inbox authorization

**Files:**
- Modify: `gtasks/server.py`
- Modify: `gtasks/local_handoff_dispatcher.py`
- Test: `tests/test_server.py`
- Test: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Consumes: `POST /api/handoffs/<id>/recover`, `PrivateClaimStore.reconcile_recovery`, `PrivateWakeInbox.enqueue`.
- Produces: recovery for `execution_started`; same-generation reconciliation replay; atomic inbox claim refresh across every nonterminal state.

- [x] **Step 1: Write failing server and client recovery tests**

  Add a server test that moves a handoff through `received` and `execution_started`, calls `/recover`, and expects a rotated generation/capability. Add a client-store test that accepts an exact same-generation reconciliation without raising or falsely claiming an advance.

- [x] **Step 2: Run the recovery reproductions and verify RED**

  Run the exact new test methods with `python3 -W error::ResourceWarning -m unittest ...` and confirm the server returns reconciliation for `execution_started` and the client rejects equal generation.

- [x] **Step 3: Implement minimal recovery support**

  Include `execution_started` in the server recovery mutation gate. Make equal-generation reconciliation an idempotent retry of the current recovery intent while still rejecting generation regression and bounding non-advancing loops.

- [x] **Step 4: Write failing inbox credential-propagation tests**

  Parameterize `accepted`, `pending`, `failed`, `launch_preparing`, `launch_spawned`, `launch_ready`, `start_granted`, `executing`, and `recovery_required`. Refresh a claim from generation N to N+1 and assert the credential changes while launch id, PID, grant reference, and launch-event rows remain byte-for-byte equal.

- [x] **Step 5: Implement and verify atomic inbox authorization refresh**

  Update `claim_json` for the exact same wake, handoff, and execution idempotency fence in all nonterminal states. Persist this refresh before any subsequent execution-start, checkpoint, abandon, or failure request.

### Task 2: Separate immutable start fence from current checkpoint authority

**Files:**
- Modify: `gtasks/handoff_dispatcher.py`
- Modify: `gtasks/server.py`
- Test: `tests/test_handoff_dispatcher.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `DurableHandoffStore.start_execution`, `checkpoint_started_execution`.
- Produces: immutable start credential/fence row; checkpoint validation against launch fence plus current lease; terminal idempotent reconciliation.

- [x] **Step 1: Write failing rotated-credential and revocation checkpoint tests**

  Start with generation N, recover to N+1, replay the start using N+1, and assert the stored original start generation/reference do not change. Checkpoint with N+1 must succeed and N must fail while authority remains. After concurrent revocation clears the lease, checkpointing the same launch must return the already-terminal handoff without a second release event.

- [x] **Step 2: Run the checkpoint reproductions and verify RED**

  Confirm current code mutates `execution_starts` on replay and rejects both rotated-current and post-revocation checkpoint paths.

- [x] **Step 3: Implement immutable/current separation**

  Never update credential columns in an existing execution-start row. Match checkpoint proof using launch id plus start registration reference; when the execution claim is nonterminal, separately require the current lease registration/generation/capability. When the same claim is already terminal, return its current handoff record idempotently.

- [x] **Step 4: Verify store and HTTP behavior GREEN**

  Run the new store tests plus the execution-start/checkpoint HTTP tests warning-strict.

### Task 3: Prove runner liveness before consuming the start grant

**Files:**
- Modify: `gtasks/local_handoff_dispatcher.py`
- Test: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Consumes: `GatedLaunchController.observe`, `LaunchObservation.runner_alive`.
- Produces: no execution-start request for a dead ready runner; fresh launch attempt with preserved failed-launch evidence.

- [x] **Step 1: Write a failing dead-ready recovery test**

  Return `LaunchObservation(state="ready", runner_alive=False)` from a recovered controller and assert the execution-start client is not called, the attempt becomes a proven prelaunch failure, and the next worker pass allocates a different launch id.

- [x] **Step 2: Verify RED and implement both liveness checks**

  Reject dead `ready` evidence before recording readiness and call `observe` again immediately before the execution-start CAS. If a grant was already persisted but the unopened runner is dead, route it through the unused-start abandonment path from Task 4.

- [x] **Step 3: Verify GREEN**

  Run the dead-ready reproduction and existing crash-boundary tests warning-strict.

### Task 4: Abandon a start proven unused and safely launch again

**Files:**
- Modify: `gtasks/handoff_dispatcher.py`
- Modify: `gtasks/server.py`
- Modify: `gtasks/local_handoff_dispatcher.py`
- Test: `tests/test_handoff_dispatcher.py`
- Test: `tests/test_server.py`
- Test: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Produces: `DurableHandoffStore.abandon_unstarted_execution(...)`; `POST /api/handoffs/<id>/execution-abandon`; `LocalDispatcherClient.execution_abandon(...)`; durable inbox pending action `abandon_start`.

- [x] **Step 1: Write failing store CAS tests**

  Prove that only the exact active launch and current lease can abandon. The CAS must archive immutable start evidence, update `execution_started` to `received`, append one audit event, and replay idempotently. A later launch receives a different grant. A replay of the abandoned launch returns `execution_started=false`, `status=received` instead of raising.

- [x] **Step 2: Implement the audited store CAS**

  Add an append-preserving `abandoned_execution_starts` table. In one write transaction archive the active start, delete only that active row, restore the handoff with a compare-and-swap, and append `execution_start_abandoned` evidence keyed by mutation reference.

- [x] **Step 3: Write failing HTTP/client tests and implement the endpoint**

  Use exact payload `{launch_id, reason}` and exact response `{handoff_id,status,launch_id,abandoned}`. Validate current lease headers while authority remains and accept exact idempotent abandon replay.

- [x] **Step 4: Write failing worker recovery tests**

  A fake Python target that raises `OSError` after gate produces `command_not_started`. Assert the worker persists `abandon_start` before the request, retries only that server action after response loss, then allocates a new launch and invokes the successful fake target once. Also reproduce a crash where the server reset succeeded but local grant state did not advance; the abandoned start replay must reconcile locally without cancellation or stranding.

- [x] **Step 5: Implement local abandon/retry transitions and verify GREEN**

  Persist `abandon_start` before network I/O. On verified `received`, clear the pending action and leave the item retryable; when attempts are exhausted, queue terminal failure only after abandon verification. Preserve the old launch row and grant reference in `wake_launches`.

### Task 5: Documentation, full verification, and commit

**Files:**
- Modify: `docs/runbooks/agent-handoff-dispatcher.md`
- Modify: `.superpowers/sdd/task-5-report.md` (ignored evidence report, after commit)

- [x] **Step 1: Update the runbook**

  Document immutable start fence versus current lease authorization, recovery credential propagation, dead-ready handling, and the audited unused-start reset. State explicitly that only proof of `command_not_started` permits post-gate retry.

- [x] **Step 2: Run focused and full verification**

  Run focused reviewer reproductions, then `python3 -W error::ResourceWarning -m unittest discover -s tests`, `python3 -m compileall -q gtasks tests`, and `git diff --check`.

- [x] **Step 3: Commit the reviewed tracked candidate**

  Stage only scoped files and commit with `fix: close delegated launch recovery races`.

- [x] **Step 4: Update the ignored Task 5 report**

  Record the final SHA, every RED/GREEN reproduction, exact full-suite counts, static checks, clean tracked status, and that no real Codex/OpenClaw command ran.
