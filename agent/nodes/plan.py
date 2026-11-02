"""Node 3: produce a short implementation plan."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.nodes.context import TaskContext
from agent.prompts.system import PLAN_PROMPT, SYSTEM
from agent.protocol import ProtocolError, parse_plan
from agent.state.state import AgentState
from execution.policies.command_policy import validate_command
from telemetry.sanitize import truncate

log = logging.getLogger(__name__)

TEST_MARKERS = ("pytest", "unittest", "npm test", "yarn test", "pnpm test", "go test", "cargo test", "mvn test", "gradle test", "make test", "phpunit", "rspec", "dotnet test")


def _looks_like_test_command(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in TEST_MARKERS)


async def plan_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    request = state.get("request") or ""
    tree = state.get("tree") or ""
    key_files = state.get("key_files") or ""
    detected = ctx.workspace.detect_test_command(ctx.workspace_id)
    await ctx.emit("plan.preparing", "Preparing implementation plan", display=True)

    prompt = PLAN_PROMPT.format(
        request=request,
        tree=truncate(tree, 2500),
        key_files=truncate(key_files, 4000),
        test_command=detected or "none",
    )
    plan: List[str] = []
    files: List[str] = []
    test_command = detected
    source = "model"
    try:
        response = await ctx.llm.complete(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=500,
            json_mode=True,
        )
        ctx.usage["prompt_tokens"] += response.prompt_tokens
        ctx.usage["completion_tokens"] += response.completion_tokens
        ctx.usage["total_tokens"] += response.total_tokens
        parsed = parse_plan(response.text)
        plan = [truncate(item, 300) for item in parsed.plan[:6]]
        files = parsed.files
        if parsed.test_command and _looks_like_test_command(parsed.test_command):
            ok, reason, _ = validate_command(parsed.test_command)
            if ok:
                test_command = parsed.test_command
    except ProtocolError as exc:
        log.info("plan response invalid: %s", exc)
        source = "fallback"

    existing = set(ctx.workspace.list_files(ctx.workspace_id))
    files = [f for f in files if f in existing]
    if not files:
        files = list((state.get("edit_paths") or [])[:4])
    if not plan:
        plan = [
            "Inspect the failing behaviour and the tests that describe the expected result.",
            f"Apply a minimal edit to {', '.join(files[:2]) or 'the relevant source file'}.",
            f"Run the test suite ({test_command or 'no test command detected'}).",
            "Review the final diff and report the verified outcome.",
        ]
        source = "fallback"
    if test_command is not None:
        ok, reason, _ = validate_command(test_command)
        if not ok:
            test_command = detected if detected and validate_command(detected)[0] else None

    ctx.repo.update_task(ctx.task_id, plan_json=__import__("json").dumps(plan), test_command=test_command, phase="edit")
    await ctx.emit(
        "plan.ready",
        "Plan:\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(plan, 1)),
        {"plan": plan, "files": files, "test_command": test_command, "source": source},
        display=True,
    )
    return {
        "plan": plan,
        "plan_files": files,
        "plan_source": source,
        "test_command": test_command,
        "phase": "edit",
        "status": "running",
        "error": None,
    }
