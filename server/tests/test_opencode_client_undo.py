"""OpenCodeClient revert 方法与 stub 模拟（undo-redo spec §2.5）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from utils.opencode_client import OpenCodeClient


class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_revert_session_posts_message_id(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, json=json, params=params)
        return _Resp({'id': 'oc1', 'revert': {'messageID': 'msg_u'}})

    monkeypatch.setattr('utils.opencode_client.requests.post', fake_post)
    out = OpenCodeClient('http://x').revert_session('oc1', 'msg_u', directory='/ws')
    assert captured['url'].endswith('/session/oc1/revert')
    assert captured['json'] == {'messageID': 'msg_u'}   # E2：user 消息 id
    assert captured['params'] == {'directory': '/ws'}
    assert out['revert']['messageID'] == 'msg_u'


def test_unrevert_session_posts_empty(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, json=json)
        return _Resp({'id': 'oc1', 'revert': None})

    monkeypatch.setattr('utils.opencode_client.requests.post', fake_post)
    OpenCodeClient('http://x').unrevert_session('oc1', directory='/ws')
    assert captured['url'].endswith('/session/oc1/unrevert')
    assert captured['json'] is None


def test_get_project_scopes_directory(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params)
        return _Resp({'vcs': 'git', 'worktree': '/ws'})

    monkeypatch.setattr('utils.opencode_client.requests.get', fake_get)
    out = OpenCodeClient('http://x').get_project('/ws')
    assert captured['url'].endswith('/project/current')
    assert out['vcs'] == 'git'


def test_stub_runtime_revert_roundtrip():
    import os
    os.environ['AI_STUB_ALLOW'] = '1'
    from utils.runtime.stub import StubRuntime
    rt = StubRuntime({'delay_ms': [1, 1]})
    sid = rt.create_session(directory='/ws')
    info = rt.revert_stage(sid, 'msg_x', directory='/ws')
    assert info['revert']['messageID'] == 'msg_x'
    assert rt.get_client().get_project('/ws') == {'vcs': None, 'worktree': ''}
    rt.revert_clear(sid, directory='/ws')
    assert rt.get_client()._sessions[sid].revert is None
    assert rt.capabilities()['revert'] is True


def test_revert_aliases_forward_to_session_endpoints(monkeypatch):
    """OpenCodeClient 的 stage/clear 薄别名（T7 接线缺口修复）：编排层
    ai_revert 经 get_runtime().get_client() 拿到的是本类、调的是 stage/clear
    形，必须原样转发到底层 revert_session/unrevert_session 端点。"""
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, json=json, params=params)
        return _Resp({'id': 'oc1', 'revert': {'messageID': 'msg_u'}})

    monkeypatch.setattr('utils.opencode_client.requests.post', fake_post)
    out = OpenCodeClient('http://x').revert_stage('oc1', 'msg_u', directory='/ws')
    assert captured['url'].endswith('/session/oc1/revert')
    assert captured['json'] == {'messageID': 'msg_u'}
    assert captured['params'] == {'directory': '/ws'}
    assert out['revert']['messageID'] == 'msg_u'

    OpenCodeClient('http://x').revert_clear('oc1', directory='/ws')
    assert captured['url'].endswith('/session/oc1/unrevert')
    assert captured['json'] is None
