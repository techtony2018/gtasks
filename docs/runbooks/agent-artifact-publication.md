# Mission Control Agent Artifact Publication

GBrain is the only canonical store for Agent Artifacts. Mission Control is a
schema-enforcing publication and read interface; it is not an Artifact database,
file index, or synchronization service.

## Eligibility: all four answers must be yes

1. Was the output produced by an approved canonical Task assigned to this Agent?
2. Is it a durable document, image, PDF, file, or Git commit intended for Tony's
   later review or reuse?
3. Is it free of secrets, credentials, private keys, browser profiles, raw
   authentication material, and unredacted private runtime data?
4. Is it materially different from an existing Artifact with the same
   idempotency key?

If any answer is no, do not publish an Artifact. Routine heartbeat reports and raw logs,
temporary screenshots, dependency caches, generated build directories,
acknowledgements, and unchanged status reports are not Artifacts.

## Canonical publication contract

- Use an immutable opaque UUIDv4 slug under `artifacts/`.
- Set compiled/frontmatter `type: artifact`, a concise mutable title,
  `artifact_kind`, zoned `created_at`, and the approved content fields.
- Derive `created_by` from the fixed Agent identity. Never accept a display name
  as identity.
- Set `produced_for` to the same approved canonical Task slug. Do not guess or
  substitute a different Task.
- Add exactly one typed `member_of` relationship to this Agent's Artifact child
  collection; never add direct root membership.
- Add one typed `created_by` relationship to the fixed Agent and one typed `produced_for`
  relationship to the Task.
- Preserve explicit provenance with optional typed `supports_project`,
  `supports_goal`, and `supersedes` links. Never infer those targets.
- Use only previously verified GBrain-served `/media/...` attachment references.
  Source code stays in Git and uses an HTTPS commit URL.
- Use a stable, bounded idempotency key derived from Agent, Task, deliverable,
  and revision. Reusing a key with different content is a conflict, not an
  overwrite.

Publish through the authenticated local `POST /api/artifacts` boundary. The
service derives the executing Agent from its installed bearer credential and
rejects any payload whose `created_by` or Artifact collection differs. The
error is intentionally generic and never enumerates other identities. A task
owner, display name, or request about another Agent is not execution identity.
Agents must not bypass this boundary with raw GBrain writes.

If the page, attachment reference, collection membership, or any required link
cannot be verified, retain the same canonical Task with `status: blocked`,
record the concrete publication failure and next action, and do not call the
deliverable or Task complete.

## Fixed heartbeat integration

Apply this rule only inside Tammy, Timmy, and Toddy's existing fixed daytime and
nighttime heartbeat protocols. Preserve each schedule and target thread ID. Do not create a new Codex task,
replacement agent task, or per-run worker for
Artifact publication.

Shared heartbeat rule:

> When authorized work produces a durable user-facing document, image, PDF,
> file, or Git commit, publish exactly one canonical Agent Artifact using the
> installed Mission Control Artifact contract. Use this Agent's Artifact
> collection, link it to the same canonical Task, preserve explicit Project and
> Goal provenance, and prove the page plus every typed link by readback before
> calling the deliverable complete. Do not capture routine scan output, logs,
> caches, secrets, or unchanged reports.

## Version-controlled automation source

`config/agent-artifact-protocol` is the only source of truth for these
instructions. It contains one generic parameterized identity template, one
isolated source instance per installed Agent, separate daytime and nighttime
templates, and six checked-in rendered prompts. Each rendered prompt contains
only its own Agent identity, task collection, Artifact collection, and
fail-closed behavior. It contains no other Agent, host, address, credential,
token, chat ID, fixed task ID, or schedule.

Render or verify one tracked prompt:

```bash
python3 scripts/verify_agent_artifact_protocol.py tammy daytime --render
python3 scripts/verify_agent_artifact_protocol.py tammy daytime
```

An installed `automation.toml` is rendered state, never source. The verifier
can read it without changing it and can emit reviewed inputs for Codex's
supported `automation_update` boundary:

```bash
python3 scripts/verify_agent_artifact_protocol.py tammy daytime \
  --automation-file /path/to/automation.toml
python3 scripts/verify_agent_artifact_protocol.py tammy daytime \
  --automation-file /path/to/automation.toml --emit-update-input
```

Submit the emitted prompt with the automation tool, preserving the existing
schedule, status, and fixed target task. Never overwrite `automation.toml`
directly. Read the installed automation back and rerun the verifier; drift is a
release failure.

## Private publisher credentials

Publisher bearer tokens are host-private runtime state and never belong in Git
or any rendered prompt. Create one unique `0600` token file for each installed
identity, then provision only their hashes into Mission Control's private state:

```bash
python3 scripts/provision_artifact_publisher_credentials.py \
  --initialize-token-dir "$PRIVATE_TOKENS" \
  --output "$PRIVATE_STATE/artifact-publisher-credentials.json"
```

For separately installed host-private token files, provision an existing set:

```bash
python3 scripts/provision_artifact_publisher_credentials.py \
  --output "$PRIVATE_STATE/artifact-publisher-credentials.json" \
  --token-file agents/tammy="$PRIVATE_TOKENS/tammy.token" \
  --token-file agents/timmy="$PRIVATE_TOKENS/timmy.token" \
  --token-file agents/toddy="$PRIVATE_TOKENS/toddy.token"
```

The provisioner requires three distinct private tokens, writes only SHA-256
digests through an atomic `0600` file, and never prints token material. The
dashboard service loads that file at startup. A missing, malformed, shared, or
wrong-permission credential fails closed before publication is available.
