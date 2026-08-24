# Goal execution overhaul pause handoff — 2026-08-24

Status: paused by Tony; resume next week.

Branch: `main`

Last committed HEAD: `066bca0 Populate waiting Goal task context`

Runtime at pause: dashboard-managed Mission Control `V0.0.169` on `http://127.0.0.1:4179`.

## Current objective

Tony's standing objective is to overhaul Mission Control Agent collaboration so Codex Agents make concrete progress on Goals and Projects with fewer unclear `waiting`, `pending`, or `blocked` outcomes. The current implementation path is the Goal execution / Agent handoff flow.

## Completed in the latest slice

- Shipped `V0.0.169`.
- Fixed `waiting_for_tony` selected Goal execution Tasks so the public headline carries canonical task title, status, and Agent owner.
- Fixed fresh Agents cold-load rendering so a populated `/api/goal-execution.last_run.task` renders the exact Task title/link before the separate `/api/agent-work` cache reconciles.
- Preserved the local-vs-remote worker boundary: local supervisor is for `agents/tammy` and `agents/tammy-oc` only. Timmy/Toddy must stay on remote machines.

## Evidence

- Commit: `066bca0 Populate waiting Goal task context`
- Push: `origin/main` updated through `066bca0`
- Health/readback:
  - `/api/health` reports `V0.0.169`
  - `/api/goal-execution` reports `public_reason=waiting_for_tony`
  - public task: `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`
  - title: `Prepare family-care goal map and weekly review brief`
  - status: `blocked`
  - agent_slug: `agents/toddy`
- Tests:
  - focused: `128 OK`
  - full: `python3 -m unittest discover -s tests` -> `1372 OK (skipped=5)`
  - `node --check static/app.js`
- Independent QA:
  - PASS: `/Users/tony/work/gtasks/artifacts/qa/v0.0.169-independent/gate-report.md`
  - results: `/Users/tony/work/gtasks/artifacts/qa/v0.0.169-independent/gate-results.json`
  - aggregate: `a984ee7d67efa0a62d16a078f50058807e3551e1921b2afcf484fd7b89074b59`
  - desktop `1440x1000` and genuine mobile `390x844`

## Live state at pause

`/api/goal-execution?refresh=1` currently returns 13 decisions:

- Public headline: `waiting_for_tony` for `goals/2c86f86c-c9fb-5f49-96d0-e4d63f489fc8`, task `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, Agent `agents/toddy`.
- Recently completed decisions remain in run history for Civic, Faith, Finance, and Career.
- `handoff_needs_repair`: Toddy Health Goal `goals/d175890b-6e89-5543-b587-b5df345c1c81`, task `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`.
- `owner_missing`: Entrepreneurship Goal `goals/d837ac94-36f5-4735-93bb-d84c69b45435`.
- Several legacy aliases are correctly suppressed.

`/api/agents?refresh=1` returns 6 profiles with `issues=[]`:

- `agents/tammy`, `agents/toddy`, `agents/timmy`
- `agents/tammy-oc`, `agents/timmy-oc`, `agents/toddy-oc`

`/api/agent-work?refresh=1` returns 44 items:

- `agents/tammy`: 33
- `agents/timmy`: 8
- `agents/toddy`: 3
- One hidden issue remains: `tasks/78147b5d-7385-431e-ae1a-cf710a160910`, message `waiting_for_input requires blocked task status`, hidden from Board and reported in Inbox.

## Worker boundary

Do not run Timmy or Toddy locally.

Known state from recent fleet work:

- Local supervisor config should contain only `agents/tammy` and `agents/tammy-oc`.
- Timmy remote was reachable and fast-forwarded through `6984f24c1fe330aca68fd95adc0a80dbcc9b4428`.
- Toddy remote was blocked by Tailscale/SSH reachability:
  - issue: `tailscale_key_expired` / `ssh_unreachable`
  - peer: `Toddy's Mac Mini-1`
  - DNS: `toddys-mac-mini-1.taildb46a7.ts.net.`
  - IP: `100.117.212.20`
- Do not place Toddy on the reachable Timmy host without an explicit host-placement decision.

## Dirty worktree note

At pause, product source from this slice is committed and clean. Existing unrelated local changes remain and must be preserved:

- `.gitignore`
- `README.md`
- `config/agent-artifact-protocol/shared-documentation.json`
- `docs/runbooks/agent-handoff-dispatcher.md`
- `docs/runbooks/mission-control-system-documentation.md`
- untracked historical QA artifacts and docs, including `docs/release-evidence/v0.0.169.md`

Do not stage or revert these unless their owner explicitly authorizes it.

## Suggested resume order

1. Refresh `git status`, `/api/health`, `/api/goal-execution?refresh=1`, `/api/agents?refresh=1`, and `/api/agent-work?refresh=1`.
2. Verify local supervisor still excludes Timmy/Toddy.
3. Decide next high-impact blocker from live state:
   - if Tony has answered `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, resume that same Toddy task/handoff;
   - otherwise, repair the `handoff_needs_repair` Toddy Health task or the `owner_missing` Entrepreneurship Goal, whichever is executable without local Timmy/Toddy placement.
4. Use TDD for any behavior change.
5. Run focused and full tests.
6. For UI-visible changes, obtain independent desktop `1440x1000` and genuine mobile `390x844` QA PASS before commit.
7. Commit, push, restart dashboard-managed Mission Control, and verify postdeploy readback.
8. Notify Documentation Manager after shipped changes.
