# -*- coding: utf-8 -*-
"""proxy.py 生产插件门（plugin-architecture spec §5 生产拓扑挂载点）。

开发态 dev:all 不经 proxy（蓝图门 + 前端守卫已兜底）；生产 :8080 上这道门
负责两件事：
1. `/suggest-ws` WS 隧道：插件禁用 → 403 拒连（且**在建上游连接之前**）；
2. se Node 受管子进程：随开关停启（决策纯函数 + reconcile 守护线程）。

纪律：纯函数级（不启 proxy）；403 行为测试沿用 test_proxy_upload_limits 的
假 handler 手法；`se_plugin_enabled` fail-open（开关面故障不掐在线协作）。
"""
import http.client
import inspect
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import proxy  # noqa: E402


# ---------- 决策纯函数：四象限 ----------

def test_se_process_action():
    from proxy import _se_process_action
    assert _se_process_action(enabled=True, running=False) == 'start'
    assert _se_process_action(enabled=True, running=True) == 'noop'
    assert _se_process_action(enabled=False, running=True) == 'stop'
    assert _se_process_action(enabled=False, running=False) == 'noop'


# ---------- fail-open：开关面故障不掐在线协作 ----------

def test_se_plugin_enabled_fails_open(monkeypatch):
    import proxy
    def boom(pid): raise RuntimeError('db down')
    monkeypatch.setattr('utils.plugin_registry.plugin_enabled', boom)
    assert proxy.se_plugin_enabled() is True   # fail-open：开关面故障不掐在线协作


def test_se_plugin_enabled_reads_registry(monkeypatch):
    """开关正常时透传 plugin_registry 的判定（True 与 False 都要透传，不能
    只会 fail-open）。"""
    calls = []

    def fake(pid):
        calls.append(pid)
        return False

    monkeypatch.setattr('utils.plugin_registry.plugin_enabled', fake)
    assert proxy.se_plugin_enabled() is False
    assert calls == ['smart-writing']


# ---------- WS 隧道门：禁用 → 403，且在建上游连接之前 ----------

class _FakeSocket:
    def settimeout(self, t):
        pass


def _make_handler(path):
    """不走 __init__（那会真的去 accept 一个连接）造一个 ProxyHandler 实例
    （手法同 test_proxy_upload_limits._make_handler）。"""
    h = proxy.ProxyHandler.__new__(proxy.ProxyHandler)
    h.path = path
    h.command = 'GET'
    h.request_version = 'HTTP/1.1'
    h.protocol_version = 'HTTP/1.1'
    h.requestline = f'GET {path} HTTP/1.1'
    h.client_address = ('127.0.0.1', 12345)
    h.headers = http.client.HTTPMessage()
    h.rfile = io.BytesIO()
    h.wfile = io.BytesIO()
    h.connection = _FakeSocket()
    h.close_connection = False
    return h


def test_tunnel_refuses_403_when_plugin_disabled(monkeypatch):
    """禁用 → 403 PLUGIN_DISABLED；且绝不向上游建连（门在
    socket.create_connection 之前——上游进程此时多半已被 reconcile 停掉，
    去连只会白白等一个 10s 超时）。"""
    monkeypatch.setattr(proxy, 'se_plugin_enabled', lambda: False)

    def no_upstream(*a, **kw):
        raise AssertionError('禁用插件时不应向上游建连')

    monkeypatch.setattr('socket.create_connection', no_upstream)
    h = _make_handler('/suggest-ws')
    h.do_GET()

    out = h.wfile.getvalue().decode('utf-8', errors='replace')
    assert out.startswith('HTTP/1.1 403'), out
    assert 'PLUGIN_DISABLED' in out
    assert 'smart-writing' in out


def test_tunnel_gate_is_first_thing_in_handler():
    """回归守卫（手法同 test_proxy.py 的 getsource 断言）：门必须是
    _tunnel_websocket 的第一件事——放在 create_connection 之后会让禁用态
    的客户端白等超时。"""
    src = inspect.getsource(proxy.ProxyHandler._tunnel_websocket)
    gate_pos = src.index('se_plugin_enabled')
    assert 'create_connection' not in src[:gate_pos]


def test_main_starts_se_behind_plugin_gate():
    """回归守卫：主循环的 se 拉起受开关门控，且 reconcile 线程已接线。"""
    src = inspect.getsource(proxy.main)
    assert 'se_plugin_enabled' in src          # 启动受门控
    assert '_se_reconcile_loop' in src         # 开关变更后无需重启 proxy
