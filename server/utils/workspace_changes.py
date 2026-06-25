"""Compute the changed files in a session workspace via `git status`.

OpenCode's native /session/{id}/diff returns nothing for the clone+edit flow,
so we read git status of the workspace's git repos directly.

Mostly read-only, with ONE deliberate side effect: a pulled 代码仓 (a nested git
repo) that has no baseline commit yet — an unborn HEAD, e.g. a clone whose .git
history was stripped, or source pulled without history — gets a one-time
"baseline" commit so its content is treated as the INITIAL STATE rather than a
pile of "新增". After that, only the user's real subsequent edits/additions show
(identical to a real `git clone` with history). The workspace ROOT repo is never
auto-committed — its untracked files are the agent's real outputs we want shown.
"""
import os
import subprocess

MAX_CHANGES = 500
MAX_DIFF_LINES = 2000      # cap added-file content & diff text by lines
MAX_DIFF_BYTES = 256 * 1024
# A brand-new untracked directory with <= this many files is expanded into its
# individual files; a larger one (e.g. code pulled in without a .git, where every
# file shows as "added") stays collapsed as a single `dir/` entry so it doesn't
# drown the user's real changes.
UNTRACKED_DIR_EXPAND_LIMIT = 10
_SKIP_DIRS = {'uploads', 'outputs', 'node_modules', '.venv', '__pycache__'}


def _find_git_repos(workspace_path, max_depth=3):
    """Return dirs that are git repos under workspace_path (bounded depth,
    skipping noise dirs). The workspace root itself counts — we still descend
    INTO it to discover nested clones (the skill-clone workflow); we do not
    descend into any other repo we find."""
    repos = []
    base_depth = workspace_path.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _files in os.walk(workspace_path):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if depth >= max_depth:
            dirnames[:] = []
        if '.git' in os.listdir(dirpath):
            repos.append(dirpath)
            # Don't descend into nested repos, but DO descend into the workspace
            # root so we still find the skill-clone scenarios inside it.
            if os.path.realpath(dirpath) != os.path.realpath(workspace_path):
                dirnames[:] = []
    return repos


def resolve_repo_for_path(workspace_path, rel_path):
    """Map a workspace-relative path to (repo_dir, repo_rel_path).

    Picks the deepest git repo (longest path) that contains the file, so a
    nested clone wins over the workspace-root repo. Returns (None, None) when
    no repo contains the path."""
    abs_target = os.path.realpath(os.path.join(workspace_path, rel_path))
    best = None
    for repo in _find_git_repos(workspace_path):
        repo_real = os.path.realpath(repo)
        prefix = repo_real + os.sep
        if abs_target == repo_real or abs_target.startswith(prefix):
            if best is None or len(repo_real) > len(os.path.realpath(best)):
                best = repo
    if best is None:
        return None, None
    repo_rel = os.path.relpath(abs_target, os.path.realpath(best)).replace(os.sep, '/')
    return best, repo_rel


def _map_status(xy):
    """Map a 2-char porcelain code to added|modified|deleted."""
    if xy == '??':
        return 'added'
    if 'D' in xy:
        return 'deleted'
    if 'A' in xy:
        return 'added'
    return 'modified'  # M / R / C / etc.


# "Secondary" files: dependency lockfiles, build/dependency/cache dirs, and
# generated artifacts. These are rarely what the user is reviewing, so they sort
# AFTER source files — when the 500-cap truncates, secondary churn (e.g. a whole
# expanded node_modules/ or dist/) can't crowd out the source files the user
# actually changed.
_SECONDARY_DIR_SEGMENTS = {
    'node_modules', 'dist', 'build', '.venv', 'venv', '__pycache__',
    '.next', '.nuxt', 'target', 'vendor', '.cache', 'coverage', '.git',
}
_SECONDARY_BASENAMES = {
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock',
    'cargo.lock', 'composer.lock', 'gemfile.lock', 'go.sum',
}
_SECONDARY_SUFFIXES = ('.min.js', '.min.css', '.map', '.pyc', '.lock', '.log')


