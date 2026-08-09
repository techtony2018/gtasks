# Start Intent and Handback Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make execution-start response loss and terminal handback reconciliation crash-safe without permitting a second launch before the exact first launch is resolved.

**Architecture:** Add a durable `start_requesting` inbox transition that snapshots the stable start-request identity and current lease credential references before every execution-start request or replay. Treat exact terminal abandon/checkpoint readbacks as successful local terminalization, then clear the matching private active claim only after the inbox terminal record commits; restart performs the same cleanup before the recovered-handoff guard can defer work.

**Tech Stack:** Python 3 standard library, SQLite transactions, private mode-0600 JSON state, `unittest`, gated fake-Python launch runners.

## Global Constraints

- Write each regression first and observe the intended RED failure before editing production code.
- Never launch real Codex or OpenClaw processes; launch tests use only controlled fake Python targets/controllers.
- Persist start intent before the execution-start CAS, and reconcile that exact launch before classifying any durable dead-ready evidence as prelaunch-safe.
- A true execution-start replay must persist the original grant before any abandon/checkpoint decision; a false exact abandoned-start replay may safely permit a later launch.
- Accept only exact response keys and exact boolean/status pairs for abandon/checkpoint reconciliation.
- Commit inbox terminal state before deleting `active.json`; restart must idempotently finish deletion after a crash between those writes.
- Do not push, merge, deploy, or mutate live Mission Control, GBrain, Codex, or OpenClaw state.

---

### Task 1: Persist and replay the exact execution-start intent

**Files:**
- Modify: `gtasks/local_handoff_dispatcher.py`
- Test: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Consumes: `WakeInboxWorker.run_once(...)`, `PrivateWakeInbox.record_ready(...)`, `LocalDispatcherClient.execution_start(...)`.
- Produces: `PrivateWakeInbox.record_start_requesting(claim, now=...) -> WakeInboxItem`; durable fields `start_request_ref`, `start_execution_idempotency_key`, `start_registration_ref`, `start_lease_generation`, and `start_lease_capability_ref`.

- [x] **Step 1: Write the response-loss/crash/dead-runner regression**

  Add a test whose first execution-start call records a server-side grant and raises `OSError`, then closes/reopens the inbox with the runner reported dead. Assert the first pass leaves `state == "start_requesting"`, the replay uses the identical launch id, persists the identical grant, queues/completes abandon for that launch, and no second launch id appears.

- [x] **Step 2: Run the exact regression and verify RED**

  Run:

  ```bash
  python3 -W error -m unittest tests.test_local_handoff_dispatcher.PrivateWakeInboxTests.test_start_response_loss_crash_and_dead_runner_reconciles_one_launch
  ```

  Expected: FAIL because response loss leaves `launch_ready`, and dead durable readiness is classified as retryable prelaunch failure without replaying the same start CAS.

- [x] **Step 3: Add the durable start-intent schema and transition**

  Extend `wake_inbox` migration and `WakeInboxItem`, then implement an idempotent transition equivalent to:

  ```python
  def record_start_requesting(self, claim, *, now):
      # Require the active worker and current launch.
      # Verify the supplied claim exactly matches the stored current claim.
      # Persist state='start_requesting', the stable request reference,
      # execution identity, registration reference, generation, and capability
      # reference in one SQLite transaction before returning.
  ```

  Include `start_requesting` in authorization refresh, worker-claim recovery, and launch selection, but exclude it from prelaunch-failure transitions.

- [x] **Step 4: Reconcile before interpreting dead-ready evidence**

  In `WakeInboxWorker.run_once`, persist `start_requesting` before every start request. On restart, replay the same `wake_token + launch_id` even when the runner is dead. Handle exact outcomes as follows:

  ```python
  execution_started=True   -> record_start_grant(...); dead runner -> abandon_start
  status='received', False -> reconcile_abandoned_start(...); safe retry
  terminal, False          -> cancel if safe and mark_suppressed(...)
  OSError/TimeoutError     -> keep start_requesting and release only worker claim
  ```

- [x] **Step 5: Verify Task 1 GREEN and legacy dead-ready coverage**

  Run the new regression, the existing response-loss test, every launch-crash-boundary test, and the upgraded legacy dead-ready test warning-strict.

### Task 2: Accept exact terminal abandon reconciliation

**Files:**
- Modify: `gtasks/local_handoff_dispatcher.py`
- Test: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Consumes: `LocalDispatcherClient.execution_abandon(...)`, `PrivateWakeInbox.complete_server_action(...)`.
- Produces: exact accepted abandon pairs `("received", True)` and `("suppressed", False)`; terminal local `suppressed` state for the latter.

- [x] **Step 1: Write exact-shape client and inbox tests**

  Test that `{handoff_id,status="suppressed",launch_id,abandoned=False}` succeeds, clears `pending_server_action`, and terminalizes the inbox as `suppressed`. Parameterize malformed/mismatched pairs such as `("received", False)`, `("suppressed", True)`, `("completed", False)`, missing keys, and extra keys; each must raise `ValueError`.

- [x] **Step 2: Run the tests and verify RED**

  Run the exact new client and inbox methods. Expected: client currently accepts overly broad false-terminal statuses while inbox rejects `suppressed/false` and leaves pending work stranded.

