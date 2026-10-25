# -*- coding: utf-8 -*-
"""GET /menus 出参按禁用插件前缀过滤（plugin-architecture spec §6）。

禁用 smart-writing → 响应里 path 以 /suggest-editor 开头的行整行消失；
启用 → 恢复出现。DB 菜单数据不动——过滤只发生在出参（对同一 DB 状态
启用/禁用两态各打一遍 /menus 即证）。优先用 DB 里现成的 suggest 菜单行
（迁移幂等种子）；测试库没有时自造探针行并在尾巴删除。
纪律：heal 夹具照抄 test_plugin_gate_se.py；smart-writing 行每例前后
钉回 TRUE（防禁用态泄漏污染后续套件）。
"""
import os
import sys
import uuid

import pytest
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token  # noqa: E402
from utils import plugin_registry as pr  # noqa: E402

ADMIN = {'id': 'user-admin', 'username': 'admin', 'role': 'admin'}
SUGGEST_PREFIX = '/suggest-editor'


# ---------------------------------------------------------------------------
# 合跑隔离（回归修复）：conftest 的 `app` 夹具是「先 patch db.get_db/db.pool，
# 再 `from app import app`」——from-import 的 get_db 副本会永久绑住一次性
# mock。本套件每例前后把已导入模块上泄漏的绑定指回真实 db.get_db。
# （完整背景见 test_open_api_takeover_link.py 同名夹具注释。）
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _heal_leaked_get_db_bindings():
    import db as _db

    def _heal():
        real = _db.get_db
        for name, mod in list(sys.modules.items()):
            if mod is None or not (name.startswith(('routes.', 'utils.'))
                                   or name in ('auth', 'app')):
                continue
            for attr in ('get_db', '_default', '_default_get_db'):
                cur = getattr(mod, attr, None)
                if cur is not None and cur is not real and callable(cur):
                    try:
                        setattr(mod, attr, real)
                    except (AttributeError, TypeError):
                        pass
        if hasattr(_db.pool, '_mock_name'):
            _db.pool = None

    _heal()
    yield
    _heal()


def _set_smart_writing(db_conn, enabled):
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO platform_plugins (plugin_id, enabled) "
            "VALUES ('smart-writing', %s) "
            "ON CONFLICT (plugin_id) DO UPDATE SET enabled = EXCLUDED.enabled",
            (enabled,))
    db_conn.commit()
    pr.invalidate('smart-writing')


@pytest.fixture(autouse=True)
def _smart_writing_enabled(db_conn):
    _set_smart_writing(db_conn, True)
    yield
    _set_smart_writing(db_conn, True)


def _hdr():
    return {'Authorization': f'Bearer {create_token(ADMIN)}'}


@pytest.fixture
def menu_client(db_conn):
    """Flask client + suggest 探针保障：DB 已有 /suggest-editor% 行则直接用
    （不改不删）；没有才自造 ppm_<uuid> 探针行并在尾巴删除。"""
    from app import app
    app.config['TESTING'] = True
    client = app.test_client()

    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM menus WHERE path LIKE %s LIMIT 1",
                    (SUGGEST_PREFIX + '%',))
        existing = cur.fetchone()
    probe_id = None
    if not existing:
        probe_id = f'ppm_{uuid.uuid4().hex[:12]}'
        with db_conn.cursor() as cur:
            cur.execute(
                'INSERT INTO menus (id, name, "order", path, roles, menu_type) '
                "VALUES (%s, %s, 999, %s, %s, 'system')",
                (probe_id, '插件过滤探针',
                 f'{SUGGEST_PREFIX}-ppm-{uuid.uuid4().hex[:6]}',
                 psycopg2.extras.Json(['admin'])))
        db_conn.commit()

    yield client

    if probe_id:
        with db_conn.cursor() as cur:
            cur.execute('DELETE FROM menus WHERE id = %s', (probe_id,))
        db_conn.commit()


def _get_menus(client):
    r = client.get('/menus', headers=_hdr())
    assert r.status_code == 200
    return r.get_json()


def _suggest_rows(rows):
    return [m for m in rows
            if (m.get('path') or '').startswith(SUGGEST_PREFIX)]


def test_suggest_menu_present_when_enabled(menu_client):
    rows = _get_menus(menu_client)
    assert _suggest_rows(rows), '启用态响应应包含 /suggest-editor 前缀菜单行'


def test_suggest_menu_absent_when_disabled(menu_client, db_conn):
    enabled = _get_menus(menu_client)
    enabled_suggest = _suggest_rows(enabled)
    assert enabled_suggest

    _set_smart_writing(db_conn, False)
    disabled = _get_menus(menu_client)

    # 前缀行整行消失
    assert not _suggest_rows(disabled)
    # 其余行一个不多一个不少：只少了被过滤的 suggest 行
    other_ids = {m['id'] for m in enabled if m not in enabled_suggest}
    assert {m['id'] for m in disabled} == other_ids
    assert len(disabled) == len(enabled) - len(enabled_suggest)


def test_suggest_menu_restored_when_reenabled(menu_client, db_conn):
    _set_smart_writing(db_conn, False)
    assert not _suggest_rows(_get_menus(menu_client))

    _set_smart_writing(db_conn, True)
    assert _suggest_rows(_get_menus(menu_client))
