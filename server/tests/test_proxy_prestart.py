# -*- coding: utf-8 -*-
"""prestart_cleanup 残留清理的角色分流与杀失败处理 + se reconcile 单轮决策。

2026-11 生产卡启动修复的回归面：
1. taskkill 失败不再静默——重试一次，仍失败按角色分流（SE 降级放行、
   backend/MCP 拒启）；
2. 父存活判定走 stale_check.parent_alive（PID 复用感知）——真父退出后 pid
   被无关进程复用的残留 SE 不再被误判成「父存活的陌生进程」拒启；
3. SE 端口被占 → _SE_BLOCK 降级：隧道拒连、main 不 spawn、reconcile 探测
   端口释放自动解除；
4. reconcile 快死退避（spawn 后秒死的 EADDRINUSE 循环不再 10s 刷屏）。

纪律：纯函数级（不启 proxy）；快照/监听者/树杀全部 fake，绝不碰真进程。
"""
import http.client
import io
import inspect
import os
import sys
import time as _time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import proxy  # noqa: E402
import stale_check  # noqa: E402

BACKEND_PORT = 3001
MCP_PORT = 3003
SE_PORT = 1234


# ---------- fakes ----------

def _entry(parent=0, created=None, cmd=''):
    return {'cmd': cmd, 'parent': parent, 'created': created}


class _Listeners:
    """serve_listener_pids 替身：按端口持 pid 表，树杀成功由测试手动摘除。"""

    def __init__(self, by_port):
        self.by_port = dict(by_port)

    def __call__(self, port):
        return list(self.by_port.get(port, []))


def _patch_ports(monkeypatch):
    """钉死所有角色端口，防 .env/环境漂移把断言带偏。"""
    monkeypatch.setattr(proxy, 'BACKEND_URL', f'http://127.0.0.1:{BACKEND_PORT}')
    monkeypatch.setattr(proxy, 'MCP_HEALTH_URL', f'http://127.0.0.1:{MCP_PORT}/health')
    monkeypatch.setattr(proxy, 'OPENCODE_BASE_URL', 'http://127.0.0.1:4096')
    monkeypatch.setattr(proxy, 'SE_WS_PORT', SE_PORT)
    monkeypatch.setattr(proxy, 'PROXY_PORT', 8080)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv('PROXY_AUTOCLEAN', '1')
    monkeypatch.setattr(_time, 'sleep', lambda s: None)   # 重试/步进间隔不真等
    monkeypatch.setattr(proxy, '_SE_BLOCK', {'blocked': False, 'reason': ''})
    _patch_ports(monkeypatch)


def _fake_scan(monkeypatch, procs):
    """快照/命令行替身；parent_alive 用真实现（复用判定是被测逻辑本体）。"""
    fake = types.SimpleNamespace(
        snapshot_processes=lambda: dict(procs),
        _cmdline_one=lambda pid: '',
        parent_alive=stale_check.parent_alive,
    )
    monkeypatch.setattr(proxy, '_stale_check', fake)


def _fake_kill(monkeypatch, results, calls):
    """_stack_stop._kill_tree 替身：按调用次序吐 (ok, err)。"""
    fake = types.SimpleNamespace(_kill_tree=_kill_tree_stub(results, calls))
    monkeypatch.setattr(proxy, '_stack_stop', fake)


def _kill_tree_stub(results, calls):
    def _kill_tree(pid, procs):
        calls.append(pid)
        if results:
            return results[min(len(calls) - 1, len(results) - 1)]
        return True, ''
    return _kill_tree


def _run_cleanup(monkeypatch, listeners):
    monkeypatch.setattr(proxy, 'serve_listener_pids', _Listeners(listeners))
    return proxy.prestart_cleanup()


# ---------- SE 孤儿：杀成功 → 正常，不降级 ----------

def test_se_orphan_killed_cleanly(monkeypatch):
    procs = {555: _entry(parent=999, created=1000.0)}   # 999 不在快照 = 父已死
    calls = []
    _fake_scan(monkeypatch, procs)
    _fake_kill(monkeypatch, [(True, '')], calls)

    r = _run_cleanup(monkeypatch, {SE_PORT: [555]})

    assert calls == [555]
    assert r == {'se_block_reason': None}
    assert proxy._SE_BLOCK['blocked'] is False


# ---------- SE 孤儿：杀不掉 → 降级放行，绝不退出 ----------

def test_se_orphan_unkillable_degrades_without_exit(monkeypatch, capsys):
    procs = {555: _entry(parent=999, created=1000.0)}
    calls = []
    _fake_scan(monkeypatch, procs)
    _fake_kill(monkeypatch, [(False, 'Access is denied')], calls)   # 重试同败

    r = _run_cleanup(monkeypatch, {SE_PORT: [555]})

    assert calls == [555, 555]                       # 失败后恰好重试一次
    assert r['se_block_reason'] and '1234' in r['se_block_reason']
    assert proxy._SE_BLOCK['blocked'] is True
    out = capsys.readouterr().out
    assert '智能写作降级' in out
    assert 'Access is denied' in out                 # taskkill 错误可见（旧实现静默）


