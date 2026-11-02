"""Pure functions for validating and applying line-based edits."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence

try:  # PyYAML is already a dependency, but keep the helper optional.
    import yaml
except Exception:  # pragma: no cover
    yaml = None


class EditError(ValueError):
    pass


@dataclass
class LineEdit:
    path: str
    start_line: int
    end_line: int
    replacement: str
    reason: str | None = None


LINE_PREFIX_RE = re.compile(r"^\s*\d+\s*\|\s?")


def clean_replacement(text: str) -> str:
    """Remove accidental ``12| `` prefixes copied from numbered context."""
    if not isinstance(text, str):
        raise EditError("replacement must be a string")
    lines = text.split("\n")
    cleaned = [LINE_PREFIX_RE.sub("", line) for line in lines]
    return "\n".join(cleaned)


def apply_line_edits(content: str, edits: Sequence[LineEdit], *, allowed_range: tuple[int, int] | None = None) -> tuple[str, list[dict]]:
    """Apply 1-based, inclusive line replacements.

    Edits are applied bottom-up so line numbers stay stable. Overlapping edits
    are rejected. A replacement may span multiple lines or be empty (delete).
    """
    if not edits:
        raise EditError("no edits supplied")
    lines = content.splitlines()
    total = len(lines)
    if total == 0:
        total = 1
    normalized: List[LineEdit] = []
    for edit in edits:
        if not edit.path:
            raise EditError("edit is missing a path")
        if edit.start_line < 1 or edit.end_line < edit.start_line:
            raise EditError(f"invalid line range {edit.start_line}-{edit.end_line} for {edit.path}")
        if edit.end_line > total:
            raise EditError(f"line range {edit.start_line}-{edit.end_line} exceeds file length {total} for {edit.path}")
        if allowed_range:
            lo, hi = allowed_range
            if edit.start_line < lo or edit.end_line > hi:
                raise EditError(
                    f"edit range {edit.start_line}-{edit.end_line} is outside the editable range {lo}-{hi} for {edit.path}"
                )
        normalized.append(edit)

    ordered = sorted(normalized, key=lambda e: (e.start_line, e.end_line), reverse=True)
    for previous, current in zip(ordered, ordered[1:]):
        if current.end_line >= previous.start_line:
            raise EditError(f"overlapping edits for {current.path}")

    details: List[dict] = []
    new_lines = list(lines)
    for edit in ordered:
        before = new_lines[edit.start_line - 1:edit.end_line]
        replacement = clean_replacement(edit.replacement)
        replacement_lines = replacement.split("\n") if replacement != "" else []
        new_lines[edit.start_line - 1:edit.end_line] = replacement_lines
        details.append(
            {
                "path": edit.path,
                "start_line": edit.start_line,
                "end_line": edit.end_line,
                "before": "\n".join(before),
                "after": replacement,
                "reason": edit.reason,
            }
        )
    new_content = "\n".join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"
    if new_content == content:
        raise EditError("the proposed edit does not change the file")
    return new_content, list(reversed(details))


def apply_search_replace(content: str, search: str, replace: str, *, path: str = "") -> tuple[str, dict]:
    if not search:
        raise EditError("search text is empty")
    count = content.count(search)
    if count == 0:
        raise EditError(f"search text was not found in {path}")
    if count > 1:
        raise EditError(f"search text occurs {count} times in {path}; provide a unique block or use line edits")
    new_content = content.replace(search, replace, 1)
    if new_content == content:
        raise EditError("the proposed edit does not change the file")
    return new_content, {"path": path, "before": search, "after": replace}


def check_syntax(path: str, content: str) -> str | None:
    """Return an error string for clearly invalid source files, else None."""
    lowered = path.lower()
    try:
        if lowered.endswith(".py"):
            ast.parse(content)
        elif lowered.endswith(".json"):
            json.loads(content)
        elif lowered.endswith((".yaml", ".yml")) and yaml is not None:
            list(yaml.safe_load_all(content))
    except SyntaxError as exc:
        return f"Python syntax error at line {exc.lineno}: {exc.msg}"
    except json.JSONDecodeError as exc:
        return f"JSON syntax error at line {exc.lineno}: {exc.msg}"
    except Exception as exc:  # noqa: BLE001
        return f"YAML syntax error: {exc}"
    return None


def summarize_edits(details: Iterable[dict], max_chars: int = 2_000) -> str:
    lines = []
    for item in details:
        lines.append(f"{item['path']}:{item['start_line']}-{item['end_line']} — {item.get('reason') or 'edited'}")
    text = "\n".join(lines)
    return text[:max_chars]
