"""Bundle a generated static project into one self-contained HTML file.

A single file cannot suffer the failure this was written for: opening
index.html straight out of a zip viewer, where the sibling CSS and JS were
never extracted and the page renders unstyled.
"""
import re
from pathlib import Path

_LINK_RE = re.compile(
    r"""<link\b[^>]*\brel\s*=\s*["']stylesheet["'][^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>""",
    re.IGNORECASE,
)
_SCRIPT_RE = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>\s*</script>""",
    re.IGNORECASE,
)

_DEFER_RE = re.compile(r"\bdefer\b", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)

_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:")


def _local_target(html_path: Path, ref: str):
    """Resolve a reference to a local file, or None if it is external/missing."""
    if not ref or ref.startswith(_EXTERNAL_PREFIXES):
        return None
    clean = ref.split("?", 1)[0].split("#", 1)[0]
    if not clean:
        return None
    target = (html_path.parent / clean)
    return target if target.is_file() else None


def inline_html(html_path) -> str:
    """Return the HTML with local stylesheets and scripts inlined.

    External URLs and references that do not resolve are left untouched, so the
    output is never worse than the input.
    """
    html_path = Path(html_path)
    text = html_path.read_text(encoding="utf-8")

    def replace_link(match):
        target = _local_target(html_path, match.group(1))
        if target is None:
            return match.group(0)
        css = target.read_text(encoding="utf-8")
        return f"<style>\n{css}\n</style>"

    deferred = []

    def replace_script(match):
        target = _local_target(html_path, match.group(1))
        if target is None:
            return match.group(0)
        js = target.read_text(encoding="utf-8")
        # </script> inside the JS would close the tag early.
        js = js.replace("</script>", r"<\/script>")
        block = f"<script>\n{js}\n</script>"
        if _DEFER_RE.search(match.group(0)):
            # `defer` means "run after the document is parsed", but an inline
            # script ignores defer and runs the moment it is reached. A deferred
            # script in <head> would then execute before the elements it wires
            # up exist, leaving a page that looks right but does nothing. Moving
            # it to the end of <body> preserves both timing and top-level scope.
            deferred.append(block)
            return ""
        return block

    # Functions as replacements, so backslashes in CSS/JS are not treated as
    # regex escape sequences.
    text = _LINK_RE.sub(replace_link, text)
    text = _SCRIPT_RE.sub(replace_script, text)

    if deferred:
        blob = "\n".join(deferred)
        if _BODY_CLOSE_RE.search(text):
            text = _BODY_CLOSE_RE.sub(lambda m: blob + "\n" + m.group(0), text, count=1)
        else:
            text = text + "\n" + blob
    return text


def find_entry_html(project_root):
    """Pick the page to bundle: index.html if present, else the only .html file."""
    root = Path(project_root)
    index = root / "index.html"
    if index.is_file():
        return index
    html_files = sorted(root.rglob("*.html"))
    return html_files[0] if len(html_files) == 1 else None
