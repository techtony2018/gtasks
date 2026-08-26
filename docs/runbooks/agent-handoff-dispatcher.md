# Agent handoff Dispatcher runbook

## Scope and source of truth

Mission Control creates a handoff only after a versioned canonical GBrain
mutation receipt and post-write readback are verified. GBrain remains the
source of truth for the Task, TODO, assignment, authority, and lifecycle.
Delivery failure does not roll back an already verified canonical GBrain
mutation.

The SQLite handoff event table is the only delivery evidence source. Task
Timeline and Agents Handoff History are read-only projections over the same append-only
handoff event table. Neither projection repairs or mutates GBrain.

Each installed local Dispatcher owns exactly one Agent identity, one private
registration, and one existing fixed Codex thread. It may only run:

```text
codex exec resume --skip-git-repo-check <fixed-thread-id> <prompt> --json
```

The Dispatcher must never create, fork, replace, or guess a Codex thread. The
fixed thread id, registration id, bearer token, and full prompt stay in private
host state and are never written to repository files or audit events.
`--skip-git-repo-check` is required because an existing Agent workspace may be
a trusted non-Git directory; it does not bypass approvals or sandboxing.

For the OpenClaw rollout, each host instead runs one paired two-worker
supervisor. Its Codex and OpenClaw workers have separate mode-`0600` configs,
credentials, fixed targets, claim state, inbox state, and runtime adapters.
The supervisor never merges identities or credentials. The accepted pairs are
exactly `agents/tammy` / `agents/tammy-oc`, `agents/timmy` /
`agents/timmy-oc`, and `agents/toddy` / `agents/toddy-oc`. OpenClaw resumes a
pre-existing fixed session; it never creates, replaces, forks, or guesses one.
As of V0.0.164, this Mac's local supervisor readback is Tammy/Tammy-OC only:
Timmy and Toddy are not local workers here. A Timmy or Toddy outage must be
repaired on that Agent's own host route, not by adding local worker configs on
this Mac.

Delegated execution is additive and does not rewrite `assigned_to`. A verified
Tony authorization may last 15 minutes through 7 days. Owned work always
outranks delegated work; a zero delegated claim is valid. Expiry, completion,
or revocation stops new delegated claims and hands unfinished work back to the
permanent Codex owner. Full provisioning and canary instructions are in
[`openclaw-agent-delegation.md`](openclaw-agent-delegation.md).

## Durable delegated launch boundary

`received` proves only that the target host durably accepted the wake. It does
not prove that a target command started. Delegated delivery follows this
ordered boundary:

```text
received
  -> local launch_preparing
  -> local launch_spawned (PID durable)
  -> local launch_ready (runner ready evidence durable)
  -> local start_requesting (request and current credential durable)
  -> server execution_started (one launch grant)
  -> local start_granted (grant reference durable)
  -> atomic gate_open
  -> local executing
  -> completed | handed_back | suppressed | exhausted failed
```

A start that is later proven unused branches through
`execution_start_abandoned -> received` and must build a new runner with a new
launch id and grant before it can approach the gate again. This branch is
legal only for `command_not_started` result evidence or a dead runner observed
while the gate is still absent.

The target command is held inside a private gated shim. The shim writes its PID
and ready evidence before Mission Control receives `execution-start`; it cannot
invoke the target argv until the exact launch has a server grant and an atomic
gate file. The server validates the lease, wake intent, execution claim,
canonical Task authority, delegation version/window/scope, and owned-work
priority in the same transaction that changes `received` to
`execution_started`. The same launch id replays the same grant; a different
launch id is fenced out.

Before every first request or replay, the inbox commits `start_requesting` with
the launch id, a reference to the exact execution-start mutation, the execution
idempotency identity, and references to the current registration, generation,
and capability. Therefore a durable `launch_ready` item is never classified as
safely prelaunch after a crash until that exact launch is reconciled with the
server. If the CAS committed, replay returns and locally persists the original
grant. If replay proves that launch was already abandoned (`received`, no
grant), only then may the failed attempt become retryable.

The start row is immutable fence evidence: its launch id, original lease
generation/reference, grant reference, and start instant never change during
recovery. The lease row is separate mutable authorization. Recovery rotates
that current capability and generation, and the host atomically refreshes the
claim JSON in every nonterminal inbox state before its next start, abandon,
checkpoint, or failure request. Launch id, PID, grant reference, and
`wake_launches` history are not rewritten by credential rotation.

Revocation before that compare-and-swap suppresses the unstarted delivery and
the host cancels the still-closed shim. Revocation after the compare-and-swap
does not erase the start record. The host either observes a verified result or
uses the idempotent execution-checkpoint path to suppress and hand the task
back. A checkpoint proves the immutable launch fence and, while authority is
live, must also present the current rotated lease credential. If revocation
already terminalized the same execution claim and cleared that credential,
the exact launch checkpoint is a read-only terminal reconciliation; it does
not create a second hand-back. A delegated acknowledgement cannot advance from `received` to active,
blocked, or completed until the server start record exists.

For a claim file named `<name>.json`, the host keeps the private inbox in
`<name>.wake-inbox.sqlite3` and launch directories under
`<name>.wake-inbox.launches/`. The inbox file and every request, lock, ready,
gate, cancel, and result file are private. `wake_launches` is append-preserving
evidence for preparing, spawned, ready, start requesting, grant received, gate
open, completion, handback, pre-launch failure, abandon required, verified
start abandonment, terminal reconciliation, and ambiguity. It stores only bounded state, PID, grant
reference, and privacy-safe detail—not bearer tokens, raw capabilities, fixed
session ids, prompts, stdout, or stderr.

The legacy in-process `LocalAgentDispatcher` is not a delegated execution
mechanism. It terminally rejects delegated claims before route or wake callback
invocation because it cannot provide the gated child handshake. Owned legacy
delivery remains available through that compatibility path.

## Canonical Agent registration projections

Before credentials, runtime restart, Serve, or host installation, update the
three existing canonical Agent pages with exactly one `handoff_dispatcher`
projection apiece. Compute each digest from the private `registration_id` as
UTF-8 bytes (no newline) with SHA-256; never put the raw id in GBrain.

`agents/tammy`:

```yaml
handoff_dispatcher:
  registration_sha256: <64-lowercase-hex SHA-256 of Tammy registration_id>
  route: hosts/tammy
  verified: true
```

`agents/timmy`:

```yaml
handoff_dispatcher:
  registration_sha256: <64-lowercase-hex SHA-256 of Timmy registration_id>
  route: hosts/timmy
  verified: true
```

`agents/toddy`:

```yaml
handoff_dispatcher:
  registration_sha256: <64-lowercase-hex SHA-256 of Toddy registration_id>
  route: hosts/toddy
  verified: true
```

Use only the supported whole-page CLI path. First save each complete current
page, edit only the projection in a private working copy, write it, and read
the same slug back:

```bash
gbrain get agents/tammy > "$PRIVATE_PROJECTION_DIR/agents-tammy.md"
gbrain put agents/tammy < "$PRIVATE_PROJECTION_DIR/agents-tammy.md"
gbrain get agents/tammy > "$PRIVATE_PROJECTION_DIR/agents-tammy.readback.md"
gbrain get agents/timmy > "$PRIVATE_PROJECTION_DIR/agents-timmy.md"
gbrain put agents/timmy < "$PRIVATE_PROJECTION_DIR/agents-timmy.md"
gbrain get agents/timmy > "$PRIVATE_PROJECTION_DIR/agents-timmy.readback.md"
gbrain get agents/toddy > "$PRIVATE_PROJECTION_DIR/agents-toddy.md"
gbrain put agents/toddy < "$PRIVATE_PROJECTION_DIR/agents-toddy.md"
gbrain get agents/toddy > "$PRIVATE_PROJECTION_DIR/agents-toddy.readback.md"
```

