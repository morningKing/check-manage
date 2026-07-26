"""
集成测试：batch-create 的主键唯一性批量检查

背景：routes/dynamic.py::batch_create_items 原本对每条待写入记录单独调用一次
check_primary_key_unique（一条记录一次 SQL，且 data->>field 没有索引支持），
大批量导入时随着已写入行数增长产生 O(N²) 的未索引 JSONB 扫描——这是"多开
窗口导入大数据把服务拖垮、CPU 跑满"的根因。

find_existing_pk_conflicts 把这个检查从"每条记录一次查询"改成"整批一次查询"
（模仿同文件里已有的 id 批量查询模式），这里直接测这个函数（不经过 Flask/鉴权），
用真实数据库验证 JSONB 提取 + NULL 语义 + 复合主键的正确性。

用真实 DB（不 mock 游标）：JSONB 提取表达式、NULL 语义这类查询语义用 mock
验证不出真实置信度。测试数据用 `_t_` 前缀自建自清理。
"""
import sys
import os
import json

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest import mock  # noqa: E402

from config import DB_CONFIG  # noqa: E402
import db as _db_module  # noqa: E402
import routes.dynamic as dynamic_module  # noqa: E402
from routes.dynamic import find_existing_pk_conflicts, _pk_key_for_data  # noqa: E402
from auth import create_token  # noqa: E402

COLLECTION = '_t_pk_batch_check'
ROUTE_COLLECTION = '_t_pk_batch_route'


@pytest.fixture
def db():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (COLLECTION,))
    conn.commit()
    yield conn
    cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (COLLECTION,))
    conn.commit()
    conn.close()


def _seed(cur, conn, rid, data):
    cur.execute(
        "INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, 'main')",
        (rid, COLLECTION, json.dumps(data)),
    )
    conn.commit()


class TestSingleFieldPk:
    def test_no_existing_rows_no_conflicts(self, db):
        cur = db.cursor()
        result = find_existing_pk_conflicts(
            cur, COLLECTION, [{'code': 'A'}, {'code': 'B'}], ['code'], branch_id='main',
        )
        assert result == {}

    def test_detects_conflict_with_existing_row(self, db):
        cur = db.cursor()
        _seed(cur, db, '_t_existing_1', {'code': 'DUP', 'name': '旧记录'})

        result = find_existing_pk_conflicts(
            cur, COLLECTION, [{'code': 'DUP'}, {'code': 'NEW'}], ['code'], branch_id='main',
        )

        dup_key = _pk_key_for_data({'code': 'DUP'}, ['code'])
        new_key = _pk_key_for_data({'code': 'NEW'}, ['code'])
        assert result.get(dup_key) == {'_t_existing_1'}
        assert new_key not in result

    def test_scoped_to_collection_and_branch(self, db):
        """同一个 code 值在别的 collection 或别的 branch 里已存在，不应算冲突。"""
        cur = db.cursor()
        cur.execute(
            "INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, 'main')",
            ('_t_other_coll_row', '_t_pk_batch_check_other', json.dumps({'code': 'X'})),
        )
        cur.execute(
            "INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, 'feature-branch')",
            ('_t_other_branch_row', COLLECTION, json.dumps({'code': 'X'})),
        )
        db.commit()
        try:
            result = find_existing_pk_conflicts(
                cur, COLLECTION, [{'code': 'X'}], ['code'], branch_id='main',
            )
            assert result == {}
        finally:
            cur.execute("DELETE FROM dynamic_data WHERE collection = %s", ('_t_pk_batch_check_other',))
            db.commit()


class TestCompositePk:
    def test_conflict_requires_all_fields_to_match(self, db):
        cur = db.cursor()
        _seed(cur, db, '_t_existing_2', {'region': 'CN', 'code': 'A'})

        result = find_existing_pk_conflicts(
            cur, COLLECTION,
            [
                {'region': 'CN', 'code': 'A'},   # 两个字段都匹配 -> 冲突
                {'region': 'US', 'code': 'A'},   # 只有 code 匹配 -> 不冲突
            ],
            ['region', 'code'], branch_id='main',
        )

        matching_key = _pk_key_for_data({'region': 'CN', 'code': 'A'}, ['region', 'code'])
        partial_key = _pk_key_for_data({'region': 'US', 'code': 'A'}, ['region', 'code'])
        assert result.get(matching_key) == {'_t_existing_2'}
        assert partial_key not in result


