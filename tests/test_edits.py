from __future__ import annotations

import pytest

from tools.filesystem.edit import EditError, LineEdit, apply_line_edits, check_syntax, clean_replacement


def test_clean_replacement_removes_line_numbers() -> None:
    assert clean_replacement("  12| return 1") == "return 1"
    assert clean_replacement("  12 | return 1") == "return 1"


def test_single_line_replacement() -> None:
    content = "def add(a, b):\n    return a - b\n"
    new_content, details = apply_line_edits(content, [LineEdit("a.py", 2, 2, "    return a + b")])
    assert new_content == "def add(a, b):\n    return a + b\n"
    assert details[0]["before"] == "    return a - b"
    assert details[0]["after"] == "    return a + b"


def test_multi_line_replacement() -> None:
    content = "a\nb\nc\nd\n"
    new_content, _ = apply_line_edits(content, [LineEdit("a.py", 2, 3, "B\nC\nC2")])
    assert new_content == "a\nB\nC\nC2\nd\n"


def test_delete_lines_with_empty_replacement() -> None:
    content = "a\nb\nc\n"
    new_content, _ = apply_line_edits(content, [LineEdit("a.py", 2, 2, "")])
    assert new_content == "a\nc\n"


def test_edits_are_applied_bottom_up() -> None:
    content = "one\ntwo\nthree\n"
    new_content, _ = apply_line_edits(
        content,
        [LineEdit("a.py", 1, 1, "ONE"), LineEdit("a.py", 3, 3, "THREE")],
    )
    assert new_content == "ONE\ntwo\nTHREE\n"


def test_overlapping_edits_are_rejected() -> None:
    with pytest.raises(EditError):
        apply_line_edits("a\nb\nc\n", [LineEdit("a.py", 1, 2, "x"), LineEdit("a.py", 2, 3, "y")])


def test_out_of_range_is_rejected() -> None:
    with pytest.raises(EditError):
        apply_line_edits("a\nb\n", [LineEdit("a.py", 1, 5, "x")])


def test_allowed_range_is_enforced() -> None:
    with pytest.raises(EditError):
        apply_line_edits("a\nb\nc\n", [LineEdit("a.py", 3, 3, "C")], allowed_range=(1, 2))


def test_noop_edit_is_rejected() -> None:
    with pytest.raises(EditError):
        apply_line_edits("a\nb\n", [LineEdit("a.py", 1, 1, "a")])


def test_python_syntax_check() -> None:
    assert check_syntax("a.py", "def f(:\n    pass\n") is not None
    assert check_syntax("a.py", "def f():\n    return 1\n") is None


def test_json_syntax_check() -> None:
    assert check_syntax("a.json", '{"a": 1}') is None
    assert check_syntax("a.json", '{"a": }') is not None
