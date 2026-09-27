"""
数据页菜单名称全局唯一性测试

真实 DB 集成测试（不 mock 游标）：验证 create_menu/update_menu 的提交前
查重、以及数据库唯一索引 idx_menus_data_name_unique 本身确实存在且生效
（应用层查重之外的最后一道防线）。测试数据用 `_t_` 前缀自建自清理，跟
test_dynamic_batch_pk_check.py / test_routes_import_runs.py 同模式。
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

PROJECT_ID = '_t_menuuniq_project'


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
    cur.execute("DELETE FROM menus WHERE id LIKE '\\_t\\_menuuniq%' ESCAPE '\\'")
    conn.close()


@pytest.fixture(autouse=True)
def _around_each():
    _cleanup()
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    # data 类型菜单要求父级是 project 类型（_validate_menu_type）
    cur.execute(
        "INSERT INTO menus (id, name, menu_type) VALUES (%s, %s, 'project')",
        (PROJECT_ID, '_t 唯一性测试项目'),
    )
    conn.close()
    yield
    _cleanup()


def _create_data_menu(client, menu_id, name):
    return client.post(
        '/menus',
        json={
            'id': menu_id,
            'name': name,
            'menuType': 'data',
            'pageId': f'page-{menu_id}',
            'parentId': PROJECT_ID,
        },
        headers=_admin_headers(),
    )


class TestCreateMenuDataNameUniqueness:
    def test_second_data_menu_with_same_name_rejected(self, client):
        resp1 = _create_data_menu(client, '_t_menuuniq_a', '重名测试X')
        assert resp1.status_code == 201, resp1.get_data(as_text=True)

        resp2 = _create_data_menu(client, '_t_menuuniq_b', '重名测试X')
        assert resp2.status_code == 400
        assert '重名测试X' in resp2.get_json()['error']

    def test_same_name_different_menu_type_is_allowed(self, client):
        resp1 = _create_data_menu(client, '_t_menuuniq_c', '重名测试Y')
        assert resp1.status_code == 201, resp1.get_data(as_text=True)

        resp2 = client.post(
            '/menus',
            json={'id': '_t_menuuniq_ws', 'name': '重名测试Y', 'menuType': 'workspace', 'path': '/x'},
            headers=_admin_headers(),
        )
        assert resp2.status_code == 201, resp2.get_data(as_text=True)


class TestUpdateMenuDataNameUniqueness:
    def test_rename_to_existing_data_menu_name_rejected(self, client):
        resp_a = _create_data_menu(client, '_t_menuuniq_d', '重名测试A')
        assert resp_a.status_code == 201
        resp_b = _create_data_menu(client, '_t_menuuniq_e', '重名测试B')
        assert resp_b.status_code == 201

        resp = client.put(
            '/menus/_t_menuuniq_e',
            json={
                'name': '重名测试A',
                'menuType': 'data',
                'pageId': 'page-_t_menuuniq_e',
                'parentId': PROJECT_ID,
            },
            headers=_admin_headers(),
        )
        assert resp.status_code == 400
        assert '重名测试A' in resp.get_json()['error']

    def test_rename_to_own_current_name_is_allowed(self, client):
        resp_a = _create_data_menu(client, '_t_menuuniq_f', '重名测试C')
        assert resp_a.status_code == 201

        resp = client.put(
            '/menus/_t_menuuniq_f',
            json={
                'name': '重名测试C',
                'menuType': 'data',
                'pageId': 'page-_t_menuuniq_f',
                'parentId': PROJECT_ID,
            },
            headers=_admin_headers(),
        )
        assert resp.status_code == 200


class TestDatabaseUniqueIndex:
    def test_direct_duplicate_insert_rejected_by_unique_index(self):
        """绕过应用层查重，直接对 menus 表插入两条同名 data 类型菜单，
        证明数据库唯一索引本身确实存在且生效（应用层查重之外的最后防线）。"""
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO menus (id, name, menu_type) VALUES (%s, %s, 'data')",
                ('_t_menuuniq_g', '重名测试D'),
            )
            conn.commit()
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO menus (id, name, menu_type) VALUES (%s, %s, 'data')",
                    ('_t_menuuniq_h', '重名测试D'),
                )
        finally:
            conn.rollback()
            conn.close()
