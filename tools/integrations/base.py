"""Base contract for internal InfoOrigin integrations.

Adapters are intentionally thin and explicit. No endpoint paths are invented:
each adapter documents the information that must be supplied before it can be
enabled. Until then, every tool returns ``not_configured`` and performs no
network call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from agent.config import Settings


@dataclass
class IntegrationStatus:
    name: str
    configured: bool
    required_env: List[str] = field(default_factory=list)
    documentation: str = ""
    missing_env: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "required_env": self.required_env,
            "missing_env": self.missing_env,
            "documentation": self.documentation,
        }


def status_for(name: str, settings: Settings, required_env: List[str], documentation: str) -> IntegrationStatus:
    missing = [key for key in required_env if not getattr(settings, key.lower(), None) and not _env_present(key)]
    return IntegrationStatus(name=name, configured=not missing, required_env=required_env, documentation=documentation, missing_env=missing)


def _env_present(key: str) -> bool:
    import os

    return bool(os.environ.get(key))


def not_configured(name: str, required_env: List[str], documentation: str) -> Dict[str, Any]:
    return {
        "status": "not_configured",
        "integration": name,
        "required_env": required_env,
        "documentation": documentation,
        "message": (
            f"{name} is not configured. Supply {', '.join(required_env)} and implement the adapter "
            "against the application's published API documentation. No request was made."
        ),
    }
