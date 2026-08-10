# OpenClaw Agent Delegation Runbook

## Scope and authority

Mission Control recognizes three existing Codex identities and three
independent OpenClaw identities:

| Host | Permanent Codex identity | Independent OpenClaw identity |
| --- | --- | --- |
| Tammy | `agents/tammy` | `agents/tammy-oc` |
| Timmy | `agents/timmy` | `agents/timmy-oc` |
| Toddy | `agents/toddy` | `agents/toddy-oc` |

Each OpenClaw profile has its own task and Artifact collections and starts with
no default Goal. It may own Goals and tasks later. Temporary delegation is
extra work only: it never rewrites permanent `assigned_to`, never replaces the
OpenClaw Agent's own work, and never interrupts already-active Codex work.
Owned work always outranks delegated work; a valid empty delegated claim is a
successful no-op.

Only Tony may authorize delegation. The selectable window is 15 minutes
through 7 days. Instants are stored in UTC and displayed in
`America/Los_Angeles`. Allowed operations are explicit and restricted. The
OpenClaw worker may not expand scope, perform external account or financial
actions, or write raw GBrain state. Expiry, completion, or revocation prevents
new claims and hands unfinished work back to its permanent Codex owner.

## Fixed-session and two-worker isolation

Each host runs one two-worker supervisor. One worker resumes the existing fixed
Codex thread; the other resumes one pre-existing fixed OpenClaw session. A
worker never creates, replaces, forks, or guesses a target. The two workers
have separate identity-scoped config, credentials, inbox, claim files, runtime
adapter, and failure counters. The supervisor must fail closed on route,
identity, session, credential, lease, or permanent-owner mismatch.

Before disabling the legacy worker, the installer verifies both workers
against one reachable Mission Control origin through authenticated,
no-side-effect `POST /api/handoffs/preflight` requests. For a pair running on
the Mission Control host, use `http://127.0.0.1:4179`; a successful health
`GET` alone is insufficient.
The paired LaunchAgent PATH includes the verified OpenClaw launcher directory
so an installed `#!/usr/bin/env node` launcher resolves Node under launchd's
otherwise minimal environment; the resolved runtime executable remains exact.

Private state belongs under:

```text
~/Library/Application Support/GTasks/handoff-dispatcher
```

The directory is mode `0700`; credential/config/claim/lock files are mode
`0600`, regular, and non-symlink. Fixed session identifiers, bearer tokens,
registration identifiers, prompts, and model output never enter Git, audit
events, UI projections, or this runbook. Both the legacy and paired installers
serialize inspection and mutation with the same host-local install lock at
`.install.lock`. Atomic canonical provisioning and lease operations use the
Memory Stargraph-owned NATS JetStream distributed compare-and-set lock; GBrain
code is not modified to provide locking. The lock is a short-lived private
coordination record, not ownership: every successful claim carries the lock
revision as a fencing token, retries reuse the same idempotency key, expired
holders cannot commit, and timeout/revoke paths release or supersede the lock.
The OpenClaw Agent's own work is never locked or replaced by delegated work.

## Hourly proactive reconciliation

Each deployed OpenClaw Agent uses the version-controlled policy in
`config/openclaw-agents/heartbeats.json` and checklist in
`config/openclaw-agents/HEARTBEAT.md`. The heartbeat runs every hour in the
Agent's existing `agent:<id>:mission-control` session, targets no external
channel, and defers while that Agent is busy. It never creates a new chat.

Heartbeat is a read-only reconciliation layer, not an alternate Dispatcher.
It reads owned work first and eligible delegated work second, then reports a
missing/stale handoff or newly unblocked work. It must not claim or execute a
Task, publish an Artifact, or mutate canonical state. Those actions still
require the authenticated Dispatcher, its execution claim, and the existing
authority checks. A quiet hourly scan returns `HEARTBEAT_OK` and produces no
external notification.

Deploy heartbeat only for an Agent whose runtime and fixed session already
exist. Install the canonical checklist into that Agent's workspace, merge the
matching `heartbeat` object into its `agents.list[]` entry, validate the full
OpenClaw configuration, restart the Gateway, and read back the exact Agent,
interval, session, target, and checklist hash. Preparing policy for an inactive
Agent does not authorize starting its runtime.

## Preflight and dry-run

Do not run execute mode until Tony explicitly authorizes the Tammy-OC canary.
From the exact reviewed GTasks commit:

