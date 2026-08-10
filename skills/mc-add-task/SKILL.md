---
name: mc-add-task
description: Use when Tony invokes /mc-add-task or explicitly asks to add a Mission Control task, reminder, action item, Agent assignment, Bible Study task, or due date; not for Mission Control System Tickets.
---

# MC Add Task

## Purpose

Create one Mission Control task and verify canonical GBrain readback. Use this for Tony-facing action items and Agent-assigned work. Do not use this for Mission Control System Tickets; system-delivery tickets use the System Tickets Manager workflow.

## Ownership rule

- Default owner: Tony.
- If the request explicitly assigns the work to an Agent, use that Agent:
  - `Tammy` -> `agents/tammy`
  - `Timmy` -> `agents/timmy`
  - `Toddy` -> `agents/toddy`
  - `Tammy OpenClaw` / `tammy-oc` -> `agents/tammy-oc`
  - `Timmy OpenClaw` / `timmy-oc` -> `agents/timmy-oc`
  - `Toddy OpenClaw` / `toddy-oc` -> `agents/toddy-oc`
- Do not infer an Agent owner from vague language like "someone" or "automation"; default to Tony unless a named Agent is present.

## Required inputs

- `title`: concise task title, 160 characters or fewer.
- `due_day`: `YYYY-MM-DD`.
  - Resolve relative dates using Tony's current local date in `America/Los_Angeles`.
  - Example: if current date is 2026-08-09, "next Wednesday" is 2026-08-12.
  - If no due date is supplied, use Tony's current local date.
- `detail`: standard Markdown that preserves the user's exact request and notes
  any date resolution. Follow the Markdown detail contract below.

Optional:

- `priority`: `low`, `normal`, `high`, or `urgent`; default `normal`. Do not invent urgency.
- `next_action`: one concise line; default empty.

## Markdown detail contract

Every new Task created by this skill, including Tony, Codex Agent, OpenClaw
Agent, and Bible Study Tasks, must pass a Markdown-formatted `detail` to the
helper. Use this structure and omit only optional sections that have no items:

```md
### 用户请求

<Preserve the user's exact wording here.>

### 日期说明

- <Explain each relative or default due-date resolution.>

### 相关链接

- [<Descriptive label>](<safe URL>)
```

Rules:

- Do not translate, paraphrase, or silently correct the text under
  `### 用户请求`.
- Keep URLs in the exact request unchanged. Also add each safe external URL to
  `### 相关链接` using standard `[label](URL)` Markdown so it is clickable.
- Link labels must come from the user's wording, a verified page title, or the
  source name/hostname. Do not invent claims about the linked content.
- Never make `javascript:`, `data:`, or `file:` URLs clickable.
- If the request references a canonical Mission Control System Ticket slug,
  such as `tasks/<uuid>`, read back that exact Ticket before creating the Task.
  Add the verified reference to `### 相关链接` using its canonical title and
  this deployment-independent Mission Control route:

  ```md
  - [<Canonical Ticket title>](#system-ticket/tasks%2F<ticket-uuid>)
  ```

- A System Ticket reference must not link to Memory Stargraph and must not
  hard-code a Mission Control hostname or port. The final result link for the
  newly created normal Task may still be the Memory Stargraph link required in
  Workflow step 6; that result link is not a referenced System Ticket link.
- If the referenced slug is missing, malformed, stale, or not a canonical
  System Ticket, keep the exact user wording but render the reference as plain
  text with `System Ticket unavailable`; never guess by title.
- Do not create or infer a GBrain relationship merely because the Markdown
  mentions or links to another Ticket.

## Workflow

1. Read the active task root when needed:

   ```bash
   gbrain get collections/tonys-tasks
   ```

2. Check for obvious duplicates before writing:

   ```bash
   gbrain search "<distinct task phrase>"
   ```

   If a matching open task already exists, report it instead of creating a duplicate unless Tony explicitly wants another copy.

3. Build `detail` with the Markdown detail contract. Resolve every referenced
   System Ticket through canonical readback before adding an internal Ticket
   link. Preserve the exact user wording under `### 用户请求`.

4. Create the task through the bundled helper. This is the only authorized
   creation path for this skill. Do not call `gbrain put`, `gbrain call
   put_page`, or an MCP `put_page` tool directly; those paths can omit the
   canonical title and other required task fields. Resolve `<active-skill-root>`
   from the loaded skill path:

   ```bash
   python3 "<active-skill-root>/scripts/mc_add_task.py" \
     --title "<title>" \
     --detail "<detail>" \
     --due-day YYYY-MM-DD
   ```

   For Agent-owned tasks:

   ```bash
   python3 "<active-skill-root>/scripts/mc_add_task.py" \
     --title "<title>" \
     --detail "<detail>" \
     --due-day YYYY-MM-DD \
     --owner-agent agents/toddy
   ```

   A `--dry-run` cannot verify the live canonical title/membership behind an
   authored internal Ticket route. In that case it exits successfully with
   `verification_required: true`, the exact unverified Ticket slugs, and
   `rendered_body: null`; this is not a verified link or a write failure. Run
   the live path to resolve the route before creation. Unsafe non-Ticket links
   remain contract errors.

5. Read the helper JSON output and require `page_title` to exactly equal the
   requested `title`, `markdown_contract == "unified-task-ticket-v1"`, and
   `rendered_body` evidence. The helper verifies:

   - page write;
   - exact canonical title readback;
   - lifecycle collection membership;
   - `assigned_to` relationship for Agent tasks;
   - compiled-body equality with the shared Markdown renderer;
   - canonical GBrain readback.

6. Report the result with a clickable Memory Stargraph link:

   `http://127.0.0.1:8788/?slug=<URL-encoded-slug>`

## Safety

- Do not create System Tickets from this skill.
- Do not create tasks with unverified owners.
- Never bypass the bundled helper with a direct page write.
- Never pass an unstructured prose `detail`; use the Markdown detail contract.
- Never link a referenced System Ticket to Memory Stargraph.
- Treat a missing, UUID-derived, or mismatched canonical title as a partial
  mutation: report the exact slug and stop without creating a replacement.
- Do not fabricate due dates beyond deterministic relative-date resolution.
- If a write partially succeeds but verification fails, stop and report the exact slug and error before retrying.
