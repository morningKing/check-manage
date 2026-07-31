"""
导出脚本名称全局唯一性测试

真实 DB 集成测试（不 mock 游标）：验证 create_script/update_script 的提交前
查重、以及数据库唯一索引 idx_export_scripts_name_unique 本身确实存在且生效。
测试数据用 `_t_` 前缀自建自清理，跟 test_routes_menus_data_name_uniqueness.py
同模式。
"""
import sys
import os

import psycopg2
import psycopg2.errors
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import DB_CONFIG  # noqa: E402
import db as _db_module  # noqa: E402
from auth import create_token  # noqa: E402


@pytest.fixture
def client():
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


def _cleanup():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM export_scripts WHERE id LIKE '\\_t\\_expscript%' ESCAPE '\\'")
    conn.close()


@pytest.fixture(autouse=True)
def _around_each():
    _cleanup()
    yield
    _cleanup()


def _create_script(client, script_id, name):
    return client.post(
        '/exportScripts',
        json={
            'id': script_id,
            'name': name,
            'script': "result = '[]'",
            'outputFormat': 'json',
            'scope': 'page',
            'boundCollection': '_t_expscript_coll',
        },
        headers=_admin_headers(),
    )


class TestCreateExportScriptNameUniqueness:
    def test_second_script_with_same_name_rejected(self, client):
        resp1 = _create_script(client, '_t_expscript_a', '重名脚本X')
        assert resp1.status_code == 201, resp1.get_data(as_text=True)

        resp2 = _create_script(client, '_t_expscript_b', '重名脚本X')
        assert resp2.status_code == 400
        assert '重名脚本X' in resp2.get_json()['error']


class TestUpdateExportScriptNameUniqueness:
    def test_rename_to_existing_script_name_rejected(self, client):
        resp_a = _create_script(client, '_t_expscript_c', '重名脚本A')
        assert resp_a.status_code == 201
        resp_b = _create_script(client, '_t_expscript_d', '重名脚本B')
        assert resp_b.status_code == 201

        resp = client.put(
            '/exportScripts/_t_expscript_d',
            json={'name': '重名脚本A'},
            headers=_admin_headers(),
        )
        assert resp.status_code == 400
        assert '重名脚本A' in resp.get_json()['error']

    def test_rename_to_own_current_name_is_allowed(self, client):
        resp_a = _create_script(client, '_t_expscript_e', '重名脚本C')
        assert resp_a.status_code == 201

        resp = client.put(
            '/exportScripts/_t_expscript_e',
            json={'name': '重名脚本C'},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_update_without_touching_name_is_unaffected(self, client):
        resp_a = _create_script(client, '_t_expscript_f', '重名脚本D')
        assert resp_a.status_code == 201

        resp = client.put(
            '/exportScripts/_t_expscript_f',
            json={'description': '新的描述'},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200


class TestDatabaseUniqueIndex:
    def test_direct_duplicate_insert_rejected_by_unique_index(self):
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO export_scripts (id, name, script) VALUES (%s, %s, 'x')",
                ('_t_expscript_g', '重名脚本E'),
            )
            conn.commit()
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO export_scripts (id, name, script) VALUES (%s, %s, 'x')",
                    ('_t_expscript_h', '重名脚本E'),
                )
        finally:
            conn.rollback()
            conn.close()
