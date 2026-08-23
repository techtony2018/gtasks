# V0.0.112 Civic Goal execution canary handoff

## Verified release baseline

- Mission Control commit: `0b0a603f8b0b9accc5e06e29e2929cf4224fd188`
- Managed runtime: `V0.0.112` at `http://127.0.0.1:4179`
- Goal execution mode was restored to `shadow` after the canary.
- Independent desktop and genuine-mobile QA passed for frozen aggregate
  `ef681f7f0c9061e4940e8f81fe8479199703378bd2b52aabd288e3c5c26f7d62`.
- QA report:
  `/Users/tony/work/gtasks/artifacts/qa/v0.0.112-goal-route-repair-independent/gate-report.md`

## Authorized canary result

The exact authorized Civic Goal
`goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10` produced exactly one deterministic
Task:

- Task: `tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92`
- Title: `Review Civic: Help California be better through political action progress and publish one bounded next-step brief`
- Status: `active`
- Agent: `agents/timmy`
- Work root: `collections/timmys-tasks`
- Derivation fingerprint:
  `f2c6d9b1eb3b181f53200cf8409adf7732e7938f1324b0f88d11491faae5f4c8`

Exact remote-MCP readback showed only these three typed `gtasks` edges:

1. `member_of -> collections/timmys-tasks`
2. `advances_goal -> goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10`
3. `assigned_to -> agents/timmy`

The second canary run adopted the same Task as `duplicate`, created no second
Task, and did not append a second handoff. The handoff event total changed
exactly from 335 to 338 on the first run and remained 338 on the second run.

## Handoff evidence and blocker

- Handoff:
  `handoff-06c7829db9cbe4f288782ecce6883c91a9b8921db1f1b8b7dc7e29463e8c26fc`
- Correlation: `corr-65bb91dfe5faee44cd54060f`
- Sequence 336: `handoff_queued`
- Sequence 337: `execution_claimed`
- Sequence 338: `handoff_leased`, attempt 1, generation 1
- Verified route: `agents/timmy` on `hosts/timmy`
- Lease expired at `2026-08-23T21:21:40.086493+00:00` without a durable
  `received` or `execution_started` acknowledgement.

Read-only inspection of Timmy host `100.100.126.85` established that the
legacy one-worker LaunchAgent claimed the handoff and then lost the claim
response before saving its private active-claim file. Its replacement process
continued long-polling, but the central handoff remained `leased` after expiry.
No Task, lease, event, private inbox, or fixed-thread data was manually edited.

The root cause is a production wiring gap: `HandoffGuardian` implements expired
lease recovery, but no production caller invokes it. `DurableHandoffStore.claim`
therefore considered only `queued` and `retrying` rows and could never reclaim
an expired `leased` row without an unrelated external Guardian tick.

## Prepared fail-closed repair

Branch `codex/goal-execution-engine` contains a TDD repair that makes the next
authenticated claim boundary reconcile expired leases before selecting work.
This preserves the existing append-only `lease_expired` event, rotates the
lease generation, and lets the same identity retry without an operator editing
SQLite or GBrain.

Verification:

```text
RED: expired lease returned None before the repair
GREEN: 81 handoff-store tests passed
REGRESSION: 372 handoff/server/local-dispatcher tests passed in 113.784s
```

## Exact next-session resume

1. Open `/Users/tony/.codex/worktrees/codex-goal-execution-engine/gtasks` and
   verify the branch and clean tracked state.
2. Review the commit containing this handoff and the two-file lease repair.
3. Rebase/merge onto current `origin/main` if it advanced; do not overwrite the
   user-owned `.gitignore` or untracked artifacts in `/Users/tony/work/gtasks`.
4. Bump the Mission Control release version, run the focused and full suites,
   and obtain any required independent pre-commit gate for the exact frozen
   candidate.
5. Push/merge, restart only through All Things Codex Dashboard, and verify the
   managed commit/version.
6. Fast-forward the clean Timmy checkout `/Users/toddy/gtasks` on
   `toddy@100.100.126.85` to the released commit and restart its existing
   reviewed dispatcher through its supported installer/launchd boundary.
   Preserve the private identity, fixed thread, token, inbox, and evidence.
7. Observe the same handoff automatically append `lease_expired`, receive a
   generation-2 lease, and advance through `received` and
   `execution_started`. Do not manually delete or rewrite the old lease/event.
8. Require canonical Task/edge readback and confirm Goal execution remains in
   `shadow` after the canary.

Canary command output and JSON readbacks are retained at
`/tmp/mission-control-v00112-civic-canary.D29Hqx` for this host session.
