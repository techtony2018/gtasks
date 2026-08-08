# OpenClaw Agents and Temporary Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tammy-OC, Timmy-OC, and Toddy-OC as independent Mission Control Agents with fixed OpenClaw sessions, plus Tony-authorized time-bounded delegation that adds work without overriding their owned work.

**Architecture:** GBrain remains canonical for Agents, Tasks, Goal relationships, and delegation leases. The existing durable Handoff Dispatcher gains lease-aware routing and task-scoped execution claims; one host-local supervisor runs two isolated single-identity workers, selecting either the existing Codex resume adapter or a new fixed-session OpenClaw adapter. Mission Control remains the only authenticated mutation and Artifact publication boundary.

**Tech Stack:** Python 3.12 standard library, GBrain CLI adapter, SQLite handoff store, local HTTP API, vanilla JavaScript/CSS, macOS LaunchAgents, OpenClaw CLI, `unittest`.

## Global Constraints

- Canonical OpenClaw identities are exactly `agents/tammy-oc`, `agents/timmy-oc`, and `agents/toddy-oc`.
- Host pairing is exactly Tammy/Tammy-OC, Timmy/Timmy-OC, and Toddy/Toddy-OC.
- Initial provisioning creates no `default_agent_for` Goal relationships.
- Every OpenClaw worker uses one pre-existing fixed session and never creates, replaces, forks, or guesses a session.
- Each worker config contains one identity; a host supervisor loads two config paths but never merges credentials or state.
- Owned OpenClaw work always outranks delegated work; a valid zero delegated claim is not failure.
- Delegation requires Tony's explicit authorization, uses a selectable start and end, defaults to a 15-minute through 7-day allowed range, and never rewrites `assigned_to`.
- Runtime instants are stored in UTC and displayed in `America/Los_Angeles`.
- OpenClaw never writes raw GBrain state; all mutations and Artifacts use authenticated Mission Control boundaries with exact readback.
- Existing Codex Agent behavior and credentials must remain backward compatible throughout rollout.
- No production Agent wake, canonical mutation, or remote install occurs until synthetic tests and the Tammy-OC isolated canary are authorized by the execution owner.
- Every UI candidate requires independent PASS at 1440x1000 and genuine 390x844 before commit.
- Preserve Tony's existing `.gitignore` edit and unrelated untracked files.

---

## File structure

**Create**

- `gtasks/delegation.py` — delegation lease domain model, lifecycle validation, pairing, eligibility, and priority rules.
- `gtasks/openclaw_adapter.py` — fixed-session OpenClaw command builder, bounded output parser, and execution result.
- `gtasks/local_handoff_supervisor.py` — two-worker host supervisor with isolated config loading and lifecycle.
- `config/openclaw-agents/agents.json` — public identity, route, collection, and runtime declarations only.
- `config/openclaw-agents/dispatcher-supervisor.plist.template` — one host supervisor LaunchAgent template.
- `scripts/provision_openclaw_agent_profiles.py` — dry-run/execute canonical profile and collection provisioner with exact readback.
- `scripts/install_local_handoff_supervisor.py` — deterministic private two-worker supervisor installer.
- `tests/test_delegation.py` — lease and delegated-work domain tests.
- `tests/test_openclaw_adapter.py` — fixed-session invocation and output parsing tests.
- `tests/test_local_handoff_supervisor.py` — two isolated workers, startup, and drift tests.
- `tests/test_artifact_publisher.py` — OpenClaw `created_by`, delegated provenance, and collection-isolation tests.
- `docs/runbooks/openclaw-agent-delegation.md` — provisioning, canary, rollback, and incident procedure.

**Modify**