For every page, compare the readback digest byte-for-byte with
`hashlib.sha256(registration_id.encode("utf-8")).hexdigest()`, require
`verified: true`, and require exactly three unique routes: `hosts/tammy`,
`hosts/timmy`, and `hosts/toddy`. A missing, duplicate, mixed, unverified, or
wrongly hashed projection is a hard stop. Preserve the rest of each page and
perform no relationship write for this frontmatter-only change.

## Dashboard-managed central runtime

The canonical checkout is `/Users/tony/work/gtasks`. All Things Codex
Dashboard owns the `gtasks` process at `http://127.0.0.1:4179/` with this
argument-array command:

```text
python3 -m gtasks.server --host 127.0.0.1 --port 4179 --artifact-publisher-credentials-file /Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/artifact-publisher-credentials.json --handoff-store /Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/handoff-dispatcher.sqlite3 --handoff-dispatcher-credentials-file /Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/handoff-dispatcher-credentials.json
```

The credential file contains only Agent slugs plus registration and token
hashes. It and every per-host identity config/token file must be a regular
mode-`0600` file. Never place plaintext credentials or thread ids in
`dashboard-integration.json`.

Provision the central hashes from exactly three reviewed private identity
configs:

```bash
python3 scripts/provision_handoff_dispatcher_credentials.py \
  --identity-config /private/tammy/handoff-dispatcher.json \
  --identity-config /private/timmy/handoff-dispatcher.json \
  --identity-config /private/toddy/handoff-dispatcher.json \
  --output /Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/handoff-dispatcher-credentials.json
```

The provisioner must report `identity_count: 3` without printing a secret.
Read back the output mode, schema, three unique Agent slugs, and hashes before
restarting the dashboard-managed service. The credential hashes must exactly
match the three verified canonical page readbacks. Then restart and read back
the managed runtime in this order:

```text
POST http://127.0.0.1:4188/api/services/gtasks/restart
GET http://127.0.0.1:4179/api/health
GET http://127.0.0.1:4179/api/releases
```

The dashboard restart must succeed, `/api/health` must return status `ok`,
canonical store `gbrain`, and version `V0.0.76`, and `/api/releases` must name
`V0.0.76` as `current_version`. Read back the dashboard service PID, cwd
`/Users/tony/work/gtasks`, and the exact argument array above; an HTTP 200
without those process and payload checks is not sufficient.

## Tailnet HTTPS boundary

The central GTasks server remains loopback-only at `http://127.0.0.1:4179/`.
After the canonical pages, credentials, dashboard restart, and runtime
readbacks pass, expose only `/api/handoffs` on the node Tailnet URL
`https://tonys-macbook-pro.taildb46a7.ts.net` with the current path-scoped
Tailscale Serve command:

```bash
tailscale serve --bg --https=443 --set-path=/api/handoffs/ http://127.0.0.1:4179/api/handoffs/
tailscale serve status --json
```

The status readback must contain one HTTPS subtree handler at `/api/handoffs/` pointing
to `http://127.0.0.1:4179/api/handoffs/` and no `/` handler. The matching backend
subtree is required because Tailscale Serve strips the mounted prefix before proxying.
Prove that the Tailnet URL root,
`/api/health`, `/api/releases`, `/api/handoff-events`, and every other
non-handoff API return HTTP 404; they must not return a redirect or any GTasks
content. Tailscale Serve is private to the tailnet; never configure Funnel.

For `/api/handoffs/claim`, prove missing and invalid bearer credentials return
HTTP 401 with no lease/event mutation. From each host, a valid bearer plus the
intentionally incomplete body `{}` must return HTTP 422 with code
`invalid_handoff_claim`, proving authenticated remote connectivity without
claiming work. Load the valid bearer from the
mode-`0600` token file inside the probe process, never in argv, stdout, shell
history, or a URL. Any redirect, TLS failure, 5xx response, unexpected 2xx, or
mutation is a stop condition. A real claim is reserved for the later,
explicitly authorized Tammy-only canary.

## Per-host installation

On each Agent host, prepare one private schema-version-1 config containing
exactly `agent_slug`, `registration_id`, `fixed_thread_id`,
`mission_control_url`, and `token_file` in addition to `schema_version`. Then
run the installer from the verified release checkout:

```bash
/absolute/path/to/python3 scripts/install_local_handoff_dispatcher.py \
  --source-config /private/<agent>/handoff-dispatcher.json \
  --python-path /absolute/path/to/python3 \
  --module-root /absolute/path/to/gtasks \
  --runner-path /absolute/path/to/gtasks/gtasks/local_handoff_dispatcher.py \
  --codex-path /absolute/path/to/codex \
  --working-directory /absolute/path/to/agent-workspace
```

Resolve and verify the absolute compatible Python path independently on Tammy,
Timmy, and Toddy; host package layouts are not assumed to match. The installer
and LaunchAgent must not use `/usr/bin/python3`. `--module-root` and
`--runner-path` verify the checked-out module and set `PYTHONPATH`, while the
rendered plist `WorkingDirectory` remains the pre-existing Agent thread's
workspace. The module checkout and resumed Agent workspace are independent
paths and both must pass exact readback.

The installer owns one label, `com.tony.gtasks-handoff-dispatcher`, and writes
only these canonical destinations:

- `~/Library/Application Support/GTasks/handoff-dispatcher.json`
- `~/Library/LaunchAgents/com.tony.gtasks-handoff-dispatcher.plist`

It verifies `codex --version`, `codex exec resume --help`, the absolute Codex
path, the fixed identity/thread readback, config and plist hashes, and loaded
LaunchAgent arguments. Any mismatch is a stop condition; do not overwrite a
different identity or thread.

The installed runner command is equivalent to:

```bash
/absolute/path/to/python3 -m gtasks.local_handoff_dispatcher \
  --config "$HOME/Library/Application Support/GTasks/handoff-dispatcher.json" \
  --codex-path /absolute/path/to/codex \
  --working-directory /absolute/path/to/agent-workspace
```

Do not put a bearer token, registration id, lease capability, or fixed thread
id on the command line.

### Paired supervisor transition and install recovery

Before reading or changing launchd state, the paired installer sends one
authenticated, no-side-effect `POST /api/handoffs/preflight` for each worker.
The response must match that worker's canonical Agent, registration hash, and
host route. Both private worker configs must use the same reachable Mission
Control origin; a local-host pair uses `http://127.0.0.1:4179`. Timeout,
authentication failure, identity mismatch, route mismatch, or an unexpected
response aborts before files, recovery markers, legacy fences, or launchd state
are changed. A health `GET` is not an activation substitute.

The paired Codex/OpenClaw installer owns
`com.tony.gtasks-handoff-dispatcher-supervisor`. Run its state-read-only dry
run first, then use `--replace-legacy` only for the reviewed transition from
the retained one-worker label. The first invocation may create the shared
private mutex described below; it does not publish config, plist, marker, or
launchd state during dry run.

```bash
/absolute/path/to/python3 scripts/install_local_handoff_supervisor.py \
  --worker-config /private/<agent>/codex.json \
  --worker-config /private/<agent>/openclaw.json \
  --python-path /absolute/path/to/python3 \
  --module-root /absolute/path/to/gtasks \
  --runner-path /absolute/path/to/gtasks/gtasks/local_handoff_supervisor.py \
  --codex-path /absolute/path/to/codex \
  --openclaw-path /absolute/path/to/openclaw \
  --working-directory /absolute/path/to/agent-workspace \
  --dry-run
```

Both installers interpret each `launchctl print-disabled` label independently
as `absent`, `explicitly_enabled`, or `explicitly_disabled`. Current launchd
renders `enabled`/`disabled`; the parser also accepts the documented boolean
`false`/`true` variants. Missing labels remain `absent` and are not treated as
explicitly enabled.

Both installer scripts acquire one exclusive interprocess `flock` at this
exact canonical path before inspecting any recovery marker, plist, config, or
launchd state:

```text
~/Library/Application Support/GTasks/handoff-dispatcher/.install.lock
```