- [x] **Step 3: Implement exact pair validation and idempotent terminalization**

  Validate `set(response) == EXECUTION_ABANDON_KEYS`, ids match, and only:

  ```python
  (response["status"], response["abandoned"]) in {
      ("received", True),
      ("suppressed", False),
  }
  ```

  Preserve current retry behavior for `received/true`. For `suppressed/false`, atomically set `state='suppressed'`, clear pending/retry/worker fields, append terminal launch evidence, and permit an exact same terminal completion replay to return the existing item.

- [x] **Step 4: Verify Task 2 GREEN**

  Run new shape tests plus existing abandon loss/replay/store/server tests warning-strict.

### Task 3: Terminalize checkpoint handback and clear the active claim

**Files:**
- Modify: `gtasks/local_handoff_dispatcher.py`
- Test: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Consumes: `LocalDispatcherClient.execution_checkpoint(...)`, `WakeInboxWorker._deliver_pending_action(...)`, `run_forever(...)`.
- Produces: `PrivateClaimStore.clear_terminal_handoff(handoff_id) -> bool`; terminal inbox states `handed_back` or `suppressed`; exact checkpoint pairs `("suppressed", True)`, `("completed", False)`, and `("dead_letter", False)`.

- [x] **Step 1: Write handback cleanup and restart-crash regressions**

  Add one test proving normal checkpoint success changes `recovery_required` to `handed_back`, clears pending action, and removes matching `active.json`. Add a crash-hook test after inbox terminal commit but before claim deletion; reopen both stores and run one loop iteration, asserting restart deletes `active.json`, does not call recover, and does not relaunch or checkpoint again.

- [x] **Step 2: Write exact already-terminal checkpoint readback tests**

  Accept complete response shapes only for `suppressed/true`, `completed/false`, and `dead_letter/false`. Assert other status/boolean pairs, missing keys, extra keys, or wrong handoff/launch ids raise `ValueError`.

- [x] **Step 3: Run Task 3 tests and verify RED**

  Expected: checkpoint success merely clears the pending action while leaving `state='recovery_required'` and `active.json`; the client rejects completed/dead-letter idempotent readback.

- [x] **Step 4: Implement terminal-first cleanup**

  Make checkpoint completion atomically transition inbox state before returning:

  ```python
  ("suppressed", True) -> state = "handed_back"
  ("completed", False) -> state = "suppressed"
  ("dead_letter", False) -> state = "suppressed"
  ```

  Add `PrivateClaimStore.clear_terminal_handoff` that verifies the handoff id and idempotently unlinks the private state. Inject the claim store into `WakeInboxWorker`; after inbox commit, invoke a crash-test hook, clear the claim, then emit the completed hook.

- [x] **Step 5: Make restart cleanup precede recovered-handoff deferral**

  At the top of each `run_forever` iteration, if the active claim's matching inbox is `handed_back` or server-terminal `suppressed`, clear the claim and discard its id from `recovered_handoffs` before invoking the inbox worker or the recovered-handoff sleep guard. Also discard when a worker returns a terminal handback result.

- [x] **Step 6: Verify Task 3 GREEN**

  Run the cleanup/restart tests, checkpoint response-loss tests, rotated-credential checkpoint test, and the relevant run-loop recovery tests warning-strict.

### Task 4: Documentation, review, full verification, and commit

**Files:**
- Modify: `docs/runbooks/agent-handoff-dispatcher.md`
- Modify: `.superpowers/sdd/task-5-report.md` (ignored evidence report, after commit)

**Interfaces:**
- Consumes: all Task 1-3 behavior and tests.
- Produces: documented crash-consistency contract, a reviewed tracked commit, and an updated ignored Task 5 receipt.

- [x] **Step 1: Update the runbook**

  Document `start_requesting`, same-launch CAS replay before dead-runner classification, exact terminal abandon/checkpoint reconciliation, terminal-first inbox/claim cleanup ordering, and restart cleanup before recovery deferral.

- [x] **Step 2: Run focused verification**

  Run:

  ```bash
  python3 -W error -m unittest tests.test_handoff_dispatcher tests.test_local_handoff_dispatcher tests.test_server tests.test_handoff_launch_runner
  ```

- [x] **Step 3: Perform the required local code review**

  Because delegation is prohibited for this task, inspect the complete diff locally against all three P1 requirements, audit every state/query set containing launch states, and run `git diff --check`. Fix any Critical or Important finding through another RED/GREEN cycle.

- [x] **Step 4: Run full warning-strict and static verification**

  Run:

  ```bash
  python3 -W error -m unittest discover -s tests
  python3 -m compileall -q gtasks tests
  git diff --check
  ```

- [x] **Step 5: Commit the tracked candidate**

  Stage only scoped files and commit:

  ```bash
  git commit -m "fix: persist start intent and finalize handback"
  ```

- [x] **Step 6: Update the ignored Task 5 report**

  Record the final commit SHA, RED/GREEN test evidence, focused/full counts, static checks, clean tracked status, exact unpushed branch state, and confirmation that no real Codex/OpenClaw process or live service was touched.
