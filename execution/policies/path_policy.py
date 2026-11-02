"""Path-based approval policy for edits and deletes."""
from __future__ import annotations

from pathlib import PurePosixPath

PROTECTED_PREFIXES = (
    ".github/", ".gitlab/", ".circleci/", "k8s/", "kubernetes/", "helm/", "terraform/",
    "infra/", "deploy/", "deployment/", "migrations/", "alembic/versions/",
)

PROTECTED_NAMES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    ".gitlab-ci.yml", "jenkinsfile", ".env", ".env.example", "makefile", "nginx.conf",
    "config.yml", "config.yaml",
}


def normalize(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/").lstrip("/")))


def is_protected(path: str) -> bool:
    norm = normalize(path)
    lowered = norm.lower()
    if any(lowered.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return True
    name = PurePosixPath(lowered).name
    if name in PROTECTED_NAMES:
        return True
    if name.startswith(".env.") or name.endswith(".pem") or name.endswith(".key"):
        return True
    if "/secrets/" in f"/{lowered}" or "/secret/" in f"/{lowered}":
        return True
    return False


def is_test_path(path: str) -> bool:
    norm = normalize(path).lower()
    name = PurePosixPath(norm).name
    if "/tests/" in f"/{norm}" or "/test/" in f"/{norm}" or norm.startswith("tests/"):
        return True
    if name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name:
        return True
    return False


def task_allows_test_edits(task: str) -> bool:
    lowered = (task or "").lower()
    if any(phrase in lowered for phrase in ("fix the failing test", "fix failing test", "make the test pass", "make tests pass")):
        return False
    return any(
        phrase in lowered
        for phrase in (
            "add test", "add tests", "write test", "write tests", "update test", "update tests",
            "create test", "create tests", "improve test", "improve tests", "test coverage",
        )
    )


def requires_approval(path: str, action: str) -> tuple[bool, str | None]:
    if action == "delete":
        return True, "deleting files requires human approval"
    if is_protected(path):
        return True, f"editing protected infrastructure/configuration path {path!r} requires human approval"
    return False, None
