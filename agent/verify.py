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

# Asset paths inside quoted strings in JS or CSS. HTML-only scanning misses
# these: a recipe card whose image comes from seed data in a .js file renders
# broken while the markup itself references nothing missing.
_ASSET_IN_CODE_RE = re.compile(
    r"""["']([^"'\s>]+\.(?:jpg|jpeg|png|gif|webp|svg|ico|mp4|mp3|woff2?|ttf))["']""",
    re.IGNORECASE,
)
_CODE_SUFFIXES = (".js", ".css")

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

    # Note: no early return when there are no HTML files -- JS and CSS are
    # scanned regardless, since a missing asset is a problem either way.
    for html in sorted(root.rglob("*.html")):
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

    problems.extend(_find_missing_assets_in_code(root))
    return problems


def _find_missing_assets_in_code(root: Path) -> list[str]:
    """Catch asset paths referenced from JS or CSS rather than from markup.

    Only text files can be generated, so a .jpg named in seed data will never
    exist -- the page renders with broken images while the HTML itself is clean.
    """
    problems = []
    seen = set()
    for code_file in sorted(root.rglob("*")):
        if not code_file.is_file() or code_file.suffix.lower() not in _CODE_SUFFIXES:
            continue
        try:
            text = code_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_code = code_file.relative_to(root).as_posix()
        for ref in _ASSET_IN_CODE_RE.findall(text):
            if not _is_local_reference(ref):
                continue
            clean = ref.split("?", 1)[0].split("#", 1)[0]
            target = (root / clean).resolve()
            if target.exists():
                continue
            key = (rel_code, clean)
            if key in seen:
                continue
            seen.add(key)
            problems.append(
                f"{rel_code} references asset '{ref}' but no such file was generated "
                f"-- binary assets cannot be created, use inline SVG, CSS, or an emoji"
            )
    return problems
