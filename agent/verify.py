"""Deterministic checks on the generated project.

These run without calling a model. That is deliberate: the failure they catch
(an HTML file pointing at a stylesheet nobody wrote) is exactly checkable, and
the free Groq tier is tight enough that spending an extra LLM call per file to
re-discover it would make generation less reliable, not more.
"""
import re
from pathlib import Path

# href= / src= on any tag, single or double quoted.
_REF_RE = re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_MODULE_RE = re.compile(r"""<script[^>]*\btype\s*=\s*["']module["']""", re.IGNORECASE)

# References that do not point at a file in the project.
_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:", "javascript:", "#")


def _is_local_reference(ref: str) -> bool:
    ref = ref.strip()
    if not ref or ref.startswith(_EXTERNAL_PREFIXES):
        return False
    return True


def find_problems(project_root) -> list[str]:
    """Return a list of human-readable problems with the generated project.

    Empty list means every asset an HTML file references exists on disk.
    """
    root = Path(project_root)
    problems = []
    if not root.is_dir():
        return [f"Project directory does not exist: {root}"]

    html_files = sorted(root.rglob("*.html"))
    if not html_files:
        return problems

    for html in html_files:
        try:
            text = html.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{html.name}: could not be read ({exc})")
            continue

        rel_html = html.relative_to(root).as_posix()

        for ref in _REF_RE.findall(text):
            if not _is_local_reference(ref):
                continue
            # Strip query string / fragment before resolving.
            clean = ref.split("?", 1)[0].split("#", 1)[0]
            if not clean:
                continue
            target = (html.parent / clean).resolve()
            if not target.exists():
                problems.append(f"{rel_html} references '{ref}' but no such file was generated")

        if _MODULE_RE.search(text):
            problems.append(
                f"{rel_html} uses <script type=\"module\">, which browsers block on "
                f"file:// URLs -- the page will be blank when opened from disk"
            )

    return problems