def test_se_orphan_unkillable_but_port_freed_no_block(monkeypatch):
    """重试仍报错、但复检时端口已空（进程其实已死，taskkill 返回码怪）→ 不降级。"""
    procs = {555: _entry(parent=999, created=1000.0)}
    calls = []
    _fake_scan(monkeypatch, procs)

    def failing_kill(pid, procs):
        calls.append(pid)
        return False, '错'

    monkeypatch.setattr(proxy, '_stack_stop',
                        types.SimpleNamespace(_kill_tree=failing_kill))
    # SE 端口首次查询命中 555；杀「失败」后复检返回空（进程其实已退出）
    seq = {'n': 0}

    def listeners_by_seq(port):
        if port != SE_PORT:
            return []
        seq['n'] += 1
        return [555] if seq['n'] == 1 else []

    monkeypatch.setattr(proxy, 'serve_listener_pids', listeners_by_seq)

    r = proxy.prestart_cleanup()

    assert calls == [555, 555]
    assert r == {'se_block_reason': None}
    assert proxy._SE_BLOCK['blocked'] is False


# ---------- PID 复用回归：父 pid 被年轻进程顶名 → 视为孤儿照杀 ----------

def test_se_pid_reused_parent_still_orphan(monkeypatch):
    """生产卡启动的实锤形态：真父退出后 pid 被无关进程复用（777 比子 555
    年轻）→ 不得判「父存活的陌生进程」，按孤儿清理。"""
    procs = {555: _entry(parent=777, created=1000.0),
             777: _entry(parent=0, created=2000.0)}   # 复用者更年轻
    calls = []
    _fake_scan(monkeypatch, procs)
    _fake_kill(monkeypatch, [(True, '')], calls)

    r = _run_cleanup(monkeypatch, {SE_PORT: [555]})

    assert calls == [555]                            # 杀了（而非 foreign 拦下）
    assert r == {'se_block_reason': None}
    assert proxy._SE_BLOCK['blocked'] is False


# ---------- SE 真·父存活 → 降级放行（不误杀、不退出） ----------

def test_se_foreign_parent_alive_degrades(monkeypatch):
    procs = {555: _entry(parent=777, created=2000.0),
             777: _entry(parent=0, created=1000.0)}   # 父更老 = 真活着
    calls = []
    _fake_scan(monkeypatch, procs)
    _fake_kill(monkeypatch, [], calls)

    r = _run_cleanup(monkeypatch, {SE_PORT: [555]})

    assert calls == []                               # 宁少勿误杀：不碰
    assert r['se_block_reason'] and '555' in r['se_block_reason']
    assert proxy._SE_BLOCK['blocked'] is True


# ---------- 关键端口（backend）：维持拒启语义 ----------

def test_backend_stranger_exits(monkeypatch):
    procs = {666: _entry(parent=777, created=2000.0),
             777: _entry(parent=0, created=1000.0)}
    calls = []
    _fake_scan(monkeypatch, procs)
    _fake_kill(monkeypatch, [], calls)

    with pytest.raises(SystemExit) as ei:
        _run_cleanup(monkeypatch, {BACKEND_PORT: [666]})

    assert ei.value.code == 1
    assert calls == []                               # 陌生进程不杀
    assert proxy._SE_BLOCK['blocked'] is False


def test_backend_orphan_unkillable_exits(monkeypatch):
    """关键端口杀不掉必须拒启（旧实现静默放行 = 「启动成功实为旧代码」）。"""
    procs = {666: _entry(parent=999, created=1000.0)}
    calls = []
    _fake_scan(monkeypatch, procs)
    _fake_kill(monkeypatch, [(False, 'Access is denied')], calls)

    with pytest.raises(SystemExit) as ei:
        _run_cleanup(monkeypatch, {BACKEND_PORT: [666]})

    assert ei.value.code == 1
    assert calls == [666, 666]
    assert proxy._SE_BLOCK['blocked'] is False       # SE 降级面不受 backend 影响


# ---------- WS 隧道降级门 ----------

class _FakeSocket:
    def settimeout(self, t):
        pass


def _make_handler(path):
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


def test_tunnel_refuses_403_when_se_blocked(monkeypatch):
    """SE 降级中 → 403 SE_UNAVAILABLE，且绝不把隧道交给 1234 上的陌生进程。"""
    monkeypatch.setattr(proxy, 'se_plugin_enabled', lambda: True)
    proxy._SE_BLOCK['blocked'] = True
    proxy._SE_BLOCK['reason'] = ':1234 occupied'

    def no_upstream(*a, **kw):
        raise AssertionError('SE 降级中不应向上游建连')

    monkeypatch.setattr('socket.create_connection', no_upstream)
    h = _make_handler('/suggest-ws')
    h.do_GET()

    out = h.wfile.getvalue().decode('utf-8', errors='replace')
    assert out.startswith('HTTP/1.1 403'), out
    assert 'SE_UNAVAILABLE' in out


