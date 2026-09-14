"""Tests for the deterministic project checks.

These guard the failure the user actually hit: a page that references a
stylesheet, shipped without it.
"""
import pytest

from agent.verify import find_problems


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_complete_project_has_no_problems(tmp_path):
    write(tmp_path, "index.html",
          '<link rel="stylesheet" href="style.css"><script src="script.js" defer></script>')
    write(tmp_path, "style.css", "body{}")
    write(tmp_path, "script.js", "// hi")
    assert find_problems(tmp_path) == []


def test_missing_stylesheet_is_reported(tmp_path):
    write(tmp_path, "index.html", '<link rel="stylesheet" href="style.css">')
    problems = find_problems(tmp_path)
    assert len(problems) == 1
    assert "style.css" in problems[0] and "index.html" in problems[0]


def test_missing_script_is_reported(tmp_path):
    write(tmp_path, "index.html", '<script src="app.js"></script>')
    assert any("app.js" in p for p in find_problems(tmp_path))


def test_nested_asset_paths_resolve(tmp_path):
    write(tmp_path, "index.html", '<link rel="stylesheet" href="assets/css/style.css">')
    write(tmp_path, "assets/css/style.css", "body{}")
    assert find_problems(tmp_path) == []


@pytest.mark.parametrize("ref", [
    "https://cdn.example.com/x.css",
    "http://example.com/x.css",
    "//example.com/x.css",
    "#",
    "mailto:a@b.c",
    "data:text/css,body{}",
])
def test_external_references_are_ignored(tmp_path, ref):
    write(tmp_path, "index.html", f'<link rel="stylesheet" href="{ref}">')
    assert find_problems(tmp_path) == []


def test_query_and_fragment_are_stripped_before_resolving(tmp_path):
    write(tmp_path, "index.html", '<link rel="stylesheet" href="style.css?v=2">')
    write(tmp_path, "style.css", "body{}")
    assert find_problems(tmp_path) == []


def test_module_script_is_flagged_because_it_breaks_on_file_urls(tmp_path):
    write(tmp_path, "index.html", '<script type="module" src="app.js"></script>')
    write(tmp_path, "app.js", "export const x = 1;")
    problems = find_problems(tmp_path)
    assert any("file://" in p for p in problems)


def test_project_with_no_html_is_not_a_problem(tmp_path):
    write(tmp_path, "main.py", "print('hi')")
    assert find_problems(tmp_path) == []


def test_missing_directory_is_reported(tmp_path):
    assert find_problems(tmp_path / "nope") != []
