"""OpenAI-compatible API schemas."""
from __future__ import annotations

import time
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = None
    name: str | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = []
            for item in self.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return "" if self.content is None else str(self.content)


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = "io-coding-agent"
    messages: List[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    # Open WebUI has shipped both a string and an object here over time.
    user: Any = None
    metadata: Dict[str, Any] | None = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "infoorigin"


class ModelList(BaseModel):
    object: str = "list"
    data: List[ModelCard]


class TaskCreateRequest(BaseModel):
    request: str
    repository: str | None = None
    ref: str | None = None
    conversation_id: str | None = None
    user: str | None = None
    metadata: Dict[str, Any] | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: str
    approver: str
    reason: str | None = None
