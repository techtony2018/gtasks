# Durable Handoff Launch Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make delegated handoff execution crash-recoverable and at-most-once across the server execution-start decision and the local external-process launch boundary.

**Architecture:** Mission Control performs one atomic `received -> execution_started` compare-and-swap and returns a launch grant tied to one stable launch id. The host creates a private gated shim first, durably records its launch id and PID, obtains the grant immediately before opening the gate, and reconciles atomic result evidence after restart without duplicating a live or ambiguous launch. Proven pre-launch failures may retry; every post-gate unknown result checkpoints and hands the execution claim back.

**Tech Stack:** Python 3.12 standard library, SQLite, local authenticated HTTP, subprocess/PID inspection, atomic private JSON files, and `unittest`.

## Global Constraints

- Never invoke a real Codex or OpenClaw command in tests.
- Never persist bearer tokens, lease capabilities, fixed thread ids, stdout, stderr, or full private prompts in launch evidence or audit events.
- The server execution-start CAS is the semantic start point; revocation before it suppresses unstarted work, while revocation after it preserves start evidence and follows checkpoint/hand-back.
- A gated child cannot invoke the target command until its PID is durable locally and the exact server start grant has been received.
- Timeout, nonzero exit, lost child, or any post-gate unknown outcome is never automatically retried.
- Exhausted or terminal pre-launch failure must durably terminalize both the local inbox and the server execution claim.
- Existing unrelated tracked and untracked work remains untouched.

---

### Task 1: Atomic server execution-start grant and terminal reconciliation

