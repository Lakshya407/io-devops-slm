from __future__ import annotations

import json
import sys
from pathlib import Path

from dataset_export import export
from persistence import db
from persistence.repository import TaskRepository


def test_export_only_approved_traces(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'agent.db'}"
    db.configure(database_url)
    db.init_db()
    repo = TaskRepository()
    repo.create_task(task_id="t1", request="Fix the failing test", model="qwen2.5-coder:1.5b")
    trace_id = repo.save_trace(
        task_id="t1",
        trace={
            "request": "Fix the failing test\ncontact me at dev@example.com",
            "repo_url": "fixture://demo",
            "branch": "io-agent/t1",
            "model": "qwen2.5-coder:1.5b",
            "outcome": "completed",
            "attempts": 0,
            "test_result": {"passed": True, "command": "python -m pytest -q"},
            "diff_stat": " app/calculator.py | 2 +-",
            "diff": "-    return a - b\n+    return a + b",
        },
    )
    repo.set_trace_training_approval(trace_id, True, "verified by reviewer")
    repo.save_trace(task_id="t2", trace={"request": "not approved", "outcome": "failed"})

    output = tmp_path / "approved.jsonl"
    monkeypatch.setattr(sys, "argv", ["export", "--output", str(output), "--database-url", database_url])
    assert export.main() == 0

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["messages"][1]["content"].startswith("Fix the failing test")
    assert "<REDACTED>" in record["messages"][1]["content"]
    assert record["metadata"]["outcome"] == "completed"
    assert record["metadata"]["approved_for_training"] is True
    assert record["metadata"]["human_feedback"] == "verified by reviewer"
    assert "+    return a + b" in record["messages"][2]["content"]