```bash
python3 scripts/provision_openclaw_agent_profiles.py \
  --config config/openclaw-agents/agents.json --dry-run

python3 scripts/install_local_handoff_supervisor.py \
  --worker-config /private/host/workers.json \
  --plist-template config/openclaw-agents/dispatcher-supervisor.plist.template \
  --python-path /absolute/path/to/python3 \
  --module-root /absolute/path/to/gtasks \
  --runner-path /absolute/path/to/gtasks/gtasks/local_handoff_supervisor.py \
  --codex-path /absolute/path/to/codex \
  --openclaw-path /absolute/path/to/openclaw \
  --working-directory /absolute/path/to/agent-workspace \
  --dry-run

curl -fsS http://127.0.0.1:4179/api/health
curl -fsS http://127.0.0.1:4179/api/releases
curl -fsS http://127.0.0.1:4179/api/agents
curl -fsS http://127.0.0.1:4179/api/agent-delegations
```

The profile dry-run must report exactly three Agents, six collections, zero
default Goal links, and `mutated: false`. The installer dry-run must report the
exact pair, two private config targets, one supervisor plist/label, and no
launchd or filesystem mutation. Stop on any existing unexpected profile,
relationship, route, fixed target, loaded label, recovery marker, permission,
or credential mismatch.

## Synthetic no-wake/no-write gate

Before any live operation, run the complete automated suite plus focused tests
with fake GBrain/Memory Stargraph, fake NATS, fake OpenClaw, fake clocks, and a
temporary SQLite store:

```bash
python3 -m unittest tests.test_delegation tests.test_handoff_dispatcher \
  tests.test_local_handoff_supervisor tests.test_openclaw_adapter \
  tests.test_artifact_publisher tests.test_server
python3 -m unittest discover -s tests
node --check static/app.js
python3 -m compileall -q gtasks tests scripts
git diff --check
```

The gate must prove no real Agent wake, GBrain/Memory Stargraph mutation, NATS
bucket/key mutation, OpenClaw session call, LaunchAgent install, or host change.
Any unexpected network or subprocess call fails the gate.

## Tammy-OC canary and sequential activation

The Tammy-OC canary is a separate live side effect and requires Tony's explicit
authorization after the frozen candidate and independent desktop/mobile QA are
available. Do not infer this authorization from approval of the design or code.

After authorization, provision one isolated completed QA fixture assigned only
to Tammy-OC. Verify, in order:

1. exact profile, task collection, Artifact collection, and zero default Goal
   readback;
2. private credential, fixed session, route, plist, PID, and argv readback;
3. one fixed-session acknowledgement without creating another session;
4. one lease/claim, one status checkpoint, one append-only Timeline chain, and
   one Artifact in `collections/tammy-oc-artifacts` with permanent owner,
   executor, `delegation_ref`, and `produced_for` provenance;
5. early completion, expiry/hand-back, restart recovery, and a valid zero-work
   claim;
6. no wake, state, credential, or Artifact effect on `agents/tammy`.

Stop immediately on any mismatch. Only after Tammy-OC canary PASS may Timmy-OC
be activated and verified with the same isolated gate. Only after Timmy-OC PASS
may Toddy-OC be activated. Never batch the three hosts or treat SSH/CLI exit
status as canonical readback.

## Rollback and incident response

Rollback must disable only the affected OpenClaw worker, stop new delegated
claims, checkpoint active claims, preserve canonical leases and events, and
leave the Codex worker running. Do not delete a lease, claim, Timeline event,
Artifact, fixed session, or recovery marker to manufacture a clean result.

For an identity/session/route mismatch, revoke or allow the delegation to
expire, preserve evidence, disable the affected OpenClaw worker, and reconcile
the permanent Codex owner before retry. For an ambiguous post-gate execution,
do not launch again; checkpoint and hand back using the recorded idempotency and
claim evidence. For install failure, keep the shared `.install-recovery.json`
receipt and follow the recovery procedure in
[`agent-handoff-dispatcher.md`](agent-handoff-dispatcher.md).

## Release evidence

Terminal release evidence must record the exact commit and push readback,
runtime version/health, full test count and skips, frozen independent QA report
for desktop `1440x1000` and genuine mobile `390x844`, Tammy-OC/Timmy-OC/Toddy-OC
activation receipts, profile/collection/zero-Goal readbacks, fixed-session
proof, delegation lifecycle, Timeline chain, Artifact provenance, rollback
proof, and any valid zero delegated claim. Until all of those exist, report the
rollout as blocked or partially activated, never completed.
