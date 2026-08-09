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

Never clear local claim state merely because a request was sent. Clear or
replace it only after a verified retry, terminal, or rotated recovery response.
Never manually delete a gate, result, `wake_launches` row, active
`execution_starts` row, or `abandoned_execution_starts` row to manufacture a
retry. Only the transactional unused-start CAS may archive and remove the
active row. An operator investigating `recovery_required` must
reconcile the fixed target session and canonical Task first, then use the
recorded checkpoint/hand-back evidence rather than launching the target again.

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
dashboard-managed deployment, exact `/api/health` V0.0.76 readback, and private
credential readback may the three host installations begin.

Install and verify Tammy, Timmy, and Toddy separately; each must see only its
own identity. V0.0.76 permits one bounded Tammy canary after all three installs
read back. Do not canary Timmy or Toddy in V0.0.76. The Tammy canary must prove
one claim, one resume of the already-approved fixed thread, received and active
acknowledgements, one stable correlation id, and zero cross-identity visibility.
