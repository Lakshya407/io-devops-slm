"""Register the IO Coding Agent as an OpenAI-compatible connection in Open WebUI.

This script runs inside the existing ``open-webui`` container. Open WebUI
stores its connection settings in the key-value ``config`` table of
``/app/backend/data/webui.db``. The script backs up the database, then appends
(or updates) one connection without touching any other provider.

Environment:
  AGENT_BASE_URL        default http://io-agent:8080/v1
  AGENT_API_KEY         required
  AGENT_PUBLIC_MODEL_ID default io-coding-agent
  WEBUI_DB              default /app/backend/data/webui.db
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

BASE_URL = (os.environ.get("AGENT_BASE_URL") or "http://io-agent:8080/v1").rstrip("/")
API_KEY = os.environ.get("AGENT_API_KEY") or ""
MODEL_ID = os.environ.get("AGENT_PUBLIC_MODEL_ID") or "io-coding-agent"
DB_PATH = Path(os.environ.get("WEBUI_DB") or "/app/backend/data/webui.db")


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


if not API_KEY:
    fail("AGENT_API_KEY is required")
if not DB_PATH.is_file():
    fail(f"Open WebUI database not found: {DB_PATH}")


def backup(path: Path) -> Path:
    target = path.with_name(
        f"{path.name}.io-agent-backup-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    )
    source = sqlite3.connect(str(path))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def get_value(connection: sqlite3.Connection, key: str, default):
    row = connection.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is None or row[0] is None:
        return default
    value = row[0]
    if isinstance(value, (dict, list, bool, int, float)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def set_value(connection: sqlite3.Connection, key: str, value) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    connection.execute(
        "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, payload, int(time.time())),
    )


def main() -> None:
    backup_path = backup(DB_PATH)
    print(f"Backed up Open WebUI database to {backup_path}")

    connection = sqlite3.connect(str(DB_PATH))
    try:
        base_urls = get_value(connection, "openai.api_base_urls", [])
        api_keys = get_value(connection, "openai.api_keys", [])
        api_configs = get_value(connection, "openai.api_configs", {})
        if not isinstance(base_urls, list):
            base_urls = []
        if not isinstance(api_keys, list):
            api_keys = []
        if isinstance(api_configs, list):
            api_configs = {str(index): item for index, item in enumerate(api_configs)}
        if not isinstance(api_configs, dict):
            api_configs = {}

        if BASE_URL in base_urls:
            index = base_urls.index(BASE_URL)
            print(f"Updating existing Open WebUI connection at index {index}")
        else:
            index = len(base_urls)
            base_urls.append(BASE_URL)
            print(f"Adding Open WebUI connection at index {index}")

        while len(api_keys) < len(base_urls):
            api_keys.append("")
        api_keys[index] = API_KEY

        api_configs[str(index)] = {
            "enable": True,
            "tags": ["io-agent"],
            "prefix_id": "",
            "model_ids": [MODEL_ID],
            "connection_type": "local",
            "auth_type": "bearer",
            "forward_cookies": False,
            "passthrough_params": [],
        }

        set_value(connection, "openai.api_base_urls", base_urls)
        set_value(connection, "openai.api_keys", api_keys)
        set_value(connection, "openai.api_configs", api_configs)
        set_value(connection, "openai.enable", True)
        connection.commit()
    finally:
        connection.close()

    print(f"Registered {MODEL_ID} at {BASE_URL} (key stored, not displayed)")


if __name__ == "__main__":
    main()
