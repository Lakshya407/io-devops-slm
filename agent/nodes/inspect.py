"""Node 2: inspect the repository and build a bounded model context."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from agent.nodes.context import TaskContext
from agent.prompts.system import INSPECT_PROMPT, SYSTEM
from agent.protocol import ProtocolError, parse_tool_choice
from agent.state.state import AgentState
from execution.workspace.manager import TEST_FILE_RE
from telemetry.sanitize import truncate

log = logging.getLogger(__name__)

INSPECT_ACTIONS = {"list_dir", "read_file", "search_code"}
LANGUAGE_NAMES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".cs": "C#", ".rb": "Ruby",
    ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".cs": "C#",
    ".swift": "Swift", ".scala": "Scala", ".sh": "Shell", ".sql": "SQL", ".vue": "Vue",
    ".svelte": "Svelte", ".html": "HTML", ".css": "CSS",
}


async def _model_inspection(ctx: TaskContext, request: str, tree: str, languages: Dict[str, int], manifests: str) -> tuple[List[str], List[str], Dict[str, int]]:
    observations: List[str] = []
    inspected: List[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if ctx.settings.max_inspect_steps <= 0:
        return observations, inspected, usage
    for step in range(ctx.settings.max_inspect_steps):
        prompt = INSPECT_PROMPT.format(
            request=request,
            tree=truncate(tree, 3500),
            languages=", ".join(f"{LANGUAGE_NAMES.get(k, k)} ({v})" for k, v in languages.items()) or "unknown",
            manifests=truncate(manifests, 2000),
            actions=", ".join(sorted(INSPECT_ACTIONS)) + ", finish",
            observations=truncate("\n".join(observations[-6:]) or "(none)", 3000),
        )
        try:
            response = await ctx.llm.complete(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                max_tokens=350,
                json_mode=True,
            )
            usage["prompt_tokens"] += response.prompt_tokens
            usage["completion_tokens"] += response.completion_tokens
            usage["total_tokens"] += response.total_tokens
            try:
                choice = parse_tool_choice(response.text, INSPECT_ACTIONS)
            except ProtocolError as exc:
                repair = await ctx.llm.complete(
                    [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": response.text[:2000]},
                        {"role": "user", "content": f"That response was invalid: {exc}. Return only valid JSON with thought, action, arguments."},
                    ],
                    max_tokens=350,
                    json_mode=True,
                )
                usage["prompt_tokens"] += repair.prompt_tokens
                usage["completion_tokens"] += repair.completion_tokens
                usage["total_tokens"] += repair.total_tokens
                choice = parse_tool_choice(repair.text, INSPECT_ACTIONS)
        except Exception as exc:  # noqa: BLE001
            log.info("inspection loop stopped after invalid model response: %s", exc)
            break
        if choice.action == "finish":
            break
        result = await ctx.tool(choice.action, choice.arguments, phase="inspect")
        if result.status != "success":
            observations.append(f"{choice.action} failed: {result.message}")
            continue
        data = result.data
        if choice.action == "read_file":
            inspected.append(str(data.get("path") or choice.arguments.get("path")))
            observations.append(f"read {data.get('path')} ({data.get('lines')} lines)")
        elif choice.action == "list_dir":
            observations.append(f"listed {data.get('path')}:\n{truncate(str(data.get('tree')), 1200)}")
        elif choice.action == "search_code":
            matches = data.get("matches") or []
            summary = "\n".join(f"{m['path']}:{m['line']}: {m['text']}" for m in matches[:15])
            observations.append(f"search {data.get('query')!r} -> {len(matches)} matches\n{summary}")
    return observations, inspected, usage


def _pick_context_files(ctx: TaskContext, request: str) -> List[str]:
    files = ctx.workspace.list_code_files(ctx.workspace_id)
    if not files:
        return []
    tests = [f for f in files if TEST_FILE_RE.search(f)]
    related = ctx.workspace.find_related_files(ctx.workspace_id, request)
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", request) if w.lower() not in {"this", "that", "with", "from", "have", "test", "tests", "code", "repo", "repository", "please", "using", "make", "should", "when", "then", "than", "into", "does", "doesn", "there", "their"}}
    keyword = [f for f in files if any(w in f.lower() for w in words)]
    preferred = [f for f in files if re.match(r"^(src|app|lib|pkg|internal|cmd)/", f)]
    ordered: List[str] = []
    for group in (tests[:4], related, keyword, preferred, files):
        for item in group:
            if item not in ordered:
                ordered.append(item)
    return ordered[: ctx.settings.max_context_files]


async def inspect_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    request = state.get("request") or ""
    await ctx.emit("repo.inspecting", "Analysing repository structure and source files", display=True)
    try:
        tree = ctx.workspace.tree(ctx.workspace_id, max_entries=300, max_depth=3)
        languages = ctx.workspace.detect_languages(ctx.workspace_id)
        manifests = ctx.workspace.read_manifest_summary(ctx.workspace_id)
    except Exception as exc:  # noqa: BLE001
        message = f"Repository inspection failed: {exc}"
        ctx.repo.update_task(ctx.task_id, status="failed", phase="inspect", error=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "inspect", "error": message, "final_summary": message}

    observations, inspected, usage = await _model_inspection(ctx, request, tree, languages, manifests)
    for key, value in usage.items():
        ctx.usage[key] += value

    context_paths = _pick_context_files(ctx, request)
    context_files: Dict[str, str] = {}
    for rel in context_paths:
        try:
            content, truncated = ctx.workspace.read_text(ctx.workspace_id, rel, max_bytes=ctx.settings.max_file_bytes)
        except Exception:  # noqa: BLE001
            continue
        context_files[rel] = truncate(content, ctx.settings.max_prompt_file_chars * 2)

    if not context_files:
        message = (
            "I could not find readable source files in the repository. "
            "Please confirm the repository contains code and that it is not empty."
        )
        ctx.repo.update_task(ctx.task_id, status="needs_clarification", phase="inspect", summary=message)
        await ctx.emit("task.needs_clarification", message, display=True)
        return {
            "status": "needs_clarification", "phase": "inspect", "tree": tree, "languages": languages,
            "manifests": manifests, "error": None, "final_summary": message,
        }

    key_lines = [f"- {path} ({len(content.splitlines())} lines)" for path, content in context_files.items()]
    key_files = "Files in context:\n" + "\n".join(key_lines) + "\n\nManifests:\n" + truncate(manifests, 3000)
    lang_text = ", ".join(f"{LANGUAGE_NAMES.get(k, k)} ({v})" for k, v in languages.items()) or "unknown"
    await ctx.emit(
        "repo.analysed",
        f"Analysed {len(context_files)} file(s); languages: {lang_text}",
        {"files": list(context_files), "languages": languages},
        display=True,
    )
    ctx.repo.update_task(ctx.task_id, phase="plan")
    return {
        "tree": tree,
        "languages": languages,
        "manifests": manifests,
        "key_files": key_files,
        "inspected_files": inspected,
        "observations": observations,
        "context_files": context_files,
        "edit_paths": [p for p in context_files if not TEST_FILE_RE.search(p)],
        "phase": "plan",
        "status": "running",
        "error": None,
    }
