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

    changes, truncated, ok = git_changes(ws)
    by = {c['path']: c['status'] for c in changes}
    assert by.get('repo/new.txt') == 'added'
    assert by.get('repo/mod.txt') == 'modified'
    assert by.get('repo/del.txt') == 'deleted'
    assert truncated is False
    assert ok is True


def test_git_changes_skips_uploads_outputs(tmp_path):
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    repo = os.path.join(ws, 'outputs', 'r')
    _init_repo(repo)
    with open(os.path.join(repo, 'x.txt'), 'w') as f:
        f.write('hi')
    changes, truncated, ok = git_changes(ws)
    assert changes == []
    assert truncated is False
    assert ok is True


def test_git_changes_no_repo_returns_empty(tmp_path):
    from utils.workspace_changes import git_changes
    with open(os.path.join(str(tmp_path), 'loose.txt'), 'w') as f:
        f.write('not in a repo')
    # No repo is a legitimate "no changes" state — the scan did not fail.
    assert git_changes(str(tmp_path)) == ([], False, True)


def test_git_changes_reports_scan_failure(tmp_path):
    """A repo whose `git status` fails (broken .git) must surface ok=False, NOT
    a silent empty list — otherwise a transient git error wipes the panel."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    broken = os.path.join(ws, 'brokenrepo')
    os.makedirs(broken)
    # a .git FILE with garbage makes `git -C brokenrepo status` fail (rc != 0)
    with open(os.path.join(broken, '.git'), 'w') as f:
        f.write('this is not a valid gitdir pointer')
    changes, truncated, ok = git_changes(ws)
    assert ok is False


def test_workspace_root_as_repo_picks_up_loose_files(tmp_path):
    """When the workspace itself is git-initialized (the new session default),
    files written directly under workspace root should show up as added."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    _init_repo(ws)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    changes, _, _ = git_changes(ws)
    assert {'path': 'loose.txt', 'status': 'added'} in changes