The lock is a persistent, regular, non-symlink mode-`0600` file. An installer
holds it continuously through validation, all writes, launchd transition,
rollback or recovery, final readback, and receipt construction. Bounded lock
contention stops without entering the installer body or mutating canonical
state. A symlink, non-regular entry, or non-`0600` mode is a stop condition;
do not replace or delete the lock while either installer may be running.

The lock is only a mutex. The mode-`0600` `.install-recovery.json` below is the
crash-evidence record. If an installer process crashes, the OS releases its
`flock`, while the recovery marker remains for the next lock holder to inspect
and reconcile before doing any new work.

Before its first canonical file write or launchd mutation, the supervisor
installer writes and reads back this private mode-`0600` transition record:

```text
~/Library/Application Support/GTasks/handoff-dispatcher/.install-recovery.json
```

It fences and verifies both labels before publishing files. On failure, an
explicit prior override is restored exactly, including the prior loaded state.
launchd has no safe per-label operation that restores an absent override. If
either prior override was absent, rollback therefore uses the conservative
contract: the absent label is booted out and left explicitly disabled and is
never enabled or bootstrapped. For an absent supervisor override, the
supervisor plist is also removed. The private record remains with status
`safe_disabled_fallback`; this is a recovery receipt, not a claim that the
absent override was restored exactly. Rollback and final readback never permit
both labels to be loaded or durably enabled.

The retained one-worker installer checks the private recovery path before
every canonical write and every launchctl action. Any entry at that path,
including a valid transition/recovery receipt, malformed JSON, a symlink, or
an unreadable entry, blocks installation. With no recovery marker, the
specific safe fallback state—supervisor explicitly disabled, unloaded, and
without its plist—is inactive and permits the retained installer to explicitly
enable and read back its own label before bootstrap.

Recover a stopped transition as follows:

1. Do not start the retained installer and do not infer prior state from an
   unvalidated or malformed record. Re-run the same paired supervisor install
   command without `--dry-run`; a valid exact-state record is rolled back and
   removed before a fresh install begins.
2. If the command reports `safe_disabled_fallback`, inspect the private receipt
   and its two `override_state` snapshots. Every label whose prior override was
   `absent` must now be unloaded with `launchctl print` and must read as
   `disabled` (or boolean `true`) in
   `launchctl print-disabled "gui/$(id -u)"`. If the absent label is the
   supervisor, its plist must also be absent. Any explicitly enabled or
   disabled counterpart must match its exact recorded load/override state. Do
   not proceed if both labels are loaded or both are enabled.
3. Archive the mode-`0600` receipt as recovery evidence. Only after the safe
   readback may an operator remove the original receipt and deliberately run
   either the paired installer or the retained one-worker installer. The
   installers do not automatically erase a `safe_disabled_fallback` receipt.
4. If the record is malformed, the paired installer attempts only the
   disable/unload safety fence and reports `recovery_required`; it does not
   fabricate an exact prior state. Preserve the record and investigate before
   the same explicit operator acknowledgement in step 3.

## Audit retention, export, and redaction

The durable store uses 90-day default retention. Retention is declared in
every read-only export and is not an instruction to silently rewrite or delete
individual audit rows. Export a bounded page with:

```text
GET /api/handoff-events?export=1
```

The export metadata format is `handoff-audit-v1`; filters, ordering, totals,
and cursors match the Task Timeline and Agents Handoff History queries. Corrections are
new append-only events that reference the superseded event.

User-visible and exported rows contain the pseudonymized `registration_ref`.
They exclude bearer tokens, raw registration ids, fixed thread ids, lease
capabilities, full prompts, thread output, and unbounded diffs. If redaction
cannot be proven, stop export and UI verification rather than substituting raw
logs.

## Failure recovery

- A retryable delivery failure moves the same handoff to `retrying`; a later
  identity-scoped claim increments its attempt and lease generation.
- A dead runner before durable readiness and before any start intent is a proven
  pre-gate failure. Durable `launch_ready` evidence is different: the host first
  commits `start_requesting` and reconciles the same launch even if the runner
  is now dead or its local evidence regressed. A replayed grant is persisted,
  then the host records `abandon_start` and verifies the server reset before
  allocating another launch. An exact `received`/no-grant replay proves the
  start was already reset and is also safe to retry.
- Only a proven pre-gate shim failure or a verified unused-start reset may
  create another local launch attempt. The new attempt has a new deterministic
  launch id and a different server grant. Exhaustion persists a pending
  terminal server action before sending it.
- Loss of an `execution-start` response is not a new attempt. The host replays
  the same launch id while the gate remains closed and verifies the same grant.
  If that start was already abandoned, replay returns `received` with no grant
  and the host reconciles its local item to retryable failure instead of trying
  to cancel or reopen the old gate.
- `command_not_started` is the only post-gate result that proves the target
  executable was never invoked. The host durably records `abandon_start`
  before calling the execution-abandon endpoint. That transactional CAS
  archives the immutable start row, changes `execution_started` back to
  `received`, and appends one audit event. A lost response retries only this
  idempotent CAS. Only after verified reset may a fresh runner be created.
- Execution-abandon has only two successful response pairs: `received/true`
  proves the unused start reset, while `suppressed/false` proves authority ended
  instead. The latter clears the pending action and terminalizes the inbox as
  `suppressed`; completed/dead-letter or mismatched boolean pairs are rejected.
- Timeout, nonzero exit, a dead runner after gate open, or malformed/missing
  post-gate result evidence is ambiguous. The local state becomes
  `recovery_required`; only the idempotent execution-checkpoint request is
  retried. A verified `suppressed/true` checkpoint commits local `handed_back`;
  exact already-completed or dead-letter readback commits local `suppressed`.
  The target command is never automatically reissued.
- Handback cleanup is terminal-first: the SQLite inbox commits
  `handed_back`/`suppressed`, then the matching private `active.json` claim is
  removed. If the host crashes between those writes, restart observes the
  terminal inbox and idempotently removes `active.json` before the inbox worker,
  recovery request, or `recovered_handoffs` deferral can run.
- An exhausted proven-prelaunch or verified-unused-start retry moves the
  handoff to `dead_letter`, releases the execution claim as
  `terminal_delivery_failure`, and retains the
  exhausted local `failed` row and launch history for audit.
- Guardian requeues only an expired leased delivery or records a terminal
  dead letter according to the bounded retry policy. Guardian is fallback
  reconciliation, not the primary sender or a business-task executor.
- After a local restart, the Dispatcher persists recovery intent before the
  request, reconciles an authoritative stale generation, rotates capability,
  and resumes only after the rotated claim is durably saved in both the claim
  store and every matching nonterminal inbox state. An exact same-generation
  reconciliation is an idempotent deferred replay, not a fabricated advance.
- `queued` or `retrying` reconciliation clears stale host state before a new
  claim. `completed` or `dead_letter` reconciliation clears host state and
  stops without claiming replacement work.
- Repeated recovery reconciliation is bounded and the exhausted count is
  persisted, preventing a restart loop against stale state.
- V0.0.141+ stale local `abandon_start` rows are reconciled through the
  authoritative server `/recover` path before the host retries the pending
  local action. If the server proves the handoff is already `completed` or
  `suppressed`, the local wake inbox row is cleared as `server_completed` or
  `server_suppressed` without touching the current Agent claim or inventing a
  replacement handoff.
- V0.0.142+ treats `codex_thread_active_writer` as retryable local Codex
  backpressure, not terminal delivery. The host records a retryable local
  failure and keeps the same handoff eligible for retry instead of promoting it
  directly to `dead_letter`.
- V0.0.143+ operator recovery can requeue an owned handoff that reached
  `dead_letter` with released execution state `terminal_delivery_failure`, but
  only when server evidence shows abandoned execution starts for that handoff.
  This preserves the same handoff and task and records
  `system_dependency_recovered`; it must not be used for arbitrary dead
  letters, missing ownership, or handoffs without abandoned-start proof.
