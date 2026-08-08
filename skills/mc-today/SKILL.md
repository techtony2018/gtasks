---
name: mc-today
description: >
  Use when user invokes /mc-today or asks for today's Mission Control focus,
  overdue tasks, due-today tasks, active workload, blocked tasks, or missing
  concrete next actions.
---

# Mission Control Today Query

Use this skill for a read-only daily task view. Apply the `mc-core` rules:
canonical GBrain readback outranks Mission Control UI/API projections, and no
task or goal may be mutated.

## What to inspect

- Tony open tasks under `collections/tonys-tasks`
- Completed tasks under `collections/tonys-completed-tasks` when checking recent
  completion context
- Due dates (`due_day` / date-only fields), status, blockers, To Dos, and
  comments/history if exposed
- Pending proposed tasks only when they materially change today's focus

## Output

Keep the result short:

- Counts: active, planned, blocked/waiting, due today, overdue, missing To Dos
- Top focus items for today, with evidence
- Data-quality warnings separated from recommendations

Do not propose more than three next actions unless the user explicitly asks for
more. Do not create proposed tasks from this skill by default.

## Useful read-only commands

```bash
curl -sS 'http://127.0.0.1:4179/api/tasks?refresh=1'
gbrain get collections/tonys-tasks
gbrain backlinks collections/tonys-tasks
gbrain get collections/tonys-completed-tasks
```
