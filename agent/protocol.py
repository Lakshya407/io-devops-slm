"""Constrained structured-action protocol.

``qwen2.5-coder:1.5b`` does not reliably emit native OpenAI ``tool_calls``
through Ollama/LiteLLM: it returns JSON (often fenced) inside the message
content, and can echo schema placeholders. The agent therefore uses an
explicit JSON protocol, validates every field with Pydantic, and repairs
invalid responses a bounded number of times.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field, ValidationError, field_validator


class ProtocolError(ValueError):
    pass


class ToolChoice(BaseModel):
    thought: str = ""
    action: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("action")
    @classmethod
    def _action_not_empty(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("action is empty")
        if len(value) > 64:
            raise ValueError("action name is too long")
        return value


class PlanResult(BaseModel):
    plan: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    test_command: str | None = None

    @field_validator("plan", "files", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        return []


class EditProposal(BaseModel):
    path: str
    start_line: int | None = None
    end_line: int | None = None
    replacement: str | None = None
    search: str | None = None
    replace: str | None = None
    reason: str | None = None

    @field_validator("path")
    @classmethod
    def _path_not_empty(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("edit path is empty")
        if len(value) > 500:
            raise ValueError("edit path is too long")
        return value


class EditResult(BaseModel):
    thought: str = ""
    edits: List[EditProposal]
    summary: str | None = None

    @field_validator("edits")
    @classmethod
    def _edits_present(cls, value: List[EditProposal]) -> List[EditProposal]:
        if not value:
            raise ValueError("edits is empty")
        if len(value) > 20:
            raise ValueError("too many edits (max 20)")
        return value


class FinalResult(BaseModel):
    status: str
    summary: str

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        allowed = {"completed", "failed", "incomplete", "needs_clarification"}
        value = (value or "").strip().lower()
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value


def _strip_fences(text: str) -> str:
    text = text.strip().lstrip("\ufeff")
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _balanced_objects(text: str) -> List[str]:
    objects: List[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    objects.append(text[start:index + 1])
                    start = -1
    return objects


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def extract_json_object(text: str) -> Dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError("model returned an empty response")
    cleaned = _strip_fences(text)
    candidates = [cleaned]
    candidates.extend(_balanced_objects(cleaned))
    last_error: Exception | None = None
    for candidate in candidates:
        for variant in (candidate, _remove_trailing_commas(candidate)):
            try:
                value = json.loads(variant)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            if isinstance(value, dict):
                return value
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
    raise ProtocolError(f"no valid JSON object found in model response: {last_error}")


def _normalize_tool_choice(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = payload.get("action") or payload.get("tool") or payload.get("name")
    arguments = payload.get("arguments")
    if arguments is None:
        arguments = payload.get("input")
    if arguments is None:
        arguments = payload.get("args")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:  # noqa: BLE001
            raise ProtocolError("arguments must be a JSON object, not a string")
    return {
        "thought": str(payload.get("thought") or payload.get("reasoning") or ""),
        "action": str(action or ""),
        "arguments": arguments if isinstance(arguments, dict) else {},
    }


def parse_tool_choice(text: str, allowed_actions: set[str]) -> ToolChoice:
    payload = _normalize_tool_choice(extract_json_object(text))
    try:
        choice = ToolChoice.model_validate(payload)
    except ValidationError as exc:
        raise ProtocolError(f"invalid tool choice: {exc.errors()}") from exc
    if choice.action not in allowed_actions and choice.action != "finish":
        raise ProtocolError(
            f"action {choice.action!r} is not allowed here; choose one of {sorted(allowed_actions)} or 'finish'"
        )
    return choice


def parse_plan(text: str) -> PlanResult:
    payload = extract_json_object(text)
    if "plan" not in payload and "steps" in payload:
        payload["plan"] = payload["steps"]
    try:
        return PlanResult.model_validate(payload)
    except ValidationError as exc:
        raise ProtocolError(f"invalid plan: {exc.errors()}") from exc


def parse_edits(text: str) -> EditResult:
    payload = extract_json_object(text)
    if "edits" not in payload:
        if any(key in payload for key in ("path", "start_line", "search")):
            payload = {"edits": [payload]}
        else:
            raise ProtocolError("response must contain an 'edits' array")
    for edit in payload.get("edits") or []:
        if isinstance(edit, dict) and "content" in edit and "replacement" not in edit:
            raise ProtocolError(
                "full-file 'content' edits are not accepted; use start_line/end_line/replacement line edits"
            )
    try:
        return EditResult.model_validate(payload)
    except ValidationError as exc:
        raise ProtocolError(f"invalid edit response: {exc.errors()}") from exc


def parse_final(text: str) -> FinalResult:
    try:
        return FinalResult.model_validate(extract_json_object(text))
    except ValidationError as exc:
        raise ProtocolError(f"invalid final response: {exc.errors()}") from exc


def repair_prompt(previous: str, error: str, schema_hint: str) -> str:
    return (
        "Your previous response was invalid.\n"
        f"Validation error: {error}\n"
        f"Previous response (truncated):\n{previous[:1500]}\n\n"
        f"Return only a single valid JSON object matching this schema:\n{schema_hint}\n"
        "Do not use markdown fences. Do not include prose outside the JSON."
    )
