---
name: mc-todos
description: >
  Use when user invokes /mc-todos or asks for Mission Control task To Dos,
  missing To Dos, To Do status, comments, agent blocker questions, or To Do
  history.
---

# Mission Control To Dos Query

Use this skill for read-only inspection of task-level To Dos. Apply the
`mc-core` rules: canonical GBrain readback outranks UI state, and this skill
must not change To Do status or comments.

## What to inspect

- To Do list on the task page body or structured task state
- Each To Do item status: open/not done vs done
- Comments attached to each To Do
- Timeline/history entries for To Do creation, status changes, and comments
- Agent-raised blockers that should be represented as To Dos

## Output

For each task, show:

- Open To Dos first
- Done To Dos only when relevant or requested
- User-answerable blocker questions
- Missing-To-Do warning when a task has no concrete current action

If the user asks to answer or complete a To Do, stop using this query skill and
switch to the appropriate write workflow only after explicit authorization.

## Useful read-only commands

```bash
gbrain get <task-slug>
gbrain backlinks <task-slug>
curl -sS 'http://127.0.0.1:4179/api/tasks?refresh=1'
```
