# Mission Control Enjoyment and Reliability Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for sequential execution. Steps use checkboxes for tracking. Independent UI/UX QA is required before committing each UI-affecting candidate.

**Goal:** Deliver the nine recommendations from Tony's September 7 review in small, verified iterations: trustworthy reads and saves, bounded refresh and failure coverage, daily missions, focus mode, progress constellations, weekly mission log, and a useful agent crew panel.

**Architecture:** Keep GBrain remote MCP canonical. Extend the existing Python service and vanilla browser UI. Browser-only selections, focus timers, and recoverable drafts are preferences, never canonical task completion or agent execution authority. Fence stale reads before adding engagement surfaces that depend on them.

**Tech Stack:** Python stdlib HTTP service/unittest, vanilla JavaScript/CSS, existing GBrain remote MCP adapter, Dashboard-managed deployment.

## Global constraints

- Work only in the existing fixed Developer task and `/Users/tony/work/gtasks`; preserve unrelated changes, including `.gitignore` and existing artifacts.
- Use remote MCP; no replacement canonical task store, no production task/goal mutations in tests, no fake agent activity.
- Each iteration has a targeted red/green regression, affected integration tests, documentation and an explicit evidence receipt.
- UI-affecting candidates require independent PASS at desktop 1440x1000 and genuine mobile 390x844 before commit; FAIL/INCONCLUSIVE requires repair/retest.
- Keep existing keyboard/focus/accessibility behavior, reduced-motion/off controls, and verified-completion celebrations.
- Release via the existing sequential patch-version tool. Deploy only through All Things Codex Dashboard; verify live version and affected read paths afterward.
- Run the full suite only at meaningful integration milestones or when the change's breadth warrants it; do not repeat unrelated tests at every edit.
- Every iteration is a checkpoint, not a redefinition of the full goal. The goal stays active until every acceptance item below is verified.

## Iteration map and acceptance

| Iteration | Scope and acceptance | Files / verification |
|---|---|---|
| 1 | A refresh begun before invalidation must never publish old payloads/errors as current, clear a newer worker, or persist obsolete data. A replacement can converge immediately. | `gtasks/read_cache.py`, `tests/test_read_cache.py`; cache + TasksApiTests + SystemTicketApiTests + ProposalApiTests |
| 2 | Ordinary task creation uses a stable operation ID across lost responses/retries. Same ID/different request conflicts. Task editing checks canonical revision, preserves the draft and explains field conflicts. Remote atomic concurrency capability must be verified; client-only timestamps cannot be called atomic protection. | `gtasks/server.py`, `gtasks/gbrain.py`, `static/app.js`, task editor HTML, new operation-journal module if needed; creation/edit adapter+HTTP tests, two-editor/lost-response fixtures |
| 3 | Refresh has an overall deadline including queue wait; selected surface stays usable with honest last-verified age. Archived detail is lazy. No unbounded worker/retry growth or starvation. | read cache, GBrain transport, surface loaders, fetch helpers; deterministic slow-loader/queue/timeout/recovery tests |
| 4 | Separate fast liveness from readiness; readiness reports per-surface age and verified canonical-read evidence without private content. Reusable failure tests cover expired auth, restart during recovery, overlapping refresh/write, and dropped response after save. | cache diagnostics, health/readiness handler, new `tests/test_reliability_scenarios.py`; readiness and privacy contracts |
| 5 | Today offers one chosen main mission and up to two optional tasks; next action is prominent. Blocked/overdue lists remain accessible below. Selection does not reschedule, create or complete tasks. Missing/completed selections reconcile honestly. | new `static/mission.js` pure helpers if useful, `static/app.js`, `static/index.html`, `static/styles.css`; mission selection/render tests and independent responsive QA |
| 6 | Focus mode offers 25/50-minute sessions, checklist and recoverable local notes. Pause/resume/reload preserve accurate remaining time; timer expiry never completes a task. Close restores focus; reduced motion supported. | mission module, focus dialog, styles; fake-clock timer tests, storage-failure test, keyboard/mobile QA |
| 7 | Goal constellations visualize verified milestones; every lit milestone opens its real task. Unknown/stale data is labelled. Task completion counts do not claim actual goal attainment. | goal rendering/mission pure helpers/styles; deduplication, reopen, zero-link, stale and accessibility tests |
| 8 | Weekly mission log shows verified personal wins, agent outputs separately, and decisions/blockers. Date boundaries use the user's local week; duplicate/reopened tasks cannot inflate wins. | mission aggregation and weekly view; fixed-date/timezone/week-boundary fixtures, personal/agent separation and no-data QA |
| 9 | Existing Agents view presents actual registered machine, latest verified activity, artifact result and actionable blocker. Idle/stale/unknown stay distinct from active execution; no invented status/personality facts. | existing agent runtime API/rendering; host/authority/artifact fixtures and independent crew-view QA |

