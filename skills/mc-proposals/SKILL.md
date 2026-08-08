---
name: mc-proposals
description: >
  Use when user invokes /mc-proposals or asks about Mission Control Proposed
  Tasks inbox items, pending/approved/rejected proposal status, proposal
  evidence, fingerprints, or proposal timeline display.
---

# Mission Control Proposed Tasks Query

Use this skill for read-only inspection of proposed tasks. Apply the `mc-core`
rules and treat every proposal as requiring Tony's explicit approval before it
becomes formal work.

## What to inspect

- Proposed Tasks root: `collections/gtasks-proposed-work`
- Proposal status: pending, approved, rejected, dismissed, superseded, or stale
- Evidence and fingerprint fields if present
- Linked formal task or ticket created after approval, if present
- Timeline/history entries for status changes
- Inbox projection mismatches, especially non-pending proposals still appearing
  in the pending list

## Output

- Group by status
- Show pending proposals first
- For approved/rejected proposals, include the status and relevant timeline note
  when the user asks about history or Inbox display
- Warn about duplicate or unlinked proposals separately

Do not approve, reject, dismiss, or convert proposals from this skill.

## Useful read-only commands

```bash
gbrain get collections/gtasks-proposed-work
gbrain backlinks collections/gtasks-proposed-work
curl -sS 'http://127.0.0.1:4179/api/proposed-tasks?refresh=1'
```