def _is_secondary(path):
    """True for dependency/build/generated files that should sort after source."""
    p = path.replace('\\', '/').lower()
    segments = p.split('/')
    if segments[-1] in _SECONDARY_BASENAMES:
        return True
    if any(seg in _SECONDARY_DIR_SEGMENTS for seg in segments[:-1]):
        return True
    return p.endswith(_SECONDARY_SUFFIXES)


def _prioritize_and_cap(changes):
    """Order changes for the 变更文件 panel (source files before secondary ones,
    each group by path), then cap to MAX_CHANGES. On overflow the secondary tier
    is dropped first, so generated/dependency churn (e.g. a whole node_modules/
    or dist/) can't crowd out the source files the user actually changed.
    (Deletions are filtered out upstream — the panel lists only added/modified.)
    Returns (capped_list, truncated)."""
    changes.sort(key=lambda c: (1 if _is_secondary(c['path']) else 0, c['path']))
    truncated = len(changes) > MAX_CHANGES
    return changes[:MAX_CHANGES], truncated


def _collapse_added_floods(changes):
    """Collapse a flood of brand-new 'added' files that share an immediate parent
    directory into one expandable `dir/` entry.

    git only folds untracked *sub*directories; files at a repo's TOP level (an
    unborn-HEAD clone, a freshly `git init`'d project, or a pull whose files
    aren't committed) are listed individually as `?? f.py`. Without this, a whole
    pulled code repo floods the 变更文件 panel as N 'added' files — the reported
    "拉取下来的代码仓所有文件都是新增状态" bug. We catch it the same way folded
    subdirectories are handled (collapse to `dir/` + count, expandable on click).

    Modified/deleted entries and already-collapsed `kind='dir'` entries pass
    through untouched, so the user's real edits stay visible. Files sitting
    directly at the workspace root (no parent directory) are left individual —
    they're usually a handful of deliverables, not a pulled repo."""
    groups = {}
    passthrough = []
    for c in changes:
        if c['status'] == 'added' and c.get('kind') != 'dir':
            path = c['path']
            parent = path.rsplit('/', 1)[0] if '/' in path else ''
            groups.setdefault(parent, []).append(c)
        else:
            passthrough.append(c)
    out = list(passthrough)
    for parent, items in groups.items():
        if parent and len(items) > UNTRACKED_DIR_EXPAND_LIMIT:
            out.append({'path': parent + '/', 'status': 'added',
                        'kind': 'dir', 'count': len(items)})
        else:
            out.extend(items)
    return out


def _run_git(args, timeout=20):
    """Run git and return (returncode, stdout_text), decoding stdout as UTF-8
    (git's encoding for -z paths).

    We deliberately do NOT pass text=True: on Windows that decodes with the
    cp936/GBK locale, which raises UnicodeDecodeError on UTF-8 path bytes (e.g.
    Chinese filenames) *inside subprocess's reader thread*. That error doesn't
    propagate, so subprocess.run returns rc=0 with stdout=None — the cause of
    "'NoneType' object has no attribute 'split'" when refreshing 变更文件."""
    out = subprocess.run(args, capture_output=True, timeout=timeout)
    text = out.stdout.decode('utf-8', 'replace') if out.stdout else ''
    return out.returncode, text


def _list_untracked_files(repo, dirpath):
    """All untracked files under `dirpath` (repo-relative POSIX paths), expanded
    via -uall. Used to count/expand a folded `?? dir/` entry."""
    try:
        rc, stdout = _run_git(
            ['git', '-C', repo, 'status', '--porcelain', '-uall', '-z', '--', dirpath])
    except Exception:
        return []
    if rc != 0:
        return []
    return [e[3:] for e in stdout.split('\0') if e and e[:2] == '??']


