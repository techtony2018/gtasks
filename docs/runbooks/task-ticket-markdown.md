# Task and Ticket Markdown Runbook

## Purpose and scope

`gtasks.markdown_policy` is the shared formatter for every newly created
Mission Control Task and System Ticket. It is the enforcement boundary for
canonical bodies; prompts and skills guide authors but must not reconstruct
their own body format.

This is a prospective contract. It does not bulk-migrate, normalize, or
silently rewrite historical Task or System Ticket Markdown. Repairing an old
page requires separately authorized, exact-scope work.

## Canonical body contract

New Tasks render as:

```md
# <Task title>

## 详情

<authored detail>
```

New System Tickets render their canonical fields in this order, omitting an
optional section only when its source field is empty:

```md
# <Ticket title>

## 用户请求

<verbatim request>

## 验收标准

<acceptance criteria>

## 关联票据

<verified references>

## 实施与验证记录

<linked evidence, implementation receipts, and QA receipts>
```

The formatter keeps authored detail and `verbatim_request` intact, flattens
only generated title headings to one line, and escapes inline Markdown
metacharacters in generated headings and link labels. The structured GBrain
fields remain authoritative.

## System Ticket references and link safety

The only internal System Ticket link is this exact route:

```md
[<Canonical Ticket title>](#system-ticket/tasks%2F<uuid>)
```

Before generating it, the adapter reads the exact `tasks/<uuid>` page and its
links, parses it as a System Ticket, and verifies the live
`member_of -> collections/mission-control-system-tickets` edge. It does not
search by title, infer a Ticket from a UUID-shaped slug, create a relationship,
or link to Memory Stargraph. An authored internal route is accepted only when
its decoded slug and label exactly match that verified canonical reference;
forged titles, stale targets, ordinary Tasks, and merely shape-valid routes are
rejected.

When an explicitly labelled System Ticket reference is missing, malformed,
stale, or lacks that canonical membership, preserve the authored text and add
plain `System Ticket unavailable: tasks/<uuid>` text. An ordinary unlabeled
Task-shaped slug is left unchanged.

Generated Markdown accepts only `https:` external links, local `http:` links
for `127.0.0.1` or `localhost`, and the exact encoded internal route above.
`javascript:`, `data:`, `file:`, malformed, double-encoded, and noncanonical
internal targets are rejected. Ticket-like tokens with path, encoded-path,
file-extension, query, or fragment continuations are not truncated into valid
references. Markdown inside inline or fenced code is treated as literal code,
not as an active link or Ticket reference. The renderer makes only safe
external targets clickable; unsafe authored text remains text. Internal links
stay in Mission Control and return focus to their originating Task or Ticket
reference when the detail closes, including nested Ticket navigation.

Exact Task reads and System Ticket list/detail payloads may expose the verified
canonical body as optional `display_markdown`. This is a display-only
projection: it never replaces `detail`, `verbatim_request`, acceptance,
implementation/QA records, status, ownership, or graph relationships. Older
pages without the projection remain readable through the existing structured
Task/Ticket fallback.

Eligibility is durable and versioned. New unified pages carry
`markdown_contract: unified-task-ticket-v1`; APIs expose `display_markdown`
only when that exact marker is present and the compiled body exactly rerenders
from current canonical fields plus freshly verified Ticket references. Marked
Task/Ticket content edits rerender and read back the updated body. Unmarked
historical edits preserve their existing body and do not silently opt in.

The safe browser renderer mirrors backend code handling for indented backtick
or tilde fences and matching inline backtick delimiter runs of arbitrary
length, decodes generated Markdown escapes/entities into text nodes, and
accepts bare `localhost`/`127.0.0.1` loopback URLs with optional ports,
paths, queries, and fragments. It validates the complete authored HTTP
candidate, so hostile authority/host continuations remain plain text instead
of producing a loopback-prefix link. Code remains inert and hostile-prefix/non-loopback
HTTP hosts remain plain text.

## Write and readback contract

For a new Task or System Ticket:

1. Resolve any explicitly labelled System Ticket references by exact canonical
   page and relationship readback before rendering.
2. Write the page with the shared rendered body.
3. Read the exact page and required typed links back.
4. Require parsed canonical fields, title, lifecycle/ownership relationships,
   and `compiled_markdown` to equal the exact shared rendered body.

System Ticket creation also proves the exact live
`member_of -> collections/mission-control-system-tickets` edge. Direct Quick
Add rereads the final page and live edges, reconstructs the complete Task,
requires Tony ownership, and rejects any unexpected `assigned_to` edge. Agent
Task and System Ticket edge writes/readbacks after the page write are all
partial-mutation boundaries that report the exact mutated slug.

If a page write succeeds but any readback differs, report the exact slug as a
partial mutation and stop. Do not create a replacement or retry blindly.

## `mc-add-task` skill synchronization

The repository copy of `skills/mc-add-task` and its helper use the same
formatter and return `markdown_contract == "unified-task-ticket-v1"` plus
`rendered_body` evidence. The live helper reuses the adapter's exact verified
`compiled_markdown` readback; it does not resolve Ticket titles a second time.
Dry-run internal routes that need a live title/membership read report
`verification_required: true` with no rendered body; they are not falsely
reported as failed writes or verified links.
Synchronize the active copy only after the candidate is otherwise authorized,
then verify both source/installed pairs by hash:

```bash
ditto skills/mc-add-task/SKILL.md /Users/tony/.codex/skills/mc-add-task/SKILL.md
ditto skills/mc-add-task/scripts/mc_add_task.py /Users/tony/.codex/skills/mc-add-task/scripts/mc_add_task.py
shasum -a 256 skills/mc-add-task/SKILL.md /Users/tony/.codex/skills/mc-add-task/SKILL.md
shasum -a 256 skills/mc-add-task/scripts/mc_add_task.py /Users/tony/.codex/skills/mc-add-task/scripts/mc_add_task.py
```

Current status: **PENDING/BLOCKED**. The synchronization attempt failed with
`Operation not permitted`; the installed copies remain out of sync. Do not
claim this gate complete until the commands succeed and each source/installed
hash pair is identical.

## Verification and release gates

Run the focused candidate suite first:

```bash
python3 -m unittest tests.test_markdown_policy tests.test_gbrain tests.test_frontend_contract tests.test_mc_add_task_skill -v
node --check static/app.js
python3 -m compileall -q gtasks tests skills/mc-add-task/scripts
git diff --check
```

Before release, also run the full suite in an environment that permits its
known sandbox-restricted checks:

```bash
python3 -m unittest discover -s tests
```

Independent pre-commit QA must freeze the candidate and return PASS at desktop
`1440x1000` and genuine mobile `390x844`. It must cover section hierarchy,
safe bare external links, the exact internal Ticket route, unavailable
fallbacks, nested detail focus restoration, no overflow, and zero GBrain
mutations. Only then may the candidate be committed, pushed, deployed through
the dashboard-managed `gtasks` service, and read back from runtime and the
bounded canonical fixture.
