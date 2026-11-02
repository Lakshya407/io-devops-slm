#!/usr/bin/env bash
# Create the dedicated io_agent database and least-privilege role.
#
# The existing PostgreSQL container is reused; no LiteLLM tables are touched.
# The password is never printed. If IO_AGENT_DB_PASSWORD is absent from .env,
# a random value is generated and appended to .env with 0600 permissions.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
PG_CONTAINER="${PG_CONTAINER:-litellm_postgres}"
PG_SUPERUSER="${PG_SUPERUSER:-litellm}"
PG_DATABASE="${PG_DATABASE:-postgres}"
DB_NAME="${DB_NAME:-io_agent}"
DB_USER="${DB_USER:-io_agent}"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: $ENV_FILE not found; copy .env.example to .env first" >&2
  exit 1
fi

read_env() {
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

validate_identifier() {
  case "$1" in
    ""|*[!A-Za-z0-9_]*) echo "error: invalid SQL identifier: $1" >&2; exit 1 ;;
  esac
}

validate_identifier "$DB_NAME"
validate_identifier "$DB_USER"

if ! sudo -n docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
  echo "error: PostgreSQL container '$PG_CONTAINER' not found" >&2
  exit 1
fi

DB_PASSWORD="$(read_env IO_AGENT_DB_PASSWORD)"
if [ -z "$DB_PASSWORD" ]; then
  DB_PASSWORD="$(openssl rand -hex 24)"
  umask 077
  printf '\nIO_AGENT_DB_PASSWORD=%s\n' "$DB_PASSWORD" >> "$ENV_FILE"
  echo "Generated IO_AGENT_DB_PASSWORD and stored it in $ENV_FILE"
fi

case "$DB_PASSWORD" in
  *[!A-Za-z0-9._~-]*) echo "error: IO_AGENT_DB_PASSWORD must be URL-safe (letters, digits, . _ ~ -)" >&2; exit 1 ;;
esac

# Keep AGENT_DATABASE_URL in sync when it still contains the template value.
if grep -q '^AGENT_DATABASE_URL=.*change-me-db-password' "$ENV_FILE"; then
  sed -i "s|^AGENT_DATABASE_URL=.*|AGENT_DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}|" "$ENV_FILE"
elif ! grep -q '^AGENT_DATABASE_URL=' "$ENV_FILE"; then
  umask 077
  printf '\nAGENT_DATABASE_URL=postgresql+psycopg://%s:%s@postgres:5432/%s\n' "$DB_USER" "$DB_PASSWORD" "$DB_NAME" >> "$ENV_FILE"
fi

psql_super() {
  sudo -n docker exec -i "$PG_CONTAINER" psql -U "$PG_SUPERUSER" -d "$PG_DATABASE" "$@"
}

psql_db() {
  sudo -n docker exec -i "$PG_CONTAINER" psql -U "$PG_SUPERUSER" -d "$DB_NAME" "$@"
}

role_exists="$(psql_super -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")"
if [ "$role_exists" = "1" ]; then
  psql_super -v ON_ERROR_STOP=1 -c "ALTER ROLE \"$DB_USER\" WITH LOGIN PASSWORD '$DB_PASSWORD'" >/dev/null
  echo "Updated existing role '$DB_USER'"
else
  psql_super -v ON_ERROR_STOP=1 -c "CREATE ROLE \"$DB_USER\" WITH LOGIN PASSWORD '$DB_PASSWORD'" >/dev/null
  echo "Created role '$DB_USER'"
fi

db_exists="$(psql_super -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")"
if [ "$db_exists" = "1" ]; then
  echo "Database '$DB_NAME' already exists"
else
  psql_super -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\"" >/dev/null
  echo "Created database '$DB_NAME'"
fi

psql_super -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE \"$DB_NAME\" TO \"$DB_USER\"" >/dev/null
psql_db -v ON_ERROR_STOP=1 -c "ALTER SCHEMA public OWNER TO \"$DB_USER\"" >/dev/null
psql_db -v ON_ERROR_STOP=1 -c "GRANT ALL ON SCHEMA public TO \"$DB_USER\"" >/dev/null

echo "Database bootstrap complete: $DB_NAME (owner: $DB_USER)"
