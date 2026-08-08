---
name: mc-goals
description: >
  Use when user invokes /mc-goals or asks for Mission Control goal coverage,
  stalled goals, goal-linked tasks, missing task coverage, or Goal Steward style
  read-only review.
---

# Mission Control Goals Query

Use this skill for read-only goal/task coverage analysis. Apply the `mc-core`
rules and treat all suggested next work as requiring Tony's explicit approval.

## What to inspect

- Goals under `collections/tonys-goals`
- Open Tony tasks under `collections/tonys-tasks`
- Completed Tony tasks under `collections/tonys-completed-tasks`
- Pending proposed tasks under `collections/gtasks-proposed-work`
- Agent roots when the user asks about delegated work

## Analysis checks

- Goals with no open task
- Goals with open tasks but no concrete To Do / next action
- Stalled goals with no recent completed task or timeline movement
- Duplicates between existing tasks and proposed tasks
- Blocked or waiting tasks that need a user-answerable To Do
- Data-quality warnings, especially root misclassification and stale backlinks

Do not create candidate tasks or proposals unless explicitly requested. If
asked to propose work, deduplicate against existing open tasks and pending
proposals first.

## Useful read-only commands

```bash
gbrain get collections/tonys-goals
gbrain backlinks collections/tonys-goals
gbrain get collections/tonys-tasks
gbrain get collections/gtasks-proposed-work
```
