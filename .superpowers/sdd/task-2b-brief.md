# Task 2B: Complete activated OpenClaw Agent integration

## Worktree

`/Users/tony/.codex/worktrees/openclaw-agent-delegation/gtasks`

## Existing protocol

Memory Stargraph now exposes a reviewed, disabled-by-default NATS-CAS activation protocol. It returns a verified active projection with exact logical Agent slugs, stable task/Artifact collections, immutable generation metadata, manifest digest, and zero-Goal initial contract. Direct multi-call GBrain execute provisioning is forbidden.

## Required implementation

1. `list_agent_profiles()` and `get_agent_profile(slug)` resolve activated `*-oc` identities through the verified active projection. Existing Codex profile behavior remains unchanged and available during OC endpoint outage.
2. Returned OC profiles use exact logical slugs and stable logical work roots. The CAS-selected immutable generation metadata is authoritative for title/name, summary, runtime, chat URL, and all other profile presentation fields. The stable logical Agent is authoritative only for its mutable avatar and canonical `default_agent_for` Goal edges. Logical title/summary/chat fields never override generation presentation, and logical runtime remains immutable activation evidence whose alteration fails closed.
3. GTasks may mutate a stable logical OC Agent only through the supported avatar and default-Goal methods. Those methods may change only the avatar field or `default_agent_for` relationship respectively; they must preserve generation-owned presentation, logical identity fields, task/Artifact collection identity, and invariant `for_agent`/`part_of` links. Task create/read/edit/status/TODO operations use stable logical Agent and task-collection anchors. No GTasks path may write to generation staging slugs.
4. Artifact publication validates the authenticated OC identity, stable OC Artifact collection, and `created_by` logical Agent anchor. Cross-identity publication fails before mutation. Every non-null `delegation_ref` remains unsupported until a verified delegation-claim model exists and must fail before mutation.
5. Avatar/default-Goal/profile reads work for activated OC Agents. Unactivated OC identities are absent and cannot receive work.
6. Remove dead direct execute/rollback provisioning code. `execute=True` on the legacy adapter fails before any runner call and directs callers to Memory Stargraph. Preserve no-write dry-run behavior and tests.
7. Remove the blanket skip on legacy `AgentCreationTests`. Replace obsolete execute tests with explicit no-direct-execute and Memory-Stargraph delegation tests. Restore the full-suite skip count to the baseline five unless a separately justified platform skip exists.
8. Strictly validate active projection identity-to-route/task/artifact/staged-page mappings against the approved three declarations. Endpoint failures return existing Codex profiles plus an explicit issue, never partial OC profiles.

## Tests

Follow TDD. Add end-to-end fake tests for list/get profile, generation-owned presentation despite logical title/summary/chat tampering, logical runtime tamper rejection, logical avatar and canonical default-Goal updates, create task, edit/status/TODO, Artifact publication and delegation-claim rejection, unactivated rejection, endpoint outage fallback, mapping tamper, direct execute pre-write rejection, and dry-run. Run focused tests, `python3 -m unittest discover -s tests`, Python compile, JS syntax if touched, and `git diff --check`. Commit and write the report to `.superpowers/sdd/task-2b-report.md`.
