"""Filesystem tools. All paths are confined to the assigned workspace."""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Dict, List

from execution.policies.path_policy import is_test_path, requires_approval
from execution.workspace.manager import SKIP_DIRS, WorkspacePathError
from telemetry.sanitize import truncate
from tools.base import ToolContext, ToolResult
from tools.filesystem.edit import EditError, LineEdit, apply_line_edits as apply_edits_pure, check_syntax, clean_replacement


async def list_dir(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    path = str(args.get("path") or ".")
    depth = int(args.get("depth") or 2)
    depth = max(1, min(depth, 4))
    try:
        tree = ctx.workspace.tree_at(ctx.workspace_id, path, max_entries=250, max_depth=depth)
    except Exception as exc:  # noqa: BLE001
        return ToolResult("error", message=str(exc))
    return ToolResult("success", {"path": path, "tree": tree}, f"listed {path}")


async def read_file(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    path = str(args.get("path") or "")
    if not path:
        return ToolResult("error", message="path is required")
    try:
        content, truncated = ctx.workspace.read_text(ctx.workspace_id, path, max_bytes=ctx.settings.max_file_bytes)
    except Exception as exc:  # noqa: BLE001
        return ToolResult("error", message=str(exc))
    lines = content.splitlines()
    start = args.get("start_line")
    end = args.get("end_line")
    if start or end:
        try:
            start_i = max(1, int(start or 1))
            end_i = min(len(lines), int(end or len(lines)))
        except (TypeError, ValueError):
            return ToolResult("error", message="start_line/end_line must be integers")
        content = "\n".join(lines[start_i - 1:end_i])
        lines = content.splitlines()
    return ToolResult(
        "success",
        {"path": path, "content": content, "lines": len(lines), "truncated": truncated},
        f"read {path} ({len(lines)} lines)",
    )


async def search_code(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult("error", message="query is required")
    glob = str(args.get("glob") or "*")
    max_results = max(1, min(int(args.get("max_results") or 50), 200))
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error as exc:
        return ToolResult("error", message=f"invalid regular expression: {exc}")
    matches: List[dict] = []
    root = ctx.workspace.open(ctx.workspace_id)
    for rel in ctx.workspace.list_files(ctx.workspace_id):
        if not fnmatch.fnmatch(rel, glob):
            continue
        if Path(rel).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".pdf", ".ico", ".woff", ".woff2"}:
            continue
        try:
            text, _ = ctx.workspace.read_text(ctx.workspace_id, rel, max_bytes=200_000)
        except Exception:  # noqa: BLE001
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                matches.append({"path": rel, "line": number, "text": truncate(line.strip(), 300)})
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break
    return ToolResult("success", {"query": query, "matches": matches, "count": len(matches)}, f"{len(matches)} matches for {query!r}")


async def write_file(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    path = str(args.get("path") or "")
    content = args.get("content")
    if not path or not isinstance(content, str):
        return ToolResult("error", message="path and string content are required")
    approval, reason = requires_approval(path, "write")
    if is_test_path(path) and not (ctx.allow_test_edits or ctx.approval_granted):
        return ToolResult("error", message="editing test files is disabled for this task; fix the implementation instead")
    if approval and not ctx.approval_granted:
        return ToolResult("approval_required", {"path": path, "action": "write"}, reason or "approval required")
    syntax_error = check_syntax(path, content)
    if syntax_error:
        return ToolResult("error", message=syntax_error)
    try:
        result = ctx.workspace.write_text(ctx.workspace_id, path, content)
    except Exception as exc:  # noqa: BLE001
        return ToolResult("error", message=str(exc))
    return ToolResult("success", result, f"wrote {path}")


async def apply_line_edits(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    path = str(args.get("path") or "")
    raw_edits = args.get("edits") or []
    if not path or not isinstance(raw_edits, list) or not raw_edits:
        return ToolResult("error", message="path and a non-empty edits list are required")
    if is_test_path(path) and not (ctx.allow_test_edits or ctx.approval_granted):
        return ToolResult(
            "error",
            message="refusing to edit a test file to make a failing test pass; fix the implementation instead",
        )
    approval, reason = requires_approval(path, "edit")
    if approval and not ctx.approval_granted:
        return ToolResult("approval_required", {"path": path, "action": "edit", "edits": raw_edits}, reason or "approval required")
    try:
        content, _ = ctx.workspace.read_text(ctx.workspace_id, path, max_bytes=ctx.settings.max_file_bytes)
    except Exception as exc:  # noqa: BLE001
        return ToolResult("error", message=str(exc))
    allowed_range = args.get("allowed_range")
    try:
        edits = [
            LineEdit(
                path=path,
                start_line=int(item["start_line"]),
                end_line=int(item["end_line"]),
                replacement=clean_replacement(item.get("replacement", "")),
                reason=item.get("reason"),
            )
            for item in raw_edits
        ]
        new_content, details = apply_edits_pure(
            content,
            edits,
            allowed_range=(int(allowed_range[0]), int(allowed_range[1])) if allowed_range else None,
        )
    except (EditError, KeyError, TypeError, ValueError) as exc:
        return ToolResult("error", message=str(exc))
    syntax_error = check_syntax(path, new_content)
    if syntax_error:
        return ToolResult("error", message=syntax_error, data={"path": path, "rejected": True})
    try:
        ctx.workspace.write_text(ctx.workspace_id, path, new_content)
    except Exception as exc:  # noqa: BLE001
        return ToolResult("error", message=str(exc))
    return ToolResult("success", {"path": path, "edits": details}, f"edited {path}")


async def delete_file(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    path = str(args.get("path") or "")
    if not path:
        return ToolResult("error", message="path is required")
    approval, reason = requires_approval(path, "delete")
    if approval and not ctx.approval_granted:
        return ToolResult("approval_required", {"path": path, "action": "delete"}, reason or "approval required")
    try:
        result = ctx.workspace.delete_file(ctx.workspace_id, path)
    except Exception as exc:  # noqa: BLE001
        return ToolResult("error", message=str(exc))
    return ToolResult("success", result, f"deleted {path}")


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec(
        name="list_dir",
        description="List a directory inside the workspace.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}, "depth": {"type": "integer"}}, "required": ["path"]},
        handler=list_dir,
        phases=["inspect"],
    ))
    registry.register(ToolSpec(
        name="read_file",
        description="Read a UTF-8 text file inside the workspace.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]},
        handler=read_file,
        phases=["inspect"],
    ))
    registry.register(ToolSpec(
        name="search_code",
        description="Search code in the workspace with a regular expression.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}, "glob": {"type": "string"}}, "required": ["query"]},
        handler=search_code,
        phases=["inspect"],
    ))
    registry.register(ToolSpec(
        name="write_file",
        description="Create or replace a file. Protected paths require approval.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        handler=write_file,
        phases=["edit"],
        destructive=True,
    ))
    registry.register(ToolSpec(
        name="apply_line_edits",
        description="Apply validated 1-based line edits to a file.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array"}}, "required": ["path", "edits"]},
        handler=apply_line_edits,
        phases=["edit"],
        destructive=True,
    ))
    registry.register(ToolSpec(
        name="delete_file",
        description="Delete a file. Always requires human approval.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=delete_file,
        phases=["edit"],
        destructive=True,
    ))
