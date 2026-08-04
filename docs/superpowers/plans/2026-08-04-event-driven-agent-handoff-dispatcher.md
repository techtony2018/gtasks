# Event-Driven Agent Handoff Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver actionable canonical Mission Control task changes exactly once to Tammy, Timmy, or Toddy's existing fixed Codex thread through one identity-scoped local Dispatcher per host, with a single append-only audit source rendered in Task Timeline and the cross-task Handoff Log.

**Architecture:** Mission Control classifies verified canonical mutation receipts, writes one idempotent SQLite outbox plus append-only audit chain, and exposes authenticated identity-scoped claim/ack/failure APIs. A host-local runner owns one Agent identity and one private fixed-thread binding, long-polls only that identity, fail-closed verifies the lease and local Codex resume contract, and resumes the existing thread. Task Timeline and Handoff Log are read-only projections over the same audit rows; GBrain remains canonical for task, To Do, assignment, authority, and lifecycle state.

**Tech Stack:** Python 3.12 standard library, SQLite, `ThreadingHTTPServer`, GBrain adapter readback, vanilla JavaScript/CSS, `codex exec resume`, LaunchAgents, Python `unittest`, Playwright-based independent UI QA.

## Global Constraints

- Use only canonical versioned mutation receipts; never use a blind task scan as the primary trigger.
- Resolve exactly one recipient from verified `assigned_to` and exactly one identity-scoped private registration; never infer from text, collection, recent activity, or title.
- Persist and read back the outbox and first audit event before any claim or wake attempt.
- Use task slug, canonical version/event id, and deterministic trigger discriminator for idempotency; replay, retry, restart, Guardian, and concurrency produce one effective handoff.
- Local configs contain one Agent identity and one private fixed-thread binding. Repository files contain no private tokens or thread bindings.
- Never create, fork, replace, or guess a Codex thread. The runner may only invoke the locally verified `codex exec resume <fixed-thread-id> <prompt> --json` contract.
- Guardian is fallback lease/dead-letter reconciliation, never the primary sender or business-task executor.
- Store only privacy-safe structured summaries and pseudonymized destination registration references; never store or render tokens, raw thread ids, private prompts, or full thread output.
- The handoff event table is the one evidence source. Task Timeline and Handoff Log are read-only projections and never repair GBrain implicitly.
- Tests and pre-commit QA use synthetic fixtures and perform no real Agent wake, external message, Telegram/email action, or Tony-task mutation.
- Any visible UI candidate requires independent PASS before commit at desktop `1440x1000` and genuine mobile `390x844`.
- Final deployment uses the dashboard-managed `gtasks` service only. A bounded one-host canary may run only after commit, push, deployment, and exact identity/thread/token readback.

---

### Task 1: Complete the durable classifier, outbox, and audit contract

**Files:**
- Create: `gtasks/handoff_dispatcher.py`
- Create: `tests/test_handoff_dispatcher.py`

**Interfaces:**
- Consumes: `ActionableChange`, verified `AgentRegistration`, UTC-aware `datetime`.
- Produces: `HandoffClassifier.classify(...)`, `DurableHandoffStore`, `HandoffDispatcher.record(...)`, `LocalAgentDispatcher.run_once(...)`, `HandoffGuardian.reconcile(...)`, task-scoped and cross-task `EventPage` projections.

- [ ] **Step 1: Extend failing tests for the complete trigger and audit contract**

Add table-driven tests for all actionable triggers, every non-actionable suppression class, missing/multiple registration, exact route mismatch, stable blocker suppression, retry versus terminal failure, acknowledgement states, ownership race, clock skew, duplicate timestamps, pagination boundaries, retention/export metadata, corrections-as-append, and restart continuity. Require `EventPage.next_sequence` to be absent at the end and require redacted `registration_ref` rather than `thread_ref` in every returned structure.

- [ ] **Step 2: Run the focused suite and verify RED**

Run: `python3 -m unittest tests.test_handoff_dispatcher -v`

Expected: FAIL on missing filters, end-of-page cursor semantics, correction events, acknowledgement detail validation, and redacted registration projection.

- [ ] **Step 3: Implement the minimal complete core**

Keep the public construction contract:

