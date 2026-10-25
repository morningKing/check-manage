# -*- coding: utf-8 -*-
"""智能写作蓝图插件门测试（plugin-architecture spec §5 后端挂载点）。

禁用 → 全蓝图（含附件路径）404 PLUGIN_DISABLED；启用 → 正常业务响应。
进程内 client 直打蓝图路径（无 /api 前缀——那是 proxy 剥掉的）。
纪律：heal 夹具照抄 test_plugins_admin_api.py；smart-writing 行每例前后
钉回 TRUE（preflight ruling，防禁用态泄漏污染后续套件）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token  # noqa: E402
from utils import plugin_registry as pr  # noqa: E402

ADMIN = {'id': 'user-admin', 'username': 'admin', 'role': 'admin'}


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


@pytest.fixture
def se_client(db_conn):
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


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


def test_se_gate_blocks_documents_when_disabled(se_client, db_conn):
    _set_smart_writing(db_conn, False)
    r = se_client.get('/suggest-editor/documents', headers=_hdr())
    assert r.status_code == 404
    assert r.get_json() == {'error': {'code': 'PLUGIN_DISABLED',
                                      'plugin': 'smart-writing'}}


def test_se_gate_blocks_attachment_paths_when_disabled(se_client, db_conn):
    """附件服务路径同蓝图内——证明整个蓝图都在门后，不止 documents。"""
    _set_smart_writing(db_conn, False)
    r = se_client.get('/suggest-editor/attachments/att_x', headers=_hdr())
    assert r.status_code == 404
    assert r.get_json()['error']['code'] == 'PLUGIN_DISABLED'


def test_se_gate_passes_when_enabled(se_client):
    r = se_client.get('/suggest-editor/documents', headers=_hdr())
    assert r.status_code == 200
    assert 'documents' in r.get_json()
