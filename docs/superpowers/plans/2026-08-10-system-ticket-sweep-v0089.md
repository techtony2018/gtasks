# Mission Control System Ticket Sweep V0.0.89 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three canonical planned Mission Control System Tickets together: explicit review-Task Artifact references, newest-updated Artifact ordering, and status/newest-updated All Tasks rows.

**Architecture:** Keep an Artifact's single `produced_for` provenance unchanged and add an explicit `Task -> Artifact` `reviews_artifact` edge for review inclusion. The Artifact adapter validates that edge, unions it with direct `produced_for` results, deduplicates before deterministic pagination, and exposes canonical `updated_at`; the frontend renders the resulting ordering and adds a compact status badge only in All Tasks. Release work preserves the three immutable ticket slugs, updates Tammy's existing automation through the supported automation tool, performs the exact canonical migration, and records verified receipts after dashboard deployment.

**Tech Stack:** Python 3 dataclasses/unittest/HTTP server, GBrain typed relationships, vanilla JavaScript/CSS, All Things Codex Dashboard, Chrome CDP independent QA.

## Global Constraints

- Work only in `/Users/tony/.codex/worktrees/system-ticket-sweep-v0089/gtasks` on `codex/system-ticket-sweep-v0089`, based on `origin/main` commit `8cad40c67f03a35dfd692e7cd9bd07645cb49d29`.
- Preserve Artifact `artifacts/0a3901c1-5ef1-4399-9d5c-fd6ee27d28e2` `produced_for` provenance to `tasks/ffc3a1ff-7ab7-4869-aeeb-f1ae4d4527cd`; never duplicate the Artifact or infer references from Markdown/Stargraph URLs.
- The review relationship is the typed canonical edge `tasks/540d2d36-4ce4-47f2-a06f-bd6ba8ae2700 -> artifacts/0a3901c1-5ef1-4399-9d5c-fd6ee27d28e2` with type `reviews_artifact` and source `gtasks`.
- All ordering uses verified canonical `updated_at` newest-first; honest missing timestamps fall back to `created_at`, then stable title/slug ordering.
- Do not change Board, Today, Calendar, Inbox, Agent Work, Completed, or System Tickets ordering.
- Update automation id `tammy-value-discover` only with the supported Codex automation update tool; never edit `automation.toml`.
- Move each canonical System Ticket `planned -> active` only when implementation starts, and `active -> completed` only after implementation, independent QA, deployment, and canonical readback pass.
- Before any git commit, independent UI/UX QA must explicitly PASS at `1440x1000` and genuine `390x844`; FAIL or INCONCLUSIVE requires repair and retest.
- Preserve unrelated files and user changes; commit only release scope.

---

### Task 1: Canonical Artifact references and updated ordering

**Files:**
- Modify: `gtasks/domain.py`
- Modify: `gtasks/gbrain.py`
- Modify: `gtasks/server.py`
- Test: `tests/test_domain.py`
- Test: `tests/test_gbrain.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: canonical `Task -> Artifact` `reviews_artifact` edges and existing `Artifact -> Task` `produced_for` edges.
- Produces: `AgentArtifact.updated_at: datetime | None`, task-filtered `ArtifactRead` union/dedup semantics, and a supported idempotent review-reference mutation/readback contract.

- [ ] **Step 1: Write failing domain and adapter tests**

Add tests that construct Artifacts with distinct `created_at`/`updated_at`, assert `to_dict()["updated_at"]`, assert newest-updated ordering, and assert a task query returns the union of direct `produced_for` plus outgoing `reviews_artifact` references exactly once. Add malformed/missing reference coverage and prove a Markdown-only string is not included.

- [ ] **Step 2: Run RED tests**

Run:

```bash
python3 -m unittest \
  tests.test_domain.AgentArtifactTests \
  tests.test_gbrain.GBrainAgentArtifactTests \
  tests.test_server.AgentArtifactApiTests -v