def _ensure_pulled_repo_baseline(repos, workspace_path):
    """Give every pulled 代码仓 (nested git repo) a baseline so its content is the
    INITIAL STATE, not a flood of "新增".

    A real `git clone` keeps history, so its HEAD already IS the baseline and we
    leave it alone. But a repo pulled without history (clone with .git stripped +
    re-`git init`, source extracted then init'd, …) has an *unborn* HEAD — git
    can't tell baseline from new, so every file shows as untracked/added. For
    those we make a one-time baseline commit (isolated identity, doesn't touch
    the agent's global git config), after which only real subsequent changes
    appear — exactly like a clone with history.

    The workspace ROOT repo is skipped: it always has its committed `.gitignore`
    (so it's never unborn anyway), and its untracked files are the agent's real
    outputs we DO want to surface."""
    ws_real = os.path.realpath(workspace_path)
    for repo in repos:
        if os.path.realpath(repo) == ws_real:
            continue  # never auto-commit the workspace root tracking repo
        try:
            rc, _ = _run_git(['git', '-C', repo, 'rev-parse', '--verify', 'HEAD'])
            if rc == 0:
                continue  # already has a baseline (e.g. a clone with history)
            rc, out = _run_git(['git', '-C', repo, 'status', '--porcelain', '-z'])
            if rc != 0 or not out.replace('\0', '').strip():
                continue  # nothing to baseline
            _run_git(['git', '-C', repo, 'add', '-A'])
            _run_git(['git', '-C', repo,
                      '-c', 'user.name=check-manage',
                      '-c', 'user.email=check-manage@local',
                      'commit', '-q', '-m', 'baseline (auto): pulled repo initial state'])
        except Exception:
            continue  # best-effort: a failed baseline just leaves it as-is


def git_changes(workspace_path):
    """Return (changes, truncated, ok).

    changes is [{'path': <rel-to-workspace POSIX>, 'status': ...}]. `ok` is
    False when any repo's `git status` failed (exception or non-zero exit), so
    callers can tell a real "no changes" from a failed scan and avoid wiping a
    previously-populated list. No git repo at all is a legitimate "no changes"
    (ok stays True)."""
    changes = []
    ok = True
    repos = _find_git_repos(workspace_path)
    # Treat pulled 代码仓 without history as the initial state (one-time baseline)
    # before scanning, so they don't show up as a flood of "新增".
    _ensure_pulled_repo_baseline(repos, workspace_path)
    # If the workspace root is also a repo (new sessions: we git init it on
    # creation) and nested clones exist below it, the outer's `git status`
    # will see each nested clone as a single untracked dir entry. Suppress
    # those — the nested repo's own changes are already reported separately.
    nested_repo_paths = set()
    for repo in repos:
        rel = os.path.relpath(repo, workspace_path).replace(os.sep, '/').rstrip('/')
        if rel and rel != '.':
            nested_repo_paths.add(rel)
            nested_repo_paths.add(rel + '/')
    for repo in repos:
        try:
            # Default porcelain FOLDS a brand-new untracked directory to a single
            # `dir/` entry (no -uall). We keep that folding, then decide per
            # directory below: small dirs expand into their files; large ones stay
            # collapsed. Tracked-file modifications/deletions are always listed
            # individually by git regardless.
            rc, stdout = _run_git(['git', '-C', repo, 'status', '--porcelain', '-z'])
        except Exception:
            ok = False
            continue
        if rc != 0:
            ok = False
            continue
        entries = stdout.split('\0')
        i = 0
        while i < len(entries):
            e = entries[i]
            if not e:
                i += 1
                continue
            xy, path = e[:2], e[3:]
            if 'R' in xy or 'C' in xy:
                i += 1  # rename/copy: the next NUL field is the original path
            rel = os.path.relpath(os.path.join(repo, path), workspace_path).replace(os.sep, '/')
            cleaned = rel.rstrip('/')
            if cleaned in nested_repo_paths or rel in nested_repo_paths:
                i += 1
                continue  # already reported by the nested repo itself
            # A folded untracked-directory entry (`?? dir/`): expand it into its
            # files if there are only a few; otherwise keep it as ONE collapsed
            # entry tagged kind='dir' + count, so pulled-in code (hundreds of
            # files, all "added") doesn't drown the user's real changes.
            if xy == '??' and path.endswith('/'):
                files = _list_untracked_files(repo, path)
                if 0 < len(files) <= UNTRACKED_DIR_EXPAND_LIMIT:
                    for fp in files:
                        frel = os.path.relpath(
                            os.path.join(repo, fp), workspace_path).replace(os.sep, '/')
                        changes.append({'path': frel, 'status': 'added'})
                else:
                    # keep a trailing slash so it reads as a directory
                    changes.append({'path': rel.rstrip('/') + '/', 'status': 'added',
                                    'kind': 'dir', 'count': len(files)})
                i += 1
                continue
            changes.append({'path': rel, 'status': _map_status(xy)})
            i += 1
    # The panel lists only additions/modifications; deletions are noise here.
    changes = [c for c in changes if c['status'] != 'deleted']
    # Collapse top-level 'added' floods (an unborn-HEAD / uncommitted pulled repo
    # lists every file individually since git only folds untracked subdirs).
    changes = _collapse_added_floods(changes)
    capped, truncated = _prioritize_and_cap(changes)
    return capped, truncated, ok


