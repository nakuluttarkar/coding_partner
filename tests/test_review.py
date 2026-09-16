"""Tests for the reviewer's deterministic helpers.

The reviewer has to fit a whole project into requests small enough for the free
tier's 8,000 tokens/minute limit, and still see how files connect. These tests
cover the batching that keeps requests under the limit, the project index that
keeps cross-file defects visible across batches, and turning issues into coder
tasks.
"""
from agent import review
from agent.states import ImplementationTask, ReviewIssue, TaskPlan


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- compaction ---------------------------------------------------------------

def test_compact_strips_js_comments_and_collapses_blank_lines():
    js = "/** Doc block\n * more doc\n */\nfunction a() {\n\n\n\n  // a note\n  return 1;\n}\n"
    out = review.compact("app.js", js)
    assert "Doc block" not in out and "a note" not in out
    assert "return 1;" in out
    assert "\n\n\n" not in out


def test_compact_keeps_trailing_code_comments_intact():
    """Only whole-line // comments are removed, so a URL or trailing note inside
    a line of code is never cut in half."""
    out = review.compact("app.js", 'const u = "https://example.com"; // keep\n')
    assert 'const u = "https://example.com"; // keep' in out


def test_compact_strips_html_and_css_comments():
    assert "hidden note" not in review.compact("index.html", "<!-- hidden note --><p>x</p>")
    assert "theme vars" not in review.compact("a.css", "/* theme vars */ .a { color: red; }")


# --- collection and batching -------------------------------------------------

def test_collect_orders_html_then_css_then_js_and_skips_other_files(tmp_path):
    write(tmp_path, "README.md", "# readme")
    write(tmp_path, "scripts/app.js", "let a = 1;")
    write(tmp_path, "index.html", "<p>x</p>")
    write(tmp_path, "styles/base.css", ".a{}")
    assert [rel for rel, _ in review.collect_review_files(tmp_path)] == [
        "index.html", "styles/base.css", "scripts/app.js"
    ]


def test_batches_respect_the_budget_and_never_drop_a_file():
    files = [(f"f{i}.js", "x" * 400) for i in range(10)]
    batches = review.batch_files(files, 1000)
    assert sum(len(batch) for batch in batches) == 10
    assert all(sum(len(text) for _, text in batch) <= 1000 for batch in batches)


def test_an_oversized_file_gets_its_own_truncated_batch():
    files = [("small.js", "a" * 10), ("huge.js", "b" * 5000), ("tail.js", "c" * 10)]
    batches = review.batch_files(files, 1000)
    huge = next(batch for batch in batches if batch[0][0] == "huge.js")
    assert len(huge) == 1
    assert "truncated" in huge[0][1]
    assert len(huge[0][1]) < 1100


def test_files_block_labels_every_file():
    block = review.format_files_block([("index.html", "<p>x</p>"), ("app.js", "1;")])
    assert "===== FILE: index.html =====" in block
    assert "===== FILE: app.js =====" in block


# --- project index -----------------------------------------------------------
# The index is what lets a batch holding stats.html see that stats.js, reviewed
# elsewhere, depends on storage.js -- the page's missing script tag becomes a
# single line away.

def test_index_exposes_a_page_missing_a_script_dependency(tmp_path):
    write(tmp_path, "stats.html", '<script src="scripts/stats.js" defer></script>')
    write(tmp_path, "scripts/storage.js", "window.storage = { getTasks() { return []; } };")
    write(tmp_path, "scripts/stats.js", "const tasks = storage.getTasks();")
    digest = review.build_digest(tmp_path, 5000)
    assert "stats.html: css []; scripts in load order ['scripts/stats.js']" in digest
    assert "storage <- scripts/storage.js" in digest


def test_index_resolves_script_paths_relative_to_the_page(tmp_path):
    write(tmp_path, "pages/about.html", '<script src="../scripts/app.js"></script>')
    write(tmp_path, "scripts/app.js", "window.app = {};")
    assert "scripts in load order ['scripts/app.js']" in review.build_digest(tmp_path, 5000)