```python
store = DurableHandoffStore(path)
dispatcher = HandoffDispatcher(store, registrations=registrations)
record = dispatcher.record(change, now=now)
page = store.query_events(
    limit=50,
    after_sequence=0,
    task_slug=None,
    agent_slug=None,
    status=None,
    event_type=None,
    correlation_id=None,
)
```

Add `append_correction(handoff_id, *, supersedes_event_id, summary, now)` and enforce allowed acknowledgement states `received`, `actively_executing`, `still_blocked`, and `completed`; `still_blocked` requires one privacy-safe nonempty detail. Hash registration ids for user-visible projection while retaining the private raw id only in the lease table.

- [ ] **Step 4: Run the focused suite and static checks**

Run: `python3 -m unittest tests.test_handoff_dispatcher -v && python3 -m py_compile gtasks/handoff_dispatcher.py && git diff --check`

Expected: all dispatcher tests PASS, compilation succeeds, diff check is clean.

---

### Task 2: Add authenticated central claim, acknowledgement, failure, and log APIs

**Files:**
- Modify: `gtasks/server.py`
- Modify: `tests/test_server.py`
- Create: `scripts/provision_handoff_dispatcher_credentials.py`
- Create: `tests/test_handoff_dispatcher_credentials.py`
- Modify: `dashboard-integration.json`

**Interfaces:**
- Consumes: `DurableHandoffStore`, identity-scoped bearer credentials, one private registration id per credential.
- Produces:
  - `POST /api/handoffs/claim` with `{registration_id, wait_seconds, lease_seconds}`.
  - `POST /api/handoffs/<handoff-id>/ack` with `{status, detail}`.
  - `POST /api/handoffs/<handoff-id>/failure` with `{failure_class}` where class is `retryable` or `terminal`.
  - `GET /api/handoff-events` and `GET /api/tasks/<encoded-slug>/handoff-events`.

- [ ] **Step 1: Write failing server/auth tests**

Cover missing/invalid/shared tokens, wrong identity, wrong registration, claim races, 0–25 second bounded wait, 5–120 second lease, route re-readback callback failure, redacted payload, allowed acknowledgements, blocked-detail requirement, retryable/terminal failures, deterministic event filters/counts/cursors, and read-only export. Assert all rejected writes leave the store unchanged.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_server.HandoffDispatcherApiTests tests.test_handoff_dispatcher_credentials -v`

Expected: FAIL because the auth loader, runtime dependencies, and endpoints do not exist.

- [ ] **Step 3: Implement identity-scoped credentials and endpoints**

Add:

```python
@dataclass(frozen=True, slots=True)
class HandoffDispatcherIdentity:
    agent_slug: str
    registration_id: str

class HandoffDispatcherAuth:
    @classmethod
    def from_file(cls, path: Path) -> "HandoffDispatcherAuth": ...
    def resolve(self, authorization: str | None) -> HandoffDispatcherIdentity | None: ...
```

Credential schema version 1 stores only `agent_slug`, `registration_sha256`, and `token_sha256`, mode `0600`. Add server arguments `--handoff-store` and `--handoff-dispatcher-credentials-file`. The claim response includes safe task/action fields, correlation/idempotency ids, and a pseudonymized registration reference, never the token or fixed thread id.

- [ ] **Step 4: Provisioner and dashboard contract**

The provisioner accepts three explicit `--identity-config` files plus token files, hashes secrets, writes mode `0600`, and prints no token. `dashboard-integration.json` declares the private runtime store and credential paths without embedding their contents.

- [ ] **Step 5: Verify focused server/auth tests**

Run: `python3 -m unittest tests.test_server.HandoffDispatcherApiTests tests.test_handoff_dispatcher_credentials -v && python3 -m json.tool dashboard-integration.json >/dev/null && git diff --check`

Expected: PASS with no live network call.

---

### Task 3: Build the one-identity host-local Dispatcher runner

**Files:**
- Create: `gtasks/local_handoff_dispatcher.py`
- Create: `tests/test_local_handoff_dispatcher.py`
- Create: `scripts/install_local_handoff_dispatcher.py`
- Create: `config/handoff-dispatcher/agent.plist.template`

**Interfaces:**
- Consumes private mode-`0600` JSON containing exactly `schema_version`, `agent_slug`, `registration_id`, `fixed_thread_id`, `mission_control_url`, and `token_file`.
- Produces `LocalDispatcherClient.claim()`, `ack()`, `fail()`, `CodexResumeAdapter.verify_contract()`, `resume_existing_thread()`, and a bounded `run_forever()` long-poll loop.

- [ ] **Step 1: Write failing local-runner tests**

Test exact config shape/mode, rejection of extra Agent identities, token isolation, identity-scoped claims, local Codex version/help verification, argument-list subprocess invocation without shell, exact existing thread id, sanitized prompt, received/active/blocked/completed helper acknowledgements, network loss, retry, process restart, signal stop, and no thread-creation command.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_local_handoff_dispatcher -v`

