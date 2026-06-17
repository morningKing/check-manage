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