**Files:**
- Modify: `gtasks/handoff_dispatcher.py`
- Modify: `gtasks/server.py`
- Modify: `tests/test_handoff_dispatcher.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Produces: `ExecutionStartGrant`, `DurableHandoffStore.start_execution(...)`, and `DurableHandoffStore.checkpoint_started_execution(...)`.
- Produces: `POST /api/handoffs/<handoff-id>/execution-start` with exact `{wake_token, launch_id}` input and an exact launch-grant response.
- Produces: `POST /api/handoffs/<handoff-id>/execution-checkpoint` with exact `{launch_id, reason}` input and idempotent suppression/hand-back output.

- [x] **Step 1: Write failing store tests**

  Add tests proving one transaction validates the exact lease, wake, expiry, task authority, delegation authority, and priority before changing `received` to `execution_started`; the same launch id replays the same grant; a different launch id is rejected; revocation before CAS suppresses; revocation after CAS retains immutable start evidence and terminalizes through hand-back; and delegated `received -> actively_executing` is rejected until CAS.

- [x] **Step 2: Run the store tests and verify RED**

  Run: `python3 -m unittest tests.test_handoff_dispatcher.HandoffDispatcherTests`

  Expected: failures for missing `start_execution`, missing `execution_started`, and missing checkpoint reconciliation.

- [x] **Step 3: Implement the minimal transactional store contract**

  Add a private `execution_starts` table keyed by `handoff_id` and unique `launch_id`, storing only wake, registration, capability, and grant references plus generation and start time. Compute the replayable grant from stable private wake/start data, insert the start record and CAS the handoff status inside one `BEGIN IMMEDIATE` transaction, append one start event, and make checkpoint replay mutation-fenced.

- [x] **Step 4: Write and run failing HTTP tests, then implement the routes**

  Run: `python3 -m unittest tests.test_server.HandoffDispatcherApiTests`

  Expected RED: the execution-start and execution-checkpoint routes are absent. Implement exact authenticated request/response shapes, two monotonic canonical authority reads before the start CAS, and no raw grant in audit output.

- [x] **Step 5: Verify Task 1 GREEN**

  Run: `python3 -m unittest tests.test_handoff_dispatcher tests.test_server.HandoffDispatcherApiTests`

  Expected: all selected tests pass.

---

### Task 2: Private gated launch shim and atomic result evidence

**Files:**
- Create: `gtasks/handoff_launch_runner.py`
- Modify: `gtasks/local_handoff_dispatcher.py`
- Create: `tests/test_handoff_launch_runner.py`
- Modify: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Produces: `LaunchRequest(argv, working_directory, timeout_seconds)` from `CodexResumeAdapter.launch_request(claim)`.
- Produces: `GatedLaunchController.start`, `observe`, `open_gate`, and `cancel`.
- The runner reads one mode-0600 request file, writes atomic ready/result JSON, takes one exclusive runner lock, and accepts one atomic gate file containing only launch id and grant evidence.

- [x] **Step 1: Write failing runner tests**

  Use `sys.executable -c` as the fake target command. Prove the command cannot run before the gate, the ready PID is observable, duplicate shim startup executes the fake command at most once, success writes an atomic zero result, missing executable writes proven `prelaunch_failure`, and nonzero/timeout writes an ambiguous result without stdout or stderr.

- [x] **Step 2: Run the runner tests and verify RED**

  Run: `python3 -m unittest tests.test_handoff_launch_runner`

  Expected: import failure for the absent runner/controller interfaces.

- [x] **Step 3: Implement the narrow shim and controller**

  Use only private regular files/directories, `os.open(..., O_EXCL)` for the one-runner lock, atomic temporary-file `fsync` plus `os.replace` for evidence, `subprocess.Popen` for the gated shim, and `subprocess.run` only inside the shim after a valid gate. Persist no command output and pass no claim or credential on the shim argv.

- [x] **Step 4: Verify Task 2 GREEN**

  Run: `python3 -m unittest tests.test_handoff_launch_runner tests.test_local_handoff_dispatcher.CodexResumeAdapterTests`

  Expected: all selected tests pass and the fake command count is exactly one.

---

### Task 3: Recoverable inbox handshake and no ambiguous retries

**Files:**
- Modify: `gtasks/local_handoff_dispatcher.py`
- Modify: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Extends `wake_inbox` with current launch and pending server-action fields.
- Adds append-preserving `wake_launches` evidence for preparing, spawned, ready, grant-received, gate-open, completed, pre-launch-failed, cancelled, and ambiguous outcomes.
- Replaces `execution_authority` with `execution_start`; adds `execution_checkpoint`.

- [x] **Step 1: Write failing crash-boundary tests**

  Cover crashes after launch-id persistence, shim spawn before PID persistence, PID persistence before ready, ready before CAS, CAS response before local grant persistence, grant persistence before gate open, gate open before inbox `executing`, live launched PID observation, and result write before reconciliation. Every case must execute the fake target zero or one times, never more than once.

- [x] **Step 2: Run the crash tests and verify RED**

  Run: `python3 -m unittest tests.test_local_handoff_dispatcher.PrivateWakeInboxTests`

  Expected: failures because launch evidence and recovery transitions do not exist.

- [x] **Step 3: Implement the inbox state machine and worker**

  Prepare a launch id durably before spawning, store the PID before asking for a start grant, require runner-ready evidence before CAS, persist the grant reference, atomically open the gate, and derive `executing` from gate evidence. On restart, reconcile the existing launch before considering a new attempt. Update recovered claim capabilities for every nonterminal launch state.

- [x] **Step 4: Write failing ambiguity and exhaustion tests**

  Prove nonzero, timeout, dead post-gate PID, and malformed/missing post-gate result become `recovery_required` and call only the idempotent checkpoint path. Prove only pre-gate failure retries, exhaustion sends terminal delivery failure, the server claim is released, and the local terminal evidence remains queryable.

- [x] **Step 5: Implement terminal server actions and verify GREEN**

  Persist the pending checkpoint or terminal-delivery action before sending it. Retry only that server action after network loss; never retry the target command. Keep `recovery_required` or exhausted `failed` as the auditable local terminal state after the server receipt is verified.

  Run: `python3 -m unittest tests.test_local_handoff_dispatcher tests.test_handoff_dispatcher tests.test_server.HandoffDispatcherApiTests`

  Expected: all selected tests pass.

---

### Task 4: Remove unsafe delegated in-process wake, document semantics, and verify

**Files:**
- Modify: `gtasks/handoff_dispatcher.py`
- Modify: `tests/test_handoff_dispatcher.py`
- Modify: `docs/runbooks/agent-handoff-dispatcher.md`
- Modify: `.superpowers/sdd/task-5-report.md` after commit; this file is intentionally ignored.

**Interfaces:**
- `LocalAgentDispatcher` terminally rejects delegated execution because it cannot provide the durable gated target handshake; owned legacy delivery remains unchanged.
- The runbook documents `received -> execution_started -> actively_executing|still_blocked|completed`, CAS race semantics, launch evidence, ambiguity handling, and terminal pre-launch exhaustion.

- [x] **Step 1: Write and run the failing in-process delegated-path test**

  Run: `python3 -m unittest tests.test_handoff_dispatcher.HandoffDispatcherTests`

  Expected RED: the delegated in-process callback is still invoked without a start grant.

- [x] **Step 2: Remove delegated execution from the in-process path and update the runbook**

  Fail it closed through durable terminal delivery failure before callback invocation. Document all server/local states and operator recovery evidence without claiming deployment or a live wake.

- [x] **Step 3: Run final verification**

  Run: `python3 -m unittest discover -s tests`

  Run: `python3 -m compileall -q gtasks tests`

  Run: `git diff --check`

  Expected: complete suite passes; compile and diff checks exit zero.

- [x] **Step 4: Commit and update the ignored report**

  Commit the reviewed tracked candidate with `fix: make delegated launch crash recoverable`. Then update `.superpowers/sdd/task-5-report.md` with the final SHA, RED/GREEN evidence, test totals, static checks, clean tracked status, and the explicit statement that no real Codex/OpenClaw command ran.
