# All Things Codex Dashboard Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing GTasks process fully manageable from All Things Codex Dashboard.

**Architecture:** Add a self-describing management contract to GTasks and one allowlisted service entry to the tracked and installed dashboard catalogs. The entry runs the existing GTasks checkout directly, so there is no vendored application or task datastore.

**Tech Stack:** Python 3.12 standard library, JSON configuration, All Things Codex Dashboard service API, macOS LaunchAgent.

## Global Constraints

- GBrain remains the only canonical task and goal store.
- Bind GTasks only to `127.0.0.1:4179`.
- Do not run the dashboard's full installer because the installed runtime has newer unrelated service state.
- Preserve all existing GTasks V1 behavior.

---

### Task 1: GTasks management contract

**Files:**
- Create: `dashboard-integration.json`
- Create: `tests/test_dashboard_integration.py`
- Modify: `README.md`

**Interfaces:**
- Produces: a JSON contract with service id `gtasks`, health path `/api/health`, and the existing module command.

- [ ] Write a test that requires the exact service URL, health path, working directory, command, and canonical-store declaration.
- [ ] Run the test and confirm it fails because the contract is absent.
- [ ] Add the minimal JSON contract and management documentation.
- [ ] Run the GTasks suite and syntax checks.

### Task 2: Dashboard allowlist registration

**Files:**
- Modify: `/Users/tony/Documents/All Things Codex Dashboard/server.py`
- Create: `/Users/tony/Documents/All Things Codex Dashboard/tests/test_gtasks_service.py`
- Modify: `/Users/tony/Documents/All Things Codex Dashboard/README.md`

**Interfaces:**
- Consumes: `SERVICE_CATALOG`, `service_public_view()`, and the existing process controls.
- Produces: a `gtasks` catalog entry with start, stop, restart, health, and open capabilities.

- [ ] Write a test that requires the exact allowlisted GTasks entry and enabled controls.
- [ ] Run the test and confirm it fails because `gtasks` is not registered.
- [ ] Add the minimal catalog block and README entry.
- [ ] Run the dashboard test and Python compilation.

### Task 3: Managed runtime deployment and proof

**Files:**
- Modify: `/Users/tony/.codex/services/all-things-codex-dashboard/server.py` with only the tested catalog block.

**Interfaces:**
- Consumes: dashboard API endpoints `POST /api/services/gtasks/{start|stop|restart}`.
- Produces: live service card and managed GTasks process at port 4179.

- [ ] Patch only the GTasks catalog block into the installed runtime.
- [ ] Restart the dashboard LaunchAgent and confirm `/api/summary` exposes the card.
- [ ] Exercise Start, Stop, Start through the dashboard API and verify each health transition.
- [ ] Verify both dashboards in a browser with no console errors.
- [ ] Commit the scoped GTasks and dashboard source changes separately.