```

Expected: failures because `updated_at`, `reviews_artifact` union, and the supported mutation route do not exist.

- [ ] **Step 3: Implement minimal canonical behavior**

Parse optional `updated_at` from canonical Artifact frontmatter/page and serialize it. In `list_agent_artifacts(task=...)`, read direct `produced_for` backlinks and exact outgoing `reviews_artifact` links from the requested Task, union Artifact slugs, intersect only with canonical Agent Artifact collection members, read/validate each Artifact through `get_agent_artifact`, deduplicate by slug, and sort by `(updated_at or created_at, created_at, slug)` descending before pagination. Surface invalid referenced targets as bounded `artifact_data` issues. Add a supported idempotent adapter/server mutation which writes the exact `reviews_artifact` edge with `link_source: gtasks`, then reads back Task, Artifact, reference edge, and unchanged `produced_for` edge.

- [ ] **Step 4: Run GREEN tests and affected regressions**

Run:

```bash
python3 -m unittest tests.test_domain tests.test_gbrain tests.test_server -v
```

Expected: PASS.

### Task 2: Artifacts and All Tasks frontend presentation

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `Artifact.updated_at`, `Task.updated_at`, task-filtered Artifact API results, and existing `taskRow` options.
- Produces: newest-updated Artifact cards/hierarchy leaves and All Tasks rows with compact visible canonical status.

- [ ] **Step 1: Write failing frontend contract tests**

Add assertions that Artifacts Recent and hierarchy leaf ordering use `updated_at || created_at` with deterministic slug fallback; `allTasksMatchingSearch()` sorts by `updated_at || created_at`, title, then slug; `renderAllTaskResults()` calls `taskRow(..., { displayRelevantDate: true, showStatus: true })`; and only `showStatus` adds a compact accessible lifecycle status field.

- [ ] **Step 2: Run RED tests**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTests -v
```

Expected: failures because the new comparator and `showStatus` row option are absent.

- [ ] **Step 3: Implement minimal frontend behavior**

Add one deterministic newest-updated comparator. Apply it to loaded Artifact results before rendering both modes and to All Tasks only. Extend `taskRow` with `showStatus = false`; when true, render a compact text status label from the canonical `task.status`, with a bounded unavailable fallback, without changing other views. Add mobile-safe wrapping/containment styles.

- [ ] **Step 4: Run GREEN tests and static checks**

Run:

```bash
python3 -m unittest tests.test_frontend_contract -v
node --check static/app.js
git diff --check
```

Expected: PASS.

### Task 3: Release V0.0.89, migrate canonical data, and update automation

**Files:**
- Modify: `gtasks/releases.json`
- Modify: `tests/test_releases.py`
- Modify: `tests/test_server.py`
- Modify: `README.md`
- Create: `docs/release-evidence/v0.0.89.md`

**Interfaces:**
- Consumes: reviewed uncommitted candidate from Tasks 1-2 and supported canonical mutation/automation contracts.
- Produces: deployed V0.0.89, exact review Task reference migration, verified Tammy automation contract, and completed canonical System Tickets with receipts.

- [ ] **Step 1: Add failing release tests**

Update release assertions to expect `V0.0.89` while the catalog is still `V0.0.88`, then run:

```bash
python3 -m unittest tests.test_releases -v
```

Expected: FAIL on current release/version.

- [ ] **Step 2: Add the sequential V0.0.89 release entry and documentation**

Record the exact three ticket slugs and behavior. Update README's latest verified release only after the candidate implementation is verified.

- [ ] **Step 3: Run focused and full verification**

Run:

```bash
python3 -m unittest tests.test_domain tests.test_gbrain tests.test_server tests.test_frontend_contract tests.test_releases -v
node --check static/app.js
python3 -m compileall -q gtasks tests
git diff --check
python3 -m unittest discover -s tests
```

Expected: PASS with zero failures.

- [ ] **Step 4: Independent pre-commit QA**

Freeze a tracked candidate aggregate and dispatch a fresh independent QA subagent. It must use the uncommitted worktree candidate, verify desktop `1440x1000` and genuine mobile `390x844`, test exact/equivalent safe review-Task Artifact inclusion and provenance, Artifacts newest-first ordering, All Tasks status/newest-first ordering, selection/links/focus/overflow, and record a report plus screenshots. Expected: explicit PASS and unchanged aggregate.

- [ ] **Step 5: Commit, push, deploy, and verify**

Commit only after independent PASS, fast-forward/push `origin/main`, restart the Dashboard-managed `gtasks` service with `POST http://127.0.0.1:4188/api/services/gtasks/restart`, then verify `/api/health`, `/api/releases`, process cwd/commit, and rendered deployed UI.

- [ ] **Step 6: Apply exact canonical migration and automation update**

Use the supported review-reference mutation to add/read back the exact review Task edge without changing `produced_for`. Use `codex_app__automation_update` to update `tammy-value-discover` so each verified Tony review Task gets the reference after Task creation and before success, preserving schedule/thread/Codex-only research/stable dedupe. Read back the automation.

- [ ] **Step 7: Complete the same three canonical tickets**

Append implementation, commit/push, deployment, migration/automation, and independent QA receipts through `GBrainAdapter.update_system_ticket`, mark each ticket completed, and read back exact page, typed `member_of`, API projection, and zero issues.
