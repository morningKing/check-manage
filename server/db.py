import psycopg2
import psycopg2.pool
import psycopg2.extras
from contextlib import contextmanager
from config import DB_CONFIG

pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, **DB_CONFIG)


@contextmanager
def get_db():
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
