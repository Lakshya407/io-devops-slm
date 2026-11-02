from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_openwebui_connection.py"


def _read_config(path: Path) -> dict:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT key, value FROM config").fetchall()
        return {key: json.loads(value) for key, value in rows}
    finally:
        connection.close()


def test_registration_preserves_existing_connection_and_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "webui.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value JSON NOT NULL, updated_at BIGINT NOT NULL)")
        connection.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            ("openai.api_base_urls", json.dumps(["http://litellm:4000/v1"]), 1),
        )
        connection.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            ("openai.api_keys", json.dumps(["sk-lite-12345"]), 1),
        )
        connection.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            ("openai.api_configs", json.dumps({"0": {"enable": True, "connection_type": "local"}}), 1),
        )
        connection.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            ("openai.enable", json.dumps(True), 1),
        )
        connection.commit()
    finally:
        connection.close()

    environment = {
        **os.environ,
        "WEBUI_DB": str(database),
        "AGENT_API_KEY": "agent-test-key",
        "AGENT_PUBLIC_MODEL_ID": "io-coding-agent",
        "AGENT_BASE_URL": "http://io-agent:8080/v1",
    }
    for _ in range(2):
        subprocess.run([sys.executable, str(SCRIPT)], env=environment, check=True, capture_output=True, text=True)

    config = _read_config(database)
    assert config["openai.api_base_urls"] == ["http://litellm:4000/v1", "http://io-agent:8080/v1"]
    assert config["openai.api_keys"] == ["sk-lite-12345", "agent-test-key"]
    assert config["openai.api_configs"]["0"] == {"enable": True, "connection_type": "local"}
    assert config["openai.api_configs"]["1"]["model_ids"] == ["io-coding-agent"]
    assert config["openai.api_configs"]["1"]["auth_type"] == "bearer"
    assert config["openai.enable"] is True
    assert len(list(tmp_path.glob("webui.db.io-agent-backup-*"))) == 2
