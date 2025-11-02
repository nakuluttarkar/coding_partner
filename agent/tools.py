import pathlib
import subprocess
from typing import Tuple

from langchain_core.tools import tool

PROJECT_ROOT = pathlib.Path.cwd()
GENERATED_PROJECT_ROOT = PROJECT_ROOT / "generated_project"


def safe_path_for_project(path: str) -> pathlib.Path:
    p = (GENERATED_PROJECT_ROOT / path).resolve()
    if GENERATED_PROJECT_ROOT.resolve() not in p.parents and GENERATED_PROJECT_ROOT.resolve() != p.parent and GENERATED_PROJECT_ROOT.resolve() != p:
        raise ValueError("Attempt to write outside project root")
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

@tool("list_file")
def list_file(directory: str = ".") -> str:
    """Alias for list_files: Lists all files in the specified directory within the project root."""
    return list_files(directory)

@tool("run_cmd")
def run_cmd(cmd: str, cwd: str = None, timeout: int = 30) -> Tuple[int, str, str]:
    """Runs a shell command in the specified directory and returns the result."""
    cwd_dir = safe_path_for_project(cwd) if cwd else GENERATED_PROJECT_ROOT
    res = subprocess.run(cmd, shell=True, cwd=str(cwd_dir), capture_output=True, text=True, timeout=timeout)
    return res.returncode, res.stdout, res.stderr


def init_project_root():
    GENERATED_PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    return str(GENERATED_PROJECT_ROOT)