- `gtasks/domain.py:23-45,924-990,1684-1760` — six Agent scopes, runtime metadata, and ownership validation.
- `gtasks/gbrain.py:3589-3980,4970-5040` — six Agent reads, canonical profile provisioning, lease persistence, and delegation readback.
- `gtasks/handoff_dispatcher.py:122-220,430-860,1180-1370` — six registrations, lease-aware classification, execution claims, audit events, and recovery.
- `gtasks/local_handoff_dispatcher.py:130-230,500-680` — reusable single-worker interface and runtime adapter injection.
- `gtasks/server.py:20-45,780-900,1210-1450,1650-1740,2200-2350,3000-3380` — delegation and Agent APIs, authorization, health, and UI projections.
- `scripts/provision_handoff_dispatcher_credentials.py:15-100` — accept exactly six unique identities while preserving secret hashing.
- `scripts/install_local_handoff_dispatcher.py:20-220` — expose reusable worker installation primitives without changing current installed behavior.
- `static/index.html` — delegation confirmation dialog and Task owner/executor fields.
- `static/app.js` — six Agent cards, delegation flow, separate owned/delegated work, lease countdown, Task details, and status recovery.
- `static/styles.css` — compact runtime/lease presentation and responsive dialog/sheet behavior.
- `tests/test_gbrain.py` — profile, collections, no-default-Goal, and lease persistence coverage.
- `tests/test_handoff_dispatcher.py` — delegation classification, claim, priority, retry, expiry, and recovery coverage.
- `tests/test_server.py` — APIs, authorization, projections, and health coverage.
- `tests/test_frontend_contract.py` — delegation UI, accessibility, focus, copy, and responsive contracts.
- `README.md`, `docs/runbooks/agent-handoff-dispatcher.md`, `docs/runbooks/mission-control-system-documentation.md` — six-Agent runtime and operational contracts.
- `gtasks/releases.json`, `tests/test_releases.py` — one patch release after the independent gate.

---

### Task 1: Define six independent Agent scopes and runtime metadata

**Files:**
- Create: `config/openclaw-agents/agents.json`
- Modify: `gtasks/domain.py:23-45,924-990,1684-1760`
- Test: `tests/test_gbrain.py:1389-1640,1960-2070`

**Interfaces:**
- Consumes: existing `AGENT_SCOPES`, `ARTIFACT_AGENT_SCOPES`, and `AgentProfile.from_page`.
- Produces: `AGENT_RUNTIME_BY_SLUG: dict[str, str]`, six-entry task and Artifact scope tables, and `AgentProfile.runtime: str` for server/UI consumers.

- [ ] **Step 1: Add failing scope and profile tests**

```python
def test_openclaw_agents_have_independent_scopes_and_no_default_goals(self) -> None:
    self.assertEqual(dict(domain.AGENT_SCOPES)["agents/tammy-oc"], "collections/tammy-oc-tasks")
    self.assertEqual(domain.ARTIFACT_BY_AGENT["agents/tammy-oc"], "collections/tammy-oc-artifacts")
    page = {
        "slug": "agents/tammy-oc",
        "type": "agent",
        "title": "Agent Tammy-OC",
        "compiled_truth": "Independent OpenClaw Agent on Tammy.",
        "frontmatter": {"runtime": "openclaw"},
    }
    profile = AgentProfile.from_page(page, work_root="collections/tammy-oc-tasks", edges=())
    self.assertEqual(profile.runtime, "openclaw")
    self.assertEqual(profile.default_goal_slugs, ())
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_gbrain.AgentProfileReadTests tests.test_gbrain.AgentArtifactAdapterTests`

Expected: FAIL because `agents/tammy-oc` is absent and `AgentProfile` has no `runtime` field.

- [ ] **Step 3: Add the exact public declaration and domain fields**

```json
{
  "schema_version": 1,
  "agents": [
    {"slug":"agents/tammy-oc","name":"Tammy-OC","runtime":"openclaw","route":"hosts/tammy","task_collection":"collections/tammy-oc-tasks","artifact_collection":"collections/tammy-oc-artifacts"},
    {"slug":"agents/timmy-oc","name":"Timmy-OC","runtime":"openclaw","route":"hosts/timmy","task_collection":"collections/timmy-oc-tasks","artifact_collection":"collections/timmy-oc-artifacts"},
    {"slug":"agents/toddy-oc","name":"Toddy-OC","runtime":"openclaw","route":"hosts/toddy","task_collection":"collections/toddy-oc-tasks","artifact_collection":"collections/toddy-oc-artifacts"}
  ]
}
```

