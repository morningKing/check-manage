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


def test_git_changes_detects_added_modified_excludes_deleted(tmp_path):
    """The panel lists additions/modifications only — deletions are filtered out."""
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
    os.remove(os.path.join(repo, 'del.txt'))            # deleted -> excluded

    changes, truncated, ok = git_changes(ws)
    by = {c['path']: c['status'] for c in changes}
    assert by.get('repo/new.txt') == 'added'
    assert by.get('repo/mod.txt') == 'modified'
    assert 'repo/del.txt' not in by                     # deletion not listed
    assert not any(c['status'] == 'deleted' for c in changes)
    assert truncated is False
    assert ok is True


def test_git_changes_handles_non_ascii_filenames(tmp_path):
    """git -z emits UTF-8 path bytes; decoding them with text=True under a cp936
    locale (Chinese Windows) crashed (stdout=None -> .split). We decode UTF-8
    ourselves, so Chinese filenames work and don't blow up the refresh."""
    from utils.workspace_changes import git_changes, file_diff
    ws = str(tmp_path)
    _init_repo(ws)
    cn_new = '新增文件.py'
    with open(os.path.join(ws, cn_new), 'w', encoding='utf-8') as f:
        f.write('print(1)\n')
    cn_mod = '已跟踪.py'
    with open(os.path.join(ws, cn_mod), 'w', encoding='utf-8') as f:
        f.write('a\n')
    _git(ws, 'add', cn_mod)
    _git(ws, 'commit', '-q', '-m', 'base')
    with open(os.path.join(ws, cn_mod), 'w', encoding='utf-8') as f:
        f.write('b\n')                                   # modified
    changes, _, ok = git_changes(ws)
    by = {c['path']: c['status'] for c in changes}
    assert ok is True
    assert by.get(cn_new) == 'added'
    assert by.get(cn_mod) == 'modified'
    # diff on a Chinese-named file must also not crash
    assert file_diff(ws, cn_mod)['status'] == 'modified'


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


def test_list_untracked_files(tmp_path):
    from utils.workspace_changes import _list_untracked_files
    ws = str(tmp_path)
    _init_repo(ws)
    os.makedirs(os.path.join(ws, 'd', 'e'))
    for p in ('d/x.txt', 'd/e/y.txt'):
        with open(os.path.join(ws, p), 'w') as f:
            f.write('x')
    assert set(_list_untracked_files(ws, 'd/')) == {'d/x.txt', 'd/e/y.txt'}


def test_large_untracked_directory_stays_collapsed(tmp_path):
    """Pulled-in code (no .git): a big brand-new dir stays ONE collapsed entry
    (kind='dir' + count), not N individual 'added' files that drown real edits."""
    from utils.workspace_changes import git_changes, UNTRACKED_DIR_EXPAND_LIMIT
    ws = str(tmp_path)
    _init_repo(ws)
    pulled = os.path.join(ws, 'pulled')
    os.makedirs(pulled)
    n = UNTRACKED_DIR_EXPAND_LIMIT + 5
    for i in range(n):
        with open(os.path.join(pulled, f'f{i:03d}.py'), 'w') as f:
            f.write('x')
    changes, _, _ = git_changes(ws)
    dir_entries = [c for c in changes if c.get('kind') == 'dir']
    assert len(dir_entries) == 1
    assert dir_entries[0] == {'path': 'pulled/', 'status': 'added', 'kind': 'dir', 'count': n}
    # none of the individual files leaked into the list
    assert not any(c['path'].startswith('pulled/f') for c in changes)


def test_expand_untracked_dir_lists_all_files(tmp_path):
    """A folded `dir/` entry can be expanded on demand into every file under it
    (recursively, workspace-relative, path-sorted)."""
    from utils.workspace_changes import (
        git_changes, expand_untracked_dir, UNTRACKED_DIR_EXPAND_LIMIT)
    ws = str(tmp_path)
    _init_repo(ws)
    pulled = os.path.join(ws, 'pulled')
    os.makedirs(os.path.join(pulled, 'sub'))
    n = UNTRACKED_DIR_EXPAND_LIMIT + 3
    for i in range(n):
        with open(os.path.join(pulled, f'f{i:03d}.py'), 'w') as f:
            f.write('x')
    with open(os.path.join(pulled, 'sub', 'deep.py'), 'w') as f:
        f.write('y')
    # git_changes folds it
    changes, _, _ = git_changes(ws)
    assert any(c.get('kind') == 'dir' and c['path'] == 'pulled/' for c in changes)
    # expand lists every file under it
    files = expand_untracked_dir(ws, 'pulled/')
    paths = [f['path'] for f in files]
    assert 'pulled/f000.py' in paths
    assert 'pulled/sub/deep.py' in paths
    assert len(files) == n + 1
    assert all(f['status'] == 'added' for f in files)
    assert paths == sorted(paths)


def test_expand_untracked_dir_no_repo_returns_empty(tmp_path):
    from utils.workspace_changes import expand_untracked_dir
    # path not under any git repo -> empty (no crash)
    assert expand_untracked_dir(str(tmp_path), 'nope/') == []


