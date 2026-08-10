# Mission Control hourly reconciliation

- This is a read-only check. Use supported Mission Control read APIs only and refresh canonical state before drawing conclusions.
- Review this Agent's owned work first, then eligible delegated work second.
- Confirm whether an actionable item lacks a recent handoff or whether a blocked item has new information.
- Do not claim, execute, or mutate a Task, TODO, delegation, Artifact, Timeline, Agent profile, account, or external system during heartbeat. The authenticated Dispatcher remains the execution authority.
- If attention is needed, report one concise verified issue and its next safe action in this fixed session.
- If nothing needs attention, reply `HEARTBEAT_OK` and do not notify externally.
