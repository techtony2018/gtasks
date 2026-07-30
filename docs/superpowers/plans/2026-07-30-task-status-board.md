# Task Status Editor and Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe canonical task-status editing and a status-based Board without displacing Today.

**Architecture:** `GBrainAdapter.set_task_status()` owns page and lifecycle readback. The HTTP layer validates PATCH requests, while the existing snapshot refresh drives Today, lists, goals, and Board from GBrain.

**Tech Stack:** Python 3.12 standard library, vanilla JavaScript, HTML, and CSS.

## Global Constraints

- GBrain remains the only task state store.
- Completion keeps active-root membership until the collection's next-Monday archive rule.
- Reopening an archived task restores active-root membership without copying the task.
- Board is read-only navigation; status persistence stays in task detail.
- Do not add drag-and-drop.

---

### Task 1: Status mutation contract

**Files:**
- Modify: `gtasks/gbrain.py`
- Modify: `tests/test_gbrain.py`

- [ ] Add failing tests for completion, archived-task reopening, and readback mismatch.
- [ ] Add a verified status mutation receipt and preserve all existing frontmatter/body data.
- [ ] Run adapter tests.

### Task 2: Status API

**Files:**
- Modify: `gtasks/server.py`
- Modify: `tests/test_server.py`

- [ ] Add failing tests for supported and invalid status PATCH requests.
- [ ] Route `/api/tasks/<slug>/status` to the adapter with Tony's local clock.
- [ ] Return clear validation, partial-write, and GBrain availability errors.

### Task 3: Detail editor and Board

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Create: `tests/test_frontend_contract.py`

- [ ] Add a failing static contract test for Board and the six status values.
- [ ] Add Board navigation, four status columns, and navigable cards.
- [ ] Add the explicit status select and Save interaction to task detail.
- [ ] Refresh the canonical snapshot and reopen task detail after verified save.

### Task 4: Managed verification

- [ ] Restart GTasks through All Things Codex Dashboard.
- [ ] Run the full Python and JavaScript checks.
- [ ] Verify Board and status detail at desktop and mobile widths without saving a real status.
- [ ] Confirm no browser errors and leave managed GTasks healthy.
