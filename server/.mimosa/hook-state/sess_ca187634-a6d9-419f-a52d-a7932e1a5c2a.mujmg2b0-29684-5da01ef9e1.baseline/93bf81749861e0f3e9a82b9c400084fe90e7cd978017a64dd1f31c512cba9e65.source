import psycopg2.extras
from concurrent.futures import ThreadPoolExecutor
from db import get_db
from utils.sequences import allocate_sequence


def _setup(coll='zzalloc'):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        conn.commit()
    return coll


def test_allocate_basic_and_format():
    coll = _setup()
    with get_db() as conn:
        cur = conn.cursor()
        vals = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)
        conn.commit()
    assert vals == ['IC-001']
    with get_db() as conn:
        cur = conn.cursor()
        vals = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=2)
        conn.commit()
    assert vals == ['IC-002', 'IC-003']


def test_allocate_seeds_from_existing():
    coll = _setup('zzalloc2')
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,'main')",
                    ('x', coll, psycopg2.extras.Json({'code': 'IC-050'})))
        conn.commit()
    with get_db() as conn:
        cur = conn.cursor()
        vals = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)
        conn.commit()
    assert vals == ['IC-051']


def test_allocate_concurrent_no_dup():
    coll = _setup('zzalloc3')
    def one(_):
        with get_db() as conn:
            cur = conn.cursor()
            v = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 4, count=1)
            conn.commit()
            return v[0]
    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(one, range(40)))
    assert len(set(results)) == 40


def test_allocate_concurrent_batches_no_overlap():
    coll = _setup('zzalloc4')
    def batch(_):
        with get_db() as conn:
            cur = conn.cursor()
            vals = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 4, count=3)
            conn.commit()
            return vals
    with ThreadPoolExecutor(max_workers=10) as ex:
        all_vals = [v for sub in ex.map(batch, range(20)) for v in sub]
    assert len(all_vals) == 60
    assert len(set(all_vals)) == 60  # 20 个并发批次、每批 3 个，全程无重叠