- V0.0.144+ active-writer retries are throttled. Fixed-thread Codex handoffs
  that encounter `codex_thread_active_writer` back off for the bounded local
  concurrency interval, currently 300 seconds, instead of rapidly creating and
  abandoning repeated execution starts.
- V0.0.145+ closes the pre-gate terminal reconciliation gap. When the host has
  an unused pre-gate launch and authoritative Mission Control recovery proves
  the same handoff is already `completed` or `suppressed`, the local dispatcher
  cancels that unused launch, clears the wake inbox as `server_completed` or
  `server_suppressed`, and does not create another launch or mutate the
  completed canonical task.
- V0.0.164+ recovers an expired owned execution claim at the next authenticated
  claim boundary for the same registered Agent host. Mission Control verifies
  current task authority, keeps delegated execution out of this owned repair
  path, refreshes the execution fence, and then leases the queued handoff. This
  covers worker-host outages or lost local claim state without leaving
  Goal-derived Agent work permanently queued, but it is not a manual
  delete/retry path and not permission to run another Agent locally.

Never clear local claim state merely because a request was sent. Clear or
replace it only after a verified retry, terminal, or rotated recovery response.
Never manually delete a gate, result, `wake_launches` row, active
`execution_starts` row, or `abandoned_execution_starts` row to manufacture a
retry. Only the transactional unused-start CAS may archive and remove the
active row. An operator investigating `recovery_required` must
reconcile the fixed target session and canonical Task first, then use the
recorded checkpoint/hand-back evidence rather than launching the target again.

## Codex-only Goal execution canary

Goal-derived work runs only inside the dashboard-managed Mission Control
runtime. Configure it through the supported service environment boundary:

```text
MISSION_CONTROL_GOAL_EXECUTION_MODE=off|shadow|canary
MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL=goals/<uuid>
```

Missing mode defaults to `shadow`; `canary` fails closed without one canonical
Goal slug. OpenClaw is excluded from this rollout. The planner may create or
adopt one automatic Task for the canary Goal only after it verifies one Codex
owner, at most one active supporting Project, available WIP capacity, and the
existing registered fixed thread. It never infers identity from prose and
never creates a new Codex thread.

Before activation, require the deterministic derivation receipt, one typed
Agent work-root membership, one `assigned_to`, one `advances_goal`, and exact
canonical readback of the same Task slug. Delivery reuses the handoff outbox,
version idempotency, acknowledgements, and immutable Timeline evidence already
defined by this runbook. Repeated planner runs must adopt the same Task and
must not enqueue another handoff for the same canonical version.

If any Task, relationship, route, acknowledgement, or WIP invariant cannot be
verified, switch back to `shadow`, keep the same Task and durable evidence, and
record the exact system-repair blocker. Do not delete the Task, fabricate a
receipt, or silently redirect it to Tony or an OpenClaw Agent.

V0.0.135 verified the first completed Faith canary path. Task
`tasks/83ed4e35-46a2-5a40-b3a3-502c573c7dea` remained canonical
`completed`, retained one `collections/tammys-tasks` membership, one
`assigned_to -> agents/tammy`, one `advances_goal` relationship, and produced
exactly one Artifact,
`artifacts/5f35baf9-e7fb-44f4-a28a-cd88e8e9581c`. Task detail must treat that
completed canonical task readback as stronger than stale dispatcher recovery
attention. The same release kept active suppressed dispatcher states visible
as repair attention, so this completion suppression does not hide genuinely
active or blocked delivery problems.

The local supervisor installer now defaults `--codex-resume-timeout` to
`1800` seconds, and the Tammy LaunchAgent was verified with that persisted
timeout during the V0.0.135 handoff. Operators must still use the private
installer/launchd boundary; do not hand-edit plist or inbox state to force a
retry.

V0.0.136 adds the planner duplicate-completion boundary: when a derived Goal
review task has the exact deterministic fingerprint and is already
`completed`, the planner returns `recently_completed` and does not immediately
offer a duplicate canary. Cancelled tasks and materially changed candidates
remain eligible through the normal planner rules.

V0.0.148+ makes manual Goal execution refresh active rather than passive:
`GET /api/goal-execution?refresh=1` wakes the bounded scheduler before status
readback while preserving the existing minimum interval and canary/shadow
safety controls. V0.0.149+ includes selected Task metadata for duplicate and
`recently_completed` canary decisions: Task slug, title, status, and Agent.
This metadata is readback context only; it does not create, complete, or
handoff work by itself.

V0.0.150/V0.0.151 distinguish active or planned goal-derived duplicates that
lack a verified Agent handoff. If the duplicate Task is still executable and
Mission Control cannot verify a handoff record, the projected decision becomes
`handoff_missing` and the UI must show Needs attention with exact copy:
`The canonical task is active, but no verified Agent handoff is recorded yet.`
Terminal handoff states still project as `handoff_needs_repair`; ordinary
`duplicate` and `recently_completed` remain separate non-missing states. A
completed handoff readback is sufficient to keep an otherwise duplicate Goal
decision ordinary, as verified by the live Civic task
`tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92`.

V0.0.152 projects the same latest dispatcher handoff evidence into
`/api/agent-work` for non-completed Agent tasks. The response may include
`dispatcher_handoff: {"status": "<latest>"}` when the handoff store has a
latest status and the canonical task `handoff` field is empty; the projection
does not overwrite canonical `handoff`. Completed Agent-work rows suppress
`dispatcher_handoff` so old execution evidence does not imply current work.
The live Civic Agent-work row read back active with canonical `handoff: null`
and `dispatcher_handoff: {"status": "completed"}` for
`tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92`.

V0.0.153 adds a separate Goal execution attention state for active or planned
non-derived Agent goal tasks that are neither actionable nor already covered by
handoff/blocker/TODO evidence. If the task has blank `next_action`, no
canonical `handoff`, no blockers or dependencies, and no open TODO, the
decision becomes `task_needs_next_action` and the UI must show Needs attention
with exact copy: `The canonical task is active, but it has no explicit next
action for the assigned Agent.` Actionable duplicate, passive scheduled waits,
`handoff_missing`, `handoff_needs_repair`, and `recently_completed` remain
distinct. The live Family/Toddy task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` read back as
`task_needs_next_action`.

V0.0.154 keeps that attention state visible but excludes it from automatic Goal
execution WIP accounting. A stalled/non-actionable Agent task with blank
`next_action`, no handoff, no blockers or dependencies, and no open TODO does
not by itself block another bounded Goal review from becoming `auto_eligible`.
The control behavior remains strict: active Agent work with a real
`next_action` still consumes WIP and can keep another Goal at `wip_full`. Live
readback kept Family/Toddy task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` as
`task_needs_next_action`, while Toddy's other Goal
`goals/d175890b-6e89-5543-b587-b5df345c1c81` became `auto_eligible` with task
`tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5`; later documentation validation
read it as `duplicate` for the same task, still not `wip_full`.

V0.0.155 adds a separate stale-worker attention state for Goal-derived active
tasks whose latest handoff is still `queued`. When the dispatcher execution
claim remains nonterminal after the bounded worker attention window, Goal
execution projects `handoff_worker_unavailable` and the UI must show Needs
attention with exact copy: `The canonical task is active and queued, but no
verified Agent worker has leased it yet. Verify the Agent host dispatcher and
private route.` Fresh queued handoffs remain Delivering. Live readback for
Toddy Health Goal `goals/d175890b-6e89-5543-b587-b5df345c1c81` showed task
`tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5` with latest handoff status
`queued` and projected `handoff_worker_unavailable`. The operational diagnosis
is that Toddy fixed thread/host configuration exists, but the Toddy host's
Tailscale session is logged out, leaving the private route
`https://tonys-macbook-pro.taildb46a7.ts.net` unreachable. Record that as
host/private-route remediation, not as completed Agent execution.

