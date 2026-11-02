from __future__ import annotations

import pytest

from agent.protocol import (
    ProtocolError,
    extract_json_object,
    parse_edits,
    parse_final,
    parse_plan,
    parse_tool_choice,
)


def test_extract_json_from_fence() -> None:
    text = 'Here is the result:\n```json\n{"action": "finish", "arguments": {}}\n```'
    assert extract_json_object(text) == {"action": "finish", "arguments": {}}


def test_extract_json_with_trailing_comma() -> None:
    assert extract_json_object('{"plan": ["a", "b",],}') == {"plan": ["a", "b"]}


def test_parse_tool_choice_normalizes_tool_alias() -> None:
    choice = parse_tool_choice('{"thought":"x","tool":"read_file","args":{"path":"a.py"}}', {"read_file"})
    assert choice.action == "read_file"
    assert choice.arguments == {"path": "a.py"}


def test_parse_tool_choice_rejects_unknown_action() -> None:
    with pytest.raises(ProtocolError):
        parse_tool_choice('{"action":"delete_everything","arguments":{}}', {"read_file"})


def test_parse_tool_choice_allows_finish() -> None:
    assert parse_tool_choice('{"action":"finish","arguments":{}}', {"read_file"}).action == "finish"


def test_parse_plan_accepts_steps_alias() -> None:
    plan = parse_plan('{"steps":["one","two"],"files":["a.py"],"test_command":"python -m pytest -q"}')
    assert plan.plan == ["one", "two"]
    assert plan.files == ["a.py"]


def test_parse_edits_line_replacement() -> None:
    result = parse_edits(
        '{"thought":"fix","edits":[{"path":"app/a.py","start_line":2,"end_line":2,'
        '"replacement":"    return a + b","reason":"correct"}],"summary":"done"}'
    )
    assert result.edits[0].start_line == 2
    assert result.edits[0].replacement == "    return a + b"


def test_parse_edits_rejects_full_file_content() -> None:
    with pytest.raises(ProtocolError):
        parse_edits('{"edits":[{"path":"a.py","content":"whole file"}]}')


def test_parse_edits_rejects_empty_list() -> None:
    with pytest.raises(ProtocolError):
        parse_edits('{"edits":[]}')


def test_parse_final_rejects_unknown_status() -> None:
    with pytest.raises(ProtocolError):
        parse_final('{"status":"probably","summary":"x"}')


def test_parse_final_accepts_completed() -> None:
    assert parse_final('{"status":"completed","summary":"ok"}').status == "completed"


def test_empty_response_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        extract_json_object("   ")