class TestNullHandling:
    def test_null_matches_null(self, db):
        """两条记录的主键字段都是 None（未填写），按旧的逐条实现语义视为冲突
        （data->>field IS NULL 匹配），这里批量实现要保持同样的行为。"""
        cur = db.cursor()
        _seed(cur, db, '_t_existing_null', {'code': None})

        result = find_existing_pk_conflicts(
            cur, COLLECTION, [{'code': None}], ['code'], branch_id='main',
        )

        null_key = _pk_key_for_data({'code': None}, ['code'])
        assert result.get(null_key) == {'_t_existing_null'}

    def test_null_does_not_match_empty_string(self, db):
        """None 和空字符串 '' 是两个不同的比较键，不应互相误判为冲突。"""
        cur = db.cursor()
        _seed(cur, db, '_t_existing_empty', {'code': ''})

        result = find_existing_pk_conflicts(
            cur, COLLECTION, [{'code': None}], ['code'], branch_id='main',
        )

        null_key = _pk_key_for_data({'code': None}, ['code'])
        empty_key = _pk_key_for_data({'code': ''}, ['code'])
        assert null_key != empty_key
        assert null_key not in result  # 数据库里存的是 '' 不是 None，不该匹配


class TestMultipleExistingRowsShareSameKey:
    def test_returns_all_ids_sharing_the_key(self, db):
        """历史脏数据：两条已有记录碰巧共享同一个主键值组合（正常不该发生，
        但如果发生了，批量检查也要能找出全部，不能因为只取一条而漏判。"""
        cur = db.cursor()
        _seed(cur, db, '_t_dup_a', {'code': 'SHARED'})
        _seed(cur, db, '_t_dup_b', {'code': 'SHARED'})

        result = find_existing_pk_conflicts(
            cur, COLLECTION, [{'code': 'SHARED'}], ['code'], branch_id='main',
        )

        key = _pk_key_for_data({'code': 'SHARED'}, ['code'])
        assert result.get(key) == {'_t_dup_a', '_t_dup_b'}


class _CountingCursorProxy:
    """包一层真实游标，只计数 execute 调用次数——psycopg2 的 C 级游标对象上
    execute 是只读属性，不能直接 monkeypatch 实例方法，所以用一个转发代理。"""

    def __init__(self, real_cursor):
        self._real_cursor = real_cursor
        self.execute_count = 0

    def execute(self, *args, **kwargs):
        self.execute_count += 1
        return self._real_cursor.execute(*args, **kwargs)

    def fetchall(self, *args, **kwargs):
        return self._real_cursor.fetchall(*args, **kwargs)

    def fetchone(self, *args, **kwargs):
        return self._real_cursor.fetchone(*args, **kwargs)


class TestQueryCountStaysConstant:
    def test_one_query_regardless_of_batch_size(self, db):
        """证明修复了 O(N) 逐条查询：不管候选记录有多少条，只发一次 SQL。"""
        cur = _CountingCursorProxy(db.cursor())

        records_data = [{'code': f'row{i}'} for i in range(500)]
        find_existing_pk_conflicts(cur, COLLECTION, records_data, ['code'], branch_id='main')

        assert cur.execute_count == 1

    def test_no_candidates_issues_no_query(self, db):
        cur = _CountingCursorProxy(db.cursor())

        result = find_existing_pk_conflicts(cur, COLLECTION, [], ['code'], branch_id='main')

        assert result == {}
        assert cur.execute_count == 0

    def test_empty_pk_fields_issues_no_query(self, db):
        cur = _CountingCursorProxy(db.cursor())

        result = find_existing_pk_conflicts(cur, COLLECTION, [{'code': 'A'}], [], branch_id='main')

        assert result == {}
        assert cur.execute_count == 0


