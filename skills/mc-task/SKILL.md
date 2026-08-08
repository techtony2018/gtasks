---
name: mc-task
description: >
  Use when user invokes /mc-task or asks to look up a Mission Control task by
  slug, title, status, date, goal, project, To Do, blocker, or timeline.
---

# Mission Control Task Lookup

Use this skill for read-only inspection of one or more specific tasks. Apply the
`mc-core` rules: verify important task claims against canonical GBrain page
readback and root membership links; do not mutate task state.

## Required task details

For each matched task, report only confirmed fields:

- Slug and clickable Memory Stargraph link
- Title
- Current status
- Root membership / owner scope
- Due date, if present
- Linked goal or project, if present
- To Dos, comments, blockers, and timeline/history, if present
- Data-quality issues, such as duplicate roots, missing backlink, stale UI state,
  malformed handoff, or soft-deleted backlink

If the user gives an ambiguous title, list the likely matches and ask for the
specific slug before making any write-oriented recommendation.

## Useful read-only commands

```bash
gbrain get <task-slug>
gbrain backlinks <task-slug>
curl -sS 'http://127.0.0.1:4179/api/tasks?refresh=1'
```
