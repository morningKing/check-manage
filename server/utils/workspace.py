"""Per-session workspace directory: create, cleanup, path-traversal defense."""

import os
import shutil
from pathlib import Path


class WorkspacePathError(ValueError):
    """Raised when a user-supplied path escapes the workspace root."""


def session_path(workspace_root: str, user_id: str, session_id: str) -> Path:
    return Path(workspace_root) / user_id / session_id


def create_session_workspace(workspace_root: str, user_id: str, session_id: str) -> str:
    p = session_path(workspace_root, user_id, session_id)
    (p / "uploads").mkdir(parents=True, exist_ok=True)
    (p / "outputs").mkdir(parents=True, exist_ok=True)
    return str(p.resolve())


def cleanup_session_workspace(workspace_root: str, user_id: str, session_id: str) -> None:
    p = session_path(workspace_root, user_id, session_id)
    if p.exists():
        shutil.rmtree(p)


def safe_resolve(root: str, rel_path: str) -> str:
    """Resolve `rel_path` under `root`; raise WorkspacePathError if it escapes.

    Uses os.path.isabs() instead of Path.is_absolute() to correctly detect
    Unix-style absolute paths (e.g. '/etc/passwd') on Windows, where
    Path('/etc/passwd').is_absolute() incorrectly returns False.
    """
    root_p = Path(root).resolve()
    if os.path.isabs(rel_path):
        raise WorkspacePathError("absolute path not allowed")
    target = (root_p / rel_path).resolve()
    try:
        target.relative_to(root_p)
    except ValueError:
        raise WorkspacePathError(f"path escapes workspace: {rel_path}")
    return str(target)