## Iteration 1 execution details

**Files:** Modify `gtasks/read_cache.py`; add deterministic regressions in `tests/test_read_cache.py`; update README and `docs/release-evidence/v0.0.230.md` after verification.

**Interface:** Preserve `ReadSurfaceCache.read`, `invalidate`, and `SurfaceRead`; invalidation revokes the in-flight generation for each named surface and releases its loading ownership. Existing `_refresh` generation checks must fence its result, failure and cleanup.

- [x] Add a held old-loader regression: seed a verified payload; start refresh; invalidate; synchronously publish the new value; release the old loader; assert subsequent value and persisted snapshot remain new/fresh.
- [x] Add the reverse ordering: old loader returns after invalidation but before replacement starts; its result must not publish; replacement must execute even with a long force cooldown.
- [x] Add stale-error/cleanup coverage while the replacement is held: obsolete failure cannot poison or clear the new worker.
- [x] Run `python3 -m unittest tests.test_read_cache` and observe the new race tests fail for the intended reason.
- [x] Implement the narrow fence inside `invalidate`:

```python
with self._condition:
    self._dirty.update(names)
    for name in set(names):
        self._generations[name] = self._generations.get(name, 0) + 1
        self._loading.pop(name, None)
    self._condition.notify_all()
```

- [x] Review publication-to-disk ordering as well as in-memory ordering; cover any same-surface persistence race exposed by the regression.
- [x] Run `python3 -m unittest tests.test_read_cache tests.test_server.TasksApiTests tests.test_server.SystemTicketApiTests tests.test_server.ProposalApiTests` (localhost tests require network permission).
- [ ] Obtain independent candidate review/QA as required, record outcome, and only then commit the exact tested candidate. **Sequencing deviation:** synthetic pre-commit PASS occurred, but the stricter README managed-service verification occurred after commit. This ordering cannot be retroactively satisfied.
- [x] Push/deploy through Dashboard and verify version/health and affected read-state convergence using read-only requests.

## Later iteration execution contracts

Before each later iteration, inspect its current implementation and append its exact failing test names and chosen interfaces here. This is a rolling implementation plan: the acceptance scope above is fixed, while adapter capabilities and the results of earlier iterations determine the smallest correct implementation.

- Iteration 2 tests: lost response after successful create returns the same slug on retry; concurrent retries create one object; changed request hash returns conflict; a newer canonical edit causes conflict with draft retained. Use the real operation/revision logic against an isolated fake canonical backend, never production.
- Iteration 3 tests: frozen queue plus slow per-call reads cannot exceed the whole-operation budget; deadline produces terminal recoverable state; later success restores freshness; archived task TODOs are not hydrated for the active summary.
- Iteration 4 tests: an alive service with expired auth reports live but not ready; readiness never contains tokens/task text; restarting recovery preserves operation identity and cannot replay a completed write.
- Iteration 5 tests: selecting a fourth task is rejected; main/optional selection survives reload and is day-scoped; completed/deleted/stale selections are handled; no selection event sends a canonical mutation.
- Iteration 6 tests: pause freezes time; resume and reload use stored timestamps; negative time clamps to zero; expiry emits no completion request; notes survive close/reopen; escape restores originating control.
- Iteration 7 tests: repeated slugs count once; reopening extinguishes completion; stale values never appear verified; actual goal metric remains independent of task counts.
- Iteration 8 tests: Sunday/Monday and DST boundaries use the intended local week; agent wins never add to personal counts; late/unknown completion timestamps remain explicit.
- Iteration 9 tests: received/queued is never executing; stale heartbeat is unknown/unavailable; actual host fields and produced-for artifacts control evidence links.

