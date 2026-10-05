import os
import time

import psycopg2
import psycopg2.pool
import psycopg2.extras
from contextlib import contextmanager
from config import DB_CONFIG

pool = None


def _env_num(name, default, cast):
    try:
        return cast(os.getenv(name, '') or default)
    except (TypeError, ValueError):
        return default


# DB_POOL_MAXCONN：池上限可配。生产 waitress 的 BACKEND_THREADS（默认 8）<
# 默认 20，默认行为不变；高并发场景（如大量 SSE 长连接）调大时需同步核对
# Postgres max_connections。
POOL_MAXCONN = _env_num('DB_POOL_MAXCONN', 20, int)
# get_db 在池耗尽时的排队等待窗口（秒）：dev Werkzeug 无界线程下，大量 SSE
# 长连接同相位 tick 会瞬时借满池——有界等待吸收相位碰撞；持续饥饿仍抛
# PoolError，由调用方降级（SSE 路由发 busy 帧而非断流）。
POOL_WAIT_SEC = _env_num('DB_POOL_WAIT_SEC', 1.0, float)


def get_pool():
    global pool
    if pool is None:
        pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=POOL_MAXCONN, **DB_CONFIG)
    return pool


@contextmanager
def get_db():
    pool = get_pool()
    deadline = time.time() + POOL_WAIT_SEC
    while True:
        try:
            conn = pool.getconn()
            break
        except psycopg2.pool.PoolError:
            if time.time() >= deadline:
                raise
            time.sleep(0.05)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
