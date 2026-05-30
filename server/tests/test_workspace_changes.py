"""Tests for utils.workspace_changes.git_changes (real temp git repo)."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.email', 't@t')
    _git(path, 'config', 'user.name', 't')


def test_git_changes_detects_added_modified_deleted(tmp_path):
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    for name in ('mod.txt', 'del.txt'):
        with open(os.path.join(repo, name), 'w') as f:
            f.write('base')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'base')
    with open(os.path.join(repo, 'new.txt'), 'w') as f:
        f.write('hi')                                   # added (untracked)
    with open(os.path.join(repo, 'mod.txt'), 'w') as f:
        f.write('changed')                              # modified
    os.remove(os.path.join(repo, 'del.txt'))            # deleted

    changes, truncated = git_changes(ws)
    by = {c['path']: c['status'] for c in changes}
    assert by.get('repo/new.txt') == 'added'
    assert by.get('repo/mod.txt') == 'modified'
    assert by.get('repo/del.txt') == 'deleted'
    assert truncated is False


def test_git_changes_skips_uploads_outputs(tmp_path):
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    repo = os.path.join(ws, 'outputs', 'r')
    _init_repo(repo)
    with open(os.path.join(repo, 'x.txt'), 'w') as f:
        f.write('hi')
    changes, truncated = git_changes(ws)
    assert changes == []
    assert truncated is False


def test_git_changes_no_repo_returns_empty(tmp_path):
    from utils.workspace_changes import git_changes
    with open(os.path.join(str(tmp_path), 'loose.txt'), 'w') as f:
        f.write('not in a repo')
    assert git_changes(str(tmp_path)) == ([], False)


def test_workspace_root_as_repo_picks_up_loose_files(tmp_path):
    """When the workspace itself is git-initialized (the new session default),
    files written directly under workspace root should show up as added."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    _init_repo(ws)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    changes, _ = git_changes(ws)
    assert {'path': 'loose.txt', 'status': 'added'} in changes


def test_workspace_root_repo_plus_nested_clone_no_duplicates(tmp_path):
    """A workspace-level repo + a nested clone: the nested clone's own files
    are reported once (by the nested repo), not twice (once via the outer
    seeing the clone dir as untracked, once via the inner status)."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    _init_repo(ws)   # workspace is itself a repo
    nested = os.path.join(ws, 'cloned-repo')
    _init_repo(nested)
    with open(os.path.join(nested, 'file.py'), 'w') as f:
        f.write('print(1)')
    changes, _ = git_changes(ws)
    paths = [c['path'] for c in changes]
    # the nested repo's own file should be reported once with the nested path
    assert 'cloned-repo/file.py' in paths
    # the outer should NOT also report the nested dir itself as a separate
    # "cloned-repo/" untracked entry
    assert 'cloned-repo' not in paths
    assert 'cloned-repo/' not in paths
