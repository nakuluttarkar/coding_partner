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

# Class names, for catching markup and stylesheet that drifted apart. Each file
# is generated in its own task, so nothing forces them to agree on a vocabulary
# -- and when the architect orders the stylesheet before the markup, the CSS is
# written for a DOM that does not exist yet.
_CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_CSS_CLASS_DEF_RE = re.compile(r"\.(-?[A-Za-z_][\w-]*)")
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
# Classes a script attaches at runtime: classList.add('x'), className = 'x y'.
_JS_CLASSLIST_RE = re.compile(
    r"""classList\s*\.\s*(?:add|remove|toggle|contains)\s*\(\s*["']([^"']+)["']""")
_JS_CLASSNAME_RE = re.compile(r"""\.className\s*=\s*["']([^"']*)["']""")

# Classes that are conventionally behavioural rather than styled, or that come
# from outside the project; flagging them would be noise.
_IGNORED_CLASSES = frozenset({"js", "no-js"})

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
    problems.extend(_find_unstyled_classes(root))
    return problems


def _collect_defined_classes(root: Path) -> set:
    """Class names that some stylesheet (or inline <style>) actually styles."""
    defined = set()
    for css_file in sorted(root.rglob("*.css")):
        try:
            defined.update(_CSS_CLASS_DEF_RE.findall(css_file.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    for html_file in sorted(root.rglob("*.html")):
        try:
            text = html_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for block in _STYLE_BLOCK_RE.findall(text):
            defined.update(_CSS_CLASS_DEF_RE.findall(block))
    return defined


def _find_unstyled_classes(root: Path) -> list[str]:
    """Report classes the markup or scripts apply that no stylesheet defines.

    This is the failure that leaves a generated app looking broken while every
    file reference is valid: a `hidden` class used to conceal a modal, which no
    rule defines, so the modal is simply always on screen.
    """
    defined = _collect_defined_classes(root)
    used = {}  # class -> file that first uses it

    def record(name, origin):
        name = name.strip()
        if name and name not in _IGNORED_CLASSES and name not in used:
            used[name] = origin

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".html", ".js"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        origin = path.relative_to(root).as_posix()
        # class="a b" appears in markup and in HTML strings built by scripts.
        for attr in _CLASS_ATTR_RE.findall(text):
            for name in attr.split():
                record(name, origin)
        if path.suffix.lower() == ".js":
            for name in _JS_CLASSLIST_RE.findall(text):
                record(name, origin)
            for attr in _JS_CLASSNAME_RE.findall(text):
                for name in attr.split():
                    record(name, origin)

    missing = sorted(name for name in used if name not in defined)
    if not missing:
        return []
    shown = ", ".join(f"'{n}' ({used[n]})" for n in missing[:6])
    more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
    return [
        f"{len(missing)} class(es) are applied but no stylesheet defines them: "
        f"{shown}{more}. The markup and the stylesheet disagree, so the page will "
        f"render unstyled or with elements that should be hidden left visible."
    ]


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
