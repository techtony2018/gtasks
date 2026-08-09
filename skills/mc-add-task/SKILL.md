---
name: mc-add-task
description: Create a verified Mission Control / GTasks task from natural language using slash command /mc-add-task or explicit requests to add a Mission Control task, ticket, reminder, action item, or due date. Defaults owner to Tony; when Tony names an Agent such as Tammy, Timmy, Toddy, tammy-oc, timmy-oc, or toddy-oc, create the task in that Agent's canonical work collection with an assigned_to relationship.
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
- `detail`: preserve the user's exact request and note any date resolution.

Optional:

- `priority`: `low`, `normal`, `high`, or `urgent`; default `normal`. Do not invent urgency.
- `next_action`: one concise line; default empty.

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

3. Create the task through the bundled helper. Resolve `<active-skill-root>` from the loaded skill path:

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

4. Read the helper JSON output. It verifies:

   - page write;
   - lifecycle collection membership;
   - `assigned_to` relationship for Agent tasks;
   - canonical GBrain readback.

5. Report the result with a clickable Memory Stargraph link:

   `http://127.0.0.1:8788/?slug=<URL-encoded-slug>`

## Safety

- Do not create System Tickets from this skill.
- Do not create tasks with unverified owners.
- Do not fabricate due dates beyond deterministic relative-date resolution.
- If a write partially succeeds but verification fails, stop and report the exact slug and error before retrying.
