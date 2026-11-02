#!/usr/bin/env bash
# Run the Milestone 1 demo task against a running agent.
#
# Usage:
#   scripts/run_demo.sh [repository] ["task text"]
#
# The default repository is the local fixture created by
# scripts/install_demo_repo.sh. The default task asks the agent to fix the
# failing test. The script polls the task API and prints the verified diff.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

read_env() {
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

API_KEY="$(read_env AGENT_API_KEY)"
PORT="$(read_env IO_AGENT_PORT)"
PORT="${PORT:-8091}"
BASE_URL="${AGENT_URL:-http://127.0.0.1:$PORT}"
REPO="${1:-fixture://demo-bugfix}"
TASK="${2:-Fix the failing test in the repository and show me the verified diff.
repo: $REPO}"

if [ -z "$API_KEY" ] || [ "$API_KEY" = "change-me-agent-api-key" ]; then
  echo "error: set a real AGENT_API_KEY in $ENV_FILE" >&2
  exit 1
fi

payload="$(python3 - "$TASK" "$REPO" <<'PY'
import json
import sys

print(json.dumps({
    "request": sys.argv[1],
    "repository": sys.argv[2],
    "user": "demo",
    "conversation_id": "milestone-1-demo",
}))
PY
)"

response="$(curl -sS --fail -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -X POST "$BASE_URL/v1/tasks" -d "$payload")"
task_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["task_id"])' <<<"$response")"
echo "Task started: $task_id"
echo "Repository:   $REPO"

terminal="completed failed incomplete needs_clarification rejected cancelled timeout"
final_status=""
for attempt in $(seq 1 360); do
  task_json="$(curl -sS --fail -H "Authorization: Bearer $API_KEY" "$BASE_URL/v1/tasks/$task_id")"
  line="$(python3 -c '
import json, sys
data = json.load(sys.stdin)
print(f"{data.get(\"status\")}|{data.get(\"phase\")}|{data.get(\"tests_passed\")}|{data.get(\"attempts\")}")
' <<<"$task_json")"
  status="${line%%|*}"
  echo "[$attempt] status=$status phase=$(echo "$line" | cut -d'|' -f2) tests_passed=$(echo "$line" | cut -d'|' -f3) attempts=$(echo "$line" | cut -d'|' -f4)"
  if echo "$terminal" | tr ' ' '\n' | grep -qx "$status"; then
    final_status="$status"
    break
  fi
  sleep 5
done

echo
curl -sS --fail -H "Authorization: Bearer $API_KEY" "$BASE_URL/v1/tasks/$task_id/diff" \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("diff") or "(no diff)")'

echo
curl -sS --fail -H "Authorization: Bearer $API_KEY" "$BASE_URL/v1/tasks/$task_id" \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("summary") or "(no summary)")'

if [ "$final_status" != "completed" ]; then
  echo "Demo did not complete successfully (status=${final_status:-timeout})." >&2
  exit 1
fi

echo "Demo completed with a verified diff."
