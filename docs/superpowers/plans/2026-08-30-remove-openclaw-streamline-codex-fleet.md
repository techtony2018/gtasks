# Remove OpenClaw Agents and Streamline the Codex Fleet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all live OpenClaw Agent identities, roles, Goal ownership, delegation/runtime paths, and creation controls from Mission Control while retaining historical audit evidence, then reduce the three-machine Codex fleet to one fixed Codex worker and one continuity contract per machine.

**Architecture:** The supported roster becomes exactly `agents/tammy`, `agents/timmy`, and `agents/toddy`, each bound to its existing task and Artifact collections. Generic canonical task handoffs remain because they provide Codex continuity, but the temporary Codex-to-OpenClaw delegation lease, OpenClaw profile activation, paired supervisor, and OpenClaw execution adapter are removed. Historical completed/cancelled Tasks and release evidence remain readable; live OpenClaw Agent/collection pages are soft-deleted only after canonical backlinks prove there is no active work or Goal ownership.

**Tech Stack:** Python 3.12, `unittest`, vanilla JavaScript/CSS, GBrain remote MCP, SQLite handoff dispatcher, macOS LaunchAgents, All Things Codex Dashboard.

## Global Constraints

- Use authenticated GBrain remote MCP only; do not replace it with local Stargraph reads or raw datastore writes.
- Preserve completed/cancelled task and Artifact history; never delete Tony business tasks, comments, TODOs, or timelines.
- Keep generic Codex task handoff history and fixed-thread continuity; remove only OpenClaw-specific delegation and runtime logic.
- Timmy and Toddy remain on their own remote machines; do not run either worker locally.
- Before any UI-affecting commit, require independent QA PASS at `1440x1000` and genuine `390x844`.
- Preserve unrelated `.gitignore` and artifact-directory changes.

---

### Task 1: Lock the supported three-Agent domain and API contract

