"""LangGraph workflow builder.

The graph keeps model reasoning separate from tool execution: each node asks
the model for a constrained JSON decision, validates it, then calls a tool.
Routing is explicit and bounded by the retry counters in the state.
"""
from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, StateGraph

from agent.nodes.context import TaskContext
from agent.nodes.diagnose import diagnose_node
from agent.nodes.edit import edit_node
from agent.nodes.finalize import finalize_node
from agent.nodes.inspect import inspect_node
from agent.nodes.plan import plan_node
from agent.nodes.test import test_node
from agent.nodes.understand import understand_node
from agent.nodes.verify import verify_node
from agent.state.state import AgentState

TERMINAL = {"completed", "failed", "incomplete", "needs_clarification", "rejected", "cancelled", "timeout"}


def _after_understand(state: AgentState) -> str:
    return "finalize" if state.get("status") in {"failed", "needs_clarification"} else "inspect"


def _after_inspect(state: AgentState) -> str:
    return "finalize" if state.get("status") in {"failed", "needs_clarification"} else "planning"


def _after_edit(state: AgentState) -> str:
    if state.get("status") == "waiting_approval":
        return "await_approval"
    if state.get("status") in {"failed", "incomplete", "needs_clarification"}:
        return "finalize"
    return "test"


def _after_test(state: AgentState) -> str:
    if state.get("status") in {"failed", "incomplete"}:
        return "finalize"
    if state.get("phase") == "diagnose":
        return "diagnose"
    return "verify"


def _after_diagnose(state: AgentState) -> str:
    if state.get("status") == "waiting_approval":
        return "await_approval"
    if state.get("status") in {"failed", "incomplete"}:
        return "finalize"
    return "test"


def build_graph(ctx: TaskContext):
    async def _understand(state: AgentState) -> Dict[str, Any]:
        return await understand_node(ctx, state)

    async def _inspect(state: AgentState) -> Dict[str, Any]:
        return await inspect_node(ctx, state)

    async def _plan(state: AgentState) -> Dict[str, Any]:
        return await plan_node(ctx, state)

    async def _edit(state: AgentState) -> Dict[str, Any]:
        return await edit_node(ctx, state)

    async def _test(state: AgentState) -> Dict[str, Any]:
        return await test_node(ctx, state)

    async def _diagnose(state: AgentState) -> Dict[str, Any]:
        return await diagnose_node(ctx, state)

    async def _verify(state: AgentState) -> Dict[str, Any]:
        return await verify_node(ctx, state)

    async def _finalize(state: AgentState) -> Dict[str, Any]:
        return await finalize_node(ctx, state)

    async def _approval(state: AgentState) -> Dict[str, Any]:
        await ctx.emit("approval.waiting", "Task paused — waiting for human approval", state.get("approval") or {}, display=True)
        return {"phase": "approval", "status": "waiting_approval"}

    graph = StateGraph(AgentState)
    graph.add_node("understand", _understand)
    graph.add_node("inspect", _inspect)
    graph.add_node("planning", _plan)
    graph.add_node("edit", _edit)
    graph.add_node("test", _test)
    graph.add_node("diagnose", _diagnose)
    graph.add_node("verify", _verify)
    graph.add_node("finalize", _finalize)
    graph.add_node("await_approval", _approval)

    graph.set_entry_point("understand")
    graph.add_conditional_edges("understand", _after_understand, {"inspect": "inspect", "finalize": "finalize"})
    graph.add_conditional_edges("inspect", _after_inspect, {"planning": "planning", "finalize": "finalize"})
    graph.add_edge("planning", "edit")
    graph.add_conditional_edges("edit", _after_edit, {"test": "test", "await_approval": "await_approval", "finalize": "finalize"})
    graph.add_conditional_edges("test", _after_test, {"verify": "verify", "diagnose": "diagnose", "finalize": "finalize"})
    graph.add_conditional_edges("diagnose", _after_diagnose, {"test": "test", "await_approval": "await_approval", "finalize": "finalize"})
    graph.add_edge("verify", "finalize")
    graph.add_edge("await_approval", END)
    graph.add_edge("finalize", END)
    return graph.compile()


def build_continuation_graph(ctx: TaskContext):
    """Graph used after an approval: test -> (verify | diagnose) -> finalize."""
    async def _test(state: AgentState) -> Dict[str, Any]:
        return await test_node(ctx, state)

    async def _diagnose(state: AgentState) -> Dict[str, Any]:
        return await diagnose_node(ctx, state)

    async def _verify(state: AgentState) -> Dict[str, Any]:
        return await verify_node(ctx, state)

    async def _finalize(state: AgentState) -> Dict[str, Any]:
        return await finalize_node(ctx, state)

    async def _approval(state: AgentState) -> Dict[str, Any]:
        await ctx.emit("approval.waiting", "Task paused — waiting for human approval", state.get("approval") or {}, display=True)
        return {"phase": "approval", "status": "waiting_approval"}

    graph = StateGraph(AgentState)
    graph.add_node("test", _test)
    graph.add_node("diagnose", _diagnose)
    graph.add_node("verify", _verify)
    graph.add_node("finalize", _finalize)
    graph.add_node("await_approval", _approval)
    graph.set_entry_point("test")
    graph.add_conditional_edges("test", _after_test, {"verify": "verify", "diagnose": "diagnose", "finalize": "finalize"})
    graph.add_conditional_edges("diagnose", _after_diagnose, {"test": "test", "await_approval": "await_approval", "finalize": "finalize"})
    graph.add_edge("verify", "finalize")
    graph.add_edge("await_approval", END)
    graph.add_edge("finalize", END)
    return graph.compile()
