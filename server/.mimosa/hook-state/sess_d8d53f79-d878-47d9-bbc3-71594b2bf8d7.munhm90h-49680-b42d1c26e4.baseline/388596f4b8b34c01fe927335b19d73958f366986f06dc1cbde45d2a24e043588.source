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


def test_check_agent_validates_primary_subagent_unknown(monkeypatch):
    """Fail fast on an unusable batch agent: empty→ok, primary→ok, subagent and
    unknown→clear error (the silent-3min-stall bug)."""
    import utils.batch_engine as eng
    from utils.batch_engine import BatchWorker
    from unittest.mock import MagicMock
    fake = MagicMock()
    fake.list_agents.return_value = [
        {'name': 'build', 'mode': 'primary'},
        {'name': 'plan', 'mode': 'primary'},
        {'name': 'explore', 'mode': 'subagent'},
    ]
    monkeypatch.setattr(eng, 'opencode_client', fake)
    assert BatchWorker._check_agent('', 'd') is None          # default
    assert BatchWorker._check_agent('build', 'd') is None      # primary
    sub = BatchWorker._check_agent('explore', 'd')
    assert sub and 'subagent' in sub
    unknown = BatchWorker._check_agent('nope', 'd')
    assert unknown and '不存在' in unknown


def test_check_agent_degrades_when_opencode_unavailable(monkeypatch):
    """If OpenCode can't be queried, don't block the run (create_session will
    surface the real connectivity error)."""
    import utils.batch_engine as eng
    from utils.batch_engine import BatchWorker
    from unittest.mock import MagicMock
    fake = MagicMock()
    fake.list_agents.side_effect = RuntimeError('opencode down')
    monkeypatch.setattr(eng, 'opencode_client', fake)
    assert BatchWorker._check_agent('build', 'd') is None


def test_provision_bad_repo_returns_warning_not_raises(tmp_path):
    """Degrade gracefully: a clone failure returns a warning string (the run
    continues with global agents/skills) instead of raising."""
    from utils.batch_engine import BatchWorker
    ws = str(tmp_path / 'ws')
    os.makedirs(ws)
    warn = BatchWorker._provision_workspace(ws, str(tmp_path / 'nope-does-not-exist'), '')
    assert isinstance(warn, str) and '克隆失败' in warn
