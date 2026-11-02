"""Git tools. The agent works on a dedicated branch and never pushes."""
from __future__ import annotations

import re
from typing import Any, Dict

from execution.policies.command_policy import validate_argv
from tools.base import ToolContext, ToolResult


def _check(args: list[str]) -> ToolResult | None:
    ok, reason = validate_argv(["git", *args])
    if not ok:
        return ToolResult("error", message=reason or "git command denied")
    return None


async def git_status(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    result = ctx.workspace.git(ctx.workspace_id, ["status", "--porcelain=v1", "-b"])
    return ToolResult("success" if result["exit_code"] == 0 else "error", result, "git status")


async def git_diff(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    result = ctx.workspace.git(ctx.workspace_id, ["diff", "--no-color", "--no-ext-diff", "--", "."])
    return ToolResult("success" if result["exit_code"] == 0 else "error", result, "git diff")


async def git_log(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    result = ctx.workspace.git(ctx.workspace_id, ["log", "--oneline", "-n", "20"])
    return ToolResult("success" if result["exit_code"] == 0 else "error", result, "git log")


async def git_branch(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    current = ctx.workspace.git(ctx.workspace_id, ["branch", "--show-current"])
    all_branches = ctx.workspace.git(ctx.workspace_id, ["branch", "--list"])
    return ToolResult("success", {"current": current["stdout"].strip(), "branches": all_branches["stdout"]}, "git branch")


async def git_checkout(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    name = str(args.get("branch") or "").strip()
    if not re.match(r"^[A-Za-z0-9._/-]{1,120}$", name):
        return ToolResult("error", message="invalid branch name")
    argv = ["git", "checkout", "-b", name]
    denied = _check(argv[1:])
    if denied:
        return denied
    result = ctx.workspace.git(ctx.workspace_id, argv[1:])
    return ToolResult("success" if result["exit_code"] == 0 else "error", result, f"created branch {name}")


async def git_add(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    paths = args.get("paths") or ["."]
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return ToolResult("error", message="paths must be a list of strings")
    if any(p.startswith("-") for p in paths):
        return ToolResult("error", message="invalid path")
    argv = ["git", "add", "--", *paths]
    denied = _check(argv[1:])
    if denied:
        return denied
    result = ctx.workspace.git(ctx.workspace_id, argv[1:])
    return ToolResult("success" if result["exit_code"] == 0 else "error", result, "git add")


async def git_commit(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    message = str(args.get("message") or "").strip()
    if not message or len(message) > 200:
        return ToolResult("error", message="commit message is required (max 200 chars)")
    argv = ["git", "commit", "-m", message]
    denied = _check(argv[1:])
    if denied:
        return denied
    result = ctx.workspace.git(ctx.workspace_id, argv[1:])
    return ToolResult("success" if result["exit_code"] == 0 else "error", result, "git commit")


def register(registry) -> None:
    from tools.base import ToolSpec

    specs = [
        ("git_status", "Show git status.", git_status, ["inspect", "verify"]),
        ("git_diff", "Show the working tree diff.", git_diff, ["verify"]),
        ("git_log", "Show recent commits.", git_log, ["inspect", "verify"]),
        ("git_branch", "Show the current branch and branch list.", git_branch, ["inspect", "verify"]),
        ("git_checkout", "Create a new branch (only -b is allowed).", git_checkout, ["edit"]),
        ("git_add", "Stage files.", git_add, ["edit"]),
        ("git_commit", "Commit staged changes to the dedicated branch (never pushes).", git_commit, ["edit"]),
    ]
    for name, description, handler, phases in specs:
        registry.register(ToolSpec(name=name, description=description, parameters={"type": "object", "properties": {}}, handler=handler, phases=phases))
