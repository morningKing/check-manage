"""反向代理层的请求体上限（`proxy.py`）。

为什么这层必须单独有测试：`proxy.py:_proxy_to_backend` 在转发前用
`self.rfile.read(content_length)` 把整个请求体**同步读进代理进程内存**，这一步
发生在 Flask 的 `before_request` 之前。只在 Flask 里设限时，那道 413 门在直连
后端 / 经 Vite 开发代理时有效，经生产入口 `:8080` 时被完全绕过。

这些测试钉住三件事：
1. 上限数值只有一份（Flask 与 proxy 都读 utils/upload_limits）；
2. 限制**只**作用于 AI 批任务对外端点 —— 尤其备份还原必须无上限；
3. 超限请求在 `rfile.read()` **被调用之前**就被拒（用一个"一读就爆"的 rfile 证明）。
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.upload_limits import (MAX_JSON_BODY_BYTES, MAX_UPLOAD_REQUEST_BYTES,
                                 body_limit_for_path)


# ---------- 上限归属：谁受限、谁绝不能受限 ----------

@pytest.mark.parametrize('path, expected', [
    # 公网路径（代理看到的）
    ('/api/v1/ai-batches/uploads', MAX_UPLOAD_REQUEST_BYTES),
    ('/api/v1/ai-batches', MAX_JSON_BODY_BYTES),
    ('/api/v1/ai-batches/b-1/retry-failed', MAX_JSON_BODY_BYTES),
    ('/api/v1/ai-batches/b-1/results?x=1', MAX_JSON_BODY_BYTES),
    # 后端路径（Flask 看到的，proxy 已剥掉 /api）
    ('/v1/ai-batches/uploads', MAX_UPLOAD_REQUEST_BYTES),
    ('/v1/ai-batches', MAX_JSON_BODY_BYTES),
])
def test_batch_endpoints_are_limited(path, expected):
    assert body_limit_for_path(path) == expected


@pytest.mark.parametrize('path', [
    # ⚠️ 这条是本次修法的核心约束：备份还原的 ZIP 含 vector_store/ 与
    # data_files/，大小本质无上界，代理层给它设任何上限都会打断灾难恢复。
    '/api/backups/upload-restore',
    '/api/data-files/upload',
    '/api/ai/chat/batches/staging/upload',
    '/api/v1/collections/orders/records',
    '/api/menus',
    '/',
    '/api/v1/ai-batches-lookalike/uploads',   # 前缀相似但不是本套接口
])
def test_other_paths_are_unlimited(path):
    assert body_limit_for_path(path) is None


def test_flask_and_proxy_read_the_same_numbers():
    """改一处、两边一致 —— routes 层不能自己再抄一份数字。"""
    import routes.open_api_batches as r
    import utils.upload_limits as lim
    assert r.MAX_UPLOAD_REQUEST_BYTES is lim.MAX_UPLOAD_REQUEST_BYTES
    assert r.MAX_JSON_BODY_BYTES is lim.MAX_JSON_BODY_BYTES
    assert r.MAX_UPLOAD_TOTAL_BYTES is lim.MAX_UPLOAD_TOTAL_BYTES


# ---------- 代理 handler：拒绝发生在 rfile.read() 之前 ----------

class _ExplodingReader:
    """一被 read 就炸 —— 用来证明超限请求的 body 根本没被读过。"""

    def read(self, *args, **kwargs):
        raise AssertionError('rfile.read() 不该被调用：超限请求必须在读 body 之前被拒')


class _DrainableReader(_ExplodingReader):
    """收尾的有界 lingering drain 允许读一次（有界、带超时），单独放行。"""

    def __init__(self):
        self.drained = 0

    def read(self, n=-1):
        self.drained += 1
        return b''


class _FakeSocket:
    def __init__(self):
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t


def _make_handler(path, content_length, rfile):
    """不走 __init__（那会真的去 accept 一个连接）造一个 ProxyHandler 实例。"""
    import http.client
    import proxy as proxy_mod

    h = proxy_mod.ProxyHandler.__new__(proxy_mod.ProxyHandler)
    h.path = path
    h.command = 'POST'
    h.request_version = 'HTTP/1.1'
    h.protocol_version = 'HTTP/1.1'
    h.requestline = f'POST {path} HTTP/1.1'
    h.client_address = ('127.0.0.1', 12345)
    h.headers = http.client.HTTPMessage()
    h.headers['Content-Length'] = str(content_length)
    h.rfile = rfile
    h.wfile = io.BytesIO()
    h.connection = _FakeSocket()
    h.close_connection = False
    return h


def test_proxy_rejects_oversized_batch_upload_before_reading_body():
    r = _DrainableReader()
    h = _make_handler('/api/v1/ai-batches/uploads',
                      MAX_UPLOAD_REQUEST_BYTES + 1, r)
    h._proxy_to_backend()

    out = h.wfile.getvalue().decode('utf-8', errors='replace')
    assert out.startswith('HTTP/1.1 413'), out
    assert 'Connection: close' in out
    assert h.close_connection is True          # keep-alive 错位不可能发生
    assert r.drained == 1                      # 只有收尾那次有界 drain
    assert h.connection.timeout == h.LINGER_DRAIN_SECONDS   # drain 带超时


def test_proxy_rejects_oversized_json_body():
    h = _make_handler('/api/v1/ai-batches', MAX_JSON_BODY_BYTES + 1,
                      _DrainableReader())
    h._proxy_to_backend()
    assert h.wfile.getvalue().decode('utf-8', errors='replace').startswith('HTTP/1.1 413')


def test_proxy_does_not_limit_backup_restore():
    """10 GB 的备份还原请求必须照常走转发路径（这里以"确实去读 body 了"为证）。

    _ExplodingReader 一读就抛 AssertionError：抛出来恰恰说明代理没有提前 413，
    而是走到了 rfile.read() 那一步 —— 正是备份还原需要的行为。
    """
    h = _make_handler('/api/backups/upload-restore', 10 * 1024 ** 3,
                      _ExplodingReader())
    with pytest.raises(AssertionError, match='不该被调用'):
        h._proxy_to_backend()
    assert h.wfile.getvalue() == b''   # 没有写过任何 413 响应
