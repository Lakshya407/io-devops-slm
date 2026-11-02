"""Policy for commands the model is allowed to run.

The primary security boundary is the isolated, unprivileged sandbox container
(no host mounts, no Docker socket, no network by default). This policy is a
second, explicit layer: only known build/test/inspection binaries are allowed,
commands are executed as an argv list (never through a shell), and dangerous
operations are rejected before they reach the runner.
"""
from __future__ import annotations

import shlex
from typing import Iterable, Sequence, Tuple

MAX_COMMAND_CHARS = 8_000
MAX_ARGS = 64

ALLOWED_BINARIES = {
    # Python
    "python", "python3", "pytest", "pip", "pip3", "mypy", "ruff", "black", "flake8",
    # JavaScript / TypeScript
    "npm", "node", "npx", "yarn", "pnpm", "eslint", "prettier", "tsc", "jest", "vitest", "mocha",
    # Other language toolchains
    "go", "cargo", "rustc", "make", "cmake", "dotnet", "mvn", "gradle", "php", "composer", "ruby", "bundle", "rake",
    # Read-only inspection / diffing
    "git", "ls", "cat", "head", "tail", "grep", "find", "wc", "sort", "uniq", "diff",
    "sed", "awk", "cut", "tr", "echo", "pwd", "test", "true", "false",
}

DENIED_BINARIES = {
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh",
    "sudo", "su", "doas", "pkexec",
    "docker", "podman", "nerdctl", "buildah", "kubectl", "helm", "kustomize",
    "ssh", "scp", "sftp", "rsync", "nc", "netcat", "ncat", "nmap", "telnet", "ftp", "socat",
    "curl", "wget", "http", "https",
    "rm", "rmdir", "unlink", "shred", "dd", "mkfs", "mount", "umount", "swapon", "swapoff",
    "chown", "chmod", "chattr", "setfacl",
    "kill", "pkill", "killall", "systemctl", "service", "crontab", "at",
    "iptables", "nft", "ufw", "ip", "ifconfig", "route",
    "apt", "apt-get", "dpkg", "yum", "dnf", "apk", "snap",
    "useradd", "userdel", "usermod", "passwd", "groupadd",
    "env", "printenv", "export", "source", "eval", "exec",
    "base64", "xxd", "openssl", "gpg",
}

DANGEROUS_TOKENS = {"&&", "||", ";", "|", ">", "<", "`", "$(", "${", "\n", "\r", "&>"}

GIT_ALLOWED_SUBCOMMANDS = {
    "status", "diff", "log", "show", "branch", "checkout", "switch", "add", "commit",
    "rev-parse", "ls-files", "ls-tree", "blame", "describe", "config", "remote", "tag",
    "stash", "shortlog", "whatchanged",
}

GIT_DENIED_FLAGS = {
    "--force", "-f", "--hard", "--mirror", "--upload-pack", "--receive-pack", "--exec",
}

NPM_ALLOWED_SUBCOMMANDS = {"test", "run", "ci", "install", "ls", "outdated", "audit"}
NPM_DENIED_SUBCOMMANDS = {"publish", "unpublish", "exec", "link", "login", "adduser", "token", "config"}

PIP_ALLOWED_SUBCOMMANDS = {"install", "download", "list", "show", "check"}
PIP_DENIED_FLAGS = {"--index-url", "--extra-index-url", "--trusted-host"}


def split_command(command: str) -> Tuple[list[str] | None, str | None]:
    if not isinstance(command, str) or not command.strip():
        return None, "command is empty"
    if len(command) > MAX_COMMAND_CHARS:
        return None, f"command exceeds {MAX_COMMAND_CHARS} characters"
    lowered = command.lower()
    for token in DANGEROUS_TOKENS:
        if token in command:
            return None, f"shell operator {token!r} is not allowed; pass a single argv list instead"
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        return None, f"could not parse command: {exc}"
    if not argv:
        return None, "command is empty"
    return argv, None


def _basename(argv: Sequence[str]) -> str:
    return argv[0].rsplit("/", 1)[-1]


def validate_argv(argv: Sequence[str]) -> Tuple[bool, str | None]:
    if not argv:
        return False, "argv is empty"
    if len(argv) > MAX_ARGS:
        return False, f"too many arguments (max {MAX_ARGS})"
    binary = _basename(argv)
    if "/" in argv[0] or "\\" in argv[0]:
        return False, "executable must be a PATH command, not an absolute or relative path"
    if binary in DENIED_BINARIES:
        return False, f"binary {binary!r} is denied by policy"
    if binary not in ALLOWED_BINARIES:
        return False, f"binary {binary!r} is not in the allowlist"

    args = list(argv[1:])
    if binary == "git":
        if not args:
            return False, "git requires a subcommand"
        sub = args[0]
        if sub not in GIT_ALLOWED_SUBCOMMANDS:
            return False, f"git subcommand {sub!r} is not allowed"
        for arg in args:
            if arg in GIT_DENIED_FLAGS or arg.startswith("--force-with-lease"):
                return False, f"git flag {arg!r} is not allowed"
        if sub in {"checkout", "switch"} and not any(a in {"-b", "-c", "--create"} for a in args):
            return False, "git checkout/switch is limited to creating a new branch (-b/-c)"
        if sub == "config":
            if not any(a in {"--get", "--list", "--local"} for a in args):
                return False, "git config is restricted to read-only or --local operations"
        if sub == "remote" and not (len(args) == 1 or "-v" in args):
            return False, "git remote is read-only (use 'git remote' or 'git remote -v')"

    if binary in {"npm", "yarn", "pnpm"} and args:
        sub = args[0]
        if sub in NPM_DENIED_SUBCOMMANDS:
            return False, f"{binary} {sub!r} is not allowed"
        if sub not in NPM_ALLOWED_SUBCOMMANDS:
            return False, f"{binary} subcommand {sub!r} is not allowed"

    if binary in {"pip", "pip3"} and args:
        if args[0] not in PIP_ALLOWED_SUBCOMMANDS:
            return False, f"{binary} subcommand {args[0]!r} is not allowed"
        for arg in args:
            if arg in PIP_DENIED_FLAGS:
                return False, f"{binary} flag {arg!r} is not allowed"

    if binary == "find":
        for arg in args:
            if arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir"}:
                return False, f"find {arg!r} is not allowed"
    if binary == "sed":
        for arg in args:
            if arg == "-i" or arg.startswith("--in-place"):
                return False, "in-place sed is not allowed; use the edit tool"
    if binary == "awk":
        joined = " ".join(args)
        if "system(" in joined or "|" in joined:
            return False, "awk system()/pipes are not allowed"
    if binary in {"echo", "cat"} and any(a.startswith("/dev/") for a in args):
        return False, "access to /dev devices is not allowed"
    return True, None


def validate_command(command: str) -> Tuple[bool, str | None, list[str] | None]:
    argv, error = split_command(command)
    if error:
        return False, error, None
    ok, reason = validate_argv(argv or [])
    if not ok:
        return False, reason, argv
    return True, None, argv


def allowed_env(extra: Iterable[str] | None = None) -> dict[str, str]:
    """Return the only environment variables the sandbox accepts."""
    env = {
        "CI": "true",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "NODE_ENV": "test",
    }
    if extra:
        allowed_keys = {"CI", "NODE_ENV", "PYTEST_ADDOPTS"}
        for item in extra:
            if "=" in item:
                key = item.split("=", 1)[0]
                if key in allowed_keys:
                    env[key] = item.split("=", 1)[1]
    return env
