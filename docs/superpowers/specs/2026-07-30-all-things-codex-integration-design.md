# All Things Codex Dashboard Integration Design

## Goal

Manage the existing local GTasks app from All Things Codex Dashboard without
copying GTasks, adding a datastore, or changing its GBrain-backed behavior.

## Architecture

All Things Codex Dashboard will add one allowlisted `gtasks` service entry.
The entry uses `/Users/tony/work/gtasks` as its working directory and launches
the existing module with the dashboard's Python interpreter:

```text
python -m gtasks.server --host 127.0.0.1 --port 4179
```

The manager will observe `http://127.0.0.1:4179/api/health`, open
`http://127.0.0.1:4179/`, and use its existing port-scoped start, stop, and
restart controls. The GTasks process continues to call GBrain directly and
does not gain any local task storage.

## Deployment

The tracked dashboard source owns the durable allowlist entry. Because the
installed dashboard runtime contains newer unrelated operational changes than
the tracked checkout, deployment will patch only the matching `gtasks` catalog
entry in the runtime copy and restart the dashboard LaunchAgent. The
destructive full installer will not be run.

GTasks will also carry a `dashboard-integration.json` contract documenting the
same URL, health path, working directory, command, and GBrain canonical-store
rule.

## Verification

- Automated tests validate both copies of the registration contract.
- The live dashboard API must show GTasks with Start, Stop, and Restart enabled.
- Start must make GTasks health report `canonical_store: gbrain`.
- A browser check must confirm the dashboard card and GTasks UI both render.
- Stop must return the card to `down`; the final desired state is started and
  healthy so the app is ready to open.
