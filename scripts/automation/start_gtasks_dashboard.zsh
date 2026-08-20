#!/bin/zsh
set -euo pipefail

export GBRAIN_HOME=/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-remote
GBRAIN_CONFIG_FILE="$GBRAIN_HOME/.gbrain/config.json"
GBRAIN_CREDENTIALS_FILE=/Users/tony/.codex/services/all-things-codex-dashboard/state/gtasks-remote/credentials.env
PROVISION_TOKEN_FILE=/Users/tony/.codex/services/all-things-codex-dashboard/state/openclaw-profile-activation/provision.token

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

exec /opt/homebrew/opt/python@3.12/libexec/bin/python3 "$@"
