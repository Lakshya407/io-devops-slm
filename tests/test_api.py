from __future__ import annotations

import json

from api.routes.chat import _chunk, _format_event, _metadata_fields
from api.schemas.openai import ChatCompletionRequest


def test_chunk_is_openai_compatible() -> None:
    raw = _chunk(task_id="t1", model="io-coding-agent", content="hello")
    assert raw.startswith("data: ")
    payload = json.loads(raw[len("data: "):].strip())
    assert payload["object"] == "chat.completion.chunk"
    assert payload["model"] == "io-coding-agent"
    assert payload["choices"][0]["delta"]["content"] == "hello"


def test_metadata_extracts_repository_and_chat_id() -> None:
    body = ChatCompletionRequest.model_validate({
        "model": "io-coding-agent",
        "messages": [{"role": "user", "content": "fix it"}],
        "metadata": {"agent": {"repository": "fixture://demo", "ref": "main"}, "chat_id": "chat-1"},
    })
    assert _metadata_fields(body) == ("fixture://demo", "main", "chat-1")


def test_user_field_accepts_object_and_string() -> None:
    assert ChatCompletionRequest.model_validate({"messages": [], "user": {"id": "u1"}}).user == {"id": "u1"}
    assert ChatCompletionRequest.model_validate({"messages": [], "user": "u1"}).user == "u1"


def test_event_formatting_hides_routine_tool_noise() -> None:
    assert _format_event({"type": "tool.call", "message": "Running read_file", "data": {}}) is None
    assert "⚠️" in _format_event({"type": "tool.result", "message": "disabled", "data": {"status": "disabled"}})
    assert "🧪" in _format_event({"type": "test.running", "message": "Running tests", "data": {}})
