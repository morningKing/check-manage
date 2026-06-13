import uuid
import psycopg2.extras
from concurrent.futures import ThreadPoolExecutor
from db import get_db
from routes.dynamic import acquire_pk_lock, check_primary_key_unique


def _setup(coll='zzcreate'):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        conn.commit()
    return coll


def _try_create_locked(coll, pk_value):
    """模拟 create_item 的「advisory lock → 唯一检查 → 插入」段。返回 'ok' 或 'dup'。"""
    with get_db() as conn:
        cur = conn.cursor()
        acquire_pk_lock(cur, coll, {'code': pk_value})
        if check_primary_key_unique(cur, coll, {'code': pk_value}, ['code'], branch_id='main'):
            conn.commit()
            return 'dup'
        rid = f'{coll}-{uuid.uuid4().hex[:8]}'
        cur.execute("INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,'main')",
                    (rid, coll, psycopg2.extras.Json({'code': pk_value})))
        conn.commit()
        return 'ok'


def test_advisory_lock_serializes_same_pk():
    coll = _setup()
    def one(_):
        return _try_create_locked(coll, 'PK-1')
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(one, range(20)))
    assert results.count('ok') == 1
    assert results.count('dup') == 19
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM dynamic_data WHERE collection=%s AND data->>'code'='PK-1'", (coll,))
        assert cur.fetchone()[0] == 1
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        conn.commit()
