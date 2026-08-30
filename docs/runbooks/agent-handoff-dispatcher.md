# Codex Agent handoff Dispatcher runbook

## Current contract

Mission Control has exactly three execution Agents:

| Agent | Canonical identity | Host route | Runtime transport |
| --- | --- | --- | --- |
| Tammy | `agents/tammy` | `hosts/tammy` | local |
| Timmy | `agents/timmy` | `hosts/timmy` | SSH to Timmy's Mac |
| Toddy | `agents/toddy` | `hosts/toddy` | SSH to Toddy's Mac |

Tammy, Timmy, and Toddy each run one singleton Codex Dispatcher. A Dispatcher owns exactly one
Agent identity, one private registration, and one existing fixed Codex task.
It may resume only that task:

```text
codex exec resume --skip-git-repo-check <fixed-thread-id> <prompt> --json
```

The Dispatcher must never create, fork, replace, or guess a Codex thread. Fixed
thread ids, registration ids, bearer tokens, and full prompts stay in private
host state. Repository files, UI payloads, and handoff events must not contain
those values.

The retired alternate-agent execution path is not part of the current system.
Historical release records and append-only handoff rows remain evidence only;
they are not runtime authority and must not be replayed as a current route.

## Source of truth and continuity

GBrain is canonical for Task status, TODO state, permanent assignment,
relationships, and lifecycle. Mission Control creates a handoff only after a
versioned canonical mutation receipt and exact post-write readback. Delivery
failure does not roll back an already verified GBrain mutation.

The SQLite handoff event table is the single delivery ledger. Task Timeline and
Agents Handoff History are read-only projections over the same append-only
handoff event table. Agent Work reads the same ledger. None of these projections
repairs or mutates GBrain.

Continuity uses one fence: the handoff lease plus the target host's durable
acknowledgement. A newly created handoff must not create a second execution
claim or a parallel ownership record. The assigned Codex Agent receives the
handoff directly through its exact registered route.

A handoff progresses through the existing durable states:

```text
queued -> received -> actively_executing -> completed
            \-> retrying -> dead_letter
                                  \-> blocked | failed | suppressed
```

`received` proves durable acceptance by the target machine. It does not prove
that Codex started. `actively_executing` requires the verified launch boundary.
Terminal acknowledgement must reconcile the same handoff and canonical Task;
it must not create replacement work or another Codex task.

Guardian may retry a bounded retryable delivery or move exhausted work to
`dead_letter`; neither state changes canonical ownership. Recovery does not
roll back an already verified canonical GBrain mutation.

Legacy nullable database columns and historical rows are retained so old audit
evidence stays readable. Any historical row that names a retired execution
route fails closed as `retired_execution_route`.

## Canonical Agent registration

Each Agent page has exactly one `handoff_dispatcher` projection:

```yaml
handoff_dispatcher:
  registration_sha256: <64-lowercase-hex SHA-256 of registration_id>
  route: hosts/<agent>
  verified: true
```

Accepted registrations are exact:

```yaml
# agents/tammy
registration_sha256: <tammy-sha256>
route: hosts/tammy
verified: true

# agents/timmy
registration_sha256: <timmy-sha256>
route: hosts/timmy
verified: true

# agents/toddy
registration_sha256: <toddy-sha256>
route: hosts/toddy
verified: true
```

Compute the digest from the private registration id as UTF-8 bytes with no
newline. Never put the raw registration id in GBrain. Read back each complete
Agent page and require the expected digest, `verified: true`, and three unique
routes before credentials, restart, installation, or a live handoff. The
readback must contain exactly three unique routes.

Use the supported whole-page CLI path and preserve every unrelated field:

```bash
gbrain get agents/tammy > "$PRIVATE_PROJECTION_DIR/agents-tammy.md"
gbrain put agents/tammy < "$PRIVATE_PROJECTION_DIR/agents-tammy.md"
gbrain get agents/tammy > "$PRIVATE_PROJECTION_DIR/agents-tammy.readback.md"
gbrain get agents/timmy > "$PRIVATE_PROJECTION_DIR/agents-timmy.md"
gbrain put agents/timmy < "$PRIVATE_PROJECTION_DIR/agents-timmy.md"
gbrain get agents/toddy > "$PRIVATE_PROJECTION_DIR/agents-toddy.md"
gbrain put agents/toddy < "$PRIVATE_PROJECTION_DIR/agents-toddy.md"
```

## Dashboard-managed central runtime

All Things Codex Dashboard owns Mission Control from
`/Users/tony/work/gtasks` at `http://127.0.0.1:4179/`. Its private credential
file is:

```text
/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/handoff-dispatcher-credentials.json
```

The file is a regular mode-`0600` file containing exactly the three Agent slugs
and their registration/token hashes. It never contains plaintext credentials
or fixed Codex task ids. Provision it from the three reviewed private identity
configs:

```bash
python3 scripts/provision_handoff_dispatcher_credentials.py \
  --identity-config /private/tammy/handoff-dispatcher.json \
  --identity-config /private/timmy/handoff-dispatcher.json \
  --identity-config /private/toddy/handoff-dispatcher.json \
  --output /Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/handoff-dispatcher-credentials.json
```

Require `identity_count: 3` without printing a secret. After a reviewed release,
restart only through All Things Codex Dashboard and verify:

