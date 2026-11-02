#!/usr/bin/env python3
"""Probe the configured model through LiteLLM with the agent's real protocol.

Run inside the agent container:

    docker compose exec io-agent python scripts/probe_model.py

It performs no repository changes. It reports whether the model can produce a
valid plan and a valid line-numbered edit, which are the two capabilities the
Milestone 1 workflow depends on.
"""
from __future__ import annotations

import asyncio
import json
import sys

from agent.config import get_settings
from agent.llm import LLMClient
from agent.prompts.system import EDIT_PROMPT, PLAN_PROMPT, SYSTEM
from agent.protocol import ProtocolError, parse_edits, parse_plan

FILE_CONTEXT = """FILE: app/calculator.py (2 lines; editable range 1-2)
    1| def add(a, b):
    2|     return a - b"""

TEST_OUTPUT = """command: python -m pytest -q
exit_code: 1
stdout:
FAILED tests/test_calculator.py::test_add - assert -1 == 5
stderr:"""


async def main() -> int:
    settings = get_settings()
    llm = LLMClient(settings)
    failures = 0
    try:
        plan_prompt = PLAN_PROMPT.format(
            request="Fix the failing test in the repository and show me the diff.",
            tree="app/\n  __init__.py\n  calculator.py\ntests/\n  __init__.py\n  test_calculator.py\nrequirements.txt",
            key_files="app/calculator.py (2 lines)\ntests/test_calculator.py (5 lines)",
            test_command="python -m pytest -q",
        )
        plan_response = await llm.complete(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": plan_prompt}],
            max_tokens=500,
            json_mode=True,
        )
        try:
            plan = parse_plan(plan_response.text)
            print("PLAN: PASS")
            print(json.dumps(plan.model_dump(), indent=2)[:2000])
        except ProtocolError as exc:
            failures += 1
            print(f"PLAN: FAIL ({exc})")
            print(plan_response.text[:1500])

        edit_prompt = EDIT_PROMPT.format(
            request="Fix the failing test in the repository and show me the diff.",
            plan="- Correct add() so it adds instead of subtracting.",
            file_context=FILE_CONTEXT,
            test_command="python -m pytest -q",
            test_output=TEST_OUTPUT,
        )
        edit_response = await llm.complete(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": edit_prompt}],
            max_tokens=700,
            json_mode=True,
        )
        try:
            edits = parse_edits(edit_response.text)
            print("EDIT: PASS")
            print(json.dumps(edits.model_dump(), indent=2)[:2000])
        except ProtocolError as exc:
            failures += 1
            print(f"EDIT: FAIL ({exc})")
            print(edit_response.text[:1500])
    finally:
        await llm.aclose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
