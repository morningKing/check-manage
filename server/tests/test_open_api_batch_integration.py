"""集成测试：Open API 批量新增记录 — 跨集合 ID 冲突回归测试。

背景（review 发现的 Important bug）：create_batch_items 的存在性预检查曾按
collection 过滤（`WHERE collection = %s AND id = ANY(%s) AND branch_id = %s`），
但 dynamic_data 的真实主键是 (id, branch_id)，并不包含 collection —— id 在同一
branch 内必须全局唯一，不是仅在单个 collection 内唯一。跨集合 id 冲突时，旧的
预检查查不到冲突（误判为可用），流程继续到 execute_values 的 INSERT，才在真实
主键约束上炸出未捕获的 psycopg2.errors.UniqueViolation，导致整个事务回滚 ——
其余原本合法的记录也一起丢失，直接违背 continueOnError=true「部分成功」的语义。

此文件走真实 DB（不 mock 游标），与 test_dynamic_keyword_search.py /
test_open_api_files.py 同模式：测试数据用 `_t_` 前缀自建自清理。
"""
import os
import sys

import psycopg2.extras
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db_module  # noqa: E402
from db import get_db  # noqa: E402
from auth import hash_api_key  # noqa: E402

API_KEY = 'cm_test_batch_integration_key_123'
API_KEY_ID = '_t_batch_api_key'
COLL_A = '_t_batch_a'
COLL_B = '_t_batch_b'
SHARED_ID = '_t_shared_id_1'


@pytest.fixture
def client():
    """真实 test client：rebind 所有已导入模块的 get_db 回真实实现（不 mock）。"""
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


def _api_headers():
    return {'X-API-Key': API_KEY}


def _cleanup():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM api_keys WHERE id = %s", (API_KEY_ID,))
        cur.execute("DELETE FROM dynamic_data WHERE collection IN (%s, %s)", (COLL_A, COLL_B))
        cur.execute("DELETE FROM page_configs WHERE id IN (%s, %s)",
                    (f'page-{COLL_A}', f'page-{COLL_B}'))
        conn.commit()


def _seed(cur):
    """创建 collection A 的 page_config（公开+可写）+ collection B 里一条已存在的行
    （id = SHARED_ID，与 A 的批量请求中的一个 id 冲突，模拟跨集合 id 碰撞）。"""
    cur.execute("DELETE FROM api_keys WHERE id = %s", (API_KEY_ID,))
    cur.execute("INSERT INTO api_keys (id, name, key_hash, is_active) VALUES (%s,%s,%s,TRUE)",
                (API_KEY_ID, 'batch-integration-test', hash_api_key(API_KEY)))

    fields = [{'fieldName': 'name', 'label': '名称', 'controlType': 'text', 'required': False}]
    cur.execute(
        "INSERT INTO page_configs (id, name, fields, api_public, api_writable) VALUES (%s,%s,%s,%s,%s)",
        (f'page-{COLL_A}', COLL_A, psycopg2.extras.Json(fields), True, True),
    )

    # collection B 只需要 dynamic_data 里存在这一行即可制造冲突，不需要完整 page_config。
    cur.execute(
        "INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s,%s,%s,'main')",
        (SHARED_ID, COLL_B, psycopg2.extras.Json({'name': 'existing in other collection'})),
    )


def test_cross_collection_id_collision_reported_as_clean_error_not_500(client):
    """批量新增到 collection A，其中一条 id 与 collection B 里已存在的行冲突。

    修复前：预检查按 collection 过滤，查不到 B 里的冲突行 -> 误判可插入 ->
    execute_values 在真实 (id, branch_id) 主键上炸 UniqueViolation -> 未捕获异常
    -> 整批（含另一条本应成功的记录）全部丢失。
    修复后：预检查不再按 collection 过滤，能查到跨集合冲突 -> 该记录被判定为
    'Record ID already exists' 干净失败，另一条记录正常创建（continueOnError=true
    的部分成功语义得到保证）。
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed(cur)
            conn.commit()

        resp = client.post(
            f'/api/v1/collections/{COLL_A}/batch',
            json={
                'records': [
                    {'id': SHARED_ID, 'name': 'colliding record'},
                    {'name': 'independent record'},
                ],
                'options': {'continueOnError': True},
            },
            headers=_api_headers(),
        )

        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['created'] == 1
        assert body['failed'] == 1
        assert len(body['errors']) == 1
        assert body['errors'][0]['error'] == 'Record ID already exists'
        assert len(body['data']) == 1
        assert body['data'][0]['name'] == 'independent record'

        # 交叉验证真实 DB 状态：collection A 里应只多了 1 条记录（独立记录），
        # 且 collection B 里原有的冲突行未被覆盖/影响。
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, data FROM dynamic_data WHERE collection = %s", (COLL_A,))
            rows_a = cur.fetchall()
            assert len(rows_a) == 1
            assert rows_a[0][1]['name'] == 'independent record'

            cur.execute("SELECT data FROM dynamic_data WHERE collection = %s AND id = %s",
                        (COLL_B, SHARED_ID))
            row_b = cur.fetchone()
            assert row_b is not None
            assert row_b[0]['name'] == 'existing in other collection'
    finally:
        _cleanup()
