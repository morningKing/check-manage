"""_auto_approve_if_bypass：bypass 会话权限询问自动放行（spec §2.3）。"""
import contextlib
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import chat_persist


def _evt(req_id='req1', oc_sid='oc_parent', permission='external_directory'):
    return {'event': 'permission.asked',
            'data': {'properties': {'id': req_id, 'sessionID': oc_sid,
                                    'permission': permission}}}


@pytest.fixture
def env(monkeypatch):
    """伪 get_db（可配置返回 mode）+ 伪 runtime client。用例改
    env['state']['mode'] 即可切换会话 permission_mode。"""
    state = {'mode': 'bypass'}
    fake_client = MagicMock()

    class _FakeCursor:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return (state['mode'],)

    class _FakeCtx:
        def cursor(self):
            return _FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    @contextlib.contextmanager
    def fake_get_db():
        yield _FakeCtx()

    class _FakeRuntime:
        def get_client(self):
            return fake_client

    monkeypatch.setattr(chat_persist, 'get_db', fake_get_db)
    monkeypatch.setattr(chat_persist, 'get_runtime', lambda: _FakeRuntime())
    return {'state': state, 'client': fake_client}


def test_bypass_parent_ask_replied_once(env):
    st = env['state']
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(), 'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_called_once_with('req1', 'once', directory='/ws')


def test_ask_mode_not_replied(env):
    env['state']['mode'] = 'ask'
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(), 'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_not_called()


def test_child_ask_uses_parent_mode(env):
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(oc_sid='oc_child1'), 'oc_parent',
        {'subtasks': {'oc_child1': {}}}, '/ws')
    env['client'].reply_permission.assert_called_once()


def test_foreign_ask_ignored(env):
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(oc_sid='oc_stranger'), 'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_not_called()


def test_non_permission_event_ignored(env):
    chat_persist._auto_approve_if_bypass(
        'sess_x', {'event': 'message.updated', 'data': {'properties': {}}},
        'oc_parent', {'subtasks': {}}, '/ws')
    env['client'].reply_permission.assert_not_called()


def test_reply_failure_swallowed(env, monkeypatch):
    env['client'].reply_permission.side_effect = RuntimeError('boom')
    # 不抛异常即通过（fail-open：回落手动权限卡）
    chat_persist._auto_approve_if_bypass(
        'sess_x', _evt(), 'oc_parent', {'subtasks': {}}, '/ws')