@pytest.fixture
def route_client():
    """真实 test client：rebind 各模块的 get_db 回真实实现（不 mock），用来验证
    batch_create_items 路由层是否真的接上了 find_existing_pk_conflicts（而不是
    仍然停留在修复前逐条调用 check_primary_key_unique 的旧路径）。"""
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
            mod_name.startswith('routes.') or mod_name.startswith('utils.') or mod_name == 'auth'
        ):
            try:
                mod.get_db = _db_module.get_db
            except (AttributeError, TypeError):
                pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _admin_headers():
    token = create_token({'id': 'admin', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {token}'}


def _cleanup_route_collection():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM dynamic_data WHERE collection = %s", (ROUTE_COLLECTION,))
    cur.execute("DELETE FROM page_configs WHERE id = %s", (f'page-{ROUTE_COLLECTION}',))
    conn.commit()
    conn.close()


def _seed_route_page():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    fields = [
        {'fieldName': 'code', 'label': 'Code', 'controlType': 'text', 'isPrimaryKey': True},
        {'fieldName': 'name', 'label': 'Name', 'controlType': 'text'},
    ]
    cur.execute(
        "INSERT INTO page_configs (id, name, fields) VALUES (%s, %s, %s)",
        (f'page-{ROUTE_COLLECTION}', ROUTE_COLLECTION, json.dumps(fields)),
    )
    conn.commit()
    conn.close()


def _seed_route_record(rid, data):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, 'main')",
        (rid, ROUTE_COLLECTION, json.dumps(data)),
    )
    conn.commit()
    conn.close()


class TestBatchCreateRouteUsesPkBatching:
    """回归测试：batch_create_items 必须用 find_existing_pk_conflicts 整批检查
    主键唯一性，而不是修复前对每条候选记录调用一次 check_primary_key_unique
    （大批量导入时随写入行数增长产生 O(N^2) 未索引 JSONB 扫描，是"多开窗口导入
    大数据把服务拖垮、CPU 跑满"的根因）。"""

    def setup_method(self):
        _cleanup_route_collection()
        _seed_route_page()

    def teardown_method(self):
        _cleanup_route_collection()

    def test_pk_conflict_reported_and_independent_record_still_created(self, route_client):
        _seed_route_record('_t_pk_route_existing', {'code': 'DUP', 'name': 'old'})

        resp = route_client.post(
            f'/{ROUTE_COLLECTION}/batch-create',
            json={
                'records': [
                    {'id': '_t_pk_route_new1', 'data': {'code': 'DUP', 'name': 'new-conflict'}},
                    {'id': '_t_pk_route_new2', 'data': {'code': 'FRESH', 'name': 'ok'}},
                ],
                'options': {'continueOnError': True},
            },
            headers=_admin_headers(),
        )

        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['created'] == 1
        assert body['failed'] == 1
        assert body['errors'][0]['error'] == '主键重复：code=DUP'

    def test_update_does_not_self_flag_as_conflict(self, route_client):
        _seed_route_record('_t_pk_route_upd', {'code': 'SAME', 'name': 'old'})

        resp = route_client.post(
            f'/{ROUTE_COLLECTION}/batch-create',
            json={
                'records': [
                    {'id': '_t_pk_route_upd', 'data': {'code': 'SAME', 'name': 'updated'}},
                ],
                'options': {'continueOnError': True},
            },
            headers=_admin_headers(),
        )

        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['updated'] == 1
        assert body['failed'] == 0

    def test_pk_check_issues_one_batched_call_not_per_record(self, route_client):
        """证明路由层真的接上了批量检查——不管批次里有多少条候选记录，
        find_existing_pk_conflicts 只被调用一次，而不是逐条调用旧的
        check_primary_key_unique。"""
        records = [{'id': f'_t_pk_route_r{i}', 'data': {'code': f'row{i}'}} for i in range(50)]

        with mock.patch.object(
            dynamic_module, 'find_existing_pk_conflicts',
            wraps=dynamic_module.find_existing_pk_conflicts,
        ) as spy:
            resp = route_client.post(
                f'/{ROUTE_COLLECTION}/batch-create',
                json={'records': records, 'options': {'continueOnError': True}},
                headers=_admin_headers(),
            )

        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert spy.call_count == 1
