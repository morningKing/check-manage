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


def test_create_session_workspace_inits_git_and_writes_gitignore(tmp_path):
    """New sessions get a workspace-level git repo + a .gitignore for the noise
    dirs so the 变更文件 panel sees agent-written files without needing the
    skill to git-clone something first."""
    from utils.workspace import create_session_workspace
    p = Path(create_session_workspace(str(tmp_path), "u", "s"))
    assert (p / ".gitignore").is_file()
    gitignore = (p / ".gitignore").read_text(encoding="utf-8")
    for noisy in ("uploads/", "outputs/", ".opencode/", "node_modules/"):
        assert noisy in gitignore
    # git init is best-effort — only assert the .git dir exists if git is on PATH
    import shutil as _sh
    if _sh.which("git"):
        assert (p / ".git").is_dir()


def test_create_session_workspace_commits_gitignore_so_no_noise(tmp_path):
    """The auto-generated .gitignore is base-committed so it doesn't show up
    in 变更文件 every session, and opencode.json is ignored so it doesn't
    either. git status on a fresh workspace must come back empty."""
    import shutil as _sh, subprocess as _sp
    if not _sh.which("git"):
        return
    from utils.workspace import create_session_workspace
    p = Path(create_session_workspace(str(tmp_path), "u", "s"))
    out = _sp.run(["git", "-C", str(p), "status", "--porcelain"],
                  capture_output=True, text=True)
    assert out.stdout == "", f"unexpected: {out.stdout!r}"
    # opencode.json appearing later (real flow) must also stay hidden
    (p / "opencode.json").write_text("{}", encoding="utf-8")
    out = _sp.run(["git", "-C", str(p), "status", "--porcelain"],
                  capture_output=True, text=True)
    assert "opencode.json" not in out.stdout


def test_create_session_workspace_skips_git_init_when_already_initialized(tmp_path):
    """Idempotency: a second call doesn't reinit or overwrite the .gitignore."""
    from utils.workspace import create_session_workspace
    p = Path(create_session_workspace(str(tmp_path), "u", "s"))
    (p / ".gitignore").write_text("custom\n", encoding="utf-8")
    create_session_workspace(str(tmp_path), "u", "s")
    assert (p / ".gitignore").read_text(encoding="utf-8") == "custom\n"


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