Add matching constants and require `runtime in {"codex", "openclaw"}`. Default existing profiles to `codex` only when the slug is one of the three existing approved identities; do not infer runtime from the display name.

- [ ] **Step 4: Run focused and domain tests**

Run: `python3 -m unittest tests.test_gbrain.AgentProfileReadTests tests.test_gbrain.AgentArtifactAdapterTests tests.test_domain`

Expected: PASS with six unique task roots and six unique Artifact roots.

- [ ] **Step 5: Commit the independent identity model**

```bash
git add config/openclaw-agents/agents.json gtasks/domain.py tests/test_gbrain.py
git commit -m "feat: define independent OpenClaw agent identities"
```

---

### Task 2: Provision canonical OpenClaw profiles and collections without Goals

**Files:**
- Create: `scripts/provision_openclaw_agent_profiles.py`
- Modify: `gtasks/gbrain.py:3589-3855`
- Test: `tests/test_gbrain.py:3140-3270,3620-3800`

**Interfaces:**
- Consumes: `config/openclaw-agents/agents.json` and `GBrainAdapter.runner`.
- Produces: `GBrainAdapter.provision_agent_profile(declaration: Mapping[str, str], *, execute: bool) -> AgentProvisioningReceipt` and a CLI JSON receipt containing three exact slugs, six collections, and zero Goal links.

- [ ] **Step 1: Write failing dry-run and execute/readback tests**

```python
def test_provision_openclaw_profile_creates_no_goal_relationship(self) -> None:
    receipt = adapter.provision_agent_profile(OPENCLAW_DECLARATION, execute=True)
    self.assertTrue(receipt.verified)
    self.assertEqual(receipt.agent_slug, "agents/tammy-oc")
    self.assertEqual(receipt.default_goal_slugs, ())
    self.assertEqual(runner.links_of_type("agents/tammy-oc", "default_agent_for"), [])
```

