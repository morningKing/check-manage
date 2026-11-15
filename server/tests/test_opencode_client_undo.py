"""OpenCodeClient revert 方法与 stub 模拟（undo-redo spec §2.5）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from ai.opencode.opencode_client import OpenCodeClient


REVERT_SURFACE = ('revert_stage', 'revert_clear', 'revert_session',
                  'unrevert_session')


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

    monkeypatch.setattr('ai.opencode.opencode_client.requests.post', fake_post)
    out = OpenCodeClient('http://x').revert_session('oc1', 'msg_u', directory='/ws')
    assert captured['url'].endswith('/session/oc1/revert')
    assert captured['json'] == {'messageID': 'msg_u'}   # E2：user 消息 id
    assert captured['params'] == {'directory': '/ws'}
    assert out['revert']['messageID'] == 'msg_u'


def test_unrevert_session_posts_empty(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, json=json, params=params)
        return _Resp({'id': 'oc1', 'revert': None})

    monkeypatch.setattr('ai.opencode.opencode_client.requests.post', fake_post)
    OpenCodeClient('http://x').unrevert_session('oc1', directory='/ws')
    assert captured['url'].endswith('/session/oc1/unrevert')
    assert captured['json'] is None
    assert captured['params'] == {'directory': '/ws'}


def test_get_project_scopes_directory(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params)
        return _Resp({'vcs': 'git', 'worktree': '/ws'})

    monkeypatch.setattr('ai.opencode.opencode_client.requests.get', fake_get)
    out = OpenCodeClient('http://x').get_project('/ws')
    assert captured['url'].endswith('/project/current')
    assert out['vcs'] == 'git'


def test_stub_runtime_revert_roundtrip(monkeypatch):
    monkeypatch.setenv('AI_STUB_ALLOW', '1')
    from ai.runtime.stub import StubRuntime
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

    monkeypatch.setattr('ai.opencode.opencode_client.requests.post', fake_post)
    out = OpenCodeClient('http://x').revert_stage('oc1', 'msg_u', directory='/ws')
    assert captured['url'].endswith('/session/oc1/revert')
    assert captured['json'] == {'messageID': 'msg_u'}
    assert captured['params'] == {'directory': '/ws'}
    assert out['revert']['messageID'] == 'msg_u'

    OpenCodeClient('http://x').revert_clear('oc1', directory='/ws')
    assert captured['url'].endswith('/session/oc1/unrevert')
    assert captured['json'] is None
    assert captured['params'] == {'directory': '/ws'}   # clear 转发同样带 directory


def test_revert_surface_parity():
    """两侧别名面 parity 守护（I3）：编排层 ai_revert 经 get_client() 消费的
    四个 revert 方法必须在 OpenCodeClient 与 StubClient 上同时存在——
    任何一侧缺方法都会把压测/联调栈（stub runtime）炸成 AttributeError，
    且只在运行时才暴露。此测试杜绝再度漂移。"""
    from ai.runtime.stub import StubClient
    for name in REVERT_SURFACE:
        assert hasattr(OpenCodeClient, name), f'OpenCodeClient 缺 {name}'
        assert hasattr(StubClient, name), f'StubClient 缺 {name}'


def test_stub_client_revert_clear_aliases_unrevert(monkeypatch):
    """StubClient.revert_clear 别名（I3）：转发 unrevert_session，清撤销标记。"""
    monkeypatch.setenv('AI_STUB_ALLOW', '1')
    from ai.runtime.stub import StubClient
    c = StubClient({'delay_ms': [1, 1]})
    sid = c.create_session(directory='/ws')
    c.revert_stage(sid, 'msg_x', directory='/ws')
    assert c._sessions[sid].revert == {'messageID': 'msg_x'}
    c.revert_clear(sid, directory='/ws')
    assert c._sessions[sid].revert is None
