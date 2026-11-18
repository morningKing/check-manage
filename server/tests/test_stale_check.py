"""stale_check 残留体检脚本的轻量测试（分类映射 + collect 形状）。

不 mock 进程扫描：collect() 是只读的（netstat/tasklist/wmic + /proc），
在本机与 CI 均可跑；断言只覆盖形状与分类映射，不依赖具体机器状态。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import stale_check  # noqa: E402


def test_classify_known_roles():
    assert stale_check._classify('python proxy.py') == '代理(proxy)'
    assert stale_check._classify(
        'python -c "import app; from waitress import serve"') == '后端'
    assert stale_check._classify('python app.py') == '后端'
    assert stale_check._classify('opencode.exe serve') == 'OC serve'
    assert stale_check._classify(
        'python -c "from multiprocessing.spawn import spawn_main" '
        '--multiprocessing-fork') == 'python 多进程子进程'
    assert stale_check._classify('some-random-binary --x') == '未知'


def test_port_parsing():
    assert stale_check._port('http://127.0.0.1:3001', 9999) == 3001
    assert stale_check._port('http://host-without-port', 9999) == 9999
    assert stale_check._port('not a url', 9999) == 9999


def test_collect_shape():
    r = stale_check.collect()
    assert set(r) >= {'listeners', 'serveOwnership', 'orphans', 'ocServeLog',
                      'findings', 'serveCreated', 'serveStale', 'newestSpawnTs'}
    assert isinstance(r['listeners'], list)
    assert isinstance(r['findings'], list)
    assert r['serveOwnership'] in ('platform', 'external', 'unknown', 'none')
    assert 'path' in r['ocServeLog'] and 'exists' in r['ocServeLog']
    for it in r['listeners']:
        assert {'pid', 'port', 'role', 'kind'} <= set(it)


def test_serve_stale_logic():
    """新旧判定：无记录=残留；取不到创建时间不判（不误杀）；偏差超容忍=残留。"""
    assert stale_check._serve_stale(None, None) is True
    assert stale_check._serve_stale(1000.0, None) is True
    assert stale_check._serve_stale(None, 1000.0) is False
    assert stale_check._serve_stale(1000.0, 1005.0) is False   # 数秒内一致
    assert stale_check._serve_stale(1000.0, 2000.0) is True


def test_newest_spawn_ts_from_log(tmp_path):
    p = tmp_path / 'oc-serve.log'
    p.write_text('===== oc-serve spawn (proxy) 2026-10-15 10:00:00 =====\n'
                 'noise line\n'
                 '===== oc-serve spawn (restart) 2026-10-15 17:36:15 =====\n',
                 encoding='utf-8')
    ts = stale_check._newest_spawn_ts(str(p))
    assert ts is not None
    import datetime
    assert datetime.datetime.fromtimestamp(ts).strftime(
        '%Y-%m-%d %H:%M:%S') == '2026-10-15 17:36:15'   # 取最近一条
    assert stale_check._newest_spawn_ts(str(tmp_path / 'nope.log')) is None


def test_platform_child_cmd_patterns():
    """平台子进程孤儿判定：backend/MCP/proxy 命中；OC serve 与无关进程不命中
    （serve 按设计在父进程退出后存活，另行判定）。"""
    assert stale_check._platform_child_cmd(
        'F:\\py\\python.exe -c "import app; from waitress import serve"')
    assert stale_check._platform_child_cmd('python app.py')
    assert stale_check._platform_child_cmd('python proxy.py')
    assert stale_check._platform_child_cmd('python mcp-server/main.py')
    assert not stale_check._platform_child_cmd('opencode.exe serve')
    assert not stale_check._platform_child_cmd('python scripts/stale_check.py')
    assert not stale_check._platform_child_cmd('')


def test_parent_alive_semantics():
    """父存活判定（PID 复用感知）：真父必然不晚于子出生；父 pid 被更年轻
    进程复用 = 真父已死判孤儿；created 缺失（POSIX/快照降级）保守按父存活。"""
    procs = {
        100: {'cmd': '', 'parent': 0, 'created': 900.0},
        101: {'cmd': '', 'parent': 100, 'created': 1000.0},
        200: {'cmd': '', 'parent': 0, 'created': 5000.0},
        201: {'cmd': '', 'parent': 200, 'created': 1000.0},
    }
    # 子自身不在快照 / 无父 → 判父已死（走孤儿分支）
    assert stale_check.parent_alive(102, procs) is False
    # 真父存活
    assert stale_check.parent_alive(101, procs) is True
    # 父 pid 被更年轻进程顶名 = 真父已死（2026-11 生产误判的实锤形态）
    assert stale_check.parent_alive(201, procs) is False
    # created 缺失 → 保守按父存活（宁少勿误杀）
    procs[300] = {'cmd': '', 'parent': 100, 'created': None}
    assert stale_check.parent_alive(300, procs) is True
    # +2s 容差：父比子早 1s 出生仍是真父
    procs[400] = {'cmd': '', 'parent': 401, 'created': 1000.0}
    procs[401] = {'cmd': '', 'parent': 0, 'created': 999.0}
    assert stale_check.parent_alive(400, procs) is True
    # 父比子晚 3.5s 出生 = 复用
    procs[500] = {'cmd': '', 'parent': 501, 'created': 1000.0}
    procs[501] = {'cmd': '', 'parent': 0, 'created': 1003.5}
    assert stale_check.parent_alive(500, procs) is False


def test_collect_flags_platform_orphan_with_reused_parent(monkeypatch):
    """collect 的「上一轮遗留平台子进程」接入 PID 复用感知：父 pid 被年轻
    无关进程顶名的 waitress 残留也要被标出（旧口径 `parent in procs` 恒真
    → 恒漏报）。"""
    procs = {
        555: {'cmd': 'F:\\py\\python.exe -c '
                     '"import app; from waitress import serve"',
              'parent': 777, 'created': 1000.0},
        777: {'cmd': 'chrome.exe --unrelated', 'parent': 0, 'created': 5000.0},
    }
    monkeypatch.setattr(stale_check, 'snapshot_processes', lambda: dict(procs))
    monkeypatch.setattr(stale_check, 'serve_listener_pids', lambda port: [])

    r = stale_check.collect(verbose=False)

    assert {o['pid'] for o in r['platformOrphans']} == {555}


def test_normalize_wmic_text_strips_leading_blank():
    """wmic 输出以 \\r\\r\\n 开头——不剥掉会让 CSV 首行空串当表头（键全 None）。"""
    t = stale_check._normalize_wmic_text('\r\r\nNode,A\r\r\nX,1\r\r\n')
    assert t.startswith('Node,A')
    assert t.endswith('X,1\n')


def test_parse_wmic_list_commas_and_continuations():
    """列表格式：值内逗号安全（CSV 会拆坏 waitress 命令行）+ 续行拼回上键。"""
    text = ('\r\r\n'
            'CommandLine=F:\\x\\python.exe -c "import app; serve(host=\\"0.0.0.0\\", '
            'port=3002, threads=32)"\r\r\n'
            'ProcessId=41472\r\r\n'
            '\r\r\n'
            'CommandLine=multi line\r\r\n  continued part\r\r\n'
            'ProcessId=999\r\r\n')
    recs = stale_check._parse_wmic_list(text)
    assert len(recs) == 2
    assert recs[0]['ProcessId'] == '41472'
    assert 'port=3002, threads=32' in recs[0]['CommandLine']
    assert recs[1]['CommandLine'] == 'multi line continued part'