Also assert an existing mismatched page, unexpected membership, or pre-existing Goal link fails without repair.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_gbrain.AgentCreationTests tests.test_gbrain.AgentReadTests`

Expected: FAIL because `provision_agent_profile` and the receipt do not exist.

- [ ] **Step 3: Implement fail-closed provisioning**

The method must create/read exactly the Agent page, task collection, Artifact collection, global Artifact membership, and typed Agent-to-collection relationships. It must not call `add_link` with `default_agent_for`. Dry-run returns the intended operations without invoking `put_page` or `add_link`; execute reports verified only after exact page/link readback.

- [ ] **Step 4: Run tests and verify CLI dry-run**

Run:

```bash
python3 -m unittest tests.test_gbrain.AgentCreationTests tests.test_gbrain.AgentReadTests
python3 scripts/provision_openclaw_agent_profiles.py --config config/openclaw-agents/agents.json --dry-run
```

Expected: PASS; CLI output reports `agent_count: 3`, `collection_count: 6`, `default_goal_link_count: 0`, and `mutated: false`.

- [ ] **Step 5: Commit provisioning support**

```bash
git add gtasks/gbrain.py scripts/provision_openclaw_agent_profiles.py tests/test_gbrain.py
git commit -m "feat: provision OpenClaw agent profiles safely"
```

---

### Task 3: Implement the canonical delegation lease domain and state machine

**Files:**
- Create: `gtasks/delegation.py`
- Create: `tests/test_delegation.py`

**Interfaces:**
- Produces:

```python
class DelegationState(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    REVOKED = "revoked"

@dataclass(frozen=True, slots=True)
class AgentDelegationLease:
    slug: str
    source_agent: str
    executor_agent: str
    authorized_by: str
    starts_at: datetime
    ends_at: datetime
    display_timezone: str
    allowed_operations: tuple[str, ...]
    state: DelegationState
    created_at: datetime
    updated_at: datetime

def paired_openclaw_agent(source_agent: str) -> str: ...
def lease_state_at(lease: AgentDelegationLease, now: datetime) -> DelegationState: ...
def delegated_work_is_eligible(*, owned_work_ready: bool, task_status: str, task_owner: str, lease: AgentDelegationLease, now: datetime) -> bool: ...
```

- [ ] **Step 1: Write exhaustive failing lifecycle and priority tests**

```python
def test_owned_work_always_prevents_delegated_claim(self) -> None:
    self.assertFalse(delegated_work_is_eligible(
        owned_work_ready=True,
        task_status="planned",
        task_owner="agents/tammy",
        lease=active_tammy_lease(),
        now=NOW,
    ))

def test_custom_duration_is_bounded_and_dst_safe(self) -> None:
    lease = make_lease(starts_at=NOW, ends_at=NOW + timedelta(hours=8))
    self.assertEqual(lease.display_timezone, "America/Los_Angeles")
    with self.assertRaisesRegex(ValueError, "15 minutes through 7 days"):
        make_lease(starts_at=NOW, ends_at=NOW + timedelta(days=8))
```

Cover wrong pair, Tony not authorizer, naive datetimes, already active task,
scheduled/active/completed/expired/revoked transitions, extension, and zero-work outcomes.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_delegation`

Expected: import failure because `gtasks.delegation` does not exist.

- [ ] **Step 3: Implement the minimal immutable domain module**

Use only `dataclasses`, `datetime`, `enum`, and `zoneinfo`. Store aware UTC
instants; accept only the three fixed Codex/OpenClaw pairs; preserve explicit
allowed operations; reject an end not strictly after start.

- [ ] **Step 4: Run and verify GREEN**

Run: `python3 -m unittest tests.test_delegation`

Expected: PASS for all lifecycle, pairing, duration, and priority cases.

- [ ] **Step 5: Commit the lease domain**

```bash
git add gtasks/delegation.py tests/test_delegation.py
git commit -m "feat: model temporary agent delegation leases"
```

---

### Task 4: Persist leases canonically and expose confirmation-bound APIs

**Files:**
- Modify: `gtasks/gbrain.py:4970-5040`
- Modify: `gtasks/server.py:1650-1740,3000-3380`
- Test: `tests/test_gbrain.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `AgentDelegationLease` from Task 3.
- Produces:

```python
GBrainAdapter.list_agent_delegations() -> tuple[AgentDelegationLease, ...]
GBrainAdapter.create_agent_delegation(lease: AgentDelegationLease) -> MutationReceipt
GBrainAdapter.update_agent_delegation(lease: AgentDelegationLease) -> MutationReceipt
POST /api/agent-delegations
PATCH /api/agent-delegations/<encoded-slug>
GET /api/agent-delegations
```

- [ ] **Step 1: Write failing canonical and HTTP tests**

Test exact `member_of -> collections/mission-control-agent-delegations`, Tony
authorization, paired identities, custom duration, schedule activation,
extension receipt, normal completion, revocation, idempotency, stale-version
conflict, and readback. Assert requests cannot set `assigned_to`, raw
credentials, registration IDs, or session keys.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_gbrain.AgentDelegationAdapterTests tests.test_server.AgentDelegationApiTests`

Expected: FAIL because adapter methods and routes are absent.

- [ ] **Step 3: Implement canonical rendering, readback, and API validation**

`POST` accepts exactly:

```json
{
  "source_agent":"agents/tammy",
  "executor_agent":"agents/tammy-oc",
  "starts_at":"2026-08-07T17:00:00Z",
  "ends_at":"2026-08-08T01:00:00Z",
  "display_timezone":"America/Los_Angeles",
  "allowed_operations":["task_status","todo","comment","artifact"]
}
```

The server derives Tony as actor and the canonical slug/idempotency input. A
successful response requires `receipt.verified: true` and the exact lease
readback. `PATCH` accepts only `ends_at` plus expected version for extension,
or one action from `complete` and `revoke`; it appends an immutable receipt.

- [ ] **Step 4: Run tests and API static checks**

Run:

```bash
python3 -m unittest tests.test_gbrain.AgentDelegationAdapterTests tests.test_server.AgentDelegationApiTests
python3 -m compileall -q gtasks tests
```

Expected: PASS; no API test produces an unverified or cross-pair mutation.

- [ ] **Step 5: Commit canonical delegation APIs**

```bash
git add gtasks/gbrain.py gtasks/server.py tests/test_gbrain.py tests/test_server.py
git commit -m "feat: add verified agent delegation APIs"
```

---

### Task 5: Add lease-aware Handoff routing and task-scoped execution claims

**Files:**
- Modify: `gtasks/handoff_dispatcher.py:122-220,430-860,1180-1370`
- Modify: `gtasks/gbrain.py:876-1155`
- Test: `tests/test_handoff_dispatcher.py`
- Test: `tests/test_gbrain.py`

**Interfaces:**
- Consumes: active `AgentDelegationLease` reads from Task 4 and
  `delegated_work_is_eligible` from Task 3.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    task_slug: str
    executor_agent: str
    permanent_owner: str
    delegation_slug: str | None
    correlation_id: str
    idempotency_key: str
    claimed_at: datetime
    expires_at: datetime

HandoffStore.claim_execution(...) -> ExecutionClaim | None
HandoffStore.release_execution_claim(...) -> HandoffEvent
```

- [ ] **Step 1: Write failing routing, concurrency, and recovery tests**

Cover: owned OpenClaw work routes directly; delegated work routes only under an
active verified lease; owned OpenClaw ready work suppresses delegated claims;
already active Codex work is ineligible; two executors cannot claim one task;
restart replay uses the same claim; expiry stops new claims; a bounded in-flight
write can checkpoint; completion/revocation emits hand-back evidence; and an
identity mismatch dead-letters without wake.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_handoff_dispatcher.HandoffDispatcherTests`

Expected: FAIL because execution claims and delegation-aware routing are absent.

- [ ] **Step 3: Add SQLite claim schema and atomic routing**

Use a unique `task_slug` key for active claims and persist permanent owner,
executor, lease, correlation, idempotency, claim/expiry instants, and terminal
state. Perform lease/owner/priority verification inside the same transaction
that creates the claim. Append audit events after the durable row exists.

- [ ] **Step 4: Run dispatcher, adapter, and restart tests**

Run:

```bash
python3 -m unittest tests.test_handoff_dispatcher tests.test_gbrain.HandoffDispatcherRegistrationReadbackTests
```

Expected: PASS with no duplicate wake and deterministic restart recovery.

- [ ] **Step 5: Commit lease-aware routing**

```bash
git add gtasks/handoff_dispatcher.py gtasks/gbrain.py tests/test_handoff_dispatcher.py tests/test_gbrain.py
git commit -m "feat: route delegated handoffs with execution claims"
```

---

### Task 6: Implement the fixed-session OpenClaw adapter

**Files:**
- Create: `gtasks/openclaw_adapter.py`
- Create: `tests/test_openclaw_adapter.py`
- Modify: `gtasks/local_handoff_dispatcher.py:130-230,500-680`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True, slots=True)
class OpenClawExecutionResult:
    status: str
    assistant_text: str
    session_key: str

