#!/usr/bin/env bash
# Register the agent as a model connection in the existing Open WebUI.
#
# This is the scripted equivalent of Settings -> Connections. It backs up the
# Open WebUI SQLite database, appends one OpenAI-compatible connection, and
# restarts Open WebUI. Existing LiteLLM connections and keys are preserved.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
CONTAINER="${OPENWEBUI_CONTAINER:-open-webui}"
AGENT_BASE_URL="${AGENT_BASE_URL:-http://io-agent:8080/v1}"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: $ENV_FILE not found" >&2
  exit 1
fi

read_env() {
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

API_KEY="$(read_env AGENT_API_KEY)"
MODEL_ID="$(read_env AGENT_PUBLIC_MODEL_ID)"
MODEL_ID="${MODEL_ID:-io-coding-agent}"

if [ -z "$API_KEY" ] || [ "$API_KEY" = "change-me-agent-api-key" ]; then
  echo "error: set a real AGENT_API_KEY in $ENV_FILE first" >&2
  exit 1
fi

if ! sudo -n docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "error: container '$CONTAINER' not found" >&2
  exit 1
fi

remote_script="/tmp/io_agent_register_openwebui.py"
sudo -n docker cp "$ROOT_DIR/scripts/register_openwebui_connection.py" "$CONTAINER:$remote_script"
sudo -n docker exec \
  -e AGENT_API_KEY="$API_KEY" \
  -e AGENT_PUBLIC_MODEL_ID="$MODEL_ID" \
  -e AGENT_BASE_URL="$AGENT_BASE_URL" \
  "$CONTAINER" python "$remote_script"
sudo -n docker exec "$CONTAINER" rm -f "$remote_script"

echo "Restarting Open WebUI to reload the connection..."
sudo -n docker restart "$CONTAINER" >/dev/null

for _ in $(seq 1 45); do
  status="$(sudo -n docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)"
  if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
    echo "Open WebUI is $status"
    exit 0
  fi
  sleep 2
done

echo "warning: Open WebUI did not report healthy in time; check 'docker logs $CONTAINER'" >&2
exit 1
