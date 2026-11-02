#!/usr/bin/env bash
# Create a dedicated LiteLLM virtual key for the coding agent.
#
# The key is scoped to the configured model alias and is written to .env as
# LITELLM_API_KEY. The value is never printed. The LiteLLM master key is read
# from the existing LiteLLM .env without sourcing it (the file contains values
# that are not safe to source).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
LITELLM_ENV_FILE="${LITELLM_ENV_FILE:-$HOME/Io-agent/litellm/.env}"
LITELLM_URL="${LITELLM_URL:-http://127.0.0.1:4000}"
FORCE="${FORCE:-0}"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: $ENV_FILE not found" >&2
  exit 1
fi

read_env_file() {
  local file="$1" key="$2"
  grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true
}

AGENT_MODEL="${AGENT_MODEL:-$(read_env_file "$ENV_FILE" AGENT_MODEL)}"
AGENT_MODEL="${AGENT_MODEL:-qwen2.5-coder:1.5b}"
EXISTING_KEY="$(read_env_file "$ENV_FILE" LITELLM_API_KEY)"

if [ "$FORCE" != "1" ] && [ -n "$EXISTING_KEY" ] && [ "$EXISTING_KEY" != "change-me-litellm-virtual-key" ]; then
  echo "LITELLM_API_KEY is already set in $ENV_FILE; use FORCE=1 to rotate it"
  exit 0
fi

MASTER_KEY="${LITELLM_MASTER_KEY:-}"
if [ -z "$MASTER_KEY" ] && [ -f "$LITELLM_ENV_FILE" ]; then
  MASTER_KEY="$(read_env_file "$LITELLM_ENV_FILE" LITELLM_MASTER_KEY)"
fi
if [ -z "$MASTER_KEY" ]; then
  echo "error: LITELLM_MASTER_KEY not found in environment or $LITELLM_ENV_FILE" >&2
  exit 1
fi

payload="$(python3 - "$AGENT_MODEL" <<'PY'
import json
import sys

print(json.dumps({
    "models": [sys.argv[1]],
    "key_alias": "io-coding-agent",
    "metadata": {"purpose": "io-coding-agent", "owner": "io-agent"},
}))
PY
)"

response="$(curl -sS --fail-with-body \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -X POST "$LITELLM_URL/key/generate" \
  -d "$payload")" || {
  echo "error: LiteLLM key generation failed" >&2
  exit 1
}

new_key="$(python3 - "$response" <<'PY'
import json
import sys

try:
    data = json.loads(sys.argv[1])
except Exception:
    print("")
    raise SystemExit(0)
print(data.get("key") or data.get("token") or "")
PY
)"

if [ -z "$new_key" ]; then
  echo "error: LiteLLM did not return a key; response fields were not stored" >&2
  exit 1
fi

umask 077
if grep -q '^LITELLM_API_KEY=' "$ENV_FILE"; then
  sed -i "s|^LITELLM_API_KEY=.*|LITELLM_API_KEY=${new_key}|" "$ENV_FILE"
else
  printf '\nLITELLM_API_KEY=%s\n' "$new_key" >> "$ENV_FILE"
fi

# Verify the scoped key against the gateway without printing it.
code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $new_key" "$LITELLM_URL/v1/models")"
echo "Stored a scoped LiteLLM key for model '$AGENT_MODEL' in $ENV_FILE (not displayed); /v1/models returned HTTP $code"
