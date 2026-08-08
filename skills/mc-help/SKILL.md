---
name: mc-help
description: >
  Use when user invokes /mc-help or asks for an overview, menu, cheatsheet,
  routing guide, or explanation of available Mission Control mc-* skills and
  which one to use.
---

# Mission Control Skills Help

Use this skill to explain the available Mission Control query skills and route
the user to the right one. Do not perform Mission Control/GTasks data queries
from this skill unless the user explicitly asks you to continue with a specific
query skill.

## Output style

Keep the answer short and actionable:

- Name each available `mc-*` skill.
- State when to use it.
- If the user's current intent is clear, recommend exactly one next skill.
- If the intent is ambiguous, ask for the missing input such as task slug, date,
  goal, proposal status, or ticket scope.

## Skill map

- `/mc-help`: Show this overview and choose the right Mission Control skill.
- `/mc-core`: Use for canonical read-only rules, source hierarchy, data-quality
  checks, and root-scoped GBrain/Mission Control query conventions.
- `/mc-today`: Use for today's focus, due-today tasks, overdue tasks, active
  workload, blocked/waiting work, and missing To Dos.
- `/mc-task`: Use for a specific task lookup by slug, title, status, due date,
  goal, project, To Do, blocker, or timeline.
- `/mc-goals`: Use for goal coverage, stalled goals, goal-linked tasks, gaps,
  missing next actions, and Goal Steward style read-only review.
- `/mc-todos`: Use for task To Dos, missing To Dos, To Do comments, status,
  blocker questions, and To Do history.
- `/mc-proposals`: Use for Proposed Tasks inbox status, pending/approved/rejected
  proposals, evidence, fingerprints, linked formal tasks, and proposal timeline.
- `/mc-tickets`: Use for Mission Control System Tickets, ticket backlog,
  planned/active/completed tickets, ticket details, and system delivery status.

## Routing rules

When the user asks "what should I do today?" route to `/mc-today`.

When the user gives a task slug or exact task title, route to `/mc-task`.

When the user asks about goals, stalled progress, or missing work under goals,
route to `/mc-goals`.

When the user asks about To Dos, comments, blocker questions, or per-task
history, route to `/mc-todos`.

When the user asks about candidate work, suggestions, approval/rejection, or
Inbox Proposed Tasks, route to `/mc-proposals`.

When the user asks about Mission Control product/system work, implementation
tickets, backlog, or delivery status, route to `/mc-tickets`.

When the user asks about canonical source rules, data quality, stale UI/API
state, or whether a result is verified, route to `/mc-core`.

## Safety boundary

All `mc-*` query skills are read-only by default. If the user asks to create,
change, complete, reject, approve, reprioritize, or delete a task, proposal,
ticket, or goal, stop treating it as an `mc-*` query and switch to the
appropriate write workflow only after explicit authorization.
