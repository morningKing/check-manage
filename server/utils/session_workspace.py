"""Shared preparation for interactive and worker-created AI workspaces."""

import os
import shutil
from pathlib import Path

from utils.mcp_servers import enabled_mcp_config, internal_mcp_enabled
from utils.workspace import (
    WorkspacePathError,
    create_session_workspace,
    interactive_workspace_root,
    legacy_batch_workspace_root,
    resolve_batch_data_path,
    safe_resolve,
    write_opencode_config,
)


def _copy_staged_inputs(workspace_path: str, staged_inputs, workspace_root: str) -> None:
    if not staged_inputs:
        return
    paths = staged_inputs if isinstance(staged_inputs, list) else [staged_inputs]
    upload_dir = Path(workspace_path) / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    roots = (workspace_root, legacy_batch_workspace_root())
    for rel in paths:
        rel = str(rel)
        if os.path.isabs(rel):
            raise WorkspacePathError('absolute path not allowed')
        for root in roots:
            safe_resolve(root, rel)
        source = Path(resolve_batch_data_path(rel, roots=roots))
        last_error = None
        for _ in range(3):
            try:
                if source.is_dir():
                    shutil.copytree(str(source), str(upload_dir), dirs_exist_ok=True)
                else:
                    shutil.copy2(str(source), str(upload_dir / Path(rel).name))
                last_error = None
                break
            except (PermissionError, OSError) as error:
                last_error = error
        if last_error is not None:
            raise last_error


def prepare_interactive_session_workspace(
    user_id: str,
    session_id: str,
    *,
    staged_inputs: str | list[str] | None,
    mcp_name: str,
    mcp_url: str,
    token: str,
    agent: str | None = None,
    model: str | None = None,
    workspace_root: str | None = None,
) -> str:
    """Create a workspace, copy staged inputs, and install OpenCode config.

    ``workspace_root`` is private plumbing for batch deployments that retain an
    explicit ``AI_CHAT_WORKSPACE_ROOT`` override; ordinary sessions use the
    configured AI workspace root. ``agent`` is accepted for parity with worker
    callers, but OpenCode selects agents at prompt dispatch time.
    """
    del agent
    root = workspace_root or interactive_workspace_root()
    if token and 'token=' not in mcp_url:
        separator = '&' if '?' in mcp_url else '?'
        mcp_url = f'{mcp_url.rstrip("/")}/mcp{separator}token={token}'
    workspace = create_session_workspace(root, user_id, session_id)
    _copy_staged_inputs(workspace, staged_inputs, root)
    try:
        external_mcp = enabled_mcp_config(reserved_names=[mcp_name])
        include_internal = internal_mcp_enabled()
    except Exception:
        external_mcp = {}
        include_internal = True
    write_opencode_config(
        workspace,
        mcp_name=mcp_name,
        mcp_url=mcp_url,
        model=model or '',
        extra_mcp=external_mcp,
        include_internal=include_internal,
    )
    return workspace
