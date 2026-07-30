"""export_runner 共享执行器测试（直连真实 DB casemanage）。"""
import json
import psycopg2.extras
import pytest
from db import get_db
from utils.export_runner import (
    execute_bound_export, check_binding, check_rbac,
    ExportBindingError, ExportPermissionError, SCRIPT_SELECT,
)


def _seed_page(cur, coll, fields, roles=('admin', 'developer', 'guest')):
    cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (coll,))
    cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{coll}',))
    cur.execute("DELETE FROM menus WHERE page_id=%s", (f'page-{coll}',))
    cur.execute("INSERT INTO page_configs (id,name,fields) VALUES (%s,%s,%s)",
                (f'page-{coll}', coll, psycopg2.extras.Json(fields)))
    cur.execute("INSERT INTO menus (id,name,page_id,roles,menu_type) VALUES (%s,%s,%s,%s,'data')",
                (f'menu-{coll}', coll, f'page-{coll}', psycopg2.extras.Json(list(roles))))


def _seed_script(cur, sid, scope='page', bound_collection=None, bound_menu_id=None,
                 script="result = json.dumps([r['id'] for r in data])", output_format='json'):
    cur.execute("DELETE FROM export_scripts WHERE id=%s", (sid,))
    cur.execute(
        "INSERT INTO export_scripts (id,name,script,output_format,scope,bound_collection,bound_menu_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (sid, sid, script, output_format, scope, bound_collection, bound_menu_id))


def _fetch_script(cur, sid):
    cur.execute(f"SELECT {SCRIPT_SELECT} FROM export_scripts WHERE id=%s", (sid,))
    return cur.fetchone()


def _cleanup(colls=(), scripts=()):
    with get_db() as conn:
        cur = conn.cursor()
        for c in colls:
            cur.execute("DELETE FROM dynamic_data WHERE collection=%s", (c,))
            cur.execute("DELETE FROM page_configs WHERE id=%s", (f'page-{c}',))
            cur.execute("DELETE FROM menus WHERE page_id=%s", (f'page-{c}',))
        for s in scripts:
            cur.execute("DELETE FROM export_scripts WHERE id=%s", (s,))
        conn.commit()


def test_bound_export_runs_when_target_matches():
    coll, sid = 'zzer_a', 'zzer_s1'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [{'fieldName': 'name', 'controlType': 'text'}])
            cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES (%s,%s,%s,'main')",
                        ('r1', coll, psycopg2.extras.Json({'name': 'A'})))
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
            row = _fetch_script(cur, sid)
            out, fname, ctype = execute_bound_export(cur, row, collection=coll, role='admin')
        assert json.loads(out) == ['r1']
        assert ctype == 'application/json'
    finally:
        _cleanup([coll], [sid])


def test_ties_on_created_at_break_by_id_matching_the_list_endpoint():
    """批量导入时同一事务内所有行的 created_at 完全相同（PostgreSQL 的 NOW()
    在事务内是常量，不是每行取一次实时时间）——这时导出必须按 id 兜底排序，
    跟数据页/列表接口的默认排序（routes/dynamic.py 的
    `ORDER BY created_at ASC, id ASC`）保持一致，否则同一批导入的数据在
    数据页上看到的顺序和导出脚本 data 里的顺序会对不上。

    故意按 r3, r1, r2 的顺序插入（且三行 created_at 完全相同，用同一条
    INSERT 语句里三次 NOW() 调用制造），如果导出查询没有 id 兜底，很可能
    按物理插入顺序返回 [r3, r1, r2]，暴露排序不一致的问题。
    """
    coll, sid = 'zzer_ties', 'zzer_ties_s1'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [{'fieldName': 'name', 'controlType': 'text'}])
            cur.execute(
                "INSERT INTO dynamic_data (id, collection, data, branch_id, created_at) VALUES "
                "(%s,%s,%s,'main', NOW()), (%s,%s,%s,'main', NOW()), (%s,%s,%s,'main', NOW())",
                ('r3', coll, psycopg2.extras.Json({'name': 'C'}),
                 'r1', coll, psycopg2.extras.Json({'name': 'A'}),
                 'r2', coll, psycopg2.extras.Json({'name': 'B'}))
            )
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
            row = _fetch_script(cur, sid)
            out, _, _ = execute_bound_export(cur, row, collection=coll, role='admin')
        assert json.loads(out) == ['r1', 'r2', 'r3']
    finally:
        _cleanup([coll], [sid])


def test_binding_mismatch_raises():
    coll, other, sid = 'zzer_b', 'zzer_b2', 'zzer_s2'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [])
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
            row = _fetch_script(cur, sid)
            with pytest.raises(ExportBindingError):
                execute_bound_export(cur, row, collection=other, role='admin')
    finally:
        _cleanup([coll], [sid])


def test_unbound_script_is_tolerant():
    coll, sid = 'zzer_c', 'zzer_s3'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [{'fieldName': 'name', 'controlType': 'text'}])
            cur.execute("INSERT INTO dynamic_data (id,collection,data,branch_id) VALUES (%s,%s,%s,'main')",
                        ('r1', coll, psycopg2.extras.Json({'name': 'A'})))
            _seed_script(cur, sid, scope='page', bound_collection=None)  # 未绑定
            conn.commit()
            row = _fetch_script(cur, sid)
            out, _, _ = execute_bound_export(cur, row, collection=coll, role='admin')
        assert json.loads(out) == ['r1']
    finally:
        _cleanup([coll], [sid])


def test_rbac_denies_role_not_in_menu_roles():
    coll, sid = 'zzer_d', 'zzer_s4'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [], roles=('admin',))  # 仅 admin
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
            row = _fetch_script(cur, sid)
            with pytest.raises(ExportPermissionError):
                execute_bound_export(cur, row, collection=coll, role='guest')
    finally:
        _cleanup([coll], [sid])


def test_execute_endpoint_rejects_binding_mismatch():
    from app import app
    from auth import create_token
    coll, other, sid = 'zzer_e', 'zzer_e2', 'zzer_s5'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [])
            _seed_page(cur, other, [])
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
        app.config['TESTING'] = True
        tok = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
        c = app.test_client()
        resp = c.post('/exportScripts/execute',
                      json={'scriptId': sid, 'collection': other, 'branchId': 'main'},
                      headers={'Authorization': f'Bearer {tok}'})
        assert resp.status_code == 400
        assert '绑定' in resp.get_json()['error']
    finally:
        _cleanup([coll, other], [sid])


def test_for_collection_returns_bound_scripts():
    from app import app
    from auth import create_token
    coll, sid = 'zzer_f', 'zzer_s6'
    try:
        with get_db() as conn:
            cur = conn.cursor()
            _seed_page(cur, coll, [])
            _seed_script(cur, sid, scope='page', bound_collection=coll)
            conn.commit()
        app.config['TESTING'] = True
        tok = create_token({'id': 'u1', 'username': 'admin', 'role': 'admin'})
        c = app.test_client()
        resp = c.get(f'/exportScripts/for-collection/{coll}',
                     headers={'Authorization': f'Bearer {tok}'})
        assert resp.status_code == 200
        ids = [s['id'] for s in resp.get_json()]
        assert sid in ids
    finally:
        _cleanup([coll], [sid])
