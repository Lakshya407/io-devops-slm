"""Small, dependency-free helpers shared by the agent and the sandbox runner."""
from __future__ import annotations

import re
from typing import Any, Dict


_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{12,})\b"),
    re.compile(r"(?i)\b(Bearer\s+[A-Za-z0-9._\-]{8,})\b"),
    re.compile(r"(?i)\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[\"']?)([^\s\"',;]{4,})"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
]


def sanitize_text(text: str, max_len: int | None = None) -> str:
    """Redact high-confidence secrets and personal identifiers from text."""
    if not isinstance(text, str):
        text = str(text)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: m.group(1) + "<REDACTED>", text)
        else:
            text = pattern.sub("<REDACTED>", text)
    if max_len is not None and len(text) > max_len:
        text = text[:max_len] + f"\n... [truncated, {len(text) - max_len} chars omitted]"
    return text


def sanitize_obj(value: Any, max_str: int = 8_000) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, max_str)
    if isinstance(value, dict):
        return {str(k): sanitize_obj(v, max_str) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_obj(v, max_str) for v in value]
    return value


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [truncated, {len(text) - max_len} chars omitted]"


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "x-api-key", "cookie", "proxy-authorization"}:
            out[key] = "<REDACTED>"
        else:
            out[key] = value
    return out