Expected: FAIL because the runner and installer do not exist.

- [ ] **Step 3: Implement the runner and resume adapter**

Use only argument arrays:

```python
subprocess.run(
    [codex_path, "exec", "resume", fixed_thread_id, prompt, "--json"],
    cwd=working_directory,
    check=False,
    capture_output=True,
    text=True,
    timeout=resume_timeout,
)
```

The prompt contains the safe handoff fields and instructs the existing Agent to acknowledge through the installed local helper; it never includes a bearer token or raw private config. Nonzero exit and timeout use the same handoff id for failure/retry.

- [ ] **Step 4: Implement deterministic LaunchAgent installation**

Install one runner label and one private config per host, verify `codex --version` plus `codex exec resume --help`, preserve existing fixed thread id, and read back plist/config hashes. The installer must fail if any config contains a second Agent identity or if the token/config mode is not `0600`.

- [ ] **Step 5: Verify local-runner tests**

Run: `python3 -m unittest tests.test_local_handoff_dispatcher -v && python3 -m py_compile gtasks/local_handoff_dispatcher.py scripts/install_local_handoff_dispatcher.py && git diff --check`

Expected: PASS with synthetic subprocess and HTTP fixtures only.

---

### Task 4: Bridge verified canonical mutations into the dispatcher

**Files:**
- Modify: `gtasks/server.py`
- Modify: `gtasks/gbrain.py`
- Modify: `tests/test_server.py`
- Modify: `tests/test_gbrain.py`
- Modify: `tests/test_handoff_dispatcher.py`

**Interfaces:**
- Consumes verified mutation receipts and authoritative post-write Task/To Do/relationship readback.
- Produces `CanonicalHandoffEventBridge.after_verified_mutation(before, after, receipt, now)` and one dispatcher record or one explicit suppression/system-attention audit event.

- [ ] **Step 1: Write failing bridge tests**

Cover Tony answer received, To Do added/materially changed, planned→active, waiting-for-information resolved, system dependency recovered, authorization granted, ownership changed, presentation-only edit, duplicate save, derived count, stale cache refresh, unchanged blocker, missing/multiple `assigned_to`, and partial write. Assert outbox creation occurs only after verified canonical readback and the same task/To Do identities are preserved.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_server.HandoffMutationBridgeTests tests.test_gbrain.HandoffMutationReadbackTests -v`

Expected: FAIL because verified mutations do not emit normalized dispatcher changes.

- [ ] **Step 3: Implement normalized bridge and wire mutation endpoints**

Add a pure normalizer that compares canonical before/after snapshots, produces one `ActionableChange`, and calls `HandoffDispatcher.record()` only after the existing adapter receipt is verified. Route/data errors append `system_attention` evidence without changing Task status or claiming Tony is blocking. Delivery failures never roll back an already verified canonical user write; they remain in retry/dead-letter evidence.

- [ ] **Step 4: Verify bridge and regression tests**

Run: `python3 -m unittest tests.test_server.HandoffMutationBridgeTests tests.test_gbrain.HandoffMutationReadbackTests tests.test_handoff_dispatcher -v`

Expected: PASS with no real GBrain mutation or Agent wake.

---

### Task 5: Render the same audit events in Task Timeline and Handoff Log

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/project_browser_fixture.py`

**Interfaces:**
- Consumes the two read-only event endpoints from Task 2.
- Produces task-scoped Timeline rows and one `Handoff Log` view with time/Agent/status/event/failure/correlation filters, direct correlation navigation to Task detail, identical totals/order, bounded pagination, and accessible states.