# ---------- reconcile 单轮：降级探测/解除 ----------

class _Proc:
    def __init__(self, pid, alive=True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 7


def test_reconcile_once_blocked_port_occupied_noops(monkeypatch):
    monkeypatch.setattr(proxy, 'serve_listener_pids', lambda port: [555])
    monkeypatch.setattr(proxy, 'se_plugin_enabled', lambda: True)
    started = []
    monkeypatch.setattr(proxy, 'start_se', lambda: started.append(1) or _Proc(1))
    proxy._SE_BLOCK['blocked'] = True
    se_state = {'proc': None}

    proxy._se_reconcile_once(se_state, [], now=1000.0)

    assert started == []                             # 还占着：本轮不动
    assert proxy._SE_BLOCK['blocked'] is True


def test_reconcile_once_unblocks_and_starts_when_port_freed(monkeypatch):
    monkeypatch.setattr(proxy, 'serve_listener_pids', lambda port: [])
    monkeypatch.setattr(proxy, 'se_plugin_enabled', lambda: True)
    started = []
    monkeypatch.setattr(proxy, 'start_se', lambda: started.append(1) or _Proc(42))
    proxy._SE_BLOCK['blocked'] = True
    se_state = {'proc': None}
    procs_list = []

    proxy._se_reconcile_once(se_state, procs_list, now=1000.0)

    assert started == [1]                            # 端口释放：解除并当轮拉起
    assert proxy._SE_BLOCK['blocked'] is False
    assert se_state['proc'].pid == 42
    assert se_state['last_start'] == 1000.0
    assert procs_list == [se_state['proc']]


# ---------- reconcile 单轮：快死/依赖缺失退避 ----------

def test_reconcile_once_quick_death_backs_off_then_recovers(monkeypatch):
    """拉起后 10s 内退出（EADDRINUSE 形态）→ 60s 退避一次，到期照常重试。"""
    monkeypatch.setattr(proxy, 'serve_listener_pids', lambda port: [])
    monkeypatch.setattr(proxy, 'se_plugin_enabled', lambda: True)
    monkeypatch.setattr(proxy, 'start_se', lambda: _Proc(7, alive=False))
    dead = _Proc(8, alive=False)                     # 上轮拉起后已死（5s 内）
    se_state = {'proc': dead, 'last_start': 990.0}
    procs_list = [dead]

    proxy._se_reconcile_once(se_state, procs_list, now=995.0)

    assert se_state['retry_after'] == pytest.approx(1055.0)   # 995 + 60

    # 退避期内不重试
    before = se_state.get('retry_after')
    proxy._se_reconcile_once(se_state, procs_list, now=1000.0)
    assert se_state.get('retry_after') == before

    # 到期后重试：拉起的新进程仍秒死 → 下一轮再退避（crash 循环频率 10s→70s）
    proxy._se_reconcile_once(se_state, procs_list, now=1100.0)
    assert se_state['last_start'] == 1100.0
    assert 'retry_after' not in se_state             # 拉起成功即清退避（新进程说了算）
    proxy._se_reconcile_once(se_state, procs_list, now=1101.0)
    assert se_state['retry_after'] == pytest.approx(1161.0)


def test_reconcile_once_dep_missing_backs_off(monkeypatch):
    """start_se 返回 None（依赖缺失）→ 退避，行为与旧版一致。"""
    monkeypatch.setattr(proxy, 'serve_listener_pids', lambda port: [])
    monkeypatch.setattr(proxy, 'se_plugin_enabled', lambda: True)
    calls = []
    monkeypatch.setattr(proxy, 'start_se', lambda: calls.append(1) or None)
    se_state = {'proc': None}

    proxy._se_reconcile_once(se_state, [], now=1000.0)
    assert calls == [1]
    assert se_state['retry_after'] == pytest.approx(1060.0)

    proxy._se_reconcile_once(se_state, [], now=1020.0)
    assert calls == [1]                              # 退避期内不再 spawn


# ---------- 接线守卫 ----------

def test_main_skips_se_when_blocked():
    """main 的 SE 段必须先看降级开关再 spawn——硬 spawn 只会 EADDRINUSE 崩溃。"""
    src = inspect.getsource(proxy.main)
    assert "_SE_BLOCK['blocked']" in src


def test_prestart_cleanup_returns_contract(monkeypatch):
    """返回契约：{'se_block_reason': str|None}——关闭/缺模块路径同样成立。"""
    monkeypatch.setenv('PROXY_AUTOCLEAN', '0')
    monkeypatch.setattr(proxy, 'serve_listener_pids', lambda port: [])
    assert proxy.prestart_cleanup() == {'se_block_reason': None}