def test_small_untracked_directory_expands(tmp_path):
    """A small brand-new dir still expands into its files (no collapse)."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    _init_repo(ws)
    d = os.path.join(ws, 'newdir', 'sub')
    os.makedirs(d)
    for name in ('a.py', 'b.py'):
        with open(os.path.join(d, name), 'w') as f:
            f.write('x')
    changes, _, _ = git_changes(ws)
    paths = {c['path'] for c in changes}
    assert 'newdir/sub/a.py' in paths
    assert 'newdir/sub/b.py' in paths
    assert not any(c.get('kind') == 'dir' for c in changes)


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


def test_unborn_nested_repo_top_level_flood_collapses(tmp_path):
    """The reported bug: a pulled/scaffolded repo whose files sit at the repo's
    TOP level (unborn HEAD — `git init` with no commit, or a clone whose files
    aren't committed) makes git list every file individually as `?? f.py` (git
    only folds untracked *sub*directories, never repo-root-level files). Those
    must collapse into ONE expandable `dir/` entry, not flood the panel as N
    'added' files."""
    from utils.workspace_changes import (
        git_changes, expand_untracked_dir, UNTRACKED_DIR_EXPAND_LIMIT)
    ws = str(tmp_path)
    _init_repo(ws)                       # workspace itself a repo (committed nothing yet is fine)
    nested = os.path.join(ws, 'cloned-repo')
    _init_repo(nested)                   # nested repo, UNBORN HEAD (no commit)
    n = UNTRACKED_DIR_EXPAND_LIMIT + 7
    for i in range(n):
        with open(os.path.join(nested, f'f{i:03d}.py'), 'w') as f:
            f.write('x')
    changes, _, _ = git_changes(ws)
    dir_entries = [c for c in changes if c.get('kind') == 'dir']
    assert dir_entries == [
        {'path': 'cloned-repo/', 'status': 'added', 'kind': 'dir', 'count': n}]
    # not one of the N individual files leaked through
    assert not any(c['path'].startswith('cloned-repo/f') for c in changes)
    # and the collapsed entry expands to every file
    files = expand_untracked_dir(ws, 'cloned-repo/')
    assert len(files) == n
    assert all(f['status'] == 'added' for f in files)


def test_nested_repo_modifications_survive_added_flood_collapse(tmp_path):
    """When a nested repo has a few real edits AND a flood of brand-new top-level
    files, the edits stay individually visible (the user's actual work) while only
    the new-file flood collapses."""
    from utils.workspace_changes import git_changes, UNTRACKED_DIR_EXPAND_LIMIT
    ws = str(tmp_path)
    _init_repo(ws)
    nested = os.path.join(ws, 'repo')
    _init_repo(nested)
    with open(os.path.join(nested, 'README'), 'w') as f:
        f.write('base\n')
    _git(nested, 'add', '-A')
    _git(nested, 'commit', '-q', '-m', 'base')
    with open(os.path.join(nested, 'README'), 'w') as f:
        f.write('edited\n')                          # one real modification
    n = UNTRACKED_DIR_EXPAND_LIMIT + 4
    for i in range(n):                               # flood of new top-level files
        with open(os.path.join(nested, f'gen{i:03d}.py'), 'w') as f:
            f.write('x')
    changes, _, _ = git_changes(ws)
    # the modification is shown individually
    assert {'path': 'repo/README', 'status': 'modified'} in changes
    # the new-file flood is collapsed to one dir entry
    dir_entries = [c for c in changes if c.get('kind') == 'dir']
    assert dir_entries == [
        {'path': 'repo/', 'status': 'added', 'kind': 'dir', 'count': n}]
    assert not any(c['path'].startswith('repo/gen') for c in changes)


def test_small_top_level_added_files_stay_individual(tmp_path):
    """A nested repo with only a FEW new top-level files keeps listing them
    individually (no collapse below the limit) — preserves existing behavior."""
    from utils.workspace_changes import git_changes
    ws = str(tmp_path)
    _init_repo(ws)
    nested = os.path.join(ws, 'repo')
    _init_repo(nested)
    for name in ('a.py', 'b.py', 'c.py'):
        with open(os.path.join(nested, name), 'w') as f:
            f.write('x')
    changes, _, _ = git_changes(ws)
    paths = {c['path'] for c in changes}
    assert {'repo/a.py', 'repo/b.py', 'repo/c.py'} <= paths
    assert not any(c.get('kind') == 'dir' for c in changes)


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


def test_read_file_preview_text(tmp_path):
    from utils.workspace_changes import read_file_preview
    p = tmp_path / 'a.txt'
    p.write_bytes(b'hello\nworld\n')  # bytes to avoid platform newline translation
    r = read_file_preview(str(p))
    assert r == {'content': 'hello\nworld\n', 'truncated': False, 'binary': False}


def test_read_file_preview_binary(tmp_path):
    from utils.workspace_changes import read_file_preview
    p = tmp_path / 'b.bin'
    p.write_bytes(b'\x89PNG\x00\x01\x02')
    r = read_file_preview(str(p))
    assert r['binary'] is True
    assert r['content'] == ''


def test_read_file_preview_truncates_large(tmp_path):
    from utils.workspace_changes import read_file_preview, MAX_DIFF_LINES
    p = tmp_path / 'big.txt'
    p.write_text('\n'.join(f'line{i}' for i in range(MAX_DIFF_LINES + 100)) + '\n', encoding='utf-8')
    r = read_file_preview(str(p))
    assert r['truncated'] is True
    assert r['content'].count('\n') <= MAX_DIFF_LINES


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


def test_git_changes_omits_deletions_end_to_end(tmp_path):
    """End-to-end through a real repo: a deleted tracked file is not listed."""
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
        f.write('changed')                       # modified
    os.remove(os.path.join(repo, 'zdel.txt'))    # deleted -> excluded
    with open(os.path.join(repo, 'bnew.txt'), 'w') as f:
        f.write('hi')                            # added
    changes, _, _ = git_changes(ws)
    paths = {c['path'] for c in changes}
    assert paths == {'repo/amod.txt', 'repo/bnew.txt'}
    assert not any(c['status'] == 'deleted' for c in changes)