V0.0.156 adds the positive reconciliation path for selected canary
Goal-derived active tasks. Mission Control may mark the canonical task
completed only after the latest dispatcher handoff is `completed` and exact
Artifact readback verifies a canonical Artifact whose `produced_for` equals the
same task slug. The projected public reason is
`completed_after_verified_handoff`, with UI copy: `Mission Control completed
the canonical task after verified Agent handoff and Artifact readback.` If the
completed handoff lacks exact Artifact evidence, the task must remain
active/duplicate. Live Civic readback shows
`tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92` completed at
`2026-08-24T03:25:19.000864-07:00`, with Artifact
`artifacts/4fb85655-dc13-4050-b3a3-0c56b27acb9f` retaining
`produced_for -> tasks/7ad5e1f5-eeb3-5fcf-850f-580eadb4ce92`. The Toddy Health
host-route blocker remains separate: Toddy fixed thread configuration exists,
but the host is still logged out of Tailscale, so its queued handoff remains
`handoff_worker_unavailable` until the private route is restored.

V0.0.157 adds a cycle key to Goal-derived Agent review deterministic
fingerprints. A completed prior-cycle review no longer leaves the Goal stuck
in `recently_completed` forever; same-cycle completed work still suppresses
repeat creation. QA verified different deterministic task slugs for cycles
`2026-08-17` and `2026-08-24`. After deploy, the dashboard-managed scheduler
created and activated current-cycle Civic/Timmy task
`tasks/44e14ea5-0f81-558b-a761-ec3540f3b4e2`, owned by `agents/timmy`, for
Goal `goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10`; the detail includes
`Review cycle starts 2026-08-24`. Dispatcher readback progressed from leased
to execution_started to `actively_executing` during the deploy handoff. A
later documentation readback showed the task still active with
`dispatcher_handoff.status=suppressed`; no Artifact was present at the bounded
handoff check. The canary target was then restored to Toddy Health;
Toddy still requires the Tailscale host login/private-route repair before its
queued handoff can advance beyond `handoff_worker_unavailable`.

V0.0.158 extends the V0.0.156 verified-completion rule to checkpointed
suppressed handoffs. If the selected Goal-derived task has latest handoff
status `suppressed`, terminal execution state `checkpointed`, and exact
Artifact readback with `produced_for` equal to the same task slug, Goal
execution reconciles the task as `completed_after_verified_handoff`.
Suppressed handoffs without checkpoint state, or checkpointed suppressed
handoffs without exact Artifact evidence, remain attention/active. V0.0.159
repairs the readback root cause: `CanonicalHandoffEventBridge.latest_task_handoff_delivery_state()`
falls back to terminal execution claims, so released checkpointed claims expose
`terminal_state=checkpointed` instead of losing the state after
`include_terminal=False`.

Final live V0.0.159 readback showed current-cycle Civic/Timmy task
`tasks/44e14ea5-0f81-558b-a761-ec3540f3b4e2` completed at
`2026-08-24T04:08:08.447338-07:00`; exact Artifact
`artifacts/6e6c331e-a181-4d8f-ab16-cda613b8fed9` was created by
`agents/timmy` and has
`produced_for -> tasks/44e14ea5-0f81-558b-a761-ec3540f3b4e2`. The canary target
was restored to Toddy Health, which still remains
`handoff_worker_unavailable` pending Toddy host Tailscale login/private-route
repair.

V0.0.160 adds the Tony-answer blocked state to Goal execution and task-detail
handoff rendering. A canonical Agent task with status `blocked`, canonical
handoff state `waiting_for_input`, and `waiting_on=people/tony-guan` projects
as `waiting_for_tony` and the UI must show Blocked with exact copy:
`The canonical task is blocked waiting for Tony's answer before the assigned
Agent can continue.` This state is not `task_needs_next_action`; it already
has a canonical blocker and an Agent handoff question. The exact task detail
API hydrates the active handoff question TODO from canonical `todo_for`
backlinks, so the handoff panel renders the real question instead of indefinite
loading or No TODO. If bounded TODO hydration fails, the response surfaces a
canonical TODO list unavailable issue, shown as
`The canonical TODO list is unavailable.`, while preserving task and handoff
visibility. Live V0.0.160 readback showed Family/Toddy task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` blocked with handoff
`waiting_for_input`, blocker `people/tony-guan`, question TODO
`todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, and Goal
`goals/2c86f86c-c9fb-5f49-96d0-e4d63f489fc8` projecting
`waiting_for_tony`.

V0.0.161 retains accepted dispatcher handoff status for selected duplicate or
recent active Goal tasks. If the planner selects an existing task whose public
reason remains `duplicate` or `recently_completed`, and the dispatcher has a
latest accepted handoff status such as `queued` or `actively_executing`, the
Goal execution response includes that handoff status so the UI can render
Delivering or Executing instead of ambiguous Ready or duplicate-only context.
This is presentation/readback context only; completion still requires the
normal terminal handoff plus exact Artifact evidence path, and canary mode
still targets one configured Goal. The V0.0.161 canary target was rotated from
Toddy Health to Faith/Tammy. Runtime readback showed Faith/Tammy Goal-derived
task `tasks/46ba34c2-9ccb-523e-a786-9b70d5673073` completed for
`agents/tammy`, and Artifact `artifacts/d2a45c21-1428-4891-ae98-531a958e1e98`
was created by `agents/tammy` with `produced_for` that task and
`supports_goal -> goals/755548a3-d556-513a-900c-45f90da5702e`. Current
remaining next-owner blockers are separate: Family/Toddy task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` is legitimately blocked waiting
for Tony via question TODO `todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, and
Toddy Health task `tasks/08ca28c3-c812-5abf-86a7-110c14cb94a5` remains
infrastructure-blocked with dispatcher status `queued` /
`handoff_worker_unavailable` until the Toddy fixed-thread host is logged into
Tailscale and its dispatcher route is available.

