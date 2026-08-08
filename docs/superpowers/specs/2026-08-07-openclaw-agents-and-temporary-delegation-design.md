# OpenClaw Agents and Temporary Delegation Design

Date: 2026-08-07
Status: approved conversational design; implementation requires a separate reviewed plan

## Objective

Add three first-class OpenClaw Agents to Mission Control:

- `agents/tammy-oc` on host Tammy;
- `agents/timmy-oc` on host Timmy;
- `agents/toddy-oc` on host Toddy.

They coexist with Tammy, Timmy, and Toddy. They are not fallback process names,
aliases, or impersonation identities. Each OpenClaw Agent can own tasks, publish
Artifacts, and later receive explicit Goal ownership. Initially they have no
default Goals.

Mission Control must also let Tony explicitly authorize a corresponding
OpenClaw Agent to accept additional work from a temporarily unavailable Codex
Agent for a user-selected period. Temporary delegation never changes permanent
task ownership and never overrides the OpenClaw Agent's own work.

## Non-goals

- Do not replace or rename Tammy, Timmy, or Toddy.
- Do not infer Goal ownership from the paired Codex Agent.
- Do not create a new OpenClaw session per task or handoff.
- Do not automatically delegate solely because a quota, network, login, or
  runtime error is observed.
- Do not let an OpenClaw Agent impersonate a Codex Agent in Tasks, Timeline,
  receipts, or Artifacts.
- Do not give OpenClaw direct raw-write access to GBrain.
- Do not make delegated work higher priority than the OpenClaw Agent's owned
  work.

## Chosen architecture

Extend the existing event-driven Handoff Dispatcher with an OpenClaw execution
adapter. Mission Control remains the central canonical classifier, durable
outbox, lease/claim service, audit source, and authenticated mutation boundary.
Each host runs one host-local Dispatcher supervisor containing two isolated
single-identity workers: one Codex worker and its paired OpenClaw worker. A
worker can claim only its own identity and cannot read or reuse its sibling's
credential. The supervisor does not learn the identities, hosts, or credentials
installed elsewhere.

The local execution adapters are distinct:

- the Codex adapter resumes one existing fixed Codex task;
- the OpenClaw adapter sends work to one existing fixed OpenClaw session.

Neither adapter may create, fork, replace, or guess a task/session. Runtime
credentials and fixed task/session identifiers remain outside Git in private
mode-`0600` host configuration.

## Canonical identities and collections

Create three canonical Agent profiles:

| Agent | Runtime | Host route | Task collection | Artifact collection |
| --- | --- | --- | --- | --- |
| `agents/tammy-oc` | `openclaw` | `hosts/tammy` | `collections/tammy-oc-tasks` | `collections/tammy-oc-artifacts` |
| `agents/timmy-oc` | `openclaw` | `hosts/timmy` | `collections/timmy-oc-tasks` | `collections/timmy-oc-artifacts` |
| `agents/toddy-oc` | `openclaw` | `hosts/toddy` | `collections/toddy-oc-tasks` | `collections/toddy-oc-artifacts` |

Each profile contains a public runtime type, route, supported capability
summary, and only hashed registration/session projections. Raw registration
IDs, bearer tokens, and session keys never enter GBrain, Git, API responses, or
Timeline events.

No `default_agent_for` relationship is created during initial provisioning.
Future Goal ownership is added only through an explicit, verified canonical
relationship mutation requested by Tony.

## Ownership and execution relationships

Mission Control keeps these meanings separate:

- `assigned_to`: the single permanent task owner;
- `delegated_executor`: the current temporary executor, when one exists;
- `default_agent_for`: explicit Goal-level responsibility;
- `serves_goal`: the Goal served by a task;
- `created_by`: the actual Agent that produced an Artifact.

Temporary delegation does not rewrite `assigned_to`, duplicate the task into a
second collection, or attribute delegated work to the OpenClaw Agent's Goals.
An Artifact produced during delegation uses the OpenClaw identity in
`created_by` and retains a typed relationship to the original task.

## OpenClaw fixed-session contract

Each OpenClaw Agent uses one fixed, durable session for normal and delegated
work. Its private worker configuration contains exactly one Agent identity, one
route, one registration, one credential, and one session key. The host
supervisor loads the Codex and OpenClaw worker config paths but does not merge
their credentials or execution state.

