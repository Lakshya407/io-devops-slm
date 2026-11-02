#!/usr/bin/env bash
# One-shot deployment for the IO Coding Agent on the existing Docker VM.
#
# It does not modify the LiteLLM or Open WebUI Compose files. It creates the
# dedicated database, a scoped LiteLLM key, host directories, the demo fixture,
# and then starts this separate Compose project.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="$ROOT_DIR/.env"
PORT="${IO_AGENT_PORT:-8091}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Creating .env from .env.example"
  umask 077
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

read_env() {
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# Docker installed as a snap is confined and cannot bind-mount /srv. Detect
# that layout and keep host data under the home directory instead.
docker_root="$(sudo -n docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
if [ -z "${IO_AGENT_HOST_ROOT:-}" ] && printf '%s' "$docker_root" | grep -q '/snap/docker'; then
  configured_root="$(read_env IO_AGENT_HOST_ROOT)"
  if [ -z "$configured_root" ] || [ "$configured_root" = "/srv/io-agent" ]; then
    set_env IO_AGENT_HOST_ROOT "$HOME/io-agent-data"
    echo "Docker runs as a snap; storing agent data in $HOME/io-agent-data"
  fi
fi

HOST_ROOT="$(read_env IO_AGENT_HOST_ROOT)"
HOST_ROOT="${HOST_ROOT:-/srv/io-agent}"
export IO_AGENT_HOST_ROOT="$HOST_ROOT"

# Generate deployment secrets only when they are still template values.
agent_key="$(read_env AGENT_API_KEY)"
if [ -z "$agent_key" ] || [ "$agent_key" = "change-me-agent-api-key" ]; then
  set_env AGENT_API_KEY "$(openssl rand -hex 32)"
fi

sandbox_token="$(read_env SANDBOX_TOKEN)"
if [ -z "$sandbox_token" ] || [ "$sandbox_token" = "change-me-sandbox-token" ]; then
  set_env SANDBOX_TOKEN "$(openssl rand -hex 32)"
fi

db_password="$(read_env IO_AGENT_DB_PASSWORD)"
if [ -z "$db_password" ]; then
  set_env IO_AGENT_DB_PASSWORD "$(openssl rand -hex 24)"
fi

chmod 600 "$ENV_FILE"

echo "Preparing host directories under $HOST_ROOT"
sudo -n mkdir -p "$HOST_ROOT/workspaces" "$HOST_ROOT/repos" "$HOST_ROOT/exports"
sudo -n chown -R 10001:10001 "$HOST_ROOT/workspaces" "$HOST_ROOT/exports"
sudo -n chmod 0770 "$HOST_ROOT/workspaces" "$HOST_ROOT/exports"
sudo -n chmod 755 "$HOST_ROOT/repos"

echo "Bootstrapping the dedicated database"
"$ROOT_DIR/scripts/bootstrap_db.sh"

echo "Creating a scoped LiteLLM virtual key"
"$ROOT_DIR/scripts/create_litellm_key.sh"

echo "Preparing the demo fixture repository"
"$ROOT_DIR/scripts/install_demo_repo.sh"

echo "Building and starting the io-agent Compose project"
sudo -n docker compose up -d --build

container_id="$(sudo -n docker compose ps -q io-agent || true)"
if [ -z "$container_id" ]; then
  echo "error: the io-agent container did not start" >&2
  sudo -n docker compose ps >&2 || true
  exit 1
fi

echo "Waiting for the agent health check..."
for _ in $(seq 1 90); do
  status="$(sudo -n docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
  if [ "$status" = "healthy" ]; then
    break
  fi
  sleep 2
done

if [ "${status:-}" != "healthy" ]; then
  echo "error: agent did not become healthy (last status: ${status:-unknown})" >&2
  sudo -n docker compose logs --tail=80 io-agent >&2 || true
  exit 1
fi
echo "Agent is healthy"

echo "Registering the agent connection in Open WebUI"
"$ROOT_DIR/scripts/register_openwebui_connection.sh"

agent_key="$(read_env AGENT_API_KEY)"
model_id="$(read_env AGENT_PUBLIC_MODEL_ID)"
model_id="${model_id:-io-coding-agent}"
echo "Verifying the public API on 127.0.0.1:$PORT"
curl -sS --fail -H "Authorization: Bearer $agent_key" "http://127.0.0.1:$PORT/v1/models" >/dev/null
curl -sS --fail "http://127.0.0.1:$PORT/health" >/dev/null

echo
echo "Deployment complete."
echo "  Agent API:      http://127.0.0.1:$PORT/v1 (loopback only)"
echo "  Health:         http://127.0.0.1:$PORT/health"
echo "  Open WebUI:     http://127.0.0.1:8080 (select '$model_id')"
echo "  Demo:           scripts/run_demo.sh"
echo
echo "The agent is also reachable from Open WebUI as http://io-agent:8080/v1."
