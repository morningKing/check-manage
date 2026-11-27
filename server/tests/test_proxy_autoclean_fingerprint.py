"""proxy 指纹自动释放（prestart_cleanup 第②步第④规则）单元测试。

纪律：纯函数级（不启 proxy）；快照/监听者/树杀/探测全部 fake，绝不碰
真进程、真端口、真套接字。覆盖：
  - _fingerprint_ok 三角色命中/不命中形态
  - _probe_fingerprint 的 URL 序列、HTTPError 401 通路、拒连保守判假
  - _kill_listener_chain 的包装链上溯（trampoline / tsx watch / cmd /c）、
    PID 复用断链、非包装祖先停步
  - prestart_cleanup 集成：命中→自动释放不退出；不匹配/探测异常/开关关/
    释放失败→拒启 exit 1；孤儿路径不过指纹（既有行为不变）
"""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
for _p in (str(SERVER_DIR), str(SERVER_DIR / 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import proxy  # noqa: E402


# ---------------------------------------------------------------------------
# 基建
# ---------------------------------------------------------------------------

def _entry(parent=0, created=None, cmd=''):
    """快照条目工厂（stale_check.snapshot_processes 的条目形态）。"""
    return {'cmd': cmd, 'parent': parent, 'created': created}


def _descendants_of(procs, root):
    """root 的全部子孙 pid（模拟 taskkill /T 的 victims 面）。"""
    children = {}
    for pid, info in procs.items():
        children.setdefault(info.get('parent') or 0, []).append(pid)
    out, stack = [], [root]
    while stack:
        for ch in children.get(stack.pop(), []):
            if ch != root:
                out.append(ch)
                stack.append(ch)
    return out


class _Listeners:
    """替代 proxy.serve_listener_pids：按端口返回当前监听者快照。"""

    def __init__(self, by_port):
        self.by_port = by_port

    def __call__(self, port):
        return list(self.by_port.get(port, []))

    def remove_by_pid(self, pid):
        for pids in self.by_port.values():
            if pid in pids:
                pids.remove(pid)


def _patch_world(monkeypatch, procs, listeners, kill_results=None,
                 probe_results=None, backend_url='http://127.0.0.1:3001'):
    """一揽子 fake：快照/命令行/树杀/探测/监听者 + 端口模块属性钉死。"""
    monkeypatch.setattr(proxy, 'BACKEND_URL', backend_url)
    monkeypatch.setattr(proxy, 'MCP_HEALTH_URL', 'http://127.0.0.1:3003/health')
    monkeypatch.setattr(proxy, 'OPENCODE_BASE_URL', 'http://127.0.0.1:4096')
    monkeypatch.setattr(proxy, 'SE_WS_PORT', 1234)
    monkeypatch.setattr(proxy, 'PROXY_PORT', 8080)

    procs = dict(procs)
    monkeypatch.setattr(proxy, '_stale_check', SimpleNamespace(
        snapshot_processes=lambda: dict(procs),
        _cmdline_one=lambda pid: (procs.get(pid) or {}).get('cmd') or ''))

    kills = []
    results = list(kill_results or [])

    def fake_kill(pid, snap):
        kills.append(pid)
        r = results.pop(0) if results else (True, '')
        if r[0]:
            for v in {pid, *_descendants_of(procs, pid)}:
                listeners.remove_by_pid(v)
        return r

    monkeypatch.setattr(proxy, '_stack_stop',
                        SimpleNamespace(_kill_tree=fake_kill))

    probes = []
    plays = list(probe_results or [])

    def fake_probe(role, port):
        probes.append((role, port))
        return plays.pop(0) if plays else (False, '无剧本')

    monkeypatch.setattr(proxy, '_probe_fingerprint', fake_probe)
    monkeypatch.setattr(proxy, 'serve_listener_pids', listeners)
    return kills, probes


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv('PROXY_AUTOCLEAN', '1')
    monkeypatch.delenv('PROXY_AUTOCLEAN_VERIFY', raising=False)
    monkeypatch.setattr(proxy.time, 'sleep', lambda s: None)  # 重试等待瞬时化
    yield


# ---------------------------------------------------------------------------
# _fingerprint_ok
# ---------------------------------------------------------------------------

def test_mcp_title_hit():
    body = json.dumps(
        {'openapi': '3.1.0',
         'info': {'title': 'check-manage MCP server'}}).encode()
    ok, detail = proxy._fingerprint_ok('MCP', 200, body)
    assert ok and 'openapi' in detail


def test_mcp_tools_shape_fallback():
    body = json.dumps([{'name': 't', 'description': 'd', 'group': 'g',
                        'group_name': 'G', 'group_order': 1,
                        'access': 'read'}]).encode()
    ok, detail = proxy._fingerprint_ok('MCP', 200, body)
    assert ok and '/tools' in detail


def test_mcp_miss_cases():
    assert not proxy._fingerprint_ok(
        'MCP', 200, json.dumps({'info': {'title': '别的服务'}}).encode())[0]
    assert not proxy._fingerprint_ok('MCP', 200, b'<html>hello</html>')[0]
    assert not proxy._fingerprint_ok('MCP', 404, b'Not Found')[0]
    assert not proxy._fingerprint_ok('MCP', 200, json.dumps([]).encode())[0]


def test_se_hit_and_miss():
    ok, _ = proxy._fingerprint_ok('智能写作(SE)', 200, b'Welcome to Hocuspocus!')
    assert ok
    ok, _ = proxy._fingerprint_ok('智能写作(SE)', 200,
                                  b'  Welcome to Hocuspocus!\n')   # 容忍首尾空白
    assert ok
    assert not proxy._fingerprint_ok(
        '智能写作(SE)', 200, b'welcome to hocuspocus!')[0]   # 大小写敏感
    assert not proxy._fingerprint_ok(
        '智能写作(SE)', 404, b'Welcome to Hocuspocus!')[0]


def test_backend_hit_and_miss():
    ok, _ = proxy._fingerprint_ok(
        'BACKEND_URL', 401,
        json.dumps({'error': '未登录'}, ensure_ascii=False).encode('utf-8'))
    assert ok
    assert not proxy._fingerprint_ok(
        'BACKEND_URL', 401, json.dumps({'error': 'x'}).encode())[0]
    assert not proxy._fingerprint_ok(
        'BACKEND_URL', 200, '{"error": "未登录"}'.encode('utf-8'))[0]  # 必须是 401
    assert not proxy._fingerprint_ok('BACKEND_URL', 401, b'<html>login</html>')[0]


def test_unknown_role_misses():
    assert not proxy._fingerprint_ok('???', 200, b'x')[0]


# ---------------------------------------------------------------------------
# _probe_fingerprint
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, n=-1):
        return self._body[:n] if n and n > 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_probe_mcp_falls_back_to_tools(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        if url.endswith('/openapi.json'):
            return _Resp(200, b'{"info": {"title": "nope"}}')
        return _Resp(200, json.dumps([{'group_order': 1,
                                       'access': 'read'}]).encode())

    monkeypatch.setattr(proxy.urllib.request, 'urlopen', fake_urlopen)
    ok, detail = proxy._probe_fingerprint('MCP', 3003)
    assert ok and '/tools' in detail
    assert len(calls) == 2
    assert calls[0].endswith('/openapi.json') and calls[1].endswith('/tools')


def test_probe_first_hit_short_circuits(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return _Resp(200, json.dumps(
            {'info': {'title': 'check-manage MCP server'}}).encode())

    monkeypatch.setattr(proxy.urllib.request, 'urlopen', fake_urlopen)
    ok, _ = proxy._probe_fingerprint('MCP', 3003)
    assert ok and len(calls) == 1


def test_probe_httperror_401_backend(monkeypatch):
    def fake_urlopen(url, timeout=None):
        raise HTTPError(url, 401, 'UNAUTHORIZED', {}, io.BytesIO(
            '{"error": "未登录"}'.encode('utf-8')))

    monkeypatch.setattr(proxy.urllib.request, 'urlopen', fake_urlopen)
    ok, detail = proxy._probe_fingerprint('BACKEND_URL', 3001)
    assert ok and '401' in detail


def test_probe_conn_refused_conservative_false(monkeypatch):
    def fake_urlopen(url, timeout=None):
        raise URLError('Connection refused')

    monkeypatch.setattr(proxy.urllib.request, 'urlopen', fake_urlopen)
    ok, detail = proxy._probe_fingerprint('MCP', 3003)
    assert not ok and '探测失败' in detail


def test_probe_unknown_role():
    ok, _ = proxy._probe_fingerprint('???', 1)
    assert not ok


# ---------------------------------------------------------------------------
# _kill_listener_chain
# ---------------------------------------------------------------------------

def _patch_chain_env(monkeypatch, cmdlines=None):
    cmdlines = cmdlines or {}
    kills = []
    monkeypatch.setattr(proxy, '_stale_check', SimpleNamespace(
        _cmdline_one=lambda pid: cmdlines.get(pid, '')))
    monkeypatch.setattr(proxy, '_stack_stop', SimpleNamespace(
        _kill_tree=lambda pid, snap: kills.append(pid) or (True, '')))
    return kills


def test_chain_trampoline_top_killed(monkeypatch):
    procs = {
        100: _entry(parent=90, created=100.0,
                    cmd='"F:\\llvm\\anaconda3\\python.exe" main.py'),
        90: _entry(parent=80, created=90.0,
                   cmd='.venv\\Scripts\\python.exe main.py'),   # venv trampoline
        80: _entry(parent=1, created=80.0, cmd='cmd.exe'),      # 交互 cmd → 停
    }
    kills = _patch_chain_env(monkeypatch)
    proxy._kill_listener_chain(100, procs)
    assert kills == [90]


def test_chain_tsx_watch_cmd_full_chain(monkeypatch):
    procs = {
        10: _entry(parent=9, created=100.0, cmd=''),    # node 真身，快照无 cmd
        9: _entry(parent=8, created=90.0, cmd=''),      # tsx watch，cmd 缺→补
        8: _entry(parent=7, created=80.0,
                  cmd='cmd.exe /d /s /c tsx watch src/index.ts'),
        7: _entry(parent=1, created=70.0, cmd='WindowsTerminal.exe'),  # 终端→停
    }
    kills = _patch_chain_env(monkeypatch, cmdlines={
        9: 'node ..\\tsx\\dist\\cli.mjs watch src/index.ts'})
    proxy._kill_listener_chain(10, procs)
    assert kills == [8]


def test_chain_no_wrapper_ancestor_kills_leaf_only(monkeypatch):
    procs = {50: _entry(parent=40, created=100.0, cmd='python main.py'),
             40: _entry(parent=1, created=90.0, cmd='explorer.exe')}
    kills = _patch_chain_env(monkeypatch)
    proxy._kill_listener_chain(50, procs)
    assert kills == [50]


def test_chain_pid_reuse_breaks_walk(monkeypatch):
    procs = {50: _entry(parent=40, created=100.0, cmd='python main.py'),
             40: _entry(parent=1, created=200.0,
                        cmd='.venv\\Scripts\\python.exe main.py')}  # 父更年轻
    kills = _patch_chain_env(monkeypatch)
    proxy._kill_listener_chain(50, procs)
    assert kills == [50]


def test_chain_missing_created_stops(monkeypatch):
    procs = {50: _entry(parent=40, created=100.0, cmd='python main.py'),
             40: _entry(parent=1, created=None,
                        cmd='.venv\\Scripts\\python.exe main.py')}
    kills = _patch_chain_env(monkeypatch)
    proxy._kill_listener_chain(50, procs)
    assert kills == [50]


def test_chain_missing_cmd_stops(monkeypatch):
    procs = {50: _entry(parent=40, created=100.0, cmd=''),
             40: _entry(parent=1, created=90.0, cmd='')}   # cmdline_one 也空
    kills = _patch_chain_env(monkeypatch)
    proxy._kill_listener_chain(50, procs)
    assert kills == [50]


# ---------------------------------------------------------------------------
# prestart_cleanup 集成
# ---------------------------------------------------------------------------

def test_clean_path_completes(monkeypatch, capsys):
    _patch_world(monkeypatch, {}, _Listeners({}))
    proxy.prestart_cleanup()
    assert '残留清理完成' in capsys.readouterr().out


def test_foreign_fingerprint_hit_auto_releases(monkeypatch, capsys):
    procs = {
        100: _entry(parent=90, created=100.0,
                    cmd='"F:\\llvm\\anaconda3\\python.exe" main.py'),
        90: _entry(parent=1, created=90.0,
                   cmd='.venv\\Scripts\\python.exe main.py'),
    }
    listeners = _Listeners({3003: [100]})
    kills, probes = _patch_world(
        monkeypatch, procs, listeners,
        probe_results=[(True, '/openapi.json openapi 标题匹配')])
    proxy.prestart_cleanup()
    assert kills == [90]                       # 链杀顶层包装，/T 连带 100
    assert probes == [('MCP', 3003)]
    assert listeners.by_port[3003] == []
    out = capsys.readouterr().out
    assert '[AUTO]' in out and '指纹确认' in out
    assert '残留清理完成' in out


def test_foreign_fingerprint_miss_exits(monkeypatch, capsys):
    procs = {100: _entry(parent=90, created=100.0, cmd='node server.js'),
             90: _entry(parent=1, created=90.0, cmd='npm start')}
    listeners = _Listeners({1234: [100]})
    kills, probes = _patch_world(
        monkeypatch, procs, listeners,
        probe_results=[(False, "/ status=200 body=b'x'")])
    with pytest.raises(SystemExit) as ei:
        proxy.prestart_cleanup()
    assert ei.value.code == 1
    assert kills == []                         # 宁少勿误杀：不匹配绝不杀
    assert probes == [('智能写作(SE)', 1234)]
    out = capsys.readouterr().out
    assert '[ERROR]' in out and '手动释放' in out


def test_probe_crash_treated_as_miss(monkeypatch):
    procs = {100: _entry(parent=90, created=100.0, cmd='x'),
             90: _entry(parent=1, created=90.0, cmd='.venv main.py')}
    listeners = _Listeners({3003: [100]})
    kills, _ = _patch_world(monkeypatch, procs, listeners)

    def boom(role, port):
        raise RuntimeError('boom')

    monkeypatch.setattr(proxy, '_probe_fingerprint', boom)
    with pytest.raises(SystemExit):
        proxy.prestart_cleanup()
    assert kills == []


def test_verify_off_keeps_old_refuse(monkeypatch):
    monkeypatch.setenv('PROXY_AUTOCLEAN_VERIFY', '0')
    procs = {100: _entry(parent=90, created=100.0, cmd='x'),
             90: _entry(parent=1, created=90.0, cmd='.venv main.py')}
    listeners = _Listeners({3003: [100]})
    kills, probes = _patch_world(monkeypatch, procs, listeners)
    with pytest.raises(SystemExit):
        proxy.prestart_cleanup()
    assert probes == []                        # 完全没探测
    assert kills == []


def test_kill_retry_recovers(monkeypatch):
    procs = {100: _entry(parent=90, created=100.0,
                         cmd='"python.exe" main.py'),
             90: _entry(parent=1, created=90.0,
                        cmd='.venv\\Scripts\\python.exe main.py')}
    listeners = _Listeners({3003: [100]})
    kills, _ = _patch_world(
        monkeypatch, procs, listeners,
        kill_results=[(False, 'Access is denied'), (True, '')],
        probe_results=[(True, 'openapi 标题匹配')])
    proxy.prestart_cleanup()                   # 一次重试后成功，不退出
    assert kills == [90, 90]
    assert listeners.by_port[3003] == []


def test_kill_twice_fail_exits(monkeypatch, capsys):
    procs = {100: _entry(parent=90, created=100.0,
                         cmd='"python.exe" main.py'),
             90: _entry(parent=1, created=90.0,
                        cmd='.venv\\Scripts\\python.exe main.py')}
    listeners = _Listeners({3003: [100]})
    kills, _ = _patch_world(
        monkeypatch, procs, listeners,
        kill_results=[(False, 'Access is denied'), (False, 'Access is denied')],
        probe_results=[(True, 'openapi 标题匹配')])
    with pytest.raises(SystemExit):
        proxy.prestart_cleanup()
    assert kills == [90, 90]                   # 恰一次重试
    out = capsys.readouterr().out
    assert '释放未确认' in out and 'Access is denied' in out


def test_orphan_path_unchanged_no_probe(monkeypatch, capsys):
    procs = {100: _entry(parent=0, created=100.0, cmd='whatever')}  # 父 0=孤儿
    listeners = _Listeners({3003: [100]})
    kills, probes = _patch_world(monkeypatch, procs, listeners)
    proxy.prestart_cleanup()
    assert kills == [100]                      # 孤儿照旧无条件杀
    assert probes == []                        # 不过指纹（既有行为不变）
    assert '清理孤儿残留' in capsys.readouterr().out


def test_backend_role_takeover(monkeypatch, capsys):
    # BACKEND_URL 指 3002 形态：dev 后端（cmd /c 包装的 python app.py）被接管
    monkeypatch.setattr(proxy, 'BACKEND_URL', 'http://127.0.0.1:3002')
    procs = {30: _entry(parent=20, created=30.0, cmd='python  app.py'),
             20: _entry(parent=1, created=20.0,
                        cmd='cmd.exe /d /s /c cd server && python app.py')}
    listeners = _Listeners({3002: [30]})
    kills, probes = _patch_world(
        monkeypatch, procs, listeners,
        probe_results=[(True, '/__fingerprint_probe__ 401 未登录匹配')],
        backend_url='http://127.0.0.1:3002')
    proxy.prestart_cleanup()
    assert kills == [20]                       # cmd /c 是顶层包装
    assert probes == [('BACKEND_URL', 3002)]
    assert listeners.by_port[3002] == []
    assert '[AUTO]' in capsys.readouterr().out


def test_mixed_release_and_miss_exits(monkeypatch):
    procs = {100: _entry(parent=90, created=100.0, cmd='x'),
             90: _entry(parent=1, created=90.0, cmd='.venv main.py'),
             200: _entry(parent=190, created=100.0, cmd='y'),
             190: _entry(parent=1, created=90.0, cmd='npm start')}
    listeners = _Listeners({3003: [100], 1234: [200]})
    kills, probes = _patch_world(
        monkeypatch, procs, listeners,
        probe_results=[(True, 'ok'), (False, 'nope')])
    with pytest.raises(SystemExit):
        proxy.prestart_cleanup()
    assert kills == [90]                       # MCP 放掉了；SE 未杀 → 拒启
    assert probes == [('MCP', 3003), ('智能写作(SE)', 1234)]


def test_proxy_port_stranger_still_exits(monkeypatch):
    # 第①步既有行为守护：PROXY_PORT 陌生占用不经过指纹，直接拒启
    procs = {500: _entry(parent=1, created=100.0, cmd='nginx.exe')}
    listeners = _Listeners({8080: [500]})
    kills, probes = _patch_world(monkeypatch, procs, listeners)
    with pytest.raises(SystemExit):
        proxy.prestart_cleanup()
    assert kills == [] and probes == []
