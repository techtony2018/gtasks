# V0.0.88 unified Task/Ticket Markdown CDP QA gate

- **Terminal gate:** PASS
- **Date:** 2026-08-10
- **Candidate aggregate:** `fad5d52fa3c864b974dd841af9e88a98f20377ac30fd9ec5d353701ffd4ef8b5`
- **Browser:** Google Chrome `150.0.7871.187` through CDP `127.0.0.1:9333`
- **Candidate runtime:** `http://127.0.0.1:4180`, `/api/health` = `V0.0.88`
- **Canonical-data policy:** GET-only synthetic projections; no GBrain mutation

## Superseded result

This full-access rerun supersedes the earlier INCONCLUSIVE sandbox result. The
same Chrome listener was reachable, Playwright connected directly over CDP, and
the candidate UI was exercised in the user's real Chrome without closing or
restarting the browser.

## Independent rendered checks

### Desktop 1440x1000 — PASS

- Opened the synthetic Task from **All Tasks**.
- Verified heading hierarchy, explicit external Markdown links, bare-URL
  linkification, unavailable-reference fallback, fenced-code and inline-code
  literal rendering, and removal of a `javascript:` navigation target.
- Opened the referenced canonical System Ticket, then a nested referenced
  System Ticket, and closed back through both levels.
- Verified focus returned to the exact originating Markdown link at each level.
- Document width equaled viewport width: `1440 == 1440`; no horizontal overflow.

### Genuine mobile 390x844 — PASS

- Reused the same Task/Ticket fixture after setting the real page viewport to
  `390x844`.
- Task Markdown remained readable and links remained operable.
- The System Ticket detail sheet measured exactly `x=0`, `width=390`,
  `height=844` and remained inside the viewport.
- Document width equaled viewport width: `390 == 390`; no horizontal overflow.

## Safety and runtime evidence

| Check | Result |
|---|---|
| Console errors | 0 |
| Page errors | 0 |
| Failed requests | 0 |
| HTTP responses >= 400 | 0 |
| Non-GET requests | 0 |
| GBrain writes | 0 |

The QA route layer served only synthetic GET projections for one Task and two
System Tickets. It explicitly recorded and would have blocked any non-GET
request; none occurred.

## Evidence files

- `output/playwright/v0.0.88-final/gate-results.json`
- `output/playwright/v0.0.88-final/qa-steps.log`
- `output/playwright/v0.0.88-final/desktop-task-markdown.png`
- `output/playwright/v0.0.88-final/desktop-primary-ticket.png`
- `output/playwright/v0.0.88-final/mobile-task-markdown.png`
- `output/playwright/v0.0.88-final/mobile-primary-ticket.png`

Visual inspection of all four screenshots passed. This gate authorizes the
V0.0.88 candidate to proceed to full-suite verification, commit, push, and the
dashboard-managed deployment gate.

The aggregate was recomputed after removing Markdown line-ending whitespace in
the specification and plan. No runtime, UI, test, skill, README, or runbook file
changed after the rendered CDP run.
