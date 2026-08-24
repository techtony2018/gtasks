#!/bin/zsh
set -euo pipefail

export GBRAIN_HOME=/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-remote
GBRAIN_CONFIG_FILE="$GBRAIN_HOME/.gbrain/config.json"
GBRAIN_CREDENTIALS_FILE=/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-remote/credentials.env
PROVISION_TOKEN_FILE=/Users/tony/.codex/services/all-things-codex-dashboard/state/openclaw-profile-activation/provision.token
BUZZ_ENV_FILE=/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/buzz.env
GOAL_EXECUTION_ENV_FILE=/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks/goal-execution.env

require_owner_file() {
  local file_path="$1"
  [[ -f "$file_path" && ! -L "$file_path" ]] || {
    print -u2 -- "Mission Control runtime credential file is unavailable"
    exit 70
  }
  [[ "$(/usr/bin/stat -f '%Lp' "$file_path")" = "600" ]] || {
    print -u2 -- "Mission Control runtime credential permissions are invalid"
    exit 70
  }
}

require_owner_file "$GBRAIN_CONFIG_FILE"
require_owner_file "$GBRAIN_CREDENTIALS_FILE"
require_owner_file "$PROVISION_TOKEN_FILE"
require_owner_file "$BUZZ_ENV_FILE"

/opt/homebrew/opt/python@3.12/libexec/bin/python3 - "$GBRAIN_CONFIG_FILE" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
remote = config.get("remote_mcp") if isinstance(config, dict) else None
if not isinstance(remote, dict):
    raise SystemExit("Mission Control GBrain remote_mcp config is unavailable")
for field in ("issuer_url", "mcp_url", "oauth_client_id"):
    if not isinstance(remote.get(field), str) or not remote[field].strip():
        raise SystemExit(f"Mission Control GBrain remote_mcp {field} is unavailable")
PY

export GBRAIN_REMOTE_CLIENT_SECRET="$(
  sed -n 's/^GBRAIN_REMOTE_CLIENT_SECRET=//p' "$GBRAIN_CREDENTIALS_FILE" | head -n 1
)"
[[ -n "$GBRAIN_REMOTE_CLIENT_SECRET" ]] || {
  print -u2 -- "Mission Control GBrain remote client secret is unavailable"
  exit 70
}

export MEMORY_STARGRAPH_URL=http://127.0.0.1:8788
export MEMORY_STARGRAPH_OC_PROVISION_TOKEN="$(<"$PROVISION_TOKEN_FILE")"
export PATH="/Users/tony/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

set -a
source "$BUZZ_ENV_FILE"
set +a

[[ -n "${BUZZ_PRIVATE_KEY:-}" && -n "${BUZZ_RELAY_URL:-}" ]] || {
  print -u2 -- "Mission Control Buzz runtime credentials are unavailable"
  exit 70
}
[[ -n "${MISSION_CONTROL_BUZZ_OUTBOX_DIR:-}" ]] || {
  print -u2 -- "Mission Control Buzz outbox is unavailable"
  exit 70
}

if [[ -f "$GOAL_EXECUTION_ENV_FILE" ]]; then
  require_owner_file "$GOAL_EXECUTION_ENV_FILE"
  set -a
  source "$GOAL_EXECUTION_ENV_FILE"
  set +a
fi

MISSION_CONTROL_GOAL_EXECUTION_MODE="${MISSION_CONTROL_GOAL_EXECUTION_MODE:-shadow}"
case "$MISSION_CONTROL_GOAL_EXECUTION_MODE" in
  off|shadow|canary) ;;
  *)
    print -u2 -- "Mission Control Goal execution mode is invalid"
    exit 70
    ;;
esac
if [[ "$MISSION_CONTROL_GOAL_EXECUTION_MODE" = "canary" ]]; then
  [[ "${MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL:-}" = "auto" || "${MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL:-}" =~ '^goals/[0-9a-f]{8}-[0-9a-f]{4}-[45][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' ]] || {
    print -u2 -- "Mission Control canary mode requires one canonical Goal slug or auto"
    exit 70
  }
fi
export MISSION_CONTROL_GOAL_EXECUTION_MODE
export MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL="${MISSION_CONTROL_GOAL_EXECUTION_CANARY_GOAL:-}"

exec /opt/homebrew/opt/python@3.12/libexec/bin/python3 "$@"
