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


# DB_POOL_MAXCONN：池上限可配。生产 waitress 的 BACKEND_THREADS（2026-10-09
# 起默认 32）必须保持 < 池上限——SSE 长连接每条占一个线程（批事件流 tick 还
# 会瞬时借连接），线程数贴着池上限走会让普通 REST 请求在 POOL_WAIT_SEC 排队。
# 默认 40（+后台 worker/调度线程余量）仍远低于 PG 默认 max_connections=100；
# 再往上调需同步核对 Postgres max_connections。
POOL_MAXCONN = _env_num('DB_POOL_MAXCONN', 40, int)
# get_db 在池耗尽时的排队等待窗口（秒）：dev Werkzeug 无界线程下，大量 SSE
# 长连接同相位 tick 会瞬时借满池——有界等待吸收相位碰撞；持续饥饿仍抛
# PoolError，由调用方降级（SSE 路由发 busy 帧而非断流）。
POOL_WAIT_SEC = _env_num('DB_POOL_WAIT_SEC', 1.0, float)


def get_pool():
    global pool
    if pool is None:
        # TCP keepalives：池内连接闲置时会被 NAT/防火墙静默掐断（半开连接），
        # 借到即 500。psycopg2 无 pool_pre_ping（那是 SQLAlchemy 的参数），
        # 正确做法是连接层 TCP keepalive：闲置 30s 后每 10s 探测，3 次失败
        # 即由 OS 判定连接死亡，下一次 getconn 拿到的不会再是死连接。
        pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=POOL_MAXCONN,
            keepalives=1, keepalives_idle=30, keepalives_interval=10,
            keepalives_count=3, **DB_CONFIG)
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
