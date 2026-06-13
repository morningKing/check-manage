"""集成测试：旁路 create_item 的两条写入路径（批量导入 / 开放 API）写入后，
必须重播种 dynamic_sequences 计数器，避免后续 create_item 分配与导入值重号。

使用真实 DB（casemanage）。每个测试自清理。
"""

import os
import sys
import importlib

import pytest
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db_module  # noqa: E402
from db import get_db  # noqa: E402
from auth import create_token, hash_api_key  # noqa: E402
from utils.sequences import allocate_sequence  # noqa: E402


@pytest.fixture
def real_client():
    """A Flask test client wired to the REAL dev DB.

    The shared `app`/`client` fixtures in conftest mock `db.get_db`. These
    bypass tests need the route handlers (`routes.dynamic`, `routes.open_api`,
    `auth`) to hit the real DB so the reseed actually runs against
    `dynamic_sequences`. Force-rebind each module's `get_db` to the real one.
    """
    from app import app as flask_app
    for mod_name in ('routes.dynamic', 'routes.open_api', 'auth'):
        mod = importlib.import_module(mod_name)
        if hasattr(mod, 'get_db'):
            mod.get_db = _db_module.get_db
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


@pytest.fixture
def auth_headers():
    """admin JWT 请求头（batch-create 走 write_required + RBAC，仅需 token）。"""
    token = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {token}'}


def _mkpage(cur, coll, api_public=False, api_writable=False):
    fields = [{'fieldName': 'code', 'controlType': 'autoSequence',
               'sequenceConfig': {'prefix': 'IC-', 'max': 999}, 'isPrimaryKey': True}]
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute(
        "INSERT INTO page_configs (id,name,fields,api_public,api_writable) "
        "VALUES (%s,%s,%s,%s,%s)",
        (f'page-{coll}', coll, psycopg2.extras.Json(fields), api_public, api_writable),
    )


def _cleanup(coll, api_key_id=None):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
        if api_key_id:
            cur.execute("DELETE FROM api_keys WHERE id=%s", (api_key_id,))
        conn.commit()


def test_batch_create_bumps_sequence_counter(real_client, auth_headers):
    """批量导入带 autoSequence 值的记录后，计数器应被抬到 >= 导入最大值。"""
    coll = 'zzbatchseq'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        _mkpage(cur, coll)
        # 预置一个"陈旧"计数器行（current_value=1）。这正是 bug 触发条件：
        # 计数器行已存在但落后于即将导入的最大编号。若 batch-create 不重播种，
        # 后续 allocate 会从 1 继续 → IC-002，与导入的 IC-040/041 撞号。
        cur.execute("INSERT INTO dynamic_sequences VALUES (%s,'main','code',1)", (coll,))
        conn.commit()
    try:
        # 批量导入两条，code 为 IC-040 / IC-041（模拟数据迁移带来的既有编号）
        records = [
            {"id": f"{coll}-a", "data": {"code": "IC-040"}},
            {"id": f"{coll}-b", "data": {"code": "IC-041"}},
        ]
        resp = real_client.post(f'/{coll}/batch-create',
                                json={"records": records}, headers=auth_headers)
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        # 计数器应 >= 41；下一次 allocate 应给 IC-042，不与导入撞号
        with get_db() as conn:
            cur = conn.cursor()
            nxt = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)[0]
            conn.commit()
        assert nxt == 'IC-042'
    finally:
        _cleanup(coll)


def test_open_api_create_bumps_sequence_counter(real_client):
    """开放 API 创建带 autoSequence 值的记录后，计数器应被抬到 >= 该值。"""
    coll = 'zzopenapiseq'
    api_key_plain = 'cm_bypass_reseed_test_key_0001'
    api_key_id = 'ak-bypass-reseed-test'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
        cur.execute("DELETE FROM dynamic_sequences WHERE collection=%s", (coll,))
        _mkpage(cur, coll, api_public=True, api_writable=True)
        # 预置陈旧计数器行（见 batch 测试说明），否则首次 allocate 会自播种掩盖 bug。
        cur.execute("INSERT INTO dynamic_sequences VALUES (%s,'main','code',1)", (coll,))
        cur.execute("DELETE FROM api_keys WHERE id=%s", (api_key_id,))
        cur.execute(
            "INSERT INTO api_keys (id, name, key_hash, is_active) VALUES (%s,%s,%s,TRUE)",
            (api_key_id, 'Bypass Reseed Test', hash_api_key(api_key_plain)),
        )
        conn.commit()
    try:
        headers = {'X-API-Key': api_key_plain}
        resp = real_client.post(f'/api/v1/collections/{coll}',
                                json={"id": f"{coll}-a", "code": "IC-040"}, headers=headers)
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        # 下一次 allocate 应给 IC-041，不与 API 写入撞号
        with get_db() as conn:
            cur = conn.cursor()
            nxt = allocate_sequence(cur, coll, 'main', 'code', 'IC-', 3, count=1)[0]
            conn.commit()
        assert nxt == 'IC-041'
    finally:
        _cleanup(coll, api_key_id=api_key_id)
