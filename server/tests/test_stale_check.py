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
                      'findings'}
    assert isinstance(r['listeners'], list)
    assert isinstance(r['findings'], list)
    assert r['serveOwnership'] in ('platform', 'external', 'unknown', 'none')
    assert 'path' in r['ocServeLog'] and 'exists' in r['ocServeLog']
    for it in r['listeners']:
        assert {'pid', 'port', 'role', 'kind'} <= set(it)
