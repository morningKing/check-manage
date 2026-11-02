"""性能快改批 ②：waitress 线程 / DB 池上限的代码默认值。

批任务会话多时 SSE 长连接（每打开一个会话 1 条 + 批事件流 1 条）整段占用
waitress 线程，32 线程 ≈16 个页面即饱和，REST 请求在队列里 pending；线程
抬高到 64 的同时 DB 池必须同步抬高（waitress 线程 + 余量 8 给 worker/
租约/SSE tick 借用），且仍低于 PG 默认 max_connections=100。

默认值走代码而非 .env：生产重启即生效（两个部署的 .env 均未覆盖）。
"""
import importlib
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_db_pool_default_72_and_env_override(monkeypatch):
    import db
    monkeypatch.delenv('DB_POOL_MAXCONN', raising=False)
    try:
        reloaded = importlib.reload(db)
        assert reloaded.POOL_MAXCONN == 72, (
            '池上限默认应为 72：BACKEND_THREADS 64 + 余量 8')
        monkeypatch.setenv('DB_POOL_MAXCONN', '90')
        reloaded = importlib.reload(db)
        assert reloaded.POOL_MAXCONN == 90, 'env 覆盖必须仍然生效'
    finally:
        monkeypatch.undo()
        importlib.reload(db)  # 恢复本进程的默认绑定


def test_backend_threads_default_64_and_wired_into_start_backend():
    import proxy
    assert getattr(proxy, 'BACKEND_THREADS_DEFAULT', None) == 64, (
        'waitress 线程默认应为 64（SSE 长连接占用翻倍缓解，须 < DB 池 72）')
    src = inspect.getsource(proxy.start_backend)
    assert 'BACKEND_THREADS_DEFAULT' in src, (
        'start_backend 必须从模块常量取默认值（保持 env 覆盖通路）')
