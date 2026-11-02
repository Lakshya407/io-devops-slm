from __future__ import annotations

import pytest

pytest.importorskip("docker")

from fastapi import HTTPException

from sandbox import runner


def test_validate_workspace_id() -> None:
    assert runner.validate_workspace_id("task_abc-123") == "task_abc-123"
    with pytest.raises(HTTPException):
        runner.validate_workspace_id("../escape")
    with pytest.raises(HTTPException):
        runner.validate_workspace_id("bad id")


def test_workspace_path_stays_inside_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runner.config, "workspaces_root", tmp_path.resolve())
    (tmp_path / "task_abc").mkdir()
    assert runner.workspace_path("task_abc") == (tmp_path / "task_abc").resolve()
    with pytest.raises(HTTPException):
        runner.workspace_path("task_missing")


def test_validate_argv_accepts_test_command() -> None:
    assert runner.validate_argv(["python", "-m", "pytest", "-q"]) == ["python", "-m", "pytest", "-q"]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["bash", "-c", "echo hi"],
        ["/usr/bin/python", "-m", "pytest"],
        ["python", "bad\narg"],
    ],
)
def test_validate_argv_rejects_unsafe_input(argv) -> None:
    with pytest.raises(HTTPException):
        runner.validate_argv(argv)


def test_validate_env_filters_unknown_keys() -> None:
    env = runner.validate_env({"CI": "true", "LD_PRELOAD": "/tmp/x.so", "PYTEST_ADDOPTS": "-x"})
    assert env["CI"] == "true"
    assert env["PYTEST_ADDOPTS"] == "-x"
    assert "LD_PRELOAD" not in env
    assert env["HOME"] == "/tmp"
