---
name: mc-core
description: >
  Use when user invokes /mc-core or asks for canonical Mission Control/GTasks
  task queries, source readback, data-quality checks, or base rules shared by
  the mc-* query skills.
---

# Mission Control Core Query Rules

Use this skill only for read-only Mission Control/GTasks inspection. Do not
create, update, complete, cancel, pause, delete, repair, reprioritize, or
otherwise mutate formal tasks, goals, proposals, tickets, or GBrain pages unless
the user explicitly switches from query/review to a write task.

## Canonical sources

Prefer canonical GBrain readback over dashboard projections when they disagree.
Use the dashboard/API as a fast projection, then verify important claims against
the GBrain page and root membership links.

Canonical roots:

- Tony Goals: `collections/tonys-goals`
- Tony Tasks: `collections/tonys-tasks`
- Tony Completed Tasks: `collections/tonys-completed-tasks`
- Proposed Tasks: `collections/gtasks-proposed-work`
- Mission Control System Tickets: `collections/mission-control-system-tickets`
- Agent task roots when relevant: `collections/toddys-tasks`, `collections/timmys-tasks`, `collections/tammys-tasks`

Local services:

- Mission Control: `http://127.0.0.1:4179`
- Memory Stargraph: `http://127.0.0.1:8788`

## Query workflow

1. State whether the answer is from canonical GBrain, Mission Control API, or
   both.
2. Use root-scoped reads. Do not infer task ownership from UI lists alone.
3. For any task-level conclusion, inspect the task page body plus backlinks or
   typed relationships if available.
4. Report data-quality warnings separately from the result.
5. Present GBrain slugs as clickable Memory Stargraph links:
   `http://127.0.0.1:8788/?slug=<URL-encoded-slug>`.

## Useful read-only commands

```bash
curl -sS http://127.0.0.1:4179/api/health
curl -sS 'http://127.0.0.1:4179/api/tasks?refresh=1'
gbrain get collections/tonys-tasks
gbrain backlinks collections/tonys-tasks
gbrain get <task-or-goal-slug>
```

If a command is unavailable, say exactly which read path failed and use the next
available read-only path.