Before installation, implementation must inspect the installed OpenClaw
version and validate the host-local invocation contract. The adapter accepts
only structured completion output from the configured fixed session. Warning
or log lines preceding structured output are tolerated only through a bounded,
tested parser. An absent session, identity mismatch, malformed response,
timeout, or unsupported CLI contract fails closed; it never causes session
creation or a switch to another identity.

## Base OpenClaw authority

OpenClaw Agents may:

- read their own canonical Tasks, TODOs, permitted Goal context, documentation,
  and Handoff history;
- acknowledge work and update status, TODOs, comments, blockers, and completion
  through authenticated Mission Control APIs;
- publish their own Artifacts through the identity-enforcing publication API;
- ask one precise question and mark the task `blocked` when required
  information or authority is missing.

They may not:

- create or reassign tasks unless Tony separately authorizes that capability;
- modify Goal ownership or the Goal graph;
- act as another Agent identity;
- send external messages, submit applications, access financial accounts,
  transfer funds, trade, or expand task scope without separate task-specific
  authority;
- extend or create their own delegation authorization.

## Temporary delegation lease

Temporary takeover is represented by a canonical
`agent_delegation_lease` record in
`collections/mission-control-agent-delegations`. It records:

- Tony as authorizing actor;
- exactly one source Codex Agent;
- the fixed paired OpenClaw Agent;
- start instant, end instant, display timezone, and creation instant;
- allowed operations and explicit exclusions;
- lifecycle state: `scheduled`, `active`, `completed`, `expired`, or `revoked`;
- immutable creation, extension, early-end, and expiry receipts.

The UI supplies common duration shortcuts and an explicit date/time picker.
The default permitted range is 15 minutes through 7 days. Times are stored as
UTC instants and displayed in `America/Los_Angeles`. Extending a lease creates
a new Tony-authorized receipt; an Agent cannot renew it. Longer continuity
requires a separate permanent reassignment design rather than an indefinitely
extended temporary lease.

A runtime failure may cause Mission Control to recommend delegation, but only
Tony's explicit confirmation activates or schedules the lease.

## Delegated work eligibility and priority

An OpenClaw Agent selects work in this order:

1. its currently executing owned task;
2. its assigned and executable owned tasks;
3. authorized work serving its own Goals;
4. eligible additional delegated work;
5. proactive recommendations and long-term planning.

Delegation therefore adds capacity only when the OpenClaw Agent has no higher
priority executable owned work. Zero delegated claims during a busy lease is a
valid outcome, not a failure.

The first version does not interrupt a task already actively executing in
Codex. Eligible delegated work is limited to not-started, waiting-to-resume, or
newly actionable work for the paired Codex identity. At lease expiry or
revocation, the OpenClaw Agent stops claiming new delegated work. If a safe
step is already executing, it may finish that bounded step, commit its verified
Mission Control mutation, write a checkpoint, and then return the task.

## Claim and concurrency contract

Before delivery, the central Dispatcher atomically verifies:

- the permanent owner is the lease's source Codex Agent;
- the lease is active for the current instant;
- the requested executor is the fixed paired OpenClaw identity;
- the task is eligible and not already protected by an execution claim;
- the requested operation is inside the lease and task authority;
- the outbox and idempotency input still match canonical readback.

It then creates a task-scoped execution claim tied to the delegation lease and
correlation ID. Codex and OpenClaw may not concurrently execute the same task.
Restart recovery reuses the same claim and idempotency key rather than
redelivering. A stale or conflicting claim is visible as system attention and
requires expiry reconciliation or explicit operator repair; it is never
silently overwritten.

## Delivery, acknowledgement, and completion

For normal OpenClaw-owned work, `assigned_to` routes directly to the OpenClaw
fixed session. For delegated work, the active delegation and claim route the
handoff without changing permanent ownership.

The fixed session reports one of the existing evidence states:

- `received`;
- `actively_executing`;
- `still_blocked` with one reason and one next action;
- `completed` with canonical mutation and Artifact readback receipts.

All delivery and execution events retain the source task, permanent owner,
actual executor, delegation lease, correlation ID, idempotency key, timestamp,
and privacy-safe summary. The Task Timeline is the primary traceability view;
Agents Handoff History is a cross-task projection of the same immutable event
source.

## Expiry, revocation, and hand-back