## Progress ledger

- Baseline: `eff5937`, live V0.0.229 confirmed during the September 7 review. Only unrelated `.gitignore` is tracked-dirty before implementation.
- Planning: full nine-iteration scope captured; execution begins with cache correctness.
- Iteration 1 implementation verified: four regression tests demonstrated the original failures; cache and affected HTTP suites passed 46 tests, release checks passed 94 tests. `git diff --check` passed.
- Independent pre-commit gate PASS: desktop 1440x1000 and genuine mobile 390x844; Today/Board/detail, refresh, H2 focus and keyboard Close restoration. Report: `/private/tmp/mc-iteration1-independent-qa/gate-report.md`. Frozen five-file manifest aggregate: `ba22e96697b5a53e2c24cc1294d00ff210cc6e182c2a641cbdeba0f82ef57c6d`. Synthetic timeline 503 and existing Escape behavior are documented scope caveats, not production readiness proof.
- Iteration 1 deployed: `5a34256627d4307dc532f7afe7e59aae0a2da583` on `main` and `origin/main`. Dashboard `POST /api/services/gtasks/restart` returned OK; health reports V0.0.230 and gbrain 0.46.28.0. Tasks, Proposals and open System Tickets subsequently converged to fresh/refreshing=false/stale=false/error=null/issues=0.
- Independent managed-service post-deploy PASS: `/private/tmp/mc-iteration1-independent-qa/managed-postdeploy-report.md`; 1440x1000 and 390x844, Today/detail/focus/Close/refresh, no browser errors or writes. This is NOT a retroactive managed-service pre-commit PASS. Future candidates must use the managed environment before commit. Unrelated `.gitignore` was preserved and excluded; deployed runtime files matched the tested commit.
- Iteration 2 initially started with a private retry identity journal and four unit tests; subsequent integration is detailed below and remains outside iteration 1. Authenticated remote MCP `tools/list` exposes unconditional `put_page` and no atomic revision/compare-and-swap write. Cross-machine atomic edit protection remains an upstream capability dependency; local stale-draft checks must not be described as atomic protection against other machines.

### Iteration 2 checkpoint (uncommitted, not deployed)

- Integrated opt-in `Idempotency-Key` support for `POST /api/tasks` in the working tree. Journal stores only request hash, UUID, creation timestamp and verified flag, not task text. Owner-only SQLite, transaction-serialized reservations; the reserved UUID explicitly controls the canonical slug because the legacy domain identity argument does not control `_opaque_slug`.
- Verified original responses persist a receipt flag before HTTP output. Same-key retries read the current canonical task rather than rewriting it, preserving later enrichment. Changed payloads conflict. Concurrent or ambiguous attempts return the original slug with no second write. An interrupted operation before its verified receipt remains explicitly unconfirmed; automatic partial-write recovery is not yet implemented.
- Red evidence: changed payload incorrectly returned201, lost-response retry returned409 instead of recovered200, ambiguous retry attempted the write again, and verified-record method was missing. After implementation, `python3 -m unittest tests.test_task_operations tests.test_server.TasksApiTests` passed22 tests in8.677s. No production write tests ran.
- Files: `gtasks/task_operations.py`, `tests/test_task_operations.py`, `gtasks/server.py`, `tests/test_server.py`. NOT in commit5a34256. The running process still holds the deployed iteration1 modules; do not restart until the next candidate is deliberately prepared.
- Next: complete full-creation/TODO/readback/restart failure coverage, decide controlled unconfirmed-operation recovery, integrate persistent browser request identity and draft-preserving conflict handling, add app-level revision guards without claiming remote atomicity. Then targeted tests, version/docs, freeze and managed independent desktop/mobile QA BEFORE commit.

## Completion audit

- [ ] All nine iteration acceptance rows verified against code and behavior.
- [ ] Targeted tests recorded for every iteration; integration suite passes at final milestone.
- [ ] Independent desktop and 390px mobile QA PASS covers all shipped UI changes.
- [ ] Runtime version and deployed affected paths verified through the managed service.
- [ ] README/release evidence and handoff ledger reflect actual results; notify the existing Documentation Manager of completed release work per Tony's standing request.
