"""Compute the changed files in a session workspace via `git status`.

OpenCode's native /session/{id}/diff returns nothing for the clone+edit flow,
so we read git status of the workspace's git repos directly. Read-only.
"""
import os
import subprocess

MAX_CHANGES = 500
_SKIP_DIRS = {'uploads', 'outputs', 'node_modules', '.venv', '__pycache__'}


def _find_git_repos(workspace_path, max_depth=3):
    """Return dirs under workspace_path that are git repos (contain .git),
    bounded depth, skipping well-known noise dirs. Does not descend into a repo."""
    repos = []
    base_depth = workspace_path.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _files in os.walk(workspace_path):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if depth >= max_depth:
            dirnames[:] = []
        if '.git' in os.listdir(dirpath):
            repos.append(dirpath)
            dirnames[:] = []  # don't descend into the repo
    return repos


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
    for repo in _find_git_repos(workspace_path):
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
            changes.append({'path': rel, 'status': _map_status(xy)})
            i += 1
    changes.sort(key=lambda c: c['path'])
    truncated = len(changes) > MAX_CHANGES
    return changes[:MAX_CHANGES], truncated
