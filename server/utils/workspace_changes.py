"""Compute the changed files in a session workspace via `git status`.

OpenCode's native /session/{id}/diff returns nothing for the clone+edit flow,
so we read git status of the workspace's git repos directly. Read-only.
"""
import os
import subprocess

MAX_CHANGES = 500
MAX_DIFF_LINES = 2000      # cap added-file content & diff text by lines
MAX_DIFF_BYTES = 256 * 1024
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


def git_changes(workspace_path):
    """Return (changes, truncated) where changes is
    [{'path': <rel-to-workspace POSIX>, 'status': 'added'|'modified'|'deleted'}]."""
    changes = []
    repos = _find_git_repos(workspace_path)
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
            out = subprocess.run(
                ['git', '-C', repo, 'status', '--porcelain', '-z'],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            continue
        if out.returncode != 0:
            continue
        entries = out.stdout.split('\0')
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
            changes.append({'path': rel, 'status': _map_status(xy)})
            i += 1
    changes.sort(key=lambda c: c['path'])
    truncated = len(changes) > MAX_CHANGES
    return changes[:MAX_CHANGES], truncated


def _classify(repo, repo_rel):
    """Return 'added'|'modified'|'deleted'|None for a repo-relative path."""
    try:
        out = subprocess.run(
            ['git', '-C', repo, 'status', '--porcelain', '-z', '--', repo_rel],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout:
        return None
    xy = out.stdout.split('\0')[0][:2]
    return _map_status(xy)


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
            out = subprocess.run(
                ['git', '-C', repo, 'diff', '--', repo_rel],
                capture_output=True, text=True, timeout=20,
            )
            diff = out.stdout if out.returncode == 0 else ''
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
