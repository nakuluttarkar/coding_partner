"""Tests for the single-file bundler.

A self-contained HTML file is immune to the failure that started this: opening
index.html from inside a zip viewer, where siblings were never extracted.
"""
from agent.bundle import find_entry_html, inline_html


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_css_and_js_are_inlined(tmp_path):
    html = write(tmp_path, "index.html",
                 '<head><link rel="stylesheet" href="style.css"></head>'
                 '<body><script src="script.js" defer></script></body>')
    write(tmp_path, "style.css", "body { color: red; }")
    write(tmp_path, "script.js", "console.log('hi');")

    out = inline_html(html)
    assert "<style>" in out and "color: red" in out
    assert "<script>" in out and "console.log" in out
    assert 'href="style.css"' not in out
    assert 'src="script.js"' not in out


def test_result_stands_alone(tmp_path):
    """The whole point: no reference to a sibling file survives."""
    html = write(tmp_path, "index.html",
                 '<link rel="stylesheet" href="style.css"><script src="script.js"></script>')
    write(tmp_path, "style.css", "body{}")
    write(tmp_path, "script.js", "1;")
    assert "style.css" not in inline_html(html)
    assert "script.js" not in inline_html(html)


def test_backslashes_in_css_survive(tmp_path):
    """Regex replacement must not treat CSS content as escape sequences."""
    html = write(tmp_path, "index.html", '<link rel="stylesheet" href="style.css">')
    write(tmp_path, "style.css", r'.a::after { content: "\2190"; }')
    assert r"\2190" in inline_html(html)


def test_closing_script_tag_inside_js_is_escaped(tmp_path):
    html = write(tmp_path, "index.html", '<script src="a.js"></script>')
    write(tmp_path, "a.js", 'var s = "</script>";')
    out = inline_html(html)
    assert r"<\/script>" in out
    # Exactly one real closing tag, so the inlined block cannot terminate early.
    assert out.count("</script>") == 1


def test_external_and_missing_references_are_left_alone(tmp_path):
    html = write(tmp_path, "index.html",
                 '<link rel="stylesheet" href="https://cdn.example.com/x.css">'
                 '<script src="missing.js"></script>')
    out = inline_html(html)
    assert "https://cdn.example.com/x.css" in out
    assert 'src="missing.js"' in out


def test_find_entry_prefers_index_html(tmp_path):
    write(tmp_path, "index.html", "<p>x</p>")
    write(tmp_path, "other.html", "<p>y</p>")
    assert find_entry_html(tmp_path).name == "index.html"


def test_find_entry_falls_back_to_a_lone_html_file(tmp_path):
    write(tmp_path, "app.html", "<p>x</p>")
    assert find_entry_html(tmp_path).name == "app.html"


def test_find_entry_is_none_when_ambiguous(tmp_path):
    write(tmp_path, "a.html", "x")
    write(tmp_path, "b.html", "y")
    assert find_entry_html(tmp_path) is None


# --- deferred scripts --------------------------------------------------------
# An inline <script> ignores `defer` and runs where it sits. Inlining a deferred
# head script in place therefore runs it before the DOM exists, producing a page
# that looks right but does nothing -- caught by clicking through a real bundle.

DEFERRED_PAGE = ('<html><head><link rel="stylesheet" href="style.css">'
                 '<script src="script.js" defer></script></head>'
                 '<body><button id="go">go</button></body></html>')


def test_deferred_script_moves_to_end_of_body(tmp_path):
    html = write(tmp_path, "index.html", DEFERRED_PAGE)
    write(tmp_path, "style.css", "body{}")
    write(tmp_path, "script.js", "document.getElementById('go').onclick = null;")

    out = inline_html(html)
    assert out.index("<script>") > out.index('id="go"'), \
        "deferred script must run after the elements it touches exist"
    assert out.index("<script>") < out.index("</body>")


def test_deferred_script_is_not_duplicated(tmp_path):
    html = write(tmp_path, "index.html", DEFERRED_PAGE)
    write(tmp_path, "style.css", "body{}")
    write(tmp_path, "script.js", "var marker = 1;")
    out = inline_html(html)
    assert out.count("var marker = 1;") == 1


def test_non_deferred_script_stays_in_place(tmp_path):
    html = write(tmp_path, "index.html",
                 '<html><body><p>x</p><script src="a.js"></script></body></html>')
    write(tmp_path, "a.js", "var inplace = 1;")
    out = inline_html(html)
    assert out.index("var inplace") > out.index("<p>x</p>")


def test_deferred_script_without_body_tag_is_appended(tmp_path):
    html = write(tmp_path, "index.html", '<script src="a.js" defer></script><p>x</p>')
    write(tmp_path, "a.js", "var x = 1;")
    out = inline_html(html)
    assert out.strip().endswith("</script>")
