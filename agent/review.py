"""Deterministic helpers for the reviewer node.

Nothing here calls a model. The reviewer's hard constraint is the Groq free
tier: 8,000 tokens per minute, input and output together. A generated project
is easily 30KB+ of code, so it cannot be reviewed in one request -- a request
over the limit is rejected outright, not queued. These helpers therefore:

- compact files (comments and blank lines removed) before they are sent,
- split them into batches small enough to fit one request each, and
- build a short index of the whole project, repeated in every batch, so that
  cross-file defects stay visible even when the two files involved land in
  different batches. Cross-file wiring is exactly what a coder writing one file
  at a time cannot see, so it is the reviewer's main job.
"""
import re
from pathlib import Path

from agent.states import ImplementationTask, ReviewIssue, TaskPlan

# Only code is reviewed in full; everything else appears in the index.
REVIEWABLE_SUFFIXES = (".html", ".css", ".js")
_ORDER = {".html": 0, ".css": 1, ".js": 2}

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$", re.MULTILINE)

_LINK_HREF_RE = re.compile(r"""<link\b[^>]*\bhref\s*=\s*["']([^"']+\.css)["']""", re.IGNORECASE)
_SCRIPT_SRC_RE = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_ID_ATTR_RE = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_WINDOW_DEF_RE = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)")
_TOP_LEVEL_DEF_RE = re.compile(
    r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|^(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_ID_LOOKUP_RE = re.compile(r"""getElementById\(\s*["']([^"']+)["']""")
_SELECTOR_LOOKUP_RE = re.compile(r"""querySelector(?:All)?\(\s*["']([^"']+)["']""")
_CSS_CLASS_RE = re.compile(r"\.(-?[A-Za-z_][\w-]*)")

_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:")


def _sort_key(rel: str):
    return (_ORDER.get(Path(rel).suffix.lower(), 9), rel)


def compact(rel: str, text: str) -> str:
    """Strip comments and collapse blank lines to save tokens.

    Used only for what is sent to the reviewer; files on disk are untouched.
    Generated code carries heavy doc comments, and removing them is the cheapest
    way to fit more real code into each request.
    """
    suffix = Path(rel).suffix.lower()
    if suffix in (".js", ".css"):
        text = _BLOCK_COMMENT_RE.sub("", text)
    if suffix == ".js":
        text = _LINE_COMMENT_RE.sub("", text)
    if suffix == ".html":
        text = _HTML_COMMENT_RE.sub("", text)

    out, previous_blank = [], False
    for line in text.splitlines():
        line = line.rstrip()
        blank = not line.strip()
        if blank and previous_blank:
            continue
        out.append(line)
        previous_blank = blank
    return "\n".join(out).strip()


def collect_review_files(root) -> list[tuple[str, str]]:
    """(relative path, compacted text) for every reviewable file, HTML first."""
    root = Path(root)
    if not root.is_dir():
        return []
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in REVIEWABLE_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        files.append((rel, compact(rel, text)))
    return sorted(files, key=lambda item: _sort_key(item[0]))


def batch_files(files: list[tuple[str, str]], budget_chars: int) -> list[list[tuple[str, str]]]:
    """Group files into batches whose combined size stays within the budget.

    No file is ever dropped. A single file larger than the budget gets a batch
    of its own and is truncated, with a marker, so the request still fits.
    """
    batches, current, used = [], [], 0
    for rel, text in files:
        if len(text) > budget_chars:
            text = text[:budget_chars] + f"\n... [truncated, {len(text) - budget_chars} more characters]"
        if current and used + len(text) > budget_chars:
            batches.append(current)
            current, used = [], 0
        current.append((rel, text))
        used += len(text)
    if current:
        batches.append(current)
    return batches


def format_files_block(batch: list[tuple[str, str]]) -> str:
    parts = []
    for rel, text in batch:
        parts.append(f"===== FILE: {rel} =====\n{text}")
    return "\n\n".join(parts)


def _resolve(root: Path, from_file: Path, ref: str):
    """Project-relative path for a reference made inside from_file, or None."""
    if not ref or ref.startswith(_EXTERNAL_PREFIXES):
        return None
    try:
        target = (from_file.parent / ref.split("?", 1)[0].split("#", 1)[0]).resolve()
        return target.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _cap(items, limit):
    items = list(dict.fromkeys(items))  # de-duplicate, keep order
    if len(items) > limit:
        return items[:limit] + [f"+{len(items) - limit} more"]
    return items


def build_digest(root, max_chars: int) -> str:
    """One line per file describing how it connects to the rest of the project.

    The point is to make wiring defects legible without sending every file:
    "stats.html loads [scripts/stats.js]" next to "scripts/stats.js uses
    [storage <- scripts/storage.js]" shows the missing script tag directly.
    """
    root = Path(root)
    if not root.is_dir():
        return "(project directory missing)"

    paths = sorted((p for p in root.rglob("*") if p.is_file()),
                   key=lambda p: _sort_key(p.relative_to(root).as_posix()))
    texts = {}
    for path in paths:
        try:
            texts[path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            texts[path] = None

    # Globals each script defines, so other scripts' uses can name their source.
    defined_by = {}
    for path, text in texts.items():
        if text is None or path.suffix.lower() != ".js":
            continue
        rel = path.relative_to(root).as_posix()
        names = _WINDOW_DEF_RE.findall(text)
        for fn_name, var_name in _TOP_LEVEL_DEF_RE.findall(text):
            names.append(fn_name or var_name)
        for name in names:
            defined_by.setdefault(name, rel)

    lines = []
    for path, text in texts.items():
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        if text is None:
            lines.append(f"{rel}: (binary or unreadable)")
            continue

        if suffix == ".html":
            css = [r for r in (_resolve(root, path, h) for h in _LINK_HREF_RE.findall(text)) if r]
            scripts = [r for r in (_resolve(root, path, s) for s in _SCRIPT_SRC_RE.findall(text)) if r]
            ids = ["#" + i for i in _ID_ATTR_RE.findall(text)]
            lines.append(f"{rel}: css {_cap(css, 6)}; scripts in load order {_cap(scripts, 12)}; "
                         f"ids {_cap(ids, 12)}")
        elif suffix == ".js":
            own = sorted(name for name, source in defined_by.items() if source == rel)
            uses = []
            for name, source in defined_by.items():
                if source == rel:
                    continue
                if re.search(rf"(?<![\w$.]){re.escape(name)}\s*[.(]|\bwindow\.{re.escape(name)}\b", text):
                    uses.append(f"{name} <- {source}")
            lookups = (["#" + i for i in _ID_LOOKUP_RE.findall(text)]
                       + _SELECTOR_LOOKUP_RE.findall(text))
            lines.append(f"{rel}: defines {_cap(own, 10)}; uses from other files {_cap(uses, 10)}; "
                         f"looks up {_cap(lookups, 12)}")
        elif suffix == ".css":
            classes = ["." + c for c in _CSS_CLASS_RE.findall(text)]
            lines.append(f"{rel}: styles classes {_cap(classes, 15)}")
        else:
            lines.append(f"{rel}: {len(text.splitlines())} lines (not reviewed in full)")

    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars] + "\n... (index truncated)"
    return digest


def normalize_issue_path(raw: str, known_paths) -> str:
    """Map a reviewer's file reference onto a real project path.

    Models write "./app.js", "scripts\\app.js" or just "app.js" for a file at
    "scripts/app.js". An exact match wins; otherwise a unique basename match is
    used. Anything else is kept as-is, since a fix may legitimately need a new
    file -- the write_file sandbox still rejects paths outside the project.
    """
    path = (raw or "").strip().strip("`'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    known = list(known_paths)
    if path in known:
        return path
    matches = [k for k in known if Path(k).name == Path(path).name]
    return matches[0] if len(matches) == 1 else path


def clean_issues(issues, known_paths, max_issues: int, is_safe_path) -> list[ReviewIssue]:
    """Normalise paths, drop unusable or duplicate issues, and cap the count."""
    cleaned, seen = [], set()
    for issue in issues:
        path = normalize_issue_path(issue.file, known_paths)
        if not path or not is_safe_path(path):
            continue
        key = (path, " ".join(issue.problem.lower().split()))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(ReviewIssue(file=path, problem=issue.problem.strip(), fix=issue.fix.strip()))
        if len(cleaned) >= max_issues:
            break
    return cleaned


def build_fix_plan(issues, original_plan: TaskPlan, review_round: int, root) -> TaskPlan:
    """Turn review issues into coder tasks: one task per file, dependency order.

    Fixes are grouped per file so each file is rewritten once, not once per
    issue, and ordered HTML -> CSS -> JS for the same reason the original plan
    is: a file should be written after the files it has to agree with.
    """
    root = Path(root)
    by_file = {}
    for issue in issues:
        by_file.setdefault(issue.file, []).append(issue)

    purposes = {step.filepath: step.task_description for step in original_plan.implementation_steps}

    steps = []
    for path in sorted(by_file, key=_sort_key):
        lines = [f"REVIEW FIX (round {review_round}). A reviewer found problems in `{path}`."]
        if (root / path).is_file():
            lines.append(
                "This file already exists. First read it with read_file, then write the "
                "COMPLETE corrected file with write_file. Keep everything that already "
                "works; change only what the fixes below require."
            )
        else:
            lines.append("This file does not exist yet. Create it with write_file.")
        lines.append("Fixes:")
        for n, issue in enumerate(by_file[path], 1):
            lines.append(f"{n}. Problem: {issue.problem}")
            lines.append(f"   Fix: {issue.fix}")
        if path in purposes:
            lines.append(f"Original purpose of this file: {purposes[path]}")
        steps.append(ImplementationTask(filepath=path, task_description="\n".join(lines)))

    return TaskPlan(shared_contract=original_plan.shared_contract, implementation_steps=steps)