- [ ] **Step 1: Write failing frontend contracts**

Require one Timeline section in Task detail, one rail entry `Handoff Log`, semantic ordered timeline/list markup, shared event renderer, direct correlation lookup, bounded load-more, consistent total count, redacted values, and empty/loading/stale/error/dead-letter states. Require no mutation fetch from either view.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_frontend_contract.FrontendContractTests -v`

Expected: FAIL because the UI projections do not exist.

- [ ] **Step 3: Implement the read-only projections**

Add `loadTaskHandoffTimeline(taskSlug)`, `loadHandoffLog({reset, filters})`, `renderHandoffEvents(events, destination)`, and `openHandoffCorrelation(correlationId, taskSlug)`. Preserve the current Task detail and mobile sheet focus-return patterns. Never render raw thread ids, tokens, or unbounded diffs.

- [ ] **Step 4: Verify frontend contracts and syntax**

Run: `python3 -m unittest tests.test_frontend_contract -v && node --check static/app.js && git diff --check`

Expected: PASS.

---

### Task 6: Document, independently QA, release, install, and canary

**Files:**
- Modify: `README.md`
- Create: `docs/runbooks/agent-handoff-dispatcher.md`
- Create: `docs/release-evidence/v0.0.76.md`
- Modify: `gtasks/releases.json`
- Modify: `tests/test_releases.py`
- Modify: `tests/test_dashboard_integration.py`

**Interfaces:**
- Consumes all completed implementation slices.
- Produces V0.0.76 source/runtime version, runbook, release evidence, three private host installations, and one bounded Tammy canary correlated through the same audit chain.

- [ ] **Step 1: Write failing release/documentation tests**

Require V0.0.76, dashboard runtime paths, one-source Timeline/Log wording, local Dispatcher command contract, retention/export/redaction policy, failure recovery, rollback, and exact no-thread-creation boundary.

- [ ] **Step 2: Verify RED, then add release documentation**

Run: `python3 -m unittest tests.test_releases tests.test_dashboard_integration -v`

Expected: FAIL until the release metadata and docs are added.

- [ ] **Step 3: Run the complete automated gate**

Run: `python3 -m unittest discover -s tests && node --check static/app.js && python3 -m compileall -q gtasks scripts && git diff --check`

Expected: all tests PASS, static/compile/diff checks succeed.

- [ ] **Step 4: Obtain independent pre-commit UI/UX QA**

Freeze the ordered source fingerprint. Independent QA must explicitly PASS at desktop `1440x1000` and genuine mobile `390x844`, covering Task Timeline, Handoff Log filters/counts/correlation/load-more, empty/loading/stale/error/dead-letter states, keyboard/focus, redaction, and zero live writes or Agent wakes. FAIL or INCONCLUSIVE returns to Task 5 and repeats this gate.

- [ ] **Step 5: Commit and push only after QA PASS**

Commit the reviewed candidate on `codex/event-driven-agent-handoff-dispatcher`, push it, then integrate to `main` without force. Verify `HEAD == origin/main` and intended tracked source is clean while preserving unrelated user-owned files.

- [ ] **Step 6: Dashboard deployment and live readback**

Restart only dashboard-managed `gtasks`, verify `/api/health` reports V0.0.76, verify authenticated synthetic claim/ack/dead-letter plus read-only Timeline/Log, and verify browser UI at both required viewports.

- [ ] **Step 7: Install all three host-local runners and run one bounded canary**

Provision private per-host config/token files, install/read back Tammy, Timmy, and Toddy LaunchAgents, and verify each sees only its own identity. Run one authorized Tammy canary against the existing fixed thread `019fb4e7-8846-71a0-8d4b-24d262979981`; verify one claim, one resume, one received/active receipt, stable correlation, and zero cross-identity visibility. Do not canary Timmy or Toddy in this release.

- [ ] **Step 8: Complete canonical ticket with evidence**

Append implementation, test, QA, commit/push, deployment, three-host install, and one-host canary receipts to the same System Ticket. Mark it completed only after exact GBrain page/link readback and `/api/system-tickets?refresh=1&include_completed=1` show the same immutable slug, completed status, and zero issues.