V0.0.162 adds an explicit private `auto` canary target for dashboard-managed
Goal execution canary mode. `auto` is still bounded to at most one
Goal-derived Agent Task activation per run; it selects the first currently
`auto_eligible` Goal instead of staying pinned to a fixed completed canary.
V0.0.167 supersedes the older fallback ordering: active/eligible work still
wins, but if no new activation or active accepted handoff is selected, the
public status surfaces actionable blockers before `recently_completed`
history. The blocker states are `waiting_for_tony`,
`handoff_needs_repair`, `handoff_missing`, `task_needs_next_action`, and
`handoff_worker_unavailable`.
V0.0.182 adds a compact reader summary to `/api/goal-execution` at both the
top level and `last_run.summary`: `total_goals`, `needs_attention`,
`waiting_for_tony`, `owner_missing`, `ready`, `in_flight`,
`recently_completed`, per-reason `reasons`, and bounded `next_action`
guidance. It is a read-only projection for dashboards and lightweight
integrations. Do not treat the summary as authority to create ownership links,
answer Tony questions, wake a worker, or mark Goal work complete; callers must
still inspect the named canonical Task, handoff, Artifact, and next-owner
evidence before taking action.
V0.0.183 renders the same verified summary inside the Agents > Goal execution
panel. The visible panel should show the `Next action:` line plus counts for
total Goals, Needs attention, Waiting for Tony, Missing owner, In flight, and
Recently completed, matching `/api/goal-execution.summary` after scheduler
readback. This is operator-facing display only; it does not change dispatcher
authority, local worker boundaries, or completion gates.
V0.0.184 extends that summary with `blocking_questions` at both top level and
`last_run.summary` for waiting-for-Tony decisions whose canonical question TODO
is available. The Agents > Goal execution panel renders the exact canonical
question with a `Question:` prefix. For the current Family/Toddy blocker, the
canonical readback is task `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, TODO
`todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`, Agent `agents/toddy`, and
question `Which family-care scope, outcomes, constraints, and first action should Toddy use next?`.
This projection helps Tony find the blocker; it is not a dispatcher wake,
handoff acknowledgement, answer, or completion receipt.
V0.0.185 adds `missing_owners` to the same summary for `owner_missing`
decisions. Each entry carries the exact Goal slug/title, required relationship
`default_agent_for`, and message `Assign exactly one Codex Agent with a verified default_agent_for link before Mission Control can derive work from this Goal.`
Agents > Goal execution renders a compact `Missing owner: <Goal title> — add
default_agent_for` line. This is not a dispatcher action; repair still belongs
to the supported Goal ownership relationship workflow.
V0.0.186 links those summary action items as exact read-only controls: the
waiting-for-Tony question opens the canonical Task detail, the missing-owner
Goal title opens the canonical Goal detail, and Close restores focus to the
originating summary control. This is navigation only; it is not a dispatcher
wake, handoff acknowledgement, Tony answer, owner repair, or mutation.
V0.0.187 adds explicit Codex-only owner assignment buttons beside the
missing-owner summary item: `Assign to Tammy`, `Assign to Timmy`, and
`Assign to Toddy`. The buttons call the verified
`POST /api/agents/<agent>/default-goals` contract with body
`{goal_slug, action: "assign"}` only after user activation. No OpenClaw
assignment controls are exposed, and no owner is inferred or assigned
automatically by the dispatcher.
V0.0.188 adds structured `action_queue` entries to the same summary and
`last_run.summary`. The queue groups next actions by owner and currently
contains Tony-owned `answer_question` for the Family/Toddy Task/TODO plus
Tony-owned `assign_goal_owner` for the Entrepreneurship Goal. Agents > Goal
execution renders `Action queue:`, `Tony action required`,
`Answer Agent question`, and `Assign Goal owner` so operators can see who must
act next without treating the dispatcher as the owner of Tony repairs.
V0.0.189 turns the Tony-owned `Answer Agent question` queue row into a direct
inline action. It opens the canonical waiting-for-input Task after readback,
focuses the existing `#task-handoff-answer` control, and restores focus to the
immutable `.goal-execution-answer-action` origin on Close. It does not submit
an answer or create a new dispatcher mutation path; answering remains the
existing verified `/api/todos/<todo>/answer` flow.
V0.0.190 adds an inline answer composer beside that queue row. The summary
action carries verified `todo_updated_at`; the form posts the answer through
the existing canonical `/api/todos/<todo>/answer` endpoint with
`expected_updated_at`, actor `people/tony-guan`, source `mission_control`, and
a UUID `idempotency_key`. Verified response reconciliation refreshes Goal
execution and Agent Work. This is a Tony answer path, not a dispatcher wake,
Agent handoff acknowledgement, or OpenClaw assignment.
V0.0.191 adds inline owner-assignment controls to the primary Action queue for
Tony-owned `assign_goal_owner` entries. The buttons are still Codex-only
`Assign to Tammy`, `Assign to Toddy`, and `Assign to Timmy`, and they use the
existing verified `POST /api/agents/<agent>/default-goals` contract with
`{goal_slug, action: "assign"}`. The separate Missing owner detail row keeps
its controls, OpenClaw remains excluded, and no owner is inferred by the
dispatcher.
V0.0.192 enriches missing-owner and Action queue assignment controls with
verified Codex candidate-owner metadata. The dispatcher reports each
candidate's verified default Goal count and marks exactly one lowest-load
Codex Agent as recommended. Current live readback for the Entrepreneurship
Goal shows Timmy count 1 recommended with
`recommended: lowest verified Codex Goal load`, Toddy count 2, and Tammy count
3. The recommendation is guidance only: no `default_agent_for` link is created
until Tony explicitly activates a verified Codex assignment control, OpenClaw
remains excluded, and the dispatcher still must not infer owners.
V0.0.193 carries the verified question detail from waiting-for-Tony TODOs into
Tony-owned `answer_question` Action queue entries. The Agents panel renders
that detail beside the inline answer form, so Tony can review the full
decision context and submit an answer without first opening the Task. This is
display/readback context plus the existing explicit answer submission path
only; no automatic answer, handoff acknowledgement, worker wake, completion,
or GBrain mutation occurs until Tony submits through the verified TODO answer
contract.
V0.0.194 derives `summary.next_action` from the verified Tony-owned Action
queue instead of generic count-based copy. When both a waiting Agent question
and a missing-owner repair are present, the line names the exact question and
the recommended Codex owner assignment, for example answering the Toddy
Family/Toddy question and assigning the Entrepreneurship Goal to Timmy with
the lowest-load recommendation. This remains operator guidance only; it is not
an answer, owner assignment, dispatcher wake, handoff acknowledgement,
completion receipt, or GBrain mutation.
V0.0.195 adds an editable answer template affordance to Tony-owned
`answer_question` Action queue entries. The backend includes an
`answer_template` draft with Scope categories, Desired outcomes, Constraints,
First action, and Notes lines, and the Agents panel renders `Insert answer
template` beside the inline answer textarea. Activating the button fills and
focuses the textarea only; it does not submit an answer, acknowledge a
handoff, wake a worker, complete a task, assign ownership, or mutate GBrain.
V0.0.196 changes that draft from placeholder choices to a concrete editable
approval template: Scope categories accepted, Desired outcomes accepted,
Constraints accepted, First action approved, and Notes to keep the work
bounded to the stated scope, outcomes, constraints, and first action. The
template remains local text preparation only until Tony reviews and submits
through the verified TODO answer contract.
V0.0.197 surfaces the same Tony-owned Goal execution Action queue in Inbox as
an expanded `Goal execution actions` Needs Attention section. It reuses the
same explicit answer/template and Codex-only owner-assignment controls already
rendered in Agents, so Inbox becomes the central triage surface without adding
automatic answer, assignment, handoff acknowledgement, worker wake, completion,
or GBrain mutation behavior. This stops the current broad Goal-execution
improvement pass: V0.0.195 added editable answer templates, V0.0.196 made
them concrete, and V0.0.197 moved the resulting actions into Inbox.
V0.0.198 adds an Inbox-only `Run recommended unblock plan` control when the
verified Action queue contains both a concrete answer draft and a recommended
Codex owner assignment. The plan sequences the existing TODO answer POST, then
the recommended `default-goals` assignment POST, from one explicit reviewed
click. It is not automatic, does not appear in Agents, is suppressed without a
concrete template or recommended Codex owner, and must not be treated as a new
dispatcher mutation path. Live readback now has two answerable questions plus
one missing-owner assignment, so the next product slice is to expand the
recommended plan beyond the first answerable question.
V0.0.199 adds private-input handling for waiting questions that ask for
credentials, tokens, API keys, passwords, secrets, or similar private values.
Those `answer_question` actions are labeled `Private input required`, receive
`private_input_required=true`, do not receive synthetic `answer_template`
drafts, do not render inline answer/template controls, and are excluded from
recommended unblock plans. Safe answer questions with concrete templates and a
recommended Codex owner assignment still use the explicit reviewed plan; the
dispatcher must never fabricate or prefill private credential answers.
V0.0.200 includes private-input blockers in `summary.next_action` instead of
letting the safe answer/owner plan hide them. When the queue has a safe
answer, a private answer, and owner assignment, the next-action text names all
three: answer the safe question, provide private input for the private
question, and assign the recommended owner. Inbox also renders
`summary.next_action` in `Goal execution actions` after the MC200-001 repair.
Private rows remain no-template, no-form, and no-plan.
V0.0.201 tightens that combined next-action copy by separating the ordinary
answer, private-input blocker, and owner-assignment clauses with semicolons.
Agents and Inbox should never render awkward `. and assign` or `?. and`
grammar for this state. The grammar fix is display-only: private input remains
non-autofilled and excluded from recommended-plan writes, while the safe
answer plus recommended Timmy owner plan still uses the same explicit
reviewed controls.
V0.0.202 groups repeated private Goal blockers before rendering Action queue
rows, so repeated private credential prompts for the same Agent question do
not crowd owner-assignment controls out of Agents or Inbox. The grouped
private action carries `blocked_goal_count` and `related_questions`; it
remains no-form, no-template, no synthetic secret, and no recommended-plan
write. V0.0.203 repairs the live shape by grouping on same Agent plus same
question even when the task detail payload differs, and strips the remaining
`.; assign` punctuation before owner-assignment next-action clauses. Live
V0.0.203 readback showed one Tammy private action with
`blocked_goal_count=3`, owner assignment still present, and no
`. and assign`, `?. and`, or `.; assign` grammar.
V0.0.204 routes Artifact publisher identity mismatch blockers out of Tony's
private-input lane and into system-owned
`repair_artifact_publisher_identity` Action queue rows. These rows are for
dashboard Artifact publisher credential/identity repair, including
`artifact_identity_mismatch`, and render as `System action required` /
`Repair Artifact publisher identity` with `blocked_goal_count`. They are not
Tony `answer_question` rows, do not render answer/template controls, and are
excluded from recommended-plan writes. The safe business question and
missing-owner assignment remain visible, and the recommended unblock plan
still writes only the safe TODO answer followed by recommended Timmy owner
assignment.
V0.0.205/V0.0.206 reconcile terminal handoff status repairs for completed
Agent handoff tasks that still have stale `ready_for_agent` handoff
frontmatter. The status repair path may PATCH completed/status through the
canonical endpoint even when the pre-mutation task snapshot is invalid, then
wake Goal execution after the canonical repair. This is not a broad bypass:
completion still depends on terminal handoff repair plus exact
`produced_for` Artifact readback. The restored Tammy Artifact publisher
blocker tasks for Faith, Finance, and Career read back completed with no
handoff or blockers and exactly one Artifact each; Timmy and Toddy remain
non-local workers.
The V0.0.206 pause handoff at
`docs/handoffs/2026-08-26-goal-execution-overhaul-pause.md` is now historical:
V0.0.207 shipped the scheduler-ordering repair from current `origin/main`, not
from an unapplied docs task. V0.0.207 prioritizes active Goal-derived tasks
with suppressed handoff release plus exact `produced_for` Artifact readback as
terminal completion candidates before unrelated in-flight work. The Civic
task `tasks/106db451-137a-5094-af72-7de3d9332a87` completed with Artifact
`artifacts/b95ad28a-eb6f-4b6f-b3a6-9e460642623a`, released Timmy WIP
capacity, and let Entrepreneurship advance to completed/recently_completed.
V0.0.208 adds explicit `goal_execution` `read_state.status=loading` for cold
or long scheduler reads; UI should render `Reading Goal execution…` instead
of blank/null first-read waiting.
V0.0.209 adds Inbox `Submit recommended answer` for the answer-only Goal
unblock case: when the queue contains exactly a safe answerable question with
a concrete template and no recommended owner/system/private work, one reviewed
click sends exactly one TODO answer POST through the existing answer contract.
V0.0.210 keeps combined recommended-plan writes contiguous by sending the
reviewed answer POST and then the recommended Codex owner `default-goals` POST
before any Goal/Agent refresh. V0.0.211 makes that owner step tolerate sparse
Agent profile hydration by trusting the verified recommended Codex owner
candidate already carried in `summary.action_queue`, while still rejecting
OpenClaw owners. If live readback has `action_queue=[]`, render no recommended
write controls and expect live POST count zero. The V0.0.209 live Family/Toddy
answer/owner change came from a faulty QA interception harness, not from
automatic product execution.
Live readback showed the auto canary completed Finance/Tammy task
`tasks/cc655813-1968-5264-a5ad-454199c1b3cb` with Artifact
`artifacts/9362d402-0f7c-4d65-9222-a8c140f1d9d3`, then Career/Tammy task
`tasks/53264f17-e5d5-5b5d-ad36-af1eadc1a770` with Artifact
`artifacts/fbffd8c1-b04e-420f-8db3-14be7a2b7f8f`. Post-deploy
`/api/goal-execution` surfaces Career as the newest `recently_completed`,
Finance as `recently_completed`, Family/Toddy separately as
`waiting_for_tony`, and Toddy Health separately as
`handoff_worker_unavailable`. Do not document `auto` as unlimited multi-Agent
automation; it is a private canary target under the same one-task safety
boundary and exact-readback gates.

