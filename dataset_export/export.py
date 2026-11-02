#!/usr/bin/env python3
"""Export human-approved execution traces as JSONL for dataset review.

Only traces explicitly marked ``approved_for_training`` are exported. Every
record is sanitized and contains no credentials. The output is a review format,
not a complete multi-turn SFT transcript: it captures the task, the verified
outcome, the final diff and the execution metadata.

Usage:
    python -m dataset_export.export --output /exports/approved_traces.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent.prompts.system import SYSTEM
from persistence import db
from persistence.repository import TaskRepository
from telemetry.sanitize import sanitize_obj, sanitize_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Output JSONL path")
    parser.add_argument("--database-url", default=os.environ.get("AGENT_DATABASE_URL"), help="SQLAlchemy database URL")
    parser.add_argument("--limit", type=int, default=0, help="Maximum records (0 = all)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.database_url:
        print("error: AGENT_DATABASE_URL is not set", file=sys.stderr)
        return 1
    output = Path(args.output or os.path.join(os.environ.get("AGENT_DATASET_EXPORT_DIR", "/exports"), "approved_traces.jsonl"))
    output.parent.mkdir(parents=True, exist_ok=True)

    db.configure(args.database_url)
    repository = TaskRepository()
    traces = repository.list_approved_traces()
    if args.limit > 0:
        traces = traces[: args.limit]

    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for item in traces:
            trace = item.get("trace") or {}
            if not isinstance(trace, dict):
                continue
            request = sanitize_text(str(trace.get("request") or ""), 20_000)
            outcome = sanitize_text(str(trace.get("outcome") or "unknown"), 200)
            diff = sanitize_text(str(trace.get("diff") or ""), 40_000)
            summary = (
                f"Outcome: {outcome}\n"
                f"Tests passed: {bool((trace.get('test_result') or {}).get('passed'))}\n"
                f"Correction attempts: {trace.get('attempts', 0)}\n\n"
                f"Verified diff:\n```diff\n{diff}\n```"
            )
            record = {
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": request},
                    {"role": "assistant", "content": summary},
                ],
                "metadata": {
                    "trace_id": item.get("id"),
                    "task_id": item.get("task_id"),
                    "repo_url": sanitize_text(str(trace.get("repo_url") or ""), 2_000),
                    "branch": sanitize_text(str(trace.get("branch") or ""), 256),
                    "model": sanitize_text(str(trace.get("model") or ""), 128),
                    "outcome": outcome,
                    "attempts": trace.get("attempts", 0),
                    "diff_stat": sanitize_text(str(trace.get("diff_stat") or ""), 4_000),
                    "human_feedback": sanitize_text(str(item.get("human_feedback") or ""), 4_000),
                    "approved_for_training": True,
                },
            }
            handle.write(json.dumps(sanitize_obj(record), ensure_ascii=False) + "\n")
            count += 1

    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    print(f"Exported {count} approved trace(s) to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
