"""Workspace creation, path confinement and repository inspection helpers."""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urlparse

from agent.config import Settings
from telemetry.sanitize import sanitize_text, truncate

log = logging.getLogger(__name__)

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    "dist", "build", "target", ".next", ".nuxt", "coverage", ".idea", ".vscode",
    "vendor", "third_party", ".terraform", ".tox", ".gradle", ".cache",
}
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".kts", ".cs",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".m", ".mm", ".swift", ".scala",
    ".sh", ".bash", ".sql", ".vue", ".svelte", ".html", ".css", ".scss", ".lua", ".pl",
}
MANIFEST_FILES = {
    "README.md", "README.rst", "README.txt", "package.json", "pyproject.toml", "setup.py",
    "setup.cfg", "requirements.txt", "Pipfile", "poetry.lock", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "Gemfile", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", ".gitlab-ci.yml",
    "Makefile",
}

TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test\.[A-Za-z0-9]+|[^/]+\.(test|spec)\.[A-Za-z0-9]+)$", re.I)


class WorkspaceError(RuntimeError):
    pass


class WorkspacePathError(WorkspaceError):
    pass


class WorkspaceManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = Path(settings.workspaces_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.repos_dir = Path(settings.repos_dir)

    # --- lifecycle ------------------------------------------------------
    def path_for(self, workspace_id: str) -> Path:
        if not TASK_ID_RE.match(workspace_id):
            raise WorkspaceError("invalid workspace id")
        return self.root / workspace_id

    def create(self, *, workspace_id: str, repo_url: str, ref: str | None = None) -> dict:
        dest = self.path_for(workspace_id)
        if dest.exists():
            raise WorkspaceError(f"workspace {workspace_id} already exists")
        url = self._validate_repo_url(repo_url)
        env = self._clean_env()
        args = ["git", "-c", "core.symlinks=false", "clone", "--depth", "1"]
        if ref:
            if not re.match(r"^[A-Za-z0-9._/-]{1,200}$", ref):
                raise WorkspaceError("invalid git ref")
            args += ["--branch", ref]
        args += [url, str(dest)]
        try:
            result = subprocess.run(args, cwd=self.root, env=env, capture_output=True, text=True, timeout=self.settings.clone_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError(f"git clone timed out after {self.settings.clone_timeout_seconds}s") from exc
        if result.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise WorkspaceError(f"git clone failed: {sanitize_text(result.stderr or result.stdout, 2000)}")

        size_mb = self.repo_size_mb(dest)
        if size_mb > self.settings.max_repo_size_mb:
            shutil.rmtree(dest, ignore_errors=True)
            raise WorkspaceError(f"repository is {size_mb:.1f} MB, above the {self.settings.max_repo_size_mb} MB limit")

        self._git(dest, ["config", "user.email", "io-agent@infoorigin.local"])
        self._git(dest, ["config", "user.name", "IO Coding Agent"])
        branch = f"io-agent/{workspace_id[:12]}"
        self._git(dest, ["checkout", "-b", branch])
        return {
            "workspace_id": workspace_id,
            "path": str(dest),
            "branch": branch,
            "repo_url": url,
            "size_mb": round(size_mb, 2),
        }

    def open(self, workspace_id: str) -> Path:
        path = self.path_for(workspace_id)
        if not path.is_dir():
            raise WorkspaceError(f"workspace {workspace_id} does not exist")
        return path

    def repo_size_mb(self, path: Path) -> float:
        total = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
        return total / (1024 * 1024)

    # --- URL validation -------------------------------------------------
    def _validate_repo_url(self, repo_url: str) -> str:
        raw = (repo_url or "").strip()
        if not raw or any(ch in raw for ch in "\r\n\t\"'` "):
            raise WorkspaceError("invalid repository URL")
        if raw.startswith("-"):
            raise WorkspaceError("invalid repository URL")

        if raw.startswith("fixture://"):
            name = raw[len("fixture://"):]
            if not re.match(r"^[A-Za-z0-9._-]{1,100}$", name):
                raise WorkspaceError("invalid fixture name")
            candidate = self.repos_dir / (name if name.endswith(".git") else name + ".git")
            if not candidate.exists():
                candidate = self.repos_dir / name
            return self._file_url(candidate)

        if raw.startswith("file://"):
            parsed = urlparse(raw)
            candidate = Path(parsed.path)
            return self._file_url(candidate)

        if raw.startswith("/"):
            return self._file_url(Path(raw))

        parsed = urlparse(raw)
        if parsed.scheme == "https":
            host = (parsed.hostname or "").lower()
            if not host:
                raise WorkspaceError("repository URL has no host")
            allow = self.settings.repo_allowlist_hosts
            if allow and host not in allow:
                raise WorkspaceError(f"repository host {host!r} is not in AGENT_REPO_ALLOWLIST")
            if not allow and not self.settings.allow_any_https_repo:
                raise WorkspaceError("HTTPS repositories are disabled by policy")
            return raw
        if parsed.scheme == "http":
            if not self.settings.allow_insecure_http:
                raise WorkspaceError("insecure HTTP repositories are disabled; use HTTPS")
            return raw
        raise WorkspaceError("only https://, file:// (under AGENT_REPOS_DIR) or local repository paths are allowed")

    def _file_url(self, candidate: Path) -> str:
        try:
            resolved = candidate.resolve()
            repos = self.repos_dir.resolve()
        except OSError as exc:
            raise WorkspaceError(f"invalid local repository path: {exc}") from exc
        if resolved != repos and repos not in resolved.parents:
            raise WorkspaceError(f"local repositories must live under {repos}")
        if not resolved.exists():
            raise WorkspaceError(f"local repository not found: {resolved}")
        return "file://" + str(resolved)

    # --- path safety ----------------------------------------------------
    def resolve_in(self, workspace_id: str, relative: str, *, for_write: bool = False) -> Path:
        root = self.open(workspace_id).resolve()
        if not relative or "\x00" in relative:
            raise WorkspacePathError("empty or invalid path")
        rel = relative.replace("\\", "/").lstrip("/")
        parts = PurePosixPath(rel).parts
        if any(part == ".git" for part in parts):
            raise WorkspacePathError("access to .git is not allowed through file tools")
        if any(part == ".." for part in parts):
            raise WorkspacePathError("path traversal is not allowed")
        candidate = (root / rel).resolve()
        if candidate != root and root not in candidate.parents:
            raise WorkspacePathError("path escapes the workspace")
        if for_write:
            current = root
            for part in parts:
                current = current / part
                if current.exists() and current.is_symlink():
                    raise WorkspacePathError("writing through symlinks is not allowed")
        return candidate

    # --- file access ----------------------------------------------------
    def read_text(self, workspace_id: str, relative: str, max_bytes: int | None = None) -> Tuple[str, bool]:
        path = self.resolve_in(workspace_id, relative)
        if not path.is_file():
            raise WorkspacePathError(f"not a file: {relative}")
        limit = max_bytes or self.settings.max_file_bytes
        size = path.stat().st_size
        if size > limit:
            with path.open("rb") as handle:
                data = handle.read(limit)
            return data.decode("utf-8", errors="replace"), True
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            raise WorkspacePathError(f"binary file is not supported: {relative}")
        return data.decode("utf-8", errors="replace"), False

    def write_text(self, workspace_id: str, relative: str, content: str) -> dict:
        if len(content.encode("utf-8")) > 2_000_000:
            raise WorkspacePathError("file content exceeds the 2 MB limit")
        path = self.resolve_in(workspace_id, relative, for_write=True)
        if path.is_dir():
            raise WorkspacePathError(f"path is a directory: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        tmp = path.with_name(path.name + ".io-agent.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return {"path": relative, "bytes": len(content.encode("utf-8")), "created": not existed}

    def delete_file(self, workspace_id: str, relative: str) -> dict:
        path = self.resolve_in(workspace_id, relative, for_write=True)
        if not path.is_file():
            raise WorkspacePathError(f"not a file: {relative}")
        path.unlink()
        return {"path": relative, "deleted": True}

    def list_files(self, workspace_id: str) -> List[str]:
        root = self.open(workspace_id)
        result: List[str] = []
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                rel = (Path(base) / name).relative_to(root).as_posix()
                result.append(rel)
        return sorted(result)

    def list_code_files(self, workspace_id: str, limit: int = 500) -> List[str]:
        files = []
        for rel in self.list_files(workspace_id):
            suffix = Path(rel).suffix.lower()
            if suffix in CODE_EXTENSIONS:
                files.append(rel)
                if len(files) >= limit:
                    break
        return files

    def tree(self, workspace_id: str, max_entries: int = 300, max_depth: int = 3) -> str:
        return self.tree_at(workspace_id, ".", max_entries=max_entries, max_depth=max_depth)

    def tree_at(self, workspace_id: str, relative: str = ".", max_entries: int = 300, max_depth: int = 3) -> str:
        base_path = self.resolve_in(workspace_id, relative) if relative not in {"", "."} else self.open(workspace_id)
        if not base_path.is_dir():
            raise WorkspacePathError(f"not a directory: {relative}")
        lines: List[str] = []
        count = 0
        for base, dirs, files in os.walk(base_path):
            rel_base = Path(base).relative_to(base_path)
            depth = len(rel_base.parts)
            if depth >= max_depth:
                dirs[:] = []
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for d in dirs:
                lines.append(f"{'  ' * depth}{d}/")
                count += 1
                if count >= max_entries:
                    return "\n".join(lines) + "\n... [tree truncated]"
            for name in sorted(files):
                if name in {".DS_Store"}:
                    continue
                lines.append(f"{'  ' * depth}{name}")
                count += 1
                if count >= max_entries:
                    return "\n".join(lines) + "\n... [tree truncated]"
        return "\n".join(lines) if lines else "(empty directory)"

    # --- git ------------------------------------------------------------
    def _clean_env(self) -> dict:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": "/tmp",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/true",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_SSH_COMMAND": "false",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        return env

    def _git(self, path: Path, args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", *args], cwd=path, env=self._clean_env(), capture_output=True, text=True, timeout=timeout)
        return result

    def git(self, workspace_id: str, args: List[str], timeout: int = 60) -> dict:
        root = self.open(workspace_id)
        result = self._git(root, args, timeout=timeout)
        return {
            "exit_code": result.returncode,
            "stdout": truncate(result.stdout, self.settings.max_output_bytes),
            "stderr": truncate(result.stderr, self.settings.max_output_bytes),
            "command": "git " + " ".join(shlex.quote(a) for a in args),
        }

    def status(self, workspace_id: str) -> str:
        result = self.git(workspace_id, ["status", "--porcelain=v1", "-b"])
        return (result["stdout"] + result["stderr"]).strip()

    def diff(self, workspace_id: str, max_chars: int = 200_000) -> str:
        unstaged = self.git(workspace_id, ["diff", "--no-color", "--no-ext-diff", "--", "."])
        staged = self.git(workspace_id, ["diff", "--cached", "--no-color", "--no-ext-diff", "--", "."])
        parts = []
        if staged["stdout"].strip():
            parts.append("# staged changes\n" + staged["stdout"])
        if unstaged["stdout"].strip():
            parts.append("# unstaged changes\n" + unstaged["stdout"])
        return truncate("\n".join(parts).strip(), max_chars)

    def diff_stat(self, workspace_id: str) -> str:
        result = self.git(workspace_id, ["diff", "--stat", "--no-color", "--", "."])
        return result["stdout"].strip()

    # --- language and test detection -----------------------------------
    def detect_languages(self, workspace_id: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for rel in self.list_files(workspace_id):
            suffix = Path(rel).suffix.lower()
            if suffix in CODE_EXTENSIONS:
                counts[suffix] = counts.get(suffix, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))

    def detect_test_command(self, workspace_id: str) -> str | None:
        root = self.open(workspace_id)
        files = set(self.list_files(workspace_id))
        if "pyproject.toml" in files:
            try:
                text = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
                if "pytest" in text or "tool.pytest" in text:
                    return "python -m pytest -q"
            except OSError:
                pass
        for name in ("pytest.ini", "tox.ini", "setup.cfg"):
            if name in files:
                try:
                    text = (root / name).read_text(encoding="utf-8", errors="replace")
                    if "pytest" in text:
                        return "python -m pytest -q"
                except OSError:
                    pass
        if "requirements.txt" in files:
            try:
                text = (root / "requirements.txt").read_text(encoding="utf-8", errors="replace").lower()
                if "pytest" in text:
                    return "python -m pytest -q"
            except OSError:
                pass
        if "package.json" in files:
            try:
                package = json.loads((root / "package.json").read_text(encoding="utf-8", errors="replace"))
                if isinstance(package.get("scripts"), dict) and "test" in package["scripts"]:
                    return "npm test"
            except (OSError, json.JSONDecodeError):
                pass
        if "go.mod" in files:
            return "go test ./..."
        if "Cargo.toml" in files:
            return "cargo test"
        if "manage.py" in files:
            return "python manage.py test"
        if any(rel.startswith("tests/") or "/tests/" in rel for rel in files):
            return "python -m pytest -q"
        if any(TEST_FILE_RE.search(rel) for rel in files):
            return "python -m pytest -q"
        return None

    def find_related_files(self, workspace_id: str, task: str, limit: int = 8) -> List[str]:
        files = self.list_code_files(workspace_id)
        test_files = [f for f in files if TEST_FILE_RE.search(f)]
        related: List[str] = []
        root = self.open(workspace_id)
        for test_file in test_files[:10]:
            try:
                text = (root / test_file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for module in re.findall(r"^\s*(?:from|import)\s+([A-Za-z0-9_.]+)", text, re.M):
                candidate = module.replace(".", "/") + ".py"
                for rel in files:
                    if rel == candidate or rel.endswith("/" + candidate):
                        if rel not in related:
                            related.append(rel)
            for module in re.findall(r"(?:require\(|from\s+)['\"](\.[^'\"]+)['\"]", text):
                base = str(PurePosixPath(test_file).parent / module)
                for ext in ("", ".js", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts"):
                    for rel in files:
                        if rel == base + ext or rel.endswith("/" + base + ext):
                            if rel not in related:
                                related.append(rel)
        return related[:limit]

    def read_manifest_summary(self, workspace_id: str, max_chars: int = 4000) -> str:
        root = self.open(workspace_id)
        snippets: List[str] = []
        for rel in sorted(MANIFEST_FILES):
            path = root / rel
            if not path.is_file():
                continue
            try:
                text, _ = self.read_text(workspace_id, rel, max_bytes=20_000)
            except Exception:  # noqa: BLE001
                continue
            snippets.append(f"--- {rel} ---\n{truncate(text.strip(), 1200)}")
        return truncate("\n\n".join(snippets), max_chars)

    # --- model context --------------------------------------------------
    def numbered_file(self, workspace_id: str, relative: str, max_chars: int = 24_000) -> str:
        text, truncated = self.read_text(workspace_id, relative)
        lines = text.splitlines()
        if len(text) <= max_chars:
            numbered = "\n".join(f"{i:5d}| {line}" for i, line in enumerate(lines, 1))
            suffix = "\n... [file truncated by size]" if truncated else ""
            return f"FILE: {relative} ({len(lines)} lines)\n{numbered}{suffix}"
        window = 140
        numbered = "\n".join(f"{i:5d}| {line}" for i, line in enumerate(lines[:window], 1))
        return (
            f"FILE: {relative} ({len(lines)} lines; first {window} shown)\n{numbered}\n"
            f"... [only lines 1-{window} are shown; do not edit outside this range]"
        )

    def edit_context(self, workspace_id: str, paths: Iterable[str], max_chars: int = 24_000) -> Tuple[str, Dict[str, List[Tuple[int, int]]]]:
        chunks: List[str] = []
        ranges: Dict[str, List[Tuple[int, int]]] = {}
        for rel in paths:
            text, _ = self.read_text(workspace_id, rel)
            lines = text.splitlines()
            if len(text) <= max_chars:
                start, end = 1, max(1, len(lines))
            else:
                start, end = 1, min(140, max(1, len(lines)))
            numbered = "\n".join(f"{i:5d}| {line}" for i, line in enumerate(lines[start - 1:end], start))
            chunks.append(
                f"FILE: {rel} ({len(lines)} lines; editable range {start}-{end})\n{numbered}"
                + ("\n... [file truncated; only the range above is editable]" if end < len(lines) else "")
            )
            ranges[rel] = [(start, end)]
        return "\n\n".join(chunks), ranges
