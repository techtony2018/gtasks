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
- A local timeout or nonzero Codex exit waits for the configured retry delay
  after recording the verified retry, preventing a tight claim/failure loop.
- A terminal delivery failure moves it to `dead_letter`. It remains visible
  in the same audit chain and is never silently requeued.
- Guardian requeues only an expired leased delivery or records a terminal
  dead letter according to the bounded retry policy. Guardian is fallback
  reconciliation, not the primary sender or a business-task executor.
- After a local restart, the Dispatcher persists recovery intent before the
  request, reconciles an authoritative stale generation, rotates capability,
  and resumes only after the rotated claim is durably saved.
- `queued` or `retrying` reconciliation clears stale host state before a new
  claim. `completed` or `dead_letter` reconciliation clears host state and
  stops without claiming replacement work.
- Repeated recovery reconciliation is bounded and the exhausted count is
  persisted, preventing a restart loop against stale state.

Never clear local claim state merely because a request was sent. Clear or
replace it only after a verified retry, terminal, or rotated recovery response.

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