class OpenClawSessionAdapter:
    def __init__(self, *, executable: str, session_key: str, timeout_seconds: int, run=subprocess.run): ...
    def execute(self, prompt: str) -> OpenClawExecutionResult: ...

def parse_openclaw_output(stdout: str, *, expected_session_key: str) -> OpenClawExecutionResult: ...
```

- [ ] **Step 1: Write failing command and parser tests**

Assert exact argument arrays, no shell, fixed session key, JSON preceded by
warnings, `finalAssistantVisibleText`, `finalAssistantRawText`, and
`payloads[].text` extraction, wrong/missing session failure, malformed output,
timeout, nonzero exit, and bounded output size. Assert no code path invokes a
session-create command.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_openclaw_adapter`

Expected: import failure because `gtasks.openclaw_adapter` does not exist.

- [ ] **Step 3: Implement the adapter and inject runtime selection**

Build the installed-version-verified command as an argument array equivalent
to:

```python
[
    executable, "agent", "--local", "--json",
    "--timeout", str(timeout_seconds),
    "--session-key", session_key,
    "--message", prompt,
]
```

Do not log the session key or full prompt. Return only bounded privacy-safe
status text to audit callers. Keep `CodexResumeAdapter` unchanged and inject the
selected adapter into the single-worker run loop.

- [ ] **Step 4: Run adapter and local Dispatcher tests**

