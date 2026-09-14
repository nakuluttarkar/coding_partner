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


# --- assets referenced from code, not markup ---------------------------------
# A live run produced recipe cards whose images came from seed data in a .js
# file. The HTML was clean, the verifier passed, and every image 404'd.

def test_missing_image_referenced_from_javascript_is_reported(tmp_path):
    write(tmp_path, "index.html", '<div id="app"></div>')
    write(tmp_path, "storage.js", 'const seed=[{title:"Pasta",image:"spaghetti.jpg"}];')
    problems = find_problems(tmp_path)
    assert any("spaghetti.jpg" in p for p in problems)
    assert any("storage.js" in p for p in problems)


def test_missing_asset_referenced_from_css_is_reported(tmp_path):
    write(tmp_path, "style.css", 'body { background: url("bg.png"); }')
    assert any("bg.png" in p for p in find_problems(tmp_path))


def test_existing_asset_referenced_from_javascript_is_fine(tmp_path):
    write(tmp_path, "app.js", 'const icon = "icon.svg";')
    write(tmp_path, "icon.svg", "<svg></svg>")
    assert find_problems(tmp_path) == []


def test_external_asset_urls_in_code_are_ignored(tmp_path):
    write(tmp_path, "app.js", 'const img = "https://cdn.example.com/a.png";')
    assert find_problems(tmp_path) == []


def test_each_missing_asset_is_reported_once(tmp_path):
    write(tmp_path, "app.js", 'const a="x.jpg"; const b="x.jpg"; const c="x.jpg";')
    assert len([p for p in find_problems(tmp_path) if "x.jpg" in p]) == 1


# --- markup / stylesheet drift ----------------------------------------------
# A live run ordered base.css before index.html, so the stylesheet was written
# for a DOM that did not exist. Ten classes were applied with no rule defining
# them, including `hidden` -- leaving the modal and empty state always visible
# while every file reference was perfectly valid.

def test_class_used_in_markup_without_a_rule_is_reported(tmp_path):
    write(tmp_path, "index.html", '<div class="app-header">x</div>')
    write(tmp_path, "style.css", ".recipe-card { color: red; }")
    problems = find_problems(tmp_path)
    assert any("app-header" in p for p in problems)


def test_missing_hidden_rule_is_reported(tmp_path):
    write(tmp_path, "index.html", '<div id="modal" class="modal hidden">x</div>')
    write(tmp_path, "style.css", ".modal { position: fixed; }")
    assert any("'hidden'" in p for p in find_problems(tmp_path))


def test_classes_added_by_scripts_are_checked(tmp_path):
    write(tmp_path, "index.html", "<div id=app></div>")
    write(tmp_path, "app.js", "el.classList.add('active');")
    write(tmp_path, "style.css", "#app { color: red; }")
    assert any("active" in p for p in find_problems(tmp_path))


def test_classname_assignment_in_scripts_is_checked(tmp_path):
    write(tmp_path, "index.html", "<div id=app></div>")
    write(tmp_path, "app.js", "card.className = 'recipe-card';")
    write(tmp_path, "style.css", "#app{}")
    assert any("recipe-card" in p for p in find_problems(tmp_path))


def test_matching_markup_and_stylesheet_is_clean(tmp_path):
    write(tmp_path, "index.html",
          '<div class="app-header"><span class="title">x</span></div>')
    write(tmp_path, "app.js", "el.classList.add('hidden');")
    write(tmp_path, "style.css",
          ".app-header{} .title{} .hidden{display:none}")
    assert find_problems(tmp_path) == []


def test_inline_style_block_counts_as_defining_classes(tmp_path):
    """The standalone bundle inlines CSS into <style>, which must still count."""
    write(tmp_path, "index.html",
          '<style>.app-header{color:red}</style><div class="app-header">x</div>')
    assert find_problems(tmp_path) == []


def test_classes_defined_but_unused_are_not_reported(tmp_path):
    """Dead CSS is untidy, not broken -- only the reverse direction breaks a page."""
    write(tmp_path, "index.html", '<div class="used">x</div>')
    write(tmp_path, "style.css", ".used{} .never-used{} .also-unused{}")
    assert find_problems(tmp_path) == []
