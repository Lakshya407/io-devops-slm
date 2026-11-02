"""Prompts for the constrained agent protocol.

Prompts are deliberately short and explicit: the 1.5B model follows a single
JSON action schema far more reliably than free-form ReAct text.
"""
from __future__ import annotations

SYSTEM = """You are IO Coding Agent, an autonomous software engineering agent working inside an isolated workspace.

Rules:
- You inspect the real repository, make minimal code changes, and run the real test suite.
- You never invent command output, test results, file contents, or diffs.
- You reply with exactly one JSON object. No markdown fences. No prose outside the JSON.
- You only use the actions listed in the current step. Never invent tool names.
- Prefer the smallest change that fixes the actual root cause.
- Do not modify tests to make them pass unless the user explicitly asked you to change tests.
- If information is missing, say so with a needs_clarification result instead of guessing.
"""

INSPECT_PROMPT = """Task:
{request}

Repository tree:
{tree}

Detected languages: {languages}
Manifests:
{manifests}

Choose ONE next inspection action from: {actions}.
Schemas:
- {{"action":"list_dir","arguments":{{"path":".","depth":2}}}}
- {{"action":"read_file","arguments":{{"path":"relative/path.py"}}}}
- {{"action":"search_code","arguments":{{"query":"text or regex","glob":"*.py"}}}}
- {{"action":"finish","arguments":{{}}}}

Observation so far:
{observations}

Return one JSON object with keys thought, action, arguments. Do not use markdown."""

PLAN_PROMPT = """Task:
{request}

Repository tree:
{tree}

Key files:
{key_files}

Detected test command: {test_command}

Write an implementation plan and choose the files that need to change.
Return exactly one JSON object:
{{"plan":["step 1","step 2"],"files":["relative/path.py"],"test_command":"the command to run tests"}}

Rules:
- Use relative paths that exist in the repository.
- Keep the plan to 2-5 concrete steps.
- If no test command is detected, return null for test_command.
- Do not use markdown."""

EDIT_PROMPT = """Task:
{request}

Plan:
{plan}

{file_context}

Test command: {test_command}
Last test result:
{test_output}

Make the minimal code change needed. Return exactly one JSON object:
{{"thought":"why this change fixes the problem","edits":[{{"path":"relative/path.py","start_line":3,"end_line":3,"replacement":"    return a + b","reason":"adds instead of subtracts"}}],"summary":"short summary"}}

Rules:
- start_line and end_line are 1-based and inclusive.
- replacement is the exact new text for those lines, without line-number prefixes.
- Use "\\n" inside the JSON string for multiple replacement lines.
- An empty replacement deletes the selected lines.
- Only edit files shown above, and only within the editable ranges shown.
- Do not edit tests to make a failing test pass unless the task explicitly asks for test changes.
- Output JSON only."""

DIAGNOSE_PROMPT = """Task:
{request}

Plan:
{plan}

The tests failed. Diagnose the failure and make the minimal correction.

Test command: {test_command}
Test output:
{test_output}

{file_context}

Previous edits:
{previous_edits}

Return exactly one JSON object:
{{"thought":"root cause and fix","edits":[{{"path":"relative/path.py","start_line":3,"end_line":3,"replacement":"    return a + b","reason":"why"}}],"summary":"short summary"}}

Rules:
- start_line and end_line are 1-based and inclusive.
- replacement is the exact new text, without line-number prefixes.
- Only edit files shown above, within the editable ranges shown.
- Do not modify tests to make them pass.
- Output JSON only."""

FINAL_PROMPT = """Task:
{request}

Verified outcome:
{outcome}

Return exactly one JSON object:
{{"status":"completed|failed|incomplete|needs_clarification","summary":"concise final report"}}

Rules:
- status is completed only if the tests actually passed and the diff is non-empty.
- Never claim success for an operation that was not verified.
- Do not use markdown."""