def expand_untracked_dir(workspace_path, rel_dir):
    """List the individual untracked files under a folded `dir/` entry (the ones
    git_changes collapsed when the directory had too many files). Returns
    [{'path': <workspace-relative POSIX>, 'status': 'added'}, ...], path-sorted."""
    rel = rel_dir.rstrip('/')
    repo, repo_rel = resolve_repo_for_path(workspace_path, rel)
    if repo is None:
        return []
    out = []
    for fp in _list_untracked_files(repo, repo_rel):
        frel = os.path.relpath(os.path.join(repo, fp), workspace_path).replace(os.sep, '/')
        out.append({'path': frel, 'status': 'added'})
    out.sort(key=lambda c: c['path'])
    return out


def _classify(repo, repo_rel):
    """Return 'added'|'modified'|'deleted'|None for a repo-relative path."""
    try:
        rc, stdout = _run_git(
            ['git', '-C', repo, 'status', '--porcelain', '-z', '--', repo_rel])
    except Exception:
        return None
    if rc != 0 or not stdout:
        return None
    xy = stdout.split('\0')[0][:2]
    return _map_status(xy)


def read_file_preview(abs_path):
    """Read a file's text for in-panel preview: {'content','truncated','binary'}.
    Capped by the same line/byte limits as diffs. Independent of git, so it works
    for produced files under outputs/ (which git ignores). Binary files (NUL byte)
    return binary=True with empty content."""
    try:
        with open(abs_path, 'rb') as f:
            raw = f.read(MAX_DIFF_BYTES + 1)
    except OSError:
        return {'content': '', 'truncated': False, 'binary': False}
    if b'\x00' in raw:
        return {'content': '', 'truncated': False, 'binary': True}
    text, truncated = _cap(raw.decode('utf-8', 'replace'))
    return {'content': text, 'truncated': truncated, 'binary': False}


def _cap(text):
    """Truncate text to the line/byte caps; return (text, truncated)."""
    truncated = False
    encoded = text.encode('utf-8', 'replace')
    if len(encoded) > MAX_DIFF_BYTES:
        text = encoded[:MAX_DIFF_BYTES].decode('utf-8', 'ignore')
        truncated = True
    lines = text.split('\n')
    if len(lines) > MAX_DIFF_LINES:
        text = '\n'.join(lines[:MAX_DIFF_LINES])
        truncated = True
    return text, truncated


def file_diff(workspace_path, rel_path):
    """Return {status, diff?|content?, truncated} for a single changed file.

    modified -> unified `git diff` (hunks only); added -> capped file content;
    deleted/unknown -> status only. No repo found -> status None."""
    repo, repo_rel = resolve_repo_for_path(workspace_path, rel_path)
    if repo is None:
        return {'status': None, 'truncated': False}
    status = _classify(repo, repo_rel)
    if status == 'modified':
        try:
            rc, stdout = _run_git(['git', '-C', repo, 'diff', '--', repo_rel])
            diff = stdout if rc == 0 else ''
        except Exception:
            diff = ''
        diff, truncated = _cap(diff)
        return {'status': 'modified', 'diff': diff, 'truncated': truncated}
    if status == 'added':
        try:
            with open(os.path.join(repo, repo_rel), 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception:
            content = ''
        content, truncated = _cap(content)
        return {'status': 'added', 'content': content, 'truncated': truncated}
    return {'status': status, 'truncated': False}