Expiry, normal early completion, and revocation prevent new claims immediately.
`completed` means Tony ended the delegation normally and all active claims
reached a verified checkpoint; `revoked` is an immediate authority withdrawal
that may require forced hand-back reconciliation. Every claimed but
unfinished delegated task receives a structured hand-back checkpoint:

- completed work;
- current canonical status;
- remaining TODOs;
- exact next action;
- unresolved blocker or risk;
- last verified receipt.

New work resumes routing to the permanent Codex owner. Recovery of Codex during
an active lease does not preempt delegated work or cancel the lease; Tony may
end it early. OpenClaw-owned work, Goals, profile, and history are unaffected
by delegation expiry.

## Failure handling

- OpenClaw unavailable: bounded exponential backoff, then dead letter with one
  visible system-attention item; never create another session.
- Host Dispatcher restart: recover lease and claim from durable state and
  reconcile acknowledgement before retrying.
- Identity, host, route, registration, or session mismatch: fail closed before
  delivery.
- Mission Control or GBrain write unavailable: retain the last verified state,
  perform no raw fallback write, and report the precise blocker.
- Lease expires during a write: allow only the already-authorized atomic write
  and its readback, then checkpoint and stop.
- Insufficient answer or authority: keep the same task blocked and ask one
  precise question; do not create another task or session.

## Mission Control UI

The Agents surface displays six first-class cards. OpenClaw cards are labeled
`OpenClaw` and show host, fixed-session health, owned work, additional delegated
work, explicit Goals, latest verified completion, and current blocker. With no
Goal relationship, the card shows `No goals assigned yet`.

A Codex Agent card offers `Temporarily delegate work`. The confirmation flow
shows the fixed paired OpenClaw Agent, start time, shortcut or custom end time,
authority summary, exclusions, timezone, and an explicit statement that
permanent ownership is unchanged.

While active, both cards show the same lease, end time, remaining duration,
`End Early`, and `Extend` controls. Owned and delegated work counts remain
separate. Task details show permanent owner and temporary executor separately,
and mark delegated entries `Additional delegated work`.

All controls are keyboard accessible, confirmation-bound, responsive, and use
the existing progressive-disclosure pattern. Mobile uses a contained 390x844
sheet without horizontal overflow.

## Version-controlled and private configuration

The GTasks repository owns:

- parameterized Agent profile, fixed-session, Dispatcher, and delegation
  templates;
- the OpenClaw adapter and response parser;
- schema, authorization, state-machine, and drift-verification code;
- tests, README, system overview, and runbooks.

Each host owns only its rendered private runtime configuration and secrets.
Installation uses supported configuration/provisioning tools and exact
readback. Manual edits to installed configuration are drift, not source of
truth.

## Test and rollout gates

Automated coverage must include:

- identity, collection, Goal, and Artifact isolation;
- lease scheduling, timezone/DST boundaries, extension, expiry, and revocation;
- owned-work priority and valid zero delegated claims;
- atomic claims, concurrent Codex/OpenClaw exclusion, retries, and restart
  recovery;
- fixed-session enforcement, mixed log/JSON parsing, timeout, malformed output,
  and missing session behavior;
- permission denial, identity mismatch, unauthorized lease creation/extension,
  and raw-write refusal;
- checkpoint and hand-back behavior;
- Timeline and Agents Handoff History correlation and redaction;
- desktop and mobile UI states.

Rollout is sequential:

1. synthetic no-wake/no-write tests;
2. one isolated Tammy-OC QA fixture and fixed-session canary;
3. independent UI/UX PASS at 1440x1000 and genuine 390x844;
4. verified commit, push, dashboard-managed deployment, health/readback, and
   rollback proof;
5. Timmy-OC canary and activation;
6. Toddy-OC canary and activation.

A failure disables only the affected OpenClaw route. Existing Codex Agents,
tasks, outbox evidence, and canonical state continue operating.

## Acceptance summary

The design succeeds when Mission Control displays six independent Agents; each
OpenClaw Agent reliably uses one fixed session and its own identity, tasks,
Artifacts, and optional future Goals; Tony can create a bounded, scheduled or
immediate delegation for a user-selected duration; owned OpenClaw work always
outranks delegated work; no task is executed concurrently or impersonated; and
all authorization, delivery, execution, expiry, and hand-back evidence is
traceable from the original Task Timeline.
