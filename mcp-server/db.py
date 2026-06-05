"""DB pool for MCP server. Same DSN env vars as Flask `server/config.py`."""

import os
from contextlib import contextmanager
import psycopg2
import psycopg2.extras
import psycopg2.pool

import app_config  # noqa: F401  — loads server/.env before the pool is built

_pool = psycopg2.pool.SimpleConnectionPool(
    1, 5,
    host=os.getenv("DB_HOST", "localhost"),
    dbname=os.getenv("DB_NAME", "casemanage"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASSWORD", "jay123"),
    port=int(os.getenv("DB_PORT", "5432")),
)


@contextmanager
def get_db():
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