def test_index_lists_ids_a_script_looks_up(tmp_path):
    write(tmp_path, "app.js", "document.getElementById('task-modal'); document.querySelector('.task-list');")
    digest = review.build_digest(tmp_path, 5000)
    assert "#task-modal" in digest and ".task-list" in digest


def test_index_ignores_external_scripts(tmp_path):
    write(tmp_path, "index.html", '<script src="https://cdn.example.com/lib.js"></script>')
    assert "scripts in load order []" in review.build_digest(tmp_path, 5000)


def test_index_is_capped():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in range(60):
            write(root, f"page{i}.html", f'<div id="element-number-{i}"></div>')
        digest = review.build_digest(root, 500)
        assert len(digest) <= 500 + len("\n... (index truncated)")
        assert digest.endswith("(index truncated)")


# --- cleaning issues ---------------------------------------------------------

def test_issue_paths_are_mapped_onto_real_files():
    known = ["index.html", "scripts/app.js", "styles/base.css"]
    assert review.normalize_issue_path("./index.html", known) == "index.html"
    assert review.normalize_issue_path("scripts\\app.js", known) == "scripts/app.js"
    assert review.normalize_issue_path("app.js", known) == "scripts/app.js"
    assert review.normalize_issue_path("`/styles/base.css`", known) == "styles/base.css"


def test_a_new_file_path_is_kept():
    assert review.normalize_issue_path("scripts/new.js", ["index.html"]) == "scripts/new.js"


def test_an_ambiguous_basename_is_not_guessed():
    assert review.normalize_issue_path("app.js", ["a/app.js", "b/app.js"]) == "app.js"


def test_clean_issues_dedupes_drops_unsafe_paths_and_caps():
    issues = [
        ReviewIssue(file="./index.html", problem="Missing  script tag", fix="add it"),
        ReviewIssue(file="index.html", problem="missing script tag", fix="add it"),
        ReviewIssue(file="../../etc/passwd", problem="escape", fix="no"),
    ] + [ReviewIssue(file="index.html", problem=f"problem {i}", fix="f") for i in range(10)]

    cleaned = review.clean_issues(issues, ["index.html"], 3, lambda p: not p.startswith(".."))

    assert len(cleaned) == 3
    assert [issue.file for issue in cleaned] == ["index.html"] * 3
    assert cleaned[0].problem == "Missing  script tag"
    assert not any("passwd" in issue.file for issue in cleaned)


# --- fix plan ----------------------------------------------------------------

def test_fix_plan_groups_by_file_orders_by_dependency_and_keeps_contract(tmp_path):
    write(tmp_path, "index.html", "<p>x</p>")
    original = TaskPlan(
        shared_contract=".hidden -> display:none",
        implementation_steps=[
            ImplementationTask(filepath="index.html", task_description="Main page"),
            ImplementationTask(filepath="scripts/app.js", task_description="App logic"),
        ],
    )
    issues = [
        ReviewIssue(file="scripts/app.js", problem="uses storage before load", fix="guard it"),
        ReviewIssue(file="index.html", problem="storage.js not loaded", fix="add script tag"),
        ReviewIssue(file="scripts/app.js", problem="modal never closes", fix="add close handler"),
    ]

    plan = review.build_fix_plan(issues, original, 1, tmp_path)

    assert [s.filepath for s in plan.implementation_steps] == ["index.html", "scripts/app.js"]
    assert plan.shared_contract == ".hidden -> display:none"

    page, script = (s.task_description for s in plan.implementation_steps)
    assert "shown under Existing Content" in page
    assert "read it with read_file" not in page, "the coder already has the content"
    assert "add script tag" in page
    assert "does not exist yet" in script
    assert "uses storage before load" in script and "add close handler" in script
    assert "Original purpose of this file: App logic" in script
    assert "REVIEW FIX (round 1)" in script


def test_index_lists_the_classes_a_page_uses(tmp_path):
    """The coder writes a stylesheet from the index rather than by reading the
    HTML, so the classes the markup uses have to be in it."""
    write(tmp_path, "index.html", '<div class="kanban-board"><p class="task-title hidden"></p></div>')
    digest = review.build_digest(tmp_path, 5000)
    assert ".kanban-board" in digest and ".task-title" in digest and ".hidden" in digest


