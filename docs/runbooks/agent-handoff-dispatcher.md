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
