"""Per-session workspace directory: create, cleanup, path-traversal defense.

Also writes the per-session opencode.json so OpenCode (scoped to this
directory) connects to our MCP server with the session's token. This is how
per-session MCP identity works — OpenCode has no per-session MCP API, only
per-directory config (see spec §12).
"""

import os
import json
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
        # Best-effort: a shared OpenCode process may still hold a handle on the
        # workspace (Windows then raises PermissionError). Don't let leftover
        # files block session teardown; they can be reclaimed later.
        shutil.rmtree(p, ignore_errors=True)


def write_opencode_config(workspace_path: str, *, mcp_name: str, mcp_url: str) -> str:
    """Write opencode.json into the workspace so OpenCode (scoped to this dir)
    connects to our MCP server at `mcp_url` (which carries the session token).
    Returns the config file path.
    """
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            mcp_name: {
                "type": "remote",
                "url": mcp_url,
                "enabled": True,
            },
        },
    }
    cfg_path = Path(workspace_path) / "opencode.json"
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return str(cfg_path)


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
