# Task 2B report: activated OpenClaw Agent integration

## Status

Completed in the GTasks worktree. Activated OpenClaw profiles now resolve only
through a strictly validated Memory Stargraph active projection and stable
logical anchors. Task, TODO, and Artifact work fails closed for unactivated or
tampered OC identities. Existing Codex profiles remain available when the OC
projection endpoint is unavailable.

All implementation and verification used deterministic local fakes and local
HTTP-handler tests. No live NATS, GBrain, profile activation, canonical graph
mutation, Memory Stargraph change, service restart, or deployment was performed.

## Implemented behavior

- Added the exact approved Tammy-OC, Timmy-OC, and Toddy-OC declaration map.
- Active projection validation now binds generation, manifest identity and
  digest, all three logical identities, exact route/task/Artifact mappings,
  exact generation staging paths, staged page hashes, and generation metadata.
- Generation zero is a healthy empty OC roster. Any endpoint, projection,
  mapping, metadata, or logical-anchor failure suppresses the entire OC roster
  and adds one explicit `openclaw_activation` issue while preserving Codex
  profiles.
- `list_agent_profiles()` and `get_agent_profile()` project mutable title,
  summary, runtime, chat/avatar fields from the verified generation metadata,
  while returning logical Agent slugs and stable task roots. Logical Agent edges
  remain authoritative for later `default_agent_for` Goal relationships.
- Activated OC task creation verifies the logical Agent and task collection
  before writing. Task read, full edit, status, and TODO read/mutation paths
  revalidate activation and the stable logical task anchor. Tests prove no
  mutation targets a generation staging slug.
- Artifact publication verifies authenticated identity before any adapter call,
  then validates the active logical Agent, stable logical task and Artifact
  collections, canonical task ownership, and exact `created_by` relationship.
  OC publication does not bootstrap collections.
- Added optional `delegation_ref` provenance with an opaque canonical
  `agent-delegations/<UUIDv4-or-v5>` contract. The value is validated, rendered,
  read back, included in idempotency comparison, and returned by the HTTP API.
- Cross-identity OC publication fails before adapter mutation.
- Deleted the unreachable direct GBrain execute, page-render, verification, and
  rollback provisioning implementation. The legacy adapter now either returns a
  validated no-write dry-run receipt or rejects `execute=True` before any runner
  call with direction to Memory Stargraph activation.
- Removed the blanket `AgentCreationTests` skip and deleted mutation-capable
  legacy provisioning test doubles.

## TDD evidence

The new tests were observed failing before production changes:

```text
delegation provenance:
Ran 1 test ... ERROR
TypeError: new_agent_artifact() got an unexpected keyword argument 'delegation_ref'

legacy provisioning:
Ran 3 tests ... FAILED (failures=1)
route tampering was accepted by the old dry-run validator

profile activation:
Ran 6 tests ... FAILED (failures=4, errors=1)
direct OC get was unavailable; generation zero emitted an issue; mapping and
logical-anchor tampering still produced partial OC profiles

activated OC work:
Ran 9 tests ... FAILED (failures=4, errors=1)
stable task anchors were not read, unactivated task/Artifact work was accepted,
and delegation provenance was unsupported

Artifact HTTP API:
Ran 2 tests ... FAILED (failures=2)
optional delegation provenance was rejected before auth/publication
```

The first broader Artifact run also exposed a Codex preflight ordering
regression. `test_prewrite_gbrain_outage_stays_gbrain_error` failed because
collection setup ran before task preflight. The order was restored and the test
passed before continuing.

Focused GREEN run:

```text
python3 -m unittest -v \
  tests.test_gbrain.AgentCreationTests \
  tests.test_gbrain.AgentReadTests \
  tests.test_gbrain.ActivatedOpenClawWorkIntegrationTests \
  tests.test_domain.AgentArtifactContractTests \
  tests.test_server.ArtifactApiTests \
  tests.test_openclaw_profile_activation_client

Ran 77 tests in 10.668s
OK
```

## Full verification

Observed full-suite result before the final fresh verification pass:

```text
python3 -m unittest discover -s tests
Ran 798 tests in 82.939s
OK (skipped=5)
```

The five-skip baseline is restored; no Task 2B class or test is skipped.

Final fresh verification after self-review and regression repair:

```text
focused integration plus Artifact regressions:
Ran 105 tests in 10.761s
OK

python3 -m unittest discover -s tests
Ran 798 tests in 82.788s
OK (skipped=5)
```

Required static verification:

```text
python3 -m compileall -q gtasks scripts tests
git diff --check
PASS
```

No JavaScript file was changed, so a JavaScript syntax run is not applicable.

## Files changed

- `gtasks/domain.py`
- `gtasks/gbrain.py`
- `gtasks/server.py`
- `tests/test_domain.py`
- `tests/test_gbrain.py`
- `tests/test_server.py`
- `.superpowers/sdd/task-2b-report.md`

## Explicit boundary and remaining concern

Live OC activation, NATS behavior, GBrain readback, and dashboard/deployment
behavior were intentionally not exercised. The integration contract was checked
against the reviewed Memory Stargraph protocol source and tested with exact local
projection/anchor fakes. A live operational canary remains a separately
authorized step.
