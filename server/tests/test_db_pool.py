"""db.py 连接池行为单测：PoolError 排队等待 / 持续饥饿抛出 / maxconn 可配。

全用 FakePool 注入，不连真实库（真实 getconn 成功路径由既有集成用例覆盖）。
背景：压测发现 dev Werkzeug 无界线程下 50 并发 SSE 同相位 tick 瞬时借满
maxconn=20 池 → PoolError → SSE 直接 500（evidence/stress R1，PoolError 102 次）。
"""
import time

import psycopg2.pool
import pytest

import db


class _FakeConn:
    def commit(self):
        pass

    def rollback(self):
        pass


class FakePool:
    def __init__(self, failures: int = 0, always_fail: bool = False):
        self.failures = failures
        self.always_fail = always_fail
        self.getconn_calls = 0
        self.putconn_calls = 0
        self._conn = _FakeConn()

    def getconn(self):
        self.getconn_calls += 1
        if self.always_fail or self.failures > 0:
            self.failures -= 1
            raise psycopg2.pool.PoolError('connection pool exhausted')
        return self._conn

    def putconn(self, conn):
        self.putconn_calls += 1


@pytest.fixture(autouse=True)
def _restore_globals(monkeypatch):
    yield
    monkeypatch.setattr(db, 'pool', None, raising=False)


def test_get_db_waits_through_transient_exhaustion(monkeypatch):
    fake = FakePool(failures=3)
    monkeypatch.setattr(db, 'pool', fake)
    monkeypatch.setattr(db, 'POOL_WAIT_SEC', 1.0)
    with db.get_db() as conn:
        assert conn is fake._conn
    assert fake.putconn_calls == 1
    assert fake.getconn_calls == 4          # 3 次饥饿 + 1 次成功


def test_get_db_raises_after_wait_window_on_sustained_starvation(monkeypatch):
    fake = FakePool(always_fail=True)
    monkeypatch.setattr(db, 'pool', fake)
    monkeypatch.setattr(db, 'POOL_WAIT_SEC', 0.05)
    t0 = time.time()
    with pytest.raises(psycopg2.pool.PoolError):
        with db.get_db():
            pass
    assert time.time() - t0 >= 0.05         # 确实等满窗口才抛
    assert fake.putconn_calls == 0          # 未借到连接不误还


def test_get_db_env_overrides(monkeypatch):
    monkeypatch.setenv('DB_POOL_MAXCONN', '37')
    monkeypatch.setenv('DB_POOL_WAIT_SEC', '2.5')
    import importlib
    importlib.reload(db)
    assert db.POOL_MAXCONN == 37
    assert db.POOL_WAIT_SEC == 2.5


def test_get_pool_uses_pool_maxconn(monkeypatch):
    captured = {}

    def fake_tcp(minconn, maxconn, **kwargs):
        captured['minconn'] = minconn
        captured['maxconn'] = maxconn
        return FakePool()

    monkeypatch.setattr(db, 'pool', None)
    monkeypatch.setattr(db, 'POOL_MAXCONN', 42)
    monkeypatch.setattr(psycopg2.pool, 'ThreadedConnectionPool', fake_tcp)
    db.get_pool()
    assert captured == {'minconn': 2, 'maxconn': 42}