Run: `python3 -m unittest tests.test_openclaw_adapter tests.test_local_handoff_dispatcher`

Expected: PASS for both Codex and OpenClaw runtime adapters.

- [ ] **Step 5: Commit fixed-session execution**

```bash
git add gtasks/openclaw_adapter.py gtasks/local_handoff_dispatcher.py tests/test_openclaw_adapter.py tests/test_local_handoff_dispatcher.py
git commit -m "feat: execute handoffs in fixed OpenClaw sessions"
```

---

### Task 7: Install one host supervisor with two isolated identity workers

**Files:**
- Create: `gtasks/local_handoff_supervisor.py`
- Create: `config/openclaw-agents/dispatcher-supervisor.plist.template`
- Create: `scripts/install_local_handoff_supervisor.py`
- Create: `tests/test_local_handoff_supervisor.py`
- Modify: `scripts/install_local_handoff_dispatcher.py:20-220`
- Modify: `scripts/provision_handoff_dispatcher_credentials.py:15-100`
- Test: `tests/test_local_handoff_dispatcher.py`

**Interfaces:**
- Consumes: two private single-worker config paths and the existing worker run loop.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    schema_version: int
    worker_config_paths: tuple[Path, Path]

def load_isolated_workers(config: SupervisorConfig) -> tuple[DispatcherConfig, DispatcherConfig]: ...
```

- [ ] **Step 1: Write failing isolation and install tests**

Assert exactly two configs per host, distinct Agent slugs, one `codex` and one
`openclaw` runtime, same approved host route, distinct registrations/tokens,
separate private claim stores, no secret in plist/log/receipt, canonical install
paths, deterministic hashes, and refusal of cross-host or duplicate identities.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_local_handoff_supervisor tests.test_local_handoff_dispatcher`

Expected: FAIL because the supervisor and installer do not exist.

- [ ] **Step 3: Implement supervisor, installer, and six-identity central hashing**

The supervisor starts two worker loops and stops both on termination. One worker
failure is reported independently and does not pass its config to the sibling.
Change central provisioning from exactly three to exactly six reviewed
identities and require unique Agent slug, registration hash, and token hash.
Keep the existing one-worker installer usable until each host's supervisor
canary passes.

- [ ] **Step 4: Run install/provision tests and dry-run receipts**

Run:

```bash
python3 -m unittest tests.test_local_handoff_supervisor tests.test_local_handoff_dispatcher
python3 -m unittest tests.test_handoff_dispatcher.HandoffCredentialTests
```

Expected: PASS; receipts contain paths, hashes, runtime versions, and identities
but no raw credential or session value.

- [ ] **Step 5: Commit host supervision and provisioning**

```bash
git add gtasks/local_handoff_supervisor.py config/openclaw-agents/dispatcher-supervisor.plist.template scripts/install_local_handoff_supervisor.py scripts/install_local_handoff_dispatcher.py scripts/provision_handoff_dispatcher_credentials.py tests/test_local_handoff_supervisor.py tests/test_local_handoff_dispatcher.py tests/test_handoff_dispatcher.py
git commit -m "feat: supervise paired Codex and OpenClaw workers"
```

---

### Task 8: Add six-Agent UI and temporary delegation controls

**Files:**
- Modify: `gtasks/server.py:780-900,1210-1450,2200-2350`
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Test: `tests/test_server.py`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: six `AgentProfile` projections, delegation APIs, execution claims,
  and existing Handoff History.
- Produces: separate owned/delegated counts, runtime/session health, lease state,
  confirmation-bound create/extend/end/revoke actions, and permanent owner plus
  temporary executor in Task details.

- [ ] **Step 1: Write failing server and frontend contract tests**

