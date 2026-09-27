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


def test_create_session_workspace_writes_agents_md_guidance(tmp_path):
    """AGENTS.md (OpenCode project context) tells the agent to clone remote code
    into a subdir and not fight the root .git — and it's gitignored so it never
    shows up as a change."""
    import shutil as _sh, subprocess as _sp
    from utils.workspace import create_session_workspace
    p = Path(create_session_workspace(str(tmp_path), "u", "s"))
    agents = p / "AGENTS.md"
    assert agents.is_file()
    body = agents.read_text(encoding="utf-8")
    assert "子目录" in body and "git clone" in body
    assert "AGENTS.md" in (p / ".gitignore").read_text(encoding="utf-8")
    if _sh.which("git"):  # ignored -> workspace still clean
        out = _sp.run(["git", "-C", str(p), "status", "--porcelain"],
                      capture_output=True, text=True)
        assert "AGENTS.md" not in out.stdout


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


# ---------------------------------------------------------------------------
# Batch root unification: batch data moves to config.AI_WORKSPACE_ROOT while
# pre-unification data stays readable/cleanable from the legacy tree — no
# migration required.
# ---------------------------------------------------------------------------

def test_batch_workspace_root_honors_env_override(monkeypatch, tmp_path):
    from utils.workspace import batch_workspace_root
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path))
    assert batch_workspace_root() == str(tmp_path)


def test_batch_workspace_root_defaults_to_config_root(monkeypatch):
    from utils import workspace as ws_mod
    from config import AI_WORKSPACE_ROOT
    monkeypatch.delenv('AI_CHAT_WORKSPACE_ROOT', raising=False)
    assert ws_mod.batch_workspace_root() == AI_WORKSPACE_ROOT


def test_batch_roots_dedupes_same_path(monkeypatch):
    """When the unified root IS the legacy dir (old deployment layout), the
    deduped list has exactly one entry — sweeps must not run twice."""
    from utils import workspace as ws_mod
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT',
                       ws_mod.legacy_batch_workspace_root())
    roots = ws_mod.batch_roots()
    assert len(roots) == 1


def test_resolve_batch_data_path_falls_back_to_legacy(tmp_path, monkeypatch):
    """A staged file uploaded before unification (legacy tree) resolves even
    though it's absent from the unified root — old pending batches still run."""
    from utils import workspace as ws_mod
    unified, legacy = tmp_path / 'unified', tmp_path / 'legacy'
    staged = legacy / 'batch-staging' / 'u1' / 'upl-1' / 'input.txt'
    staged.parent.mkdir(parents=True)
    staged.write_text('X', encoding='utf-8')
    monkeypatch.setattr(ws_mod, 'legacy_batch_workspace_root', lambda: str(legacy))
    got = ws_mod.resolve_batch_data_path(
        'batch-staging/u1/upl-1/input.txt', roots=(str(unified), str(legacy)))
    assert Path(got) == staged


def test_resolve_batch_data_path_prefers_unified_root(tmp_path, monkeypatch):
    from utils.workspace import resolve_batch_data_path
    unified, legacy = tmp_path / 'unified', tmp_path / 'legacy'
    for root in (unified, legacy):
        p = root / 'batch-staging' / 'u1' / 'upl-1' / 'input.txt'
        p.parent.mkdir(parents=True)
        p.write_text(root.name, encoding='utf-8')
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(unified))
    got = resolve_batch_data_path('batch-staging/u1/upl-1/input.txt')
    assert Path(got).read_text(encoding='utf-8') == 'unified'


def test_resolve_batch_data_path_missing_raises(tmp_path, monkeypatch):
    from utils.workspace import resolve_batch_data_path
    monkeypatch.setenv('AI_CHAT_WORKSPACE_ROOT', str(tmp_path / 'unified'))
    with pytest.raises(FileNotFoundError):
        resolve_batch_data_path('batch-staging/u1/gone.txt')


def test_cleanup_batch_workspaces_sweeps_legacy_root(tmp_path, monkeypatch):
    """Deleting a batch tears down child workspaces in BOTH roots: sessions
    created before unification keep their dirs under the legacy tree."""
    from utils import workspace as ws_mod
    unified_root = str(tmp_path / 'unified')
    legacy_root = str(tmp_path / 'legacy')
    monkeypatch.setattr(ws_mod, 'legacy_batch_workspace_root', lambda: legacy_root)
    sessions = [{'id': 'old-sess'}, {'id': 'new-sess'}]
    for root, sid in ((legacy_root, 'old-sess'), (unified_root, 'new-sess')):
        ws = Path(root) / 'user-1' / sid
        ws.mkdir(parents=True)
        (ws / 'keep.txt').write_text('x', encoding='utf-8')
    ws_mod.cleanup_batch_workspaces(unified_root, 'user-1', sessions)
    assert not (Path(legacy_root) / 'user-1' / 'old-sess').exists()
    assert not (Path(unified_root) / 'user-1' / 'new-sess').exists()
