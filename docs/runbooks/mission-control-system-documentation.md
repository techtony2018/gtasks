# Mission Control System Documentation Runbook

## Purpose and authority

This Runbook is the durable operating contract for the single fixed Mission
Control Documentation Manager task
`019fcb77-2886-7f31-a4d2-0b8bbe7e1477`.

The Documentation Manager maintains evidence-based system documentation. It
does not implement product code, create System Tickets, change task lifecycle,
deploy merely to make documentation agree, or bypass the implementation
owner's QA/release gates.

## Canonical shared registration

- Documentation collection:
  [`collections/mission-control-documentation`](http://127.0.0.1:8788/?slug=collections%2Fmission-control-documentation)
- System Overview:
  [`docs/f2516aa8-89ae-4570-a205-118d5c038ad7`](http://127.0.0.1:8788/?slug=docs%2Ff2516aa8-89ae-4570-a205-118d5c038ad7)
- Machine-readable Agent registration:
  [`config/agent-artifact-protocol/shared-documentation.json`](../../config/agent-artifact-protocol/shared-documentation.json)

The Overview is one canonical page with one typed
`member_of -> collections/mission-control-documentation` relationship. Agent
runtime instructions reference that shared collection; they must not copy the
page into Agent pages, work collections, Artifact collections, or
identity-specific prompts. Documentation membership is read-only context and
never execution authority. The global GBrain `index` is out of scope.

Direct read availability and installed prompt deployment are separate gates.
Verify the collection, Overview hash, and typed edge from every Agent host.
Then inspect installed prompts independently; a pushed template does not prove
that an installed automation has received it. Do not update external
automations from this documentation task without separate authority.

## When to refresh documentation

Refresh after a material Mission Control feature or architecture change is
actually released and verified. Do not refresh the current-state description
from a planned, blocked, uncommitted, QA-failed, or partially deployed
candidate. Keep those items in an explicitly labelled future/blocked section.

For each refresh, inspect:

1. deployed `/api/health` version and the process working directory;
2. exact source commit and remote push state;
3. canonical System Ticket page, typed membership, implementation receipts,
   QA receipts, and final lifecycle readback;
4. automated tests, static checks, and independent desktop 1440x1000 plus
   genuine mobile 390x844 QA for UI-affecting work;
5. the live runtime behavior and affected APIs;
6. README, every affected version-controlled Runbook, Agent instruction source,
   and the canonical Overview for contradictions.

If implementation, deployment, and documentation disagree, report the
mismatch and require the implementation owner to resolve it. Do not modify
product code or canonical lifecycle from this documentation task.

## Refresh procedure

1. Reconcile `HEAD`, `origin/main`, tracked-worktree cleanliness, release
   metadata, and `GET http://127.0.0.1:4179/api/health`.
2. Refresh and deduplicate the canonical documentation collection and Overview
   before any write. Adopt the existing opaque Overview identity above; never
   create a replacement merely because its title or body changes.
3. Refresh System Tickets through
   `GET /api/system-tickets?refresh=1&include_completed=1`, then read the exact
   affected page and its typed links directly from GBrain. A stale projection
   is not release evidence.
4. Update the Overview with provenance for every material claim. Keep deployed,
   active, planned, blocked, and unknown states distinct. Preserve exact paths,
   commands, endpoints, owners, authority boundaries, recovery, rollback, and
   verification instructions where useful.
5. Perform the canonical documentation write sequence: supported page write,
   exact page readback, typed relationship write only when the membership is
   missing, and exact outgoing-link plus collection-backlink readback. The
   steady state must contain exactly one Overview membership edge.
6. Update README, all affected Runbooks, and the shared Agent instruction
   source to the same verified reality. Supersede stale instructions rather
   than adding contradictory notes.
7. Validate JSON, rendered Agent instructions, Markdown links/headings,
   commands, diff scope, GBrain page content hash/readback, discovery
   relationship, and unchanged global `index`.
8. Documentation-only changes may use the non-UI exception. Commit and push
   only the documentation/configuration paths after validation. Any UI change
   remains Developer-owned and requires the normal independent QA gate.

## Verification commands

```bash
curl -fsS http://127.0.0.1:4179/api/health
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
gbrain call get_page '{"slug":"docs/f2516aa8-89ae-4570-a205-118d5c038ad7"}'
gbrain call get_links '{"slug":"docs/f2516aa8-89ae-4570-a205-118d5c038ad7"}'
gbrain call get_backlinks '{"slug":"collections/mission-control-documentation"}'
python3 scripts/verify_agent_artifact_protocol.py tammy daytime
python3 scripts/verify_agent_artifact_protocol.py tammy nighttime
python3 scripts/verify_agent_artifact_protocol.py timmy daytime
python3 scripts/verify_agent_artifact_protocol.py timmy nighttime
python3 scripts/verify_agent_artifact_protocol.py toddy daytime
python3 scripts/verify_agent_artifact_protocol.py toddy nighttime
python3 -m unittest discover -s tests
```

For an Overview body large enough to risk command-line or pipe limits, write
from a reviewed local file through the supported `gbrain put <slug>` stdin
contract, then compare `get_page.compiled_truth`, frontmatter, and content hash
to the reviewed source before updating relationships.

## Failure recovery and rollback

- If a page write succeeds but readback differs, stop before relationship
  mutation. Preserve the returned slug/hash and inspect `gbrain history`.
- If page readback succeeds but membership verification fails, do not delete or
  create another page. Re-read both directions, then repair only the one exact
  typed edge if authorized.
- If a documentation update is factually wrong, restore the last verified
  GBrain page version with the supported history/revert contract, read it back,
  and correct repository documentation in a focused follow-up commit.
- If a product rollback is required, hand it to the implementation owner. After
  rollback, re-run health/version/runtime checks before changing documentation.
- Never expose credentials, raw event payloads, private Calendar content, or
  unredacted logs in documentation or verification output.

## Current verification baseline

- Last verified local date: `2026-08-04` (`America/Los_Angeles`)
- Mission Control version: `V0.0.76`
- Deployed product repair commit:
  `44abd12ea56f15f50bf1232daaa04be05fd877d6`
- Source/evidence base commit:
  `4d3cd4d02825970b5e318098320884821ca1ddbd`
- Service: `http://127.0.0.1:4179/`
- Health: `http://127.0.0.1:4179/api/health`
- Canonical store: `gbrain`
- Full automated gate: `715` tests passed, `5` skipped.
- V0.0.76 Dispatcher evidence: release evidence records independent
  desktop `1440x1000` and genuine mobile `390x844` QA PASS, three-host
  Dispatcher installation, one bounded Tammy-only canary, and completed
  canonical System Ticket `tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620`.
- Cross-host readback: Tammy, Timmy, and Toddy each returned the same canonical
  collection hash, Overview `V0.0.76` document version `4`, Overview hash
  `d485bbbfa54f1ac6d6cdf969c3aa080320edfc44bc779ed5e37030368a6ef9ba`, and
  one discovery edge on `2026-08-04`.
- Installed prompt state: all six active fixed-target Agent automations lacked
  the shared collection reference at the `2026-08-03` snapshot. The pushed
  source contract is current; installed rollout remains a separately
  authorized step and was not mutated by the `V0.0.76` documentation refresh.

Known documentation-quality issue at this baseline:
`collections/mission-control-system-tickets` still lists legacy `waiting` in
frontmatter while the deployed contract writes only `blocked`. The
Documentation Manager must not mutate that ticket-owned collection merely to
hide the mismatch; its owner should reconcile it through the supported
contract.
