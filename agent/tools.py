import pathlib
import shutil

from langchain_core.tools import tool

# Anchored to this file rather than the process cwd. Streamlit (and some IDEs)
# launch from a different directory, which previously scattered generated_project/
# wherever the process happened to start instead of the repo root.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERATED_PROJECT_ROOT = PROJECT_ROOT / "generated_project"


def safe_path_for_project(path: str) -> pathlib.Path:
    """Resolve `path` inside the generated-project sandbox.

    Raises ValueError if it escapes, whether by `..` traversal or by being
    absolute -- pathlib's `/` discards the left operand when the right side is
    absolute, so both cases have to be caught after resolving.
    """
    root = GENERATED_PROJECT_ROOT.resolve()
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"Attempt to access path outside project root: {path}")
    return p


@tool("write_file")
def write_file(path: str, content: str) -> str:
    """Writes content to a file at the specified path within the project root."""
    p = safe_path_for_project(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return f"WROTE:{p}"


@tool("read_file")
def read_file(path: str) -> str:
    """Reads content from a file at the specified path within the project root."""
    p = safe_path_for_project(path)
    if not p.exists():
        return ""
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


@tool("get_current_directory")
def get_current_directory() -> str:
    """Returns the current working directory."""
    return str(GENERATED_PROJECT_ROOT)


@tool("list_files")
def list_files(directory: str = ".") -> str:
    """Lists all files in the specified directory within the project root."""
    p = safe_path_for_project(directory)
    if not p.is_dir():
        return f"ERROR: {p} is not a directory"
    files = [str(f.relative_to(GENERATED_PROJECT_ROOT)) for f in p.glob("**/*") if f.is_file()]
    return "\n".join(files) if files else "No files found."


def init_project_root():
    GENERATED_PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    return str(GENERATED_PROJECT_ROOT)


def project_fingerprint():
    """Identify exactly which files are in the generated project right now.

    (relative path, size, modification time) for every file, or None if there
    are none. generated_project/ is shared on disk and outlives any one browser
    session, so the UI records this when a session finishes generating, and
    only offers the project while it still matches -- a fresh session, or one
    whose project another run has since written over, gets nothing stale.
    """
    if not GENERATED_PROJECT_ROOT.is_dir():
        return None
    entries = []
    for path in sorted(GENERATED_PROJECT_ROOT.rglob("*")):
        if path.is_file():
            stat = path.stat()
            entries.append((path.relative_to(GENERATED_PROJECT_ROOT).as_posix(),
                            stat.st_size, stat.st_mtime_ns))
    return tuple(entries) or None


def clear_project_root():
    """Empty the generated project before a new run.

    Without this, a run only overwrites files whose names collide, so leftovers
    from an earlier project ship inside the next download -- which is how a
    calculator ended up packaged with a todo app's README.
    """
    if GENERATED_PROJECT_ROOT.exists():
        shutil.rmtree(GENERATED_PROJECT_ROOT)
    GENERATED_PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    return str(GENERATED_PROJECT_ROOT)