V0.0.167 shipped in commit `5a3a51c81196cfcfdcbce3722802b90e58271d25`
(`Prioritize actionable Goal execution blockers`). Independent QA PASS is at
`artifacts/qa/v0.0.167-independent/gate-report.md` with frozen aggregate
`8b44466616156a9491f18db25da846641ecd70bb795f8fb0e5a0d34525a97df6`, including
desktop `1440x1000` and genuine mobile `390x844`. Developer verification
reported focused `126` OK and full regression `1371` OK with `5` skipped.
Post-deploy `/api/goal-execution` readback reported
`public_reason=waiting_for_tony`, `decision_count=13`, and `last_error=null`.

V0.0.163 exposes open handoff question TODO text in waiting-for-Tony Goal
execution surfaces. Full Goal execution rows and compact Agent cards now show
the exact canonical question for Goal-derived work waiting on Tony. For the
current Family/Toddy blocker, the copy is exactly `Answer: Which family-care
scope, outcomes, constraints, and first action should Toddy use next?`, backed
by task `tasks/561640dd-8e34-43e1-a03e-e3f3f270033d` and question TODO
`todos/99b64fec-aebe-57de-bf79-cc9d640a2db2`. The frontend task lookup also
merges richer Agent-work projections with same-slug snapshot rows, so a thin
cached row cannot hide the verified handoff or TODO context. Preserve the
state as `Blocked` / `waiting_for_tony`; the visible question is the next
owner prompt for Tony, not permission to create replacement work or mark the
task complete.

V0.0.168/V0.0.169 populate the selected waiting-for-Tony Goal task context in
Goal execution readback. The headline task now carries `slug`, `title`,
`status`, and `agent_slug`, and the Agents cold-load UI uses
`goalExecution.last_run.task` before the separate Agent Work cache has
reconciled. Current live readback for Family/Toddy shows task
`tasks/561640dd-8e34-43e1-a03e-e3f3f270033d`, title `Prepare family-care goal
map and weekly review brief`, status `blocked`, and agent `agents/toddy`.
This improves immediate linking/rendering only; it does not answer the Tony
question, unblock the task, or imply local Toddy worker availability.

V0.0.169 shipped in commit `066bca00433deb313b79b2383b0156677c38c2e6`
(`Populate waiting Goal task context`). Independent QA PASS is at
`artifacts/qa/v0.0.169-independent/gate-report.md` with structured results at
`artifacts/qa/v0.0.169-independent/gate-results.json`, including desktop
`1440x1000` and genuine mobile `390x844`. Developer verification reported
focused `128` OK, full regression `1372` OK with `5` skipped, and JS syntax
OK. Dashboard-managed `/api/health` and `/api/releases` read back `V0.0.169`.
The local worker boundary was reaffirmed: local configs are only
`agents/tammy` and `agents/tammy-oc`; Timmy/Toddy workers remain remote-host
responsibilities.

V0.0.164 recovers expired owned execution claims during authenticated
dispatcher polling. The verified release commit is
`f9e9e424b6a57df0dc8fc2d5873912aca88efadf`, dashboard-managed health and
releases read back `V0.0.164`, full regression reported `1361` OK with `5`
skipped, and independent QA PASS is recorded at
`artifacts/qa/v0.0.164-independent/gate-report.md`. QA confirmed the local
supervisor config contains exactly `agents/tammy` and `agents/tammy-oc` and no
local Timmy/Toddy worker. Preserve that host-local ownership boundary when
remediating stale queued handoffs: Timmy/Toddy recovery belongs on their own
host machines, while this Mac's local dispatcher remains Tammy/Tammy-OC only.

### Read-only remote worker verification