Assert six cards, `OpenClaw` runtime label, `No goals assigned yet`, separate
`Owned work` and `Additional delegated work`, fixed pairing, shortcut/custom
end time, timezone, unchanged-owner copy, Extend/End Early, status countdown,
Task owner/executor separation, no raw session/registration values, keyboard
focus restoration, accessible errors, stale-read preservation, and mobile sheet
containment. Assert no delegation control submits without explicit confirmation.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
python3 -m unittest tests.test_server.AgentApiTests tests.test_server.AgentDelegationApiTests
python3 -m unittest tests.test_frontend_contract.FrontendContractTests
```

Expected: FAIL because runtime/delegation presentation and controls are absent.

- [ ] **Step 3: Implement projections and progressive-disclosure UI**

Use the exact user-facing labels:

- `OpenClaw`
- `No goals assigned yet`
- `Owned work`
- `Additional delegated work`
- `Temporarily delegate work`
- `Permanent owner`
- `Temporary executor`
- `End Early`
- `Extend`

Render lease remaining time from server instants, but re-read canonical state
immediately before every mutation. Never treat the client countdown as
authority. Preserve verified cards and form inputs on transient refresh errors.

- [ ] **Step 4: Run frontend/server tests and syntax checks**

Run:

```bash
python3 -m unittest tests.test_server.AgentApiTests tests.test_server.AgentDelegationApiTests tests.test_frontend_contract.FrontendContractTests
node --check static/app.js
git diff --check
```

Expected: PASS with accessible desktop/mobile contracts and no mutation from
read-only card rendering.

- [ ] **Step 5: Commit the delegation UI**

```bash
git add gtasks/server.py static/index.html static/app.js static/styles.css tests/test_server.py tests/test_frontend_contract.py
git commit -m "feat: manage OpenClaw delegation in Agents"
```

---

### Task 9: Enforce Artifact identity and delegated execution provenance

**Files:**
- Modify: `gtasks/domain.py:634-900`
- Modify: `gtasks/gbrain.py:2070-2630`
- Modify: `gtasks/server.py:2200-2350`
- Test: `tests/test_artifact_publisher.py`
- Test: `tests/test_gbrain.py:1651-2070`

**Interfaces:**
- Consumes: six-entry `ARTIFACT_BY_AGENT`, active execution claim, and authenticated publisher identity.
- Produces: exact `created_by` OpenClaw provenance plus optional privacy-safe `delegation_ref` on delegated Artifacts.

- [ ] **Step 1: Write failing identity and delegation provenance tests**

Assert Tammy-OC publishes only to `collections/tammy-oc-artifacts`, cannot use
Tammy's collection or identity, must reference the claimed source task, and
records the delegation reference without exposing credentials. Assert normal
OpenClaw-owned Artifacts omit delegation provenance.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_artifact_publisher tests.test_gbrain.AgentArtifactAdapterTests`

Expected: FAIL because OpenClaw Artifact scopes and delegation provenance are not enforced end to end.

- [ ] **Step 3: Implement exact publisher/claim verification**

Resolve executing identity only from authenticated publisher credentials. When
`delegation_ref` is present, verify the active/just-completed claim matches
Artifact `produced_for`, executor, and permanent owner before canonical write.
Do not infer delegation from names or collection membership.

- [ ] **Step 4: Run Artifact and GBrain tests**

Run: `python3 -m unittest tests.test_artifact_publisher tests.test_gbrain.AgentArtifactAdapterTests`

Expected: PASS with cross-identity attempts rejected before any GBrain write.

- [ ] **Step 5: Commit provenance enforcement**

```bash
git add gtasks/domain.py gtasks/gbrain.py gtasks/server.py tests/test_artifact_publisher.py tests/test_gbrain.py
git commit -m "feat: preserve OpenClaw artifact provenance"
```

---

### Task 10: Document operations, run full gates, and perform sequential canaries

**Files:**
- Create: `docs/runbooks/openclaw-agent-delegation.md`
- Modify: `README.md`
- Modify: `docs/runbooks/agent-handoff-dispatcher.md`
- Modify: `docs/runbooks/mission-control-system-documentation.md`
- Modify: `gtasks/releases.json`
- Modify: `tests/test_releases.py`
- Create after QA: the exact versioned release-evidence file reported by
  `python3 -m gtasks.release`, under `docs/release-evidence/`.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: reviewed source/runtime contract, independent QA evidence, one patch release, and sequential Tammy-OC/Timmy-OC/Toddy-OC activation receipts.

- [ ] **Step 1: Write failing documentation and release contract tests**

