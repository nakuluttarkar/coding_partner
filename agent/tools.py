import pathlib

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