Use the version-controlled verifier before changing any host-local worker
state. It performs only safe configuration/readback checks: private dispatcher
config parsing, authenticated `POST /api/handoffs/preflight`, optional Git HEAD
readback, and optional LaunchAgent presence. It never claims, wakes,
acknowledges, or mutates a handoff, and its JSON output redacts tokens,
registration IDs, and fixed thread IDs.

Example for the Timmy host after fast-forwarding its clean checkout:

```bash
python3 scripts/verify_handoff_worker_runtime.py \
  --config "$HOME/Library/Application Support/GTasks/handoff-dispatcher.json" \
  --expected-agent agents/timmy \
  --expected-commit <released-gtasks-commit> \
  --repo "$HOME/gtasks" \
  --launch-label com.tony.gtasks-handoff-dispatcher
```

Expected success includes `ok: true`, `preflight_verified: true`, route
`hosts/timmy`, the exact released `repo_head`, and `launch_loaded: true`.
For Toddy, run the same verifier on the Toddy host with `--expected-agent
agents/toddy`; do not install or run a Toddy worker on this Mac to bypass an
unreachable Toddy host.

Post-release verifier commit
`f5a2aa77d44561a9d7279a185c184388759945ad` added
`scripts/verify_handoff_worker_runtime.py` and
`tests/test_handoff_worker_runtime_verifier.py`. Reported verification: full
regression `1364` OK with `5` skipped before commit; focused verifier/release
tests `84` OK; Timmy's own host was fast-forwarded and the verifier returned
`ok: true` for `agents/timmy`, route `hosts/timmy`, a loaded LaunchAgent label,
and repo HEAD exactly
`f5a2aa77d44561a9d7279a185c184388759945ad`. Toddy remains blocked because its
host/SSH/control plane is unreachable; do not document Toddy as recovered until
the same read-only verifier passes on the Toddy host.

For the current Codex worker fleet, the non-secret inventory is
`config/handoff-dispatcher/remote-workers.json`:

```bash
python3 scripts/verify_handoff_worker_fleet.py \
  --inventory config/handoff-dispatcher/remote-workers.json
```

This reports each remote host independently. A healthy Timmy result with
`ok: true` and a Toddy result such as `issues: ["ssh_unreachable"]` means the
Timmy route is verified and the Toddy route needs host access or SSH repair;
it is not permission to run Toddy locally.

Fleet verifier commit `d7622b7272df3c8979d1db8e6b0c7b396c7a093c` added the
non-secret inventory `config/handoff-dispatcher/remote-workers.json`, fleet
CLI `scripts/verify_handoff_worker_fleet.py`, and
`tests/test_handoff_worker_fleet_verifier.py`. Reported validation: full
regression `1367` OK with `5` skipped, and focused verifier/release tests `87`
OK. Final fleet evidence was summary `ok=1 failed=1`: Timmy returned
`ok: true`, route `hosts/timmy`, preflight verified, launch loaded, repo head
exactly `d7622b7`; Toddy returned `ok: false` with issue `ssh_unreachable` for
`toddy@100.117.212.20`. Keep this distinction explicit: Timmy is verified,
Toddy is host-access blocked, and local Toddy worker installation remains
forbidden.

Fleet verifier improvement commit
`8d4f31b458286ac9750b4b1e3a9f1b375189ff96` changed omitted
`--expected-commit` behavior: the fleet CLI now resolves this checkout's local
HEAD and verifies each remote worker against that exact commit, instead of
letting each remote validate against its own potentially stale repo HEAD. Use
an explicit `--expected-commit` only when intentionally checking a different
reviewed commit. Reported verification: focused runtime/fleet verifier tests
`7` OK; Timmy host `toddy@100.100.126.85` was fast-forwarded to
`8d4f31b458286ac9750b4b1e3a9f1b375189ff96`, LaunchAgent restarted, and runtime
verifier PASS. The fleet verifier now reports Timmy OK against current HEAD and
Toddy still `ssh_unreachable` at `toddy@100.117.212.20`; do not document Toddy
as recovered or install a local Toddy worker.

Fleet verifier diagnostic commit
`6984f24c1fe330aca68fd95adc0a80dbcc9b4428` added safe Tailscale diagnostics
to SSH failure reports. The fleet verifier may now attach peer metadata and
specific issues such as `tailscale_key_expired` or `tailscale_peer_offline`
without exposing secrets. Reported verification: focused runtime/fleet
verifier tests `8` OK; Timmy host `toddy@100.100.126.85` was fast-forwarded to
`6984f24c1fe330aca68fd95adc0a80dbcc9b4428` and runtime verifier PASS. Current
fleet readback reports Timmy OK and Toddy blocked with `ssh_unreachable` plus
`tailscale_key_expired`; Toddy peer metadata is `Toddy's Mac Mini-1`, DNS
`toddys-mac-mini-1.taildb46a7.ts.net.`, and IP `100.117.212.20`. This remains
a host/Tailscale remediation item for Toddy, not a recovered worker and not
permission to install Toddy locally.

The V0.0.136/V0.0.137 Finance canary for
`goals/840b3122-b299-5991-96be-30364c7f2e12` created, activated, and
completed `tasks/3d54d11c-db8e-59bf-8039-e050fa763dc9` for `agents/tammy`.
The task retained `member_of -> collections/tammys-tasks`,
`assigned_to -> agents/tammy`, `advances_goal -> goals/840b3122-b299-5991-96be-30364c7f2e12`,
and project membership to `projects/fe9b0d37-d756-42ef-b8f1-98217f79eae7`.
Tammy published Artifact `artifacts/b6acc5bc-4af2-42f2-a829-8c97e3dd0838`,
with `produced_for` pointing to the same task and `supports_project` /
`supports_goal` pointing to the Finance project and Goal. The task-scoped
handoff history reached `completed` and `execution_claim_released`, and Goal
execution mode was returned to `shadow`.

V0.0.137 repaired one recovery edge for local Codex handoffs by accepting a
server-completed handoff during manual same-thread recovery. V0.0.142 through
V0.0.144 supersede the earlier active-writer handling: a fixed Codex thread
with an active writer is retryable local backpressure with bounded 300-second
backoff, not terminal delivery and not authority to create duplicate Codex
threads. The automatic path still requires the normal claim, wake
authorization, `received`, `execution_started`, acknowledgement, and terminal
release chain.

## Rollback

Rollback restores the previous verified release, not a partially reviewed
candidate:

1. Stop the three local Dispatcher LaunchAgents so no new wake can occur.
2. Restart dashboard-managed `gtasks` from the previous verified release and
   restore its matching command/readback contract.
3. Preserve the handoff SQLite database and append-only audit evidence; do not
   delete or edit delivery history to make rollback appear clean.
4. Restore the prior private credential file only from its verified backup,
   then read back mode `0600`, hashes, and identity count.
5. Verify health/version, read-only Task Timeline and Agents Handoff History, and zero
   active canary work before considering a later retry.

A verified canonical GBrain mutation is not rolled back because delivery or
deployment failed. Repair delivery through retry, Guardian, or an explicit
correction event.

## Release and canary boundary

Automated tests and independent desktop/mobile UI QA use synthetic fixtures
and perform zero live Agent wakes. Only after QA PASS, commit, push,
dashboard-managed deployment, exact `/api/health` readback for the released
version, and private credential readback may host installation or live canary
work proceed.

Install and verify Tammy, Timmy, and Toddy separately; each must see only its
own identity. V0.0.76 permitted one bounded Tammy handoff canary after all
three installs read back. V0.0.135 verified a controlled Codex-only Faith Goal
execution canary for Tammy; V0.0.136/V0.0.137 verified a controlled
Codex-only Finance Goal execution canary for Tammy. Each canary returned
private runtime Goal execution mode to `shadow` afterward. Do not generalize
these canaries to Timmy, Toddy, OpenClaw, or recurring autonomous Goal
execution without a new verified release and explicit authorization.
