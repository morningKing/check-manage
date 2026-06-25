"""Tests for batch workspace provisioning (clone agent/skill repo into .opencode/)."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_repo(path):
    """A throwaway git repo laid out like an OpenCode config dir."""
    os.makedirs(os.path.join(path, 'agent'))
    with open(os.path.join(path, 'agent', 'my-agent.md'), 'w') as f:
        f.write('# my project agent')
    os.makedirs(os.path.join(path, 'skill', 'my-skill'))
    with open(os.path.join(path, 'skill', 'my-skill', 'SKILL.md'), 'w') as f:
        f.write('# my skill')
    subprocess.run(['git', 'init', '-q', path], check=True, capture_output=True)
    subprocess.run(['git', '-C', path, 'add', '-A'], check=True, capture_output=True)
    subprocess.run(['git', '-C', path, '-c', 'user.name=t', '-c', 'user.email=t@t',
                    'commit', '-q', '-m', 'init'], check=True, capture_output=True)


def test_provision_clones_repo_into_dot_opencode(tmp_path):
    from utils.batch_engine import BatchWorker
    src = str(tmp_path / 'src')
    _make_repo(src)
    ws = str(tmp_path / 'ws')
    os.makedirs(ws)
    warn = BatchWorker._provision_workspace(ws, src, '')
    assert warn is None
    # repo root contents land directly under .opencode/
    assert os.path.isfile(os.path.join(ws, '.opencode', 'agent', 'my-agent.md'))
    assert os.path.isfile(os.path.join(ws, '.opencode', 'skill', 'my-skill', 'SKILL.md'))
    # the cloned .git is stripped
    assert not os.path.isdir(os.path.join(ws, '.opencode', '.git'))


def test_provision_empty_repo_is_noop(tmp_path):
    from utils.batch_engine import BatchWorker
    ws = str(tmp_path / 'ws')
    os.makedirs(ws)
    assert BatchWorker._provision_workspace(ws, '', '') is None
    assert BatchWorker._provision_workspace(ws, None, None) is None
    assert not os.path.exists(os.path.join(ws, '.opencode'))


def test_provision_bad_repo_returns_warning_not_raises(tmp_path):
    """Degrade gracefully: a clone failure returns a warning string (the run
    continues with global agents/skills) instead of raising."""
    from utils.batch_engine import BatchWorker
    ws = str(tmp_path / 'ws')
    os.makedirs(ws)
    warn = BatchWorker._provision_workspace(ws, str(tmp_path / 'nope-does-not-exist'), '')
    assert isinstance(warn, str) and '克隆失败' in warn
