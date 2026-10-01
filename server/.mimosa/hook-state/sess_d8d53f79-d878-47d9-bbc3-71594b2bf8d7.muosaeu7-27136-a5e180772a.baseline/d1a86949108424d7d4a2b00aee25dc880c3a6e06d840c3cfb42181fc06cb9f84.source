# -*- coding: utf-8 -*-
"""快速重启场景回归：租约被占时 start() 进入重试模式，旧租约释放后自动接管
（修复前：TTL 窗口内重启会让新实例永久放弃 dispatcher，批次永远 pending）。"""
import time as _time
import uuid

import pytest

pytest.importorskip('psycopg2')


def test_start_retries_until_lease_released(db_conn):
    from utils.batch_engine import BatchWorker
    from utils import execution_lease
    key = 'batch-test-' + uuid.uuid4().hex[:8]
    owner_old = execution_lease.owner_id()
    ok, _ = execution_lease.acquire(key, owner_old, ttl_sec=300)
    assert ok
    try:
        w = BatchWorker()
        w._lease_key = key
        # 重试间隔调短，模拟"旧租约很快过期"
        w.ACQUIRE_RETRY_SEC = 0.2
        w.start()
        assert w._holds_lease is False
        assert w._dispatcher is None
        assert w._acquire_retry_thread is not None and w._acquire_retry_thread.is_alive()

        # 旧实例释放租约 → 重试线程自动接管
        execution_lease.release(key, owner_old)
        deadline = _time.time() + 10
        while _time.time() < deadline and not w._holds_lease:
            _time.sleep(0.1)
        assert w._holds_lease is True
        assert w._dispatcher is not None and w._dispatcher.is_alive()
        w.stop()
    finally:
        execution_lease.release(key, owner_old)
