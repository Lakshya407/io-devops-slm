"""ioDB adapter (internal database services).

TODO before enabling:
- Obtain the connection method (API vs. direct database), allowed schemas/tables,
  and the read-only credentials or API docs.
- Default to read-only, row-limited, timeout-bounded queries.
- Any write/DDL requires explicit human approval and a separate scoped account.
- Never pass production credentials to the model, prompt, logs or workspace.
"""
from __future__ import annotations

from typing import Any, Dict

from tools.base import ToolContext, ToolResult
from tools.integrations.base import not_configured

NAME = "ioDB"
REQUIRED_ENV = ["IODB_BASE_URL", "IODB_TOKEN"]
DOCS = __doc__ or ""


async def _tool(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    payload = not_configured(NAME, REQUIRED_ENV, DOCS)
    return ToolResult("not_configured", payload, payload["message"])


async def describe_schema(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def query_readonly(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def execute_write(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec("iodb_describe_schema", "Describe an ioDB schema (adapter not configured).", {"type": "object", "properties": {"database": {"type": "string"}, "schema": {"type": "string"}}}, describe_schema, phases=["integrations"]))
    registry.register(ToolSpec("iodb_query_readonly", "Run a read-only, row-limited ioDB query (adapter not configured).", {"type": "object", "properties": {"database": {"type": "string"}, "sql": {"type": "string"}, "max_rows": {"type": "integer"}}, "required": ["sql"]}, query_readonly, phases=["integrations"]))
    registry.register(ToolSpec("iodb_execute_write", "Run a write/DDL statement in ioDB (adapter not configured; requires approval).", {"type": "object", "properties": {"database": {"type": "string"}, "sql": {"type": "string"}}, "required": ["sql"]}, execute_write, phases=["integrations"], destructive=True))