```text
POST http://127.0.0.1:4188/api/services/gtasks/restart
GET  http://127.0.0.1:4179/api/health
GET  http://127.0.0.1:4179/api/releases
```

Health and releases must report the same expected Mission Control version. The
dashboard service readback must show the canonical checkout and expected
argument array; HTTP 200 alone is not deployment evidence.

## Per-machine singleton installation

Each machine keeps one private schema-version-1 config with exactly
`agent_slug`, `registration_id`, `fixed_thread_id`, `mission_control_url`, and
`token_file` in addition to `schema_version`.

Install the one canonical LaunchAgent from the verified release checkout:

```bash
/absolute/path/to/python3 scripts/install_local_handoff_dispatcher.py \
  --source-config /private/<agent>/handoff-dispatcher.json \
  --python-path /absolute/path/to/python3 \
  --module-root /absolute/path/to/gtasks \
  --runner-path /absolute/path/to/gtasks/gtasks/local_handoff_dispatcher.py \
  --codex-path /absolute/path/to/codex \
  --working-directory /absolute/path/to/agent-workspace
```

The installer owns only:

- `~/Library/Application Support/GTasks/handoff-dispatcher.json`
- `~/Library/LaunchAgents/com.tony.gtasks-handoff-dispatcher.plist`

The LaunchAgent label is exactly
`com.tony.gtasks-handoff-dispatcher`. The installer validates the private
identity, imports the exact module checkout, verifies `codex exec resume`, and
reads back the loaded argument array. It uses the shared private install mutex
at `~/Library/Application Support/GTasks/handoff-dispatcher/.install.lock`.
Never install a second worker identity on a machine to bypass a remote-host
failure.

Resolve and verify the compatible Python path independently on Tammy, Timmy,
and Toddy. The installer must not use `/usr/bin/python3`. The module checkout
and the pre-existing Agent thread's workspace are independent paths: the runner
must import from `--module-root`, while launchd `WorkingDirectory` remains the
Agent workspace. The loaded command is equivalent to:

```bash
/absolute/path/to/python3 -m gtasks.local_handoff_dispatcher \
  --config "$HOME/Library/Application Support/GTasks/handoff-dispatcher.json" \
  --codex-path /absolute/path/to/codex \
  --working-directory /absolute/path/to/agent-workspace
```

## Tailnet API boundary

Mission Control stays loopback-only at `http://127.0.0.1:4179/`. Tailnet Serve
exposes only `/api/handoffs`:

```bash
tailscale serve --bg --https=443 --set-path=/api/handoffs/ http://127.0.0.1:4179/api/handoffs/
tailscale serve status --json
```

The Tailnet origin is `https://tonys-macbook-pro.taildb46a7.ts.net`. Its root,
health, releases, handoff-events, and every non-handoff route must return
HTTP 404. Missing or invalid authorization on a claim must return HTTP 401. A
valid bearer with the intentionally incomplete `{}` body must return HTTP 422 with
`invalid_handoff_claim`, proving authenticated connectivity without claiming
work. Never configure Funnel.

## Audit retention and export

The append-only handoff ledger has a 90-day default retention. Export reviewed
evidence with `GET /api/handoff-events?export=1`; the bundle schema is
`handoff-audit-v1`. Exported rows may contain a privacy-safe `registration_ref`
but never bearer tokens, raw registration ids, fixed thread ids, or full
prompts. Export does not mutate, acknowledge, or retry a handoff.

## Three-machine fleet verification

The non-secret fleet inventory is
`config/handoff-dispatcher/remote-workers.json`. It is the only current
machine-routing roster. Run the read-only verifier from the release checkout:

```bash
python3 scripts/verify_handoff_worker_fleet.py \
  --inventory config/handoff-dispatcher/remote-workers.json \
  --expected-commit "$(git rev-parse HEAD)"
```

The verifier checks:

- exact Agent identity and `hosts/<agent>` route;
- authenticated no-side-effect handoff preflight;
- repository HEAD against the expected release commit;
- singleton LaunchAgent presence;
- no secrets in the inventory or output.

Tammy runs locally. Timmy and Toddy run only on their listed SSH machines. A
remote failure stays attributed to that machine; it is not permission to start
Timmy or Toddy locally. A release is fleet-complete only when all three results
return `ok: true` against the same expected commit.

## Release sequence

1. Finish code, tests, version metadata, and current documentation.
2. For UI changes, obtain independent PASS at desktop `1440x1000` and genuine
   mobile `390x844` before any commit.
3. Commit and push the reviewed aggregate.
4. Restart Mission Control through All Things Codex Dashboard and verify health,
   releases, UI, and affected APIs.
5. Fast-forward Timmy and Toddy to the exact release commit on their own
   machines, restart their singleton LaunchAgents, and run the fleet verifier.
6. Record canonical receipts only after all applicable readbacks pass.

## Rollback

Rollback restores the previous verified release without deleting evidence:

1. Stop only the affected singleton Dispatcher so it cannot claim new work.
2. Restore the previous verified code and matching private credential backup.
3. Restart Mission Control through All Things Codex Dashboard.
4. Preserve the handoff SQLite database and append-only events.
5. Verify health, releases, canonical Task state, Handoff History, and the
   three-machine fleet before resuming delivery.

A verified canonical GBrain mutation is not rolled back merely because
delivery failed. Repair delivery on the assigned Agent's own host and preserve
the same Task and handoff history.
