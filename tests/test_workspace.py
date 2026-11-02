from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.config import Settings
from execution.workspace.manager import WorkspaceError, WorkspacePathError, WorkspaceManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_fixture_repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "app").mkdir(parents=True)
    (work / "tests").mkdir()
    (work / "app" / "__init__.py").write_text("", encoding="utf-8")
    (work / "app" / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (work / "tests" / "test_calculator.py").write_text(
        "from app.calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (work / "requirements.txt").write_text("pytest==8.3.4\n", encoding="utf-8")
    _git(work, "init", "-q", "-b", "main")
    _git(work, "add", ".")
    _git(work, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "fixture")
    repos = tmp_path / "repos"
    repos.mkdir()
    bare = repos / "demo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True, text=True)
    return bare


@pytest.fixture()
def manager(tmp_path: Path) -> WorkspaceManager:
    make_fixture_repo(tmp_path)
    settings = Settings(
        workspaces_dir=str(tmp_path / "workspaces"),
        repos_dir=str(tmp_path / "repos"),
        max_repo_size_mb=20,
    )
    return WorkspaceManager(settings)


def test_create_clone_and_branch(manager: WorkspaceManager) -> None:
    info = manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    assert Path(info["path"]).is_dir()
    assert info["branch"].startswith("io-agent/")
    assert (Path(info["path"]) / "app" / "calculator.py").is_file()
    assert manager.status("task_abc").find("io-agent/") != -1


def test_duplicate_create_is_rejected(manager: WorkspaceManager) -> None:
    manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    with pytest.raises(WorkspaceError):
        manager.create(workspace_id="task_abc", repo_url="fixture://demo")


def test_read_write_and_diff(manager: WorkspaceManager) -> None:
    manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    content, _ = manager.read_text("task_abc", "app/calculator.py")
    assert "return a - b" in content
    manager.write_text("task_abc", "app/calculator.py", content.replace("a - b", "a + b"))
    diff = manager.diff("task_abc")
    assert "+    return a + b" in diff
    assert "-    return a - b" in diff


def test_path_traversal_is_rejected(manager: WorkspaceManager) -> None:
    manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    with pytest.raises(WorkspacePathError):
        manager.resolve_in("task_abc", "../outside.txt")
    with pytest.raises(WorkspacePathError):
        manager.resolve_in("task_abc", "app/../../outside.txt")
    with pytest.raises(WorkspacePathError):
        manager.resolve_in("task_abc", ".git/config")


def test_absolute_looking_path_stays_inside_workspace(manager: WorkspaceManager) -> None:
    manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    resolved = manager.resolve_in("task_abc", "/app/calculator.py")
    assert str(resolved).endswith("app\\calculator.py") or str(resolved).endswith("app/calculator.py")


def test_detect_test_command(manager: WorkspaceManager) -> None:
    manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    assert manager.detect_test_command("task_abc") == "python -m pytest -q"


def test_find_related_files(manager: WorkspaceManager) -> None:
    manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    assert "app/calculator.py" in manager.find_related_files("task_abc", "fix the failing test")


def test_numbered_and_edit_context(manager: WorkspaceManager) -> None:
    manager.create(workspace_id="task_abc", repo_url="fixture://demo")
    numbered = manager.numbered_file("task_abc", "app/calculator.py")
    assert "1| def add(a, b):" in numbered
    context, ranges = manager.edit_context("task_abc", ["app/calculator.py"])
    assert "editable range 1-2" in context
    assert ranges["app/calculator.py"] == [(1, 2)]


def test_repo_url_outside_repos_dir_is_rejected(manager: WorkspaceManager, tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        manager.create(workspace_id="task_abc", repo_url=f"file://{tmp_path / 'elsewhere'}")
