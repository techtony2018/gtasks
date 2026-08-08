---
name: mc-tickets
description: >
  Use when user invokes /mc-tickets or asks about Mission Control System Tickets,
  ticket backlog, planned/active/completed tickets, ticket detail, or system
  delivery status.
---

# Mission Control System Tickets Query

Use this skill for read-only inspection of Mission Control System Tickets. Apply
the `mc-core` rules. Do not create system tickets from this skill; ticket
creation belongs to the System Tickets Manager workflow unless Tony explicitly
asks for a write action.

## What to inspect

- System Ticket root: `collections/mission-control-system-tickets`
- Ticket status: planned, active, blocked, completed, cancelled, or stale
- Ticket detail page, timeline, To Dos, comments, blockers, and linked artifacts
- Completed-ticket scope when the user asks about delivery history
- Dashboard/API projection latency or hydration mismatches

## Output

- Current active/planned tickets first
- Completed tickets only when requested or relevant
- Include clickable Memory Stargraph links
- Separate product/system delivery facts from data-quality warnings

## Useful read-only commands

```bash
gbrain get collections/mission-control-system-tickets
gbrain backlinks collections/mission-control-system-tickets
curl -sS 'http://127.0.0.1:4179/api/system-tickets?include_completed=0&refresh=1'
```