# --- function signatures -----------------------------------------------------
# The live drag-and-drop bug: dragDrop.js called updateTask(taskId, {status})
# against updateTask(updatedTask). Caller and callee were reviewed in different
# batches and the index listed names only, so nothing showed the mismatch.

LIVE_STORAGE_JS = """
(function () {
  const KEY = 'kanban-tasks';
  function getTasks() { return JSON.parse(localStorage.getItem(KEY) || '[]'); }
  function saveTasks(tasks) { localStorage.setItem(KEY, JSON.stringify(tasks)); }
  function updateTask(updatedTask) { /* ... */ }
  function deleteTask(id) { /* ... */ }
  window.StorageAPI = {
    getTasks,
    saveTasks,
    updateTask, // replaces a task by id
    deleteTask,
  };
})();
"""


def test_api_lists_parameters_of_an_exported_object_literal():
    api = review.script_api(LIVE_STORAGE_JS)
    assert "StorageAPI.updateTask(updatedTask)" in api
    assert "StorageAPI.saveTasks(tasks)" in api
    assert "StorageAPI.getTasks()" in api
    assert "StorageAPI.deleteTask(id)" in api


def test_index_shows_the_signature_a_caller_in_another_file_must_match(tmp_path):
    write(tmp_path, "storage.js", LIVE_STORAGE_JS)
    write(tmp_path, "dragDrop.js", "StorageAPI.updateTask(taskId, { status: status });")
    digest = review.build_digest(tmp_path, 5000)
    assert "StorageAPI.updateTask(updatedTask)" in digest
    assert "StorageAPI <- storage.js" in digest


def test_api_covers_the_export_shapes_generated_scripts_use():
    cases = [
        ("window.UI = { render(tasks) { }, clear: function () { } };", (),
         ["UI.render(tasks)", "UI.clear()"]),
        ("window.Modal = { open: (task, onSave) => task };", (), ["Modal.open(task, onSave)"]),
        ("window.Modal = { close: done => done };", (), ["Modal.close(done)"]),
        ("function applyTheme(theme) {}\nwindow.applyTheme = applyTheme;", ("applyTheme",),
         ["applyTheme(theme)"]),
        ("window.init = function (root, options) {};", (), ["init(root, options)"]),
        ("const storage = { get(key) {}, set(key, value) {} };\nwindow.storage = storage;", (),
         ["storage.get(key)", "storage.set(key, value)"]),
        ("function helper(a, b) {}", ("helper",), ["helper(a, b)"]),
    ]
    for source, top_level, expected in cases:
        api = review.script_api(source, top_level)
        for signature in expected:
            assert signature in api, f"{signature} missing for: {source}"


def test_api_ignores_non_function_members():
    api = review.script_api("window.CONFIG = { key: 'kanban', retries: 3, open(x) {} };")
    assert api == ["CONFIG.open(x)"]


# --- class lists -------------------------------------------------------------

def _index_line(root, prefix):
    return next(line for line in review.build_digest(root, 100000).splitlines()
                if line.startswith(prefix))


def test_state_classes_are_never_hidden_by_the_stylesheet_cap(tmp_path):
    """A stylesheet with more classes than the cap, defining .hidden last, must
    still show .hidden -- the live false positive came from exactly this."""
    rules = "".join(f".c{i} {{ color: red; }}\n" for i in range(60)) + ".hidden { display: none; }\n"
    write(tmp_path, "base.css", rules)
    line = _index_line(tmp_path, "base.css")
    assert "'.hidden'" in line
    assert line.index("'.hidden'") < line.index("'.c0'")


def test_a_stylesheet_lists_up_to_forty_classes(tmp_path):
    write(tmp_path, "base.css", "".join(f".c{i} {{}}\n" for i in range(40)))
    line = _index_line(tmp_path, "base.css")
    assert "'.c39'" in line and "more" not in line


def test_a_page_lists_up_to_thirty_classes(tmp_path):
    write(tmp_path, "index.html", "".join(f'<p class="k{i}"></p>' for i in range(30)))
    line = _index_line(tmp_path, "index.html")
    assert "'.k29'" in line and "more" not in line
