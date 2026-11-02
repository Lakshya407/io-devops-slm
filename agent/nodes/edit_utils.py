"""Shared edit helpers for the first edit and the diagnose retry."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from agent.nodes.context import TaskContext
from agent.prompts.system import SYSTEM
from agent.protocol import EditProposal, ProtocolError, parse_edits, repair_prompt
from execution.policies.path_policy import is_test_path, requires_approval
from tools.filesystem.edit import EditError, LineEdit, apply_line_edits as apply_pure, check_syntax

log = logging.getLogger(__name__)


class WorkflowError(RuntimeError):
    pass


@dataclass
class EditOutcome:
    applied: bool
    details: List[dict] = field(default_factory=list)
    summary: str = ""
    thought: str = ""
    approval: Dict[str, Any] | None = None
    errors: List[str] = field(default_factory=list)


def build_file_context(ctx: TaskContext, state: Dict[str, Any], *, max_chars: int = 24_000) -> Tuple[str, Dict[str, List[Tuple[int, int]]], List[str]]:
    context_files: Dict[str, str] = state.get("context_files") or {}
    editable = [p for p in (state.get("edit_paths") or []) if p in context_files and not is_test_path(p)]
    if not editable:
        editable = [p for p in (state.get("plan_files") or []) if p in context_files and not is_test_path(p)]
    if not editable:
        editable = [p for p in context_files if not is_test_path(p)]
    editable = editable[: ctx.settings.max_context_files]
    reference = [p for p in context_files if p not in editable][:3]

    text, ranges = ctx.workspace.edit_context(ctx.workspace_id, editable, max_chars=max_chars)
    if reference:
        ref_chunks = []
        for rel in reference:
            ref_chunks.append("REFERENCE ONLY — DO NOT EDIT:\n" + ctx.workspace.numbered_file(ctx.workspace_id, rel, max_chars=12_000))
        text = text + "\n\n" + "\n\n".join(ref_chunks)
    return text, ranges, editable


def _proposal_to_line_edit(ctx: TaskContext, proposal: EditProposal, allowed_ranges: Dict[str, List[Tuple[int, int]]]) -> LineEdit:
    path = proposal.path
    if path not in allowed_ranges:
        raise EditError(f"{path} is not one of the files you are allowed to edit")
    if proposal.start_line is not None or proposal.end_line is not None:
        if proposal.start_line is None or proposal.end_line is None:
            raise EditError("both start_line and end_line are required")
        return LineEdit(
            path=path,
            start_line=int(proposal.start_line),
            end_line=int(proposal.end_line),
            replacement=proposal.replacement or "",
            reason=proposal.reason,
        )
    if proposal.search is not None:
        content, _ = ctx.workspace.read_text(ctx.workspace_id, path, max_bytes=ctx.settings.max_file_bytes)
        search = proposal.search
        count = content.count(search)
        if count == 0:
            raise EditError(f"search text was not found in {path}")
        if count > 1:
            raise EditError(f"search text occurs {count} times in {path}; use line edits instead")
        start_line = content[: content.index(search)].count("\n") + 1
        end_line = start_line + search.count("\n")
        return LineEdit(path=path, start_line=start_line, end_line=end_line, replacement=proposal.replace or "", reason=proposal.reason)
    raise EditError("each edit needs start_line/end_line/replacement or search/replace")


def _group_edits(ctx: TaskContext, proposals: List[EditProposal], allowed_ranges: Dict[str, List[Tuple[int, int]]]) -> Dict[str, List[LineEdit]]:
    grouped: Dict[str, List[LineEdit]] = {}
    for proposal in proposals:
        edit = _proposal_to_line_edit(ctx, proposal, allowed_ranges)
        grouped.setdefault(edit.path, []).append(edit)
    for path, edits in grouped.items():
        lo, hi = allowed_ranges[path][0]
        for edit in edits:
            if edit.start_line < lo or edit.end_line > hi:
                raise EditError(f"edit for {path} is outside the editable range {lo}-{hi}")
    return grouped


async def request_and_apply_edits(
    ctx: TaskContext,
    prompt: str,
    *,
    allowed_ranges: Dict[str, List[Tuple[int, int]]],
    phase: str,
    max_tokens: int = 900,
) -> EditOutcome:
    previous_text = ""
    last_error = "the model did not return valid edits"
    for attempt in range(ctx.settings.max_repairs + 1):
        user_content = prompt
        if attempt > 0:
            user_content = prompt + "\n\n" + repair_prompt(
                previous_text,
                last_error,
                '{"thought":"...","edits":[{"path":"file.py","start_line":1,"end_line":1,"replacement":"new text"}],"summary":"..."}',
            )
        response = await ctx.llm.complete(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_content}],
            max_tokens=max_tokens,
            json_mode=True,
        )
        ctx.usage["prompt_tokens"] += response.prompt_tokens
        ctx.usage["completion_tokens"] += response.completion_tokens
        ctx.usage["total_tokens"] += response.total_tokens
        previous_text = response.text
        try:
            parsed = parse_edits(response.text)
            grouped = _group_edits(ctx, parsed.edits, allowed_ranges)
        except (ProtocolError, EditError) as exc:
            last_error = str(exc)
            log.info("edit response rejected (attempt %s): %s", attempt + 1, last_error)
            continue

        # Test-file guard: refuse to "fix" a bug by editing its test.
        if not ctx.allow_test_edits and any(is_test_path(path) for path in grouped):
            last_error = "editing test files is disabled for this task; change the implementation instead"
            continue

        # Protected infrastructure/config paths require explicit approval.
        approval_needed = {path: requires_approval(path, "edit") for path in grouped}
        blocked = {path: reason for path, (need, reason) in approval_needed.items() if need and not ctx.approval_granted}
        if blocked:
            payload = {
                "action": "edit",
                "paths": list(grouped),
                "edits": [
                    {"path": edit.path, "start_line": edit.start_line, "end_line": edit.end_line, "replacement": edit.replacement, "reason": edit.reason}
                    for edits in grouped.values() for edit in edits
                ],
                "reason": "; ".join(str(reason) for reason in blocked.values()),
            }
            return EditOutcome(applied=False, approval=payload, summary=parsed.summary or "Pending approval", thought=parsed.thought)

        started = time.monotonic()
        originals: Dict[str, str] = {}
        applied_details: List[dict] = []
        try:
            for path, edits in grouped.items():
                original, _ = ctx.workspace.read_text(ctx.workspace_id, path, max_bytes=ctx.settings.max_file_bytes)
                originals[path] = original
                new_content, details = apply_pure(original, edits, allowed_range=allowed_ranges[path][0])
                syntax_error = check_syntax(path, new_content)
                if syntax_error:
                    raise EditError(f"{path}: {syntax_error}")
                ctx.workspace.write_text(ctx.workspace_id, path, new_content)
                applied_details.extend(details)
                await ctx.emit("file.edited", f"Edited {path} ({len(details)} change(s))", {"path": path, "details": details}, display=True)
        except (EditError, OSError) as exc:
            for path, original in originals.items():
                try:
                    ctx.workspace.write_text(ctx.workspace_id, path, original)
                except Exception:  # noqa: BLE001
                    log.exception("rollback failed for %s", path)
            last_error = str(exc)
            log.info("edit application rejected (attempt %s): %s", attempt + 1, last_error)
            continue

        duration_ms = int((time.monotonic() - started) * 1000)
        ctx.repo.add_tool_call(
            task_id=ctx.task_id,
            tool_name="apply_edits",
            arguments={"paths": list(grouped), "edit_count": sum(len(v) for v in grouped.values())},
            result={"details": applied_details},
            status="success",
            duration_ms=duration_ms,
        )
        return EditOutcome(applied=True, details=applied_details, summary=parsed.summary or "Applied code changes", thought=parsed.thought)
    raise WorkflowError(f"could not produce a valid, applicable edit after {ctx.settings.max_repairs + 1} attempts: {last_error}")
