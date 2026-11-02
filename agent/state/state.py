"""Typed state for the LangGraph workflow."""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    task_id: str
    request: str
    conversation: List[Dict[str, str]]
    user_id: str | None

    repo_url: str | None
    repo_ref: str | None
    workspace_id: str | None
    branch: str | None

    tree: str
    languages: Dict[str, int]
    manifests: str
    key_files: str
    inspected_files: List[str]
    observations: List[str]
    context_files: Dict[str, str]
    edit_paths: List[str]

    plan: List[str]
    plan_files: List[str]
    plan_source: str
    test_command: str | None

    test_result: Dict[str, Any] | None
    attempts: int
    max_attempts: int

    edits: List[Dict[str, Any]]
    diagnosis: str | None

    diff: str | None
    diff_stat: str | None
    git_status: str | None

    status: str
    phase: str
    final_summary: str | None
    error: str | None
    approval: Dict[str, Any] | None
    approval_granted: bool