**Files:**
- Modify: `gtasks/domain.py`
- Modify: `gtasks/gbrain.py`
- Modify: `gtasks/server.py`
- Modify: `tests/test_domain.py`
- Modify: `tests/test_gbrain.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Produces: `AGENT_SCOPES`, `ARTIFACT_AGENT_SCOPES`, and `AGENT_RUNTIME_BY_SLUG` containing exactly Tammy, Timmy, and Toddy with runtime `codex`.
- Produces: `/api/agents` returning exactly three supported Agents and `/api/health.agent_work_roots` returning their three task roots.
- Consumes: existing `AgentProfile`, `Task`, Artifact publication, and generic handoff APIs.

- [ ] **Step 1: Write failing domain/API tests**

  Add assertions that the supported scope constants contain exactly the three Codex identities, OpenClaw slugs are rejected as new task owners/Artifact publishers, and `/api/agents` never returns runtime `openclaw`.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run: `python3 -m unittest tests.test_domain tests.test_gbrain tests.test_server`

  Expected: FAIL because the current constants and agent projection still contain `agents/*-oc`.

- [ ] **Step 3: Remove OpenClaw scopes and activation branches**

  Delete OpenClaw entries from domain constants, make approved runtime exactly `codex`, remove active-profile merging/activation validation from normal agent/task/Artifact paths, and make handoff credentials accept exactly the three Codex identities.

- [ ] **Step 4: Run focused tests and verify GREEN**

  Run: `python3 -m unittest tests.test_domain tests.test_gbrain tests.test_server`

  Expected: all selected tests pass with no supported OpenClaw identity.

### Task 2: Remove OpenClaw delegation and paired runtime components

**Files:**
- Delete: `gtasks/delegation.py`
- Delete: `gtasks/openclaw_adapter.py`
- Delete: `gtasks/local_handoff_supervisor.py`
- Delete: `scripts/install_local_handoff_supervisor.py`
- Delete: `scripts/provision_openclaw_agent_profiles.py`
- Delete: `config/openclaw-agents/agents.json`
- Delete: `config/openclaw-agents/heartbeats.json`
- Delete: `config/openclaw-agents/HEARTBEAT.md`
- Delete: `config/openclaw-agents/dispatcher-supervisor.plist.template`
- Modify: `gtasks/server.py`
- Modify: `gtasks/gbrain.py`
- Modify: `scripts/provision_handoff_dispatcher_credentials.py`
- Modify: `scripts/automation/start_gtasks_dashboard.zsh`
- Modify: `tests/test_handoff_dispatcher.py`
- Modify: `tests/test_handoff_dispatcher_credentials.py`
- Delete: OpenClaw-only test modules and cases whose production interfaces no longer exist.

**Interfaces:**
- Produces: no `/api/agent-delegations` GET/POST/PATCH route and no OpenClaw profile activation dependency.
- Preserves: `/api/handoffs/*`, `LocalDispatcherClient`, `CodexResumeAdapter`, durable claim store, acknowledgement helper, and generic handoff event history.

- [ ] **Step 1: Write failing removal-boundary tests**

  Assert `/api/agent-delegations` is `404`, dashboard startup no longer requires `MEMORY_STARGRAPH_OC_PROVISION_TOKEN`, credential provisioning emits three identities only, and singleton Codex routes remain valid.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run: `python3 -m unittest tests.test_server tests.test_handoff_dispatcher tests.test_handoff_dispatcher_credentials`

  Expected: FAIL while OpenClaw endpoints and six-identity credentials remain supported.

- [ ] **Step 3: Remove the runtime and route implementation**

  Remove delegation imports, locks, payload helpers, API handlers, profile activation client code, paired-supervisor code, provisioning scripts/config, and the OpenClaw startup token requirement. Keep generic task handoff dispatch intact.

- [ ] **Step 4: Run focused tests and verify GREEN**

  Run: `python3 -m unittest tests.test_server tests.test_handoff_dispatcher tests.test_handoff_dispatcher_credentials tests.test_local_handoff_dispatcher`

  Expected: all retained Codex handoff tests pass.

### Task 3: Simplify the UI and task-creation surface to Codex-only

**Files:**
- Modify: `static/app.js`
- Modify: `skills/mc-add-task/SKILL.md`
- Modify: `skills/mc-add-task/scripts/mc_add_task.py`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_mc_add_task_skill.py`

**Interfaces:**
- Produces: Agent UI with exactly Tammy, Timmy, and Toddy; no OpenClaw pairing, delegation controls, health copy, or owner aliases.
- Preserves: Task details, generic handoff status/history, Agent Goal execution, and exact Codex owner selection.

- [ ] **Step 1: Write failing UI/skill contract tests**

  Assert the shipped JavaScript and task skill contain no `OPENCLAW_PAIR_BY_SOURCE`, `runtime === "openclaw"`, `tammy-oc`, `timmy-oc`, or `toddy-oc`, while all three Codex aliases remain accepted.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run: `python3 -m unittest tests.test_frontend_contract tests.test_mc_add_task_skill`

  Expected: FAIL because current UI and skill still expose OpenClaw branches and aliases.

- [ ] **Step 3: Remove OpenClaw-only UI and creation behavior**

  Delete pair maps, delegation panels/actions, OpenClaw runtime labels, and OpenClaw owner aliases. Render the generic Codex Agent/handoff surfaces directly.

- [ ] **Step 4: Run focused tests and verify GREEN**

  Run: `python3 -m unittest tests.test_frontend_contract tests.test_mc_add_task_skill`

  Expected: all selected tests pass.

### Task 4: Codify one-worker-per-machine continuity

**Files:**
- Modify: `config/handoff-dispatcher/remote-workers.json`
- Modify: `config/handoff-dispatcher/agent.plist.template`
- Modify: `scripts/install_local_handoff_dispatcher.py`
- Modify: `scripts/verify_handoff_worker_runtime.py`
- Modify: `scripts/verify_handoff_worker_fleet.py`
- Create: `config/handoff-dispatcher/local-worker.json.example`
- Modify: relevant installer and fleet verification tests.

**Interfaces:**
- Produces: inventory entries for Tammy local, Timmy remote, and Toddy remote, each with one agent slug, one host route, one fixed thread id supplied through a private runtime config, and one LaunchAgent label.
- Preserves: private bearer tokens, fixed-thread resume, durable claim/checkpoint, acknowledgement, retry, and stale-lease recovery.

- [ ] **Step 1: Write failing fleet-contract tests**

  Assert the inventory has exactly three unique Agent/host entries, no supervisor or paired worker fields, and the verifier rejects duplicate routes, duplicate Agents, or a Timmy/Toddy config on the local host.

- [ ] **Step 2: Run focused tests and verify RED**

  Run: `python3 -m unittest tests.test_local_handoff_dispatcher tests.test_handoff_dispatcher`

  Expected: FAIL until the three-host singleton contract is represented and validated.

- [ ] **Step 3: Implement the singleton fleet contract**

  Reuse `gtasks.local_handoff_dispatcher` directly on each machine, make the example config secret-free, and ensure verifier output includes exact agent, route, fixed thread, repository commit, LaunchAgent state, and Mission Control registration readback.

- [ ] **Step 4: Run local and read-only remote verification**

  Run: `python3 scripts/verify_handoff_worker_fleet.py --inventory config/handoff-dispatcher/remote-workers.json`

  Expected: exact per-host PASS/BLOCKED receipts without installing Timmy or Toddy locally or mutating remote hosts before the reviewed release is deployed.

### Task 5: Retire canonical OpenClaw nodes without erasing history

**Files:**
- Create: `scripts/retire_openclaw_agent_graph.py`
- Create: `tests/test_retire_openclaw_agent_graph.py`
- Create: `artifacts/runtime/openclaw-retirement-<timestamp>.json` at execution time (ignored runtime receipt).

**Interfaces:**
- Consumes: authenticated remote MCP `get_page`, `get_links`, `get_backlinks`, `remove_link`, and `delete_page`.
- Produces: idempotent preflight/execute/readback receipts for the three Agent pages and six Agent collection pages.

- [ ] **Step 1: Write failing migration tests**

  Cover no-op reruns, refusal on active/planned/blocked/proposed assigned work, refusal on Goal ownership/default Goal links, preservation of completed/cancelled history, exact link removal, partial-failure reporting, and soft-delete readback.

- [ ] **Step 2: Run the migration tests and verify RED**

  Run: `python3 -m unittest tests.test_retire_openclaw_agent_graph`

  Expected: FAIL because the retirement script does not exist.

- [ ] **Step 3: Implement dry-run-first retirement**

  Read every page/backlink first; stop if live work or Goal ownership exists. Remove only live collection ownership edges, soft-delete the nine retired identity/scope pages, and verify each page with `include_deleted=true` plus surviving historical task/Artifact readback.

- [ ] **Step 4: Execute through remote MCP and verify canonical state**

  Run dry-run first, inspect its exact slug list, then run execute once. Verify `/api/agents?refresh=1` returns exactly three Codex Agents and direct page/backlink reads show the retired nodes deleted with no active owners.

### Task 6: Release, independent QA, deploy, and documentation cleanup

**Files:**
- Modify: `gtasks/releases.json`
- Modify: `README.md`
- Rewrite: `docs/runbooks/agent-handoff-dispatcher.md`
- Modify: `docs/runbooks/mission-control-system-documentation.md`
- Delete: `docs/runbooks/openclaw-agent-delegation.md`
- Preserve: historical `docs/release-evidence/**`, `docs/handoffs/**`, and prior plans/specs as explicitly historical evidence.

**Interfaces:**
- Produces: one release describing the Codex-only roster, retired OpenClaw runtime, and three-host singleton continuity model.

- [ ] **Step 1: Bump the Mission Control version and run the full suite**

  Run: `python3 -m unittest discover -s tests`

  Expected: all tests pass with zero OpenClaw runtime modules or supported identities.

- [ ] **Step 2: Hand the uncommitted UI candidate to independent QA**

  Require explicit PASS at desktop `1440x1000` and genuine mobile `390x844`; verify Agent roster, task creation, task handoff/history, version string, containment, accessibility, console/network cleanliness, and absence of OpenClaw controls.

- [ ] **Step 3: Commit, push, and deploy through All Things Codex Dashboard**

  Commit only the scoped files after QA PASS, push `main`, restart service `gtasks` through Dashboard, and verify `/api/health`, `/api/agents?refresh=1`, `/api/agent-work?refresh=1`, and `/api/system-tickets?include_completed=0&refresh=1`.

- [ ] **Step 4: Clean current documentation and verify references**

  Replace current six-Agent/paired-supervisor instructions with the three-Codex singleton model, delete the live OpenClaw runbook, and retain older plans/release evidence only with historical context. Run `rg -n -i "openclaw|tammy-oc|timmy-oc|toddy-oc" README.md docs/runbooks skills config gtasks static scripts` and classify every remaining match as historical migration evidence or remove it.

- [ ] **Step 5: Complete the requirement-by-requirement audit**

  Verify exactly three live Agent pages, no live OpenClaw roles/default Goals/assigned active work, no OpenClaw runtime/service requirement, one Codex worker per machine, fixed-thread continuity receipts, clean docs, clean scoped Git status, pushed commits, and Dashboard runtime version readback.
