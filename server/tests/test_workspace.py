"""Tests for server/utils/workspace.py path-traversal defense and mkdir."""

import os
import pytest
from pathlib import Path


def test_create_session_workspace_makes_uploads_outputs(tmp_path):
    from utils.workspace import create_session_workspace
    p = create_session_workspace(str(tmp_path), "user-1", "sess-1")
    assert (Path(p) / "uploads").is_dir()
    assert (Path(p) / "outputs").is_dir()
    assert Path(p).name == "sess-1"


def test_safe_resolve_rejects_traversal(tmp_path):
    from utils.workspace import safe_resolve, WorkspacePathError
    root = str(tmp_path)
    with pytest.raises(WorkspacePathError):
        safe_resolve(root, "../../etc/passwd")


def test_safe_resolve_rejects_absolute(tmp_path):
    from utils.workspace import safe_resolve, WorkspacePathError
    with pytest.raises(WorkspacePathError):
        safe_resolve(str(tmp_path), "/etc/passwd")


def test_safe_resolve_accepts_inside(tmp_path):
    from utils.workspace import safe_resolve
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "x.txt").write_text("hi")
    p = safe_resolve(str(tmp_path), "uploads/x.txt")
    assert Path(p).read_text() == "hi"


def test_cleanup_removes_session_dir(tmp_path):
    from utils.workspace import create_session_workspace, cleanup_session_workspace
    p = create_session_workspace(str(tmp_path), "u", "s")
    assert Path(p).exists()
    cleanup_session_workspace(str(tmp_path), "u", "s")
    assert not Path(p).exists()


def test_write_opencode_config_writes_mcp_with_token(tmp_path):
    import json
    from utils.workspace import write_opencode_config
    ws = create_ws(tmp_path)
    write_opencode_config(ws, mcp_name="check-manage",
                          mcp_url="http://127.0.0.1:3003/mcp?token=tok123")
    cfg = json.loads((Path(ws) / "opencode.json").read_text(encoding="utf-8"))
    entry = cfg["mcp"]["check-manage"]
    assert entry["type"] == "remote"
    assert entry["url"].endswith("?token=tok123")
    assert entry["enabled"] is True


def test_write_opencode_config_includes_model_when_given(tmp_path):
    import json
    from utils.workspace import write_opencode_config
    ws = create_ws(tmp_path)
    write_opencode_config(ws, mcp_name="check-manage",
                          mcp_url="http://x/mcp?token=t",
                          model="opencode/deepseek-v4-flash-free")
    cfg = json.loads((Path(ws) / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["model"] == "opencode/deepseek-v4-flash-free"


def create_ws(tmp_path):
    from utils.workspace import create_session_workspace
    return create_session_workspace(str(tmp_path), "u", "s")