def test_git_changes_expands_untracked_directory(tmp_path):
    """A brand-new untracked directory tree must list each file, not collapse to
    a single directory entry. `git status --porcelain` folds untracked dirs
    (`?? src/`); we pass -uall so every file inside is reported and clickable."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    _init_repo(ws)
    deep = os.path.join(ws, 'src', 'components', 'deep')
    os.makedirs(deep)
    for name in ('a.txt', 'b.txt'):
        with open(os.path.join(deep, name), 'w') as f:
            f.write(name)
    changes, _, _ = git_changes(ws)
    paths = {c['path'] for c in changes}
    assert 'src/components/deep/a.txt' in paths
    assert 'src/components/deep/b.txt' in paths
    # must NOT collapse to a bare directory entry
    assert 'src/' not in paths
    assert 'src/components/deep/' not in paths
    assert all(c['status'] == 'added' for c in changes)


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
    changes, _, _ = git_changes(ws)
    paths = [c['path'] for c in changes]
    # the nested repo's own file should be reported once with the nested path
    assert 'cloned-repo/file.py' in paths
    # the outer should NOT also report the nested dir itself as a separate
    # "cloned-repo/" untracked entry
    assert 'cloned-repo' not in paths
    assert 'cloned-repo/' not in paths


def test_resolve_repo_for_path_nested_clone(tmp_path):
    from utils.workspace_changes import resolve_repo_for_path
    ws = str(tmp_path)
    _init_repo(ws)                       # workspace itself a repo
    nested = os.path.join(ws, 'cloned-repo')
    _init_repo(nested)
    with open(os.path.join(nested, 'file.py'), 'w') as f:
        f.write('print(1)')
    repo, repo_rel = resolve_repo_for_path(ws, 'cloned-repo/file.py')
    assert os.path.realpath(repo) == os.path.realpath(nested)
    assert repo_rel == 'file.py'


def test_resolve_repo_for_path_workspace_root(tmp_path):
    from utils.workspace_changes import resolve_repo_for_path
    ws = str(tmp_path)
    _init_repo(ws)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    repo, repo_rel = resolve_repo_for_path(ws, 'loose.txt')
    assert os.path.realpath(repo) == os.path.realpath(ws)
    assert repo_rel == 'loose.txt'


def test_resolve_repo_for_path_no_repo_returns_none(tmp_path):
    from utils.workspace_changes import resolve_repo_for_path
    ws = str(tmp_path)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    assert resolve_repo_for_path(ws, 'loose.txt') == (None, None)


def test_file_diff_modified_returns_hunks(tmp_path):
    from utils.workspace_changes import file_diff
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    with open(os.path.join(repo, 'a.txt'), 'w') as f:
        f.write('line1\nline2\nline3\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'base')
    with open(os.path.join(repo, 'a.txt'), 'w') as f:
        f.write('line1\nCHANGED\nline3\n')
    res = file_diff(ws, 'repo/a.txt')
    assert res['status'] == 'modified'
    assert '@@' in res['diff']
    assert '-line2' in res['diff']
    assert '+CHANGED' in res['diff']
    assert res['truncated'] is False


def test_file_diff_added_returns_content(tmp_path):
    from utils.workspace_changes import file_diff
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    with open(os.path.join(repo, 'new.txt'), 'w') as f:
        f.write('fresh content\n')
    res = file_diff(ws, 'repo/new.txt')
    assert res['status'] == 'added'
    assert res['content'] == 'fresh content\n'
    assert res['truncated'] is False


def test_file_diff_added_truncates_large_file(tmp_path):
    from utils.workspace_changes import file_diff, MAX_DIFF_LINES
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    big = '\n'.join(f'line{i}' for i in range(MAX_DIFF_LINES + 50)) + '\n'
    with open(os.path.join(repo, 'big.txt'), 'w') as f:
        f.write(big)
    res = file_diff(ws, 'repo/big.txt')
    assert res['status'] == 'added'
    assert res['truncated'] is True
    assert res['content'].count('\n') <= MAX_DIFF_LINES


def test_file_diff_no_repo_returns_none_status(tmp_path):
    from utils.workspace_changes import file_diff
    ws = str(tmp_path)
    with open(os.path.join(ws, 'loose.txt'), 'w') as f:
        f.write('hi')
    res = file_diff(ws, 'loose.txt')
    assert res['status'] is None


# --- truncation priority: added/modified first, deletions only with leftover room ---

def _mk(n, status, prefix):
    return [{'path': f'{prefix}{i:04d}.txt', 'status': status} for i in range(n)]


def test_prioritize_groups_deleted_after_non_deleted():
    from utils.workspace_changes import _prioritize_and_cap
    changes = _mk(2, 'deleted', 'd') + _mk(2, 'added', 'a') + _mk(1, 'modified', 'm')
    capped, truncated = _prioritize_and_cap(list(changes))
    assert truncated is False
    statuses = [c['status'] for c in capped]
    first_deleted = statuses.index('deleted')
    assert all(s != 'deleted' for s in statuses[:first_deleted])      # non-deleted first
    assert all(s == 'deleted' for s in statuses[first_deleted:])      # deletions last


def test_truncation_drops_all_deletions_when_non_deleted_fills_cap():
    from utils.workspace_changes import _prioritize_and_cap, MAX_CHANGES
    changes = _mk(MAX_CHANGES, 'added', 'a') + _mk(10, 'deleted', 'd')
    capped, truncated = _prioritize_and_cap(changes)
    assert truncated is True
    assert len(capped) == MAX_CHANGES
    assert all(c['status'] == 'added' for c in capped)               # no deletion survives


def test_truncation_keeps_leftover_deletions_path_sorted():
    from utils.workspace_changes import _prioritize_and_cap, MAX_CHANGES
    changes = _mk(MAX_CHANGES - 5, 'added', 'a') + _mk(20, 'deleted', 'd')
    capped, truncated = _prioritize_and_cap(changes)
    assert truncated is True
    assert len(capped) == MAX_CHANGES
    kept_deleted = sorted(c['path'] for c in capped if c['status'] == 'deleted')
    assert kept_deleted == [f'd{i:04d}.txt' for i in range(5)]       # only 5 slots, lowest paths


def test_prioritize_sorts_by_path_within_group():
    from utils.workspace_changes import _prioritize_and_cap
    changes = [{'path': 'b.txt', 'status': 'added'}, {'path': 'a.txt', 'status': 'added'}]
    capped, _ = _prioritize_and_cap(changes)
    assert [c['path'] for c in capped] == ['a.txt', 'b.txt']


def test_is_secondary_classifies_dependency_and_build_files():
    from utils.workspace_changes import _is_secondary
    # secondary
    assert _is_secondary('package-lock.json')
    assert _is_secondary('frontend/yarn.lock')
    assert _is_secondary('node_modules/react/index.js')
    assert _is_secondary('dist/app.js')
    assert _is_secondary('build/out.bin')
    assert _is_secondary('server/__pycache__/x.pyc')
    assert _is_secondary('a/b.min.js')
    assert _is_secondary('a/b.css.map')
    # source / key files
    assert not _is_secondary('src/main.py')
    assert not _is_secondary('src/components/App.vue')
    assert not _is_secondary('README.md')
    assert not _is_secondary('go.mod')                  # go.mod is source, go.sum is not


def test_source_files_sort_before_secondary():
    from utils.workspace_changes import _prioritize_and_cap
    changes = [
        {'path': 'dist/bundle.js', 'status': 'added'},
        {'path': 'src/app.py', 'status': 'modified'},
        {'path': 'package-lock.json', 'status': 'modified'},
        {'path': 'src/util.py', 'status': 'added'},
    ]
    capped, _ = _prioritize_and_cap(changes)
    # source (added/modified) first, secondary after — all before any deletion
    assert [c['path'] for c in capped] == [
        'src/app.py', 'src/util.py', 'dist/bundle.js', 'package-lock.json',
    ]


def test_truncation_keeps_modified_source_over_flood_of_secondary_added():
    """The reported bug: a flood of generated/added files (e.g. expanded
    node_modules) pushed the user's modified source out of the 500-cap. Source
    modifications must survive."""
    from utils.workspace_changes import _prioritize_and_cap, MAX_CHANGES
    flood = [{'path': f'node_modules/pkg/f{i:05d}.js', 'status': 'added'}
             for i in range(MAX_CHANGES + 200)]
    src = [{'path': 'src/zzz_last.py', 'status': 'modified'}]   # path sorts late on purpose
    capped, truncated = _prioritize_and_cap(flood + src)
    assert truncated is True
    assert len(capped) == MAX_CHANGES
    assert {'path': 'src/zzz_last.py', 'status': 'modified'} in capped  # survived
    assert capped[0]['path'] == 'src/zzz_last.py'                       # in fact, first


def test_git_changes_orders_deleted_last(tmp_path):
    """End-to-end through a real repo: deletions sort after added/modified."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    repo = os.path.join(ws, 'repo')
    _init_repo(repo)
    for name in ('amod.txt', 'zdel.txt'):
        with open(os.path.join(repo, name), 'w') as f:
            f.write('base')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'base')
    with open(os.path.join(repo, 'amod.txt'), 'w') as f:
        f.write('changed')                       # modified (path sorts FIRST)
    os.remove(os.path.join(repo, 'zdel.txt'))    # deleted (path sorts LAST anyway)
    with open(os.path.join(repo, 'bnew.txt'), 'w') as f:
        f.write('hi')                            # added
    changes, _, _ = git_changes(ws)
    statuses = [c['status'] for c in changes]
    first_deleted = statuses.index('deleted')
    assert all(s != 'deleted' for s in statuses[:first_deleted])