Assert README/runbooks name all six identities, fixed sessions, two-worker
supervisor isolation, no-default-Goal rule, delegation priority, authority,
duration, expiry/hand-back, private paths, dry-run, canary, rollback, and exact
health/readback commands. Add the next sequential patch release test only after
the implementation candidate is frozen.

- [ ] **Step 2: Run documentation/release tests and verify RED**

Run: `python3 -m unittest tests.test_releases`

Expected: FAIL because the new runbook and release entry are absent.

- [ ] **Step 3: Write runbooks and create the next patch release candidate**

Use `python3 -m gtasks.release --title "OpenClaw Agents and temporary delegation" --summary "Adds three independent fixed-session OpenClaw Agents and Tony-authorized time-bounded delegation that preserves permanent ownership and prioritizes each OpenClaw Agent's own work."`. Document exact rollback: disable only the affected OpenClaw worker, stop new delegated claims, checkpoint active claims, preserve canonical leases/events, and leave the Codex worker running.

- [ ] **Step 4: Run the complete automated gate**

Run:

```bash
python3 -m unittest discover -s tests
node --check static/app.js
python3 -m compileall -q gtasks tests scripts
git diff --check
```

Expected: all tests PASS; only explicitly documented platform skips are allowed.

- [ ] **Step 5: Run synthetic and Tammy-OC pre-commit canary gates**

First run synthetic no-wake/no-write tests using fake GBrain, fake OpenClaw,
fake clocks, and a temporary SQLite store. Then provision one isolated completed
QA fixture assigned to Tammy-OC only after Tony's live-canary authorization.
Verify one fixed-session acknowledgement, one status/checkpoint mutation, one
Artifact in Tammy-OC's collection, one Timeline chain, restart recovery, early
completion, and no effect on Tammy's Codex worker.

- [ ] **Step 6: Obtain independent UI/UX PASS before commit**

QA the frozen uncommitted candidate at 1440x1000 and genuine 390x844. Verify six
cards, no default Goals for OC identities, create/customize/extend/end delegation,
owned versus delegated counts, Task owner/executor, Timeline, Artifact
provenance, focus, overflow, GET/write audit, console, and network. A FAIL or
INCONCLUSIVE result blocks commit and requires repair plus fresh independent QA.

- [ ] **Step 7: Commit, push, and deploy the verified release**

```bash
python3 -c 'import json; print(json.load(open("gtasks/releases.json"))[0]["version"])'
git status --short docs/release-evidence
git add README.md docs/runbooks/openclaw-agent-delegation.md docs/runbooks/agent-handoff-dispatcher.md docs/runbooks/mission-control-system-documentation.md docs/release-evidence gtasks/releases.json tests/test_releases.py
git commit -m "docs: release OpenClaw agent delegation"
git push origin main
curl -fsS -X POST http://127.0.0.1:4188/api/services/gtasks/restart
curl -fsS http://127.0.0.1:4179/api/health
curl -fsS http://127.0.0.1:4179/api/releases
```

Expected: origin/main matches local main; dashboard-managed runtime reports the
new exact patch version and canonical store `gbrain`.

- [ ] **Step 8: Activate hosts sequentially with stop gates**

Install and verify Tammy-OC first. Require profile/collection/session/credential,
supervisor PID/arguments, fixed-session canary, claim, Timeline, Artifact,
restart, expiry, and rollback readbacks. Only after Tammy-OC PASS, repeat the
same gate for Timmy-OC; only after Timmy-OC PASS, repeat for Toddy-OC. Never
batch three host mutations or infer success from SSH exit status alone.

- [ ] **Step 9: Refresh shared Mission Control documentation**

Update the canonical System Overview through the Documentation Manager workflow
only after deployed behavior is verified. Read back the GBrain page and shared
documentation relationship for all six Agents; do not copy the Overview into
six identity pages.

- [ ] **Step 10: Record terminal evidence**

Record commit, push, version, health, full test count, independent QA report,
three host activation receipts, canonical Agent/collection/Goal-link readbacks,
and any valid zero delegated claims. Do not mark rollout complete while any
host, identity, session, or rollback proof remains unverified.
