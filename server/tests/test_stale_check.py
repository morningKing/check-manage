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
