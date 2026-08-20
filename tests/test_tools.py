"""Tests for the generated-project sandbox.

The file tools are the only thing standing between an LLM-chosen path and the
rest of the disk, so the escape cases matter more than the happy path.
"""
import pytest

from agent import tools


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "generated_project"
    root.mkdir()
    monkeypatch.setattr(tools, "GENERATED_PROJECT_ROOT", root)
    return root


def test_write_then_read_round_trip(sandbox):
    tools.write_file.invoke({"path": "index.html", "content": "<h1>hi</h1>"})
    assert (sandbox / "index.html").read_text(encoding="utf-8") == "<h1>hi</h1>"
    assert tools.read_file.invoke({"path": "index.html"}) == "<h1>hi</h1>"


def test_write_creates_nested_directories(sandbox):
    tools.write_file.invoke({"path": "assets/css/style.css", "content": "body{}"})
    assert (sandbox / "assets" / "css" / "style.css").exists()


def test_read_missing_file_returns_empty_string(sandbox):
    assert tools.read_file.invoke({"path": "nope.txt"}) == ""


def test_list_files_reports_relative_paths(sandbox):
    tools.write_file.invoke({"path": "a.txt", "content": "1"})
    tools.write_file.invoke({"path": "sub/b.txt", "content": "2"})
    listed = tools.list_files.invoke({"directory": "."}).splitlines()
    assert sorted(p.replace("\\", "/") for p in listed) == ["a.txt", "sub/b.txt"]


def test_list_files_on_empty_project(sandbox):
    assert tools.list_files.invoke({"directory": "."}) == "No files found."


# --- sandbox escapes -------------------------------------------------------

@pytest.mark.parametrize("escape", [
    "../evil.txt",
    "../../evil.txt",
    "sub/../../evil.txt",
])
def test_traversal_escapes_are_rejected(sandbox, escape):
    with pytest.raises(ValueError, match="outside project root"):
        tools.safe_path_for_project(escape)


def test_absolute_path_is_rejected(sandbox, tmp_path):
    # pathlib's `/` discards the left operand when the right side is absolute,
    # so this would silently land outside the sandbox without the guard.
    outside = tmp_path / "outside.txt"
    with pytest.raises(ValueError, match="outside project root"):
        tools.safe_path_for_project(str(outside))


def test_nested_paths_and_root_itself_are_allowed(sandbox):
    assert tools.safe_path_for_project("a/b/c.txt") == (sandbox / "a/b/c.txt").resolve()
    assert tools.safe_path_for_project(".") == sandbox.resolve()


def test_escape_is_blocked_through_the_tool_not_just_the_helper(sandbox, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    with pytest.raises(Exception):
        tools.write_file.invoke({"path": "../victim.txt", "content": "overwritten"})
    assert victim.read_text(encoding="utf-8") == "original"
