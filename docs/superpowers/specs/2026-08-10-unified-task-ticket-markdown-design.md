# Unified Task and Ticket Markdown Contract

> Historical scope note (V0.0.222): references below to OpenClaw Agent task
> creation describe the former supported roster. Current Mission Control task
> creation supports Tony, Tammy, Timmy, Toddy, and System Tickets only; retired
> `*-oc` identities cannot own or receive new work.

**Date:** 2026-08-10
**Status:** Approved for implementation
**Scope:** Every newly created Mission Control Task and System Ticket

## Outcome

Every new Mission Control Task or System Ticket is stored with a predictable,
readable Markdown body regardless of whether it was created for Tony, a Codex
Agent, an OpenClaw Agent, Bible Study, or another supported workflow.

The contract is enforced in the shared canonical creation layer. Prompts may
guide authors, but prompts are not the enforcement boundary. Every successful
creation still requires canonical page readback and typed relationship
readback.

This change applies prospectively. It does not bulk-rewrite historical pages.
Existing pages are repaired only through separately authorized work.

## Covered creation paths

The same policy applies to:

- Tony-owned Tasks;
- Codex Agent Tasks;
- OpenClaw Agent Tasks;
- Bible Study Tasks and their parent plans;
- Mission Control System Tickets;
- future Task/Ticket creation entry points that use the canonical adapter.

Artifacts, Goals, Projects, Agents, and other GBrain entity types are outside
this contract unless they create a Task or System Ticket through one of these
paths.

## Canonical Markdown templates

### Normal Task

```md
# <Task title>

## 详情

<Task detail in Markdown>
```

The title is canonical plain text and is escaped when placed into the Markdown
heading. The detail may contain headings below level two, paragraphs, lists,
checklists, blockquotes, code, and supported links.

### Mission Control System Ticket

```md
# <Ticket title>

## 用户请求

<Tony's exact request>

## 验收标准

- <Acceptance criterion>

## 实施与验证记录

- <Implementation or QA receipt when present>
```

The structured canonical fields remain authoritative. In particular,
`verbatim_request` is preserved exactly rather than rewritten for style. The
page body is a deterministic Markdown projection of those fields.

Empty optional sections are omitted rather than filled with invented content.

## Link policy

### External links

Generated content uses standard Markdown links:

```md
[Descriptive label](https://example.com/resource)
```

Bare `http://` or `https://` URLs in exact user-authored content are preserved
in the canonical structured field and safely autolinked by the renderer. The
system must not mutate quoted user wording merely to change link syntax.

Unsafe schemes such as `javascript:`, `data:`, and `file:` remain non-clickable.

### References to Mission Control System Tickets

A verified reference to another System Ticket must open that Ticket inside
Mission Control. It must not open the Memory Stargraph page for the Ticket.

The stored Markdown uses a deployment-independent internal route:

```md
[<Canonical Ticket title>](#system-ticket/tasks%2F<ticket-uuid>)
```

Rules:

1. Resolve the referenced slug through canonical System Ticket readback.
2. Use the canonical Ticket title as the link label.
3. Percent-encode the canonical `tasks/<uuid>` slug in the route.
4. Do not hard-code port `4179`, a hostname, or a Tailnet origin.
5. The safe Markdown renderer recognizes only the exact internal
   `#system-ticket/<encoded canonical task slug>` form.
6. Clicking opens the exact System Ticket detail in the same Mission Control
   application and preserves a usable return-focus target.
7. A missing, malformed, stale, or non-System-Ticket target is rendered as
   non-clickable text with a clear unavailable state; the system does not guess
   a target from title text.
8. The presence of a Ticket reference does not create or infer a GBrain graph
   relationship unless an independently defined workflow explicitly writes
   and verifies that relationship.

Other GBrain entity links may continue to use the Memory Stargraph URL where
that behavior is explicitly intended. The System Ticket exception takes
precedence whenever the target is a canonical Mission Control System Ticket.

## Enforcement boundary

Add one shared Markdown policy/formatter used by the canonical creation paths.
The policy owns:

- template selection by Task versus System Ticket;
- heading escaping and stable section ordering;
- generated list and link formatting;
- verified System Ticket reference projection;
- safe handling of exact user-authored Markdown;
- post-render contract validation.

`create_task`, Agent/OpenClaw Task creation, and `create_system_ticket` must all
invoke this shared policy before a page write. A new creation path cannot opt
out silently.

Prompt instructions and UI placeholders should mirror the contract, but a
prompt-only or UI-only implementation is insufficient because API, automation,
and adapter callers must receive the same behavior.

## Validation and readback

Before write, validation checks:

- the expected top-level heading and required section order;
- balanced, supported generated link syntax;
- exact preservation of structured user-authored fields;
- canonical System Ticket identity before generating an internal Ticket link;
- absence of unsafe generated link schemes;
- no empty invented prose or acceptance criteria.

After write, the adapter reads back:

1. the canonical page;
2. its rendered Markdown body;
3. its expected typed collection membership;
4. its expected ownership relationships;
5. any other relationships required by the existing creation contract.

Success is reported only if the readback matches. A pre-write validation error
causes no GBrain mutation. A write followed by failed readback is reported as a
partial mutation with evidence; it is never reported as successful.

## Rendering behavior

Mission Control continues to use the safe Markdown renderer for Task and Ticket
content. The renderer adds narrowly scoped support for the internal System
Ticket route described above. It must not broaden support to arbitrary relative
fragments or unsafe URL schemes.

External links retain safe `target` and `rel` behavior. Internal Ticket links
remain in the current Mission Control surface and use the existing canonical
System Ticket detail-loading path rather than duplicating read logic.

## Backward compatibility

- Existing Tasks and Tickets are not rewritten automatically.
- Existing structured fields and typed links remain canonical.
- Existing user Markdown remains displayable.
- The implementation must tolerate historical bodies that do not use the new
  section templates.
- Editing a historical page does not silently rewrite its whole body unless
  the edit explicitly opts into the new formatter and passes preservation
  checks.

## Verification

Automated coverage must include:

- Tony Task creation;
- Codex Agent Task creation;
- OpenClaw Agent Task creation;
- Bible Study parent and daily Task creation;
- System Ticket creation;
- exact `verbatim_request` preservation;
- standard external Markdown links;
- safe autolinking of bare URLs without changing canonical user text;
- verified System Ticket reference conversion to an internal Mission Control
  hyperlink;
- missing/stale/wrong-type Ticket reference fallback;
- rejection of unsafe generated links;
- page and relationship readback failures;
- historical nonconforming body compatibility.

Because internal-link rendering changes visible UI behavior, the uncommitted
candidate requires independent UI/UX QA at desktop `1440x1000` and genuine
mobile `390x844`. QA must prove that external links remain safe, a referenced
Ticket opens the exact Mission Control Ticket detail, unavailable references do
not become broken links, focus is recoverable, and no QA action mutates live
Task/Ticket data.

## Documentation and rollout

Update the Mission Control README and relevant Task/System Ticket creation
runbooks so future developers and automation authors use the shared formatter
rather than reconstructing Markdown in prompts.

Release only after tests, static checks, independent UI/UX PASS, canonical
commit/push verification, dashboard-managed restart, runtime version readback,
and a bounded read-only production verification.
