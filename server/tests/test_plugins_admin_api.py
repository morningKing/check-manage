# -*- coding: utf-8 -*-
"""插件管理 API（GET/PUT /plugins，plugin-architecture spec §4）。

真实 JWT 走 auth.create_token（payload 形状照抄 test_batch_execution_safety_p0
的 chat_client 夹具：{'id','username','role'}）；真库直写开关行 +
pr.invalidate() 控制状态；PUT 用例断言 oplog 行、updated_by 与同进程缓存
即时失效（先 GET 暖缓存，PUT 后立即 GET 必须看到新值——TTL 3s 未到，
只有 invalidate 被调用才翻得过来）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token  # noqa: E402
from utils import plugin_registry as pr  # noqa: E402

ADMIN = {'id': 'user-admin', 'username': 'admin', 'role': 'admin'}
GUEST = {'id': 'user-guest', 'username': 'guest', 'role': 'guest'}
DEVELOPER = {'id': 'user-dev', 'username': 'developer', 'role': 'developer'}


# ---------------------------------------------------------------------------
# 合跑隔离（回归修复）：conftest 的 `app` 夹具是「先 patch db.get_db/db.pool，
# 再 `from app import app`」——进程内第一次用到它时，整个 app 导入图都在
# patch 窗口内首次加载，所有顶层 `from db import get_db` 的模块（auth /
# utils.ai_query / utils.memory / utils.gate_policy_repo / utils.agent_ledger /
# routes.* …）从此永久绑住那个 yield 一次性 mock_conn 的 lambda；patch 退出
# 只还原 db.get_db 本身，from-import 副本不回头。同进程后续的真库用例
# （batch_execution_safety_p0 的门禁四例）就会读到假连接：
# merged_action_checks 查无批次 → applicable=0 → 门禁被误判 'skipped'
# （应为 'inconclusive'）。conftest 的 _rebind_module_get_db_to_real 只 Heal
# 4 个模块、test_batch_execution_safety_p0._rebind_real_db 不认别名
# （_default/_default_get_db），所以在本套件的每个用例前后把已导入模块上
# 泄漏的 get_db 绑定统一指回真实 db.get_db。
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
        # app 夹具留下的 MagicMock 池：下次 get_db() 需重建真池
        if hasattr(_db.pool, '_mock_name'):
            _db.pool = None

    _heal()
    yield
    _heal()


# ---------------------------------------------------------------------------
# 夹具：真库 + 真 app；smart-writing 行每例前后钉回种子态（TRUE + 失效缓存）
# ---------------------------------------------------------------------------
@pytest.fixture
def plugins_client(db_conn):
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
def _smart_writing_seeded(db_conn):
    """任何用例翻动 smart-writing 开关后必须还原（种子态 enabled=TRUE）。

    setup 钉 TRUE 保证断言起点确定；teardown 再钉一次 + invalidate，
    下一文件/下次迁移种子看到的是干净种子态。"""
    _set_smart_writing(db_conn, True)
    yield
    _set_smart_writing(db_conn, True)


def _hdr(user):
    return {'Authorization': f'Bearer {create_token(user)}'}


def _smart_writing(body):
    return next(p for p in body if p['id'] == 'smart-writing')


# ---------------------------------------------------------------------------
# 1. GET：未登录 401；admin 200 含 smart-writing 且 enabled 为真（种子后）
# ---------------------------------------------------------------------------
def test_get_requires_login(plugins_client):
    r = plugins_client.get('/plugins')
    assert r.status_code == 401


def test_get_admin_lists_smart_writing_enabled(plugins_client):
    r = plugins_client.get('/plugins', headers=_hdr(ADMIN))
    assert r.status_code == 200
    body = r.get_json()
    assert {p['id'] for p in body} == set(pr.PLUGINS)
    sw = _smart_writing(body)
    assert set(sw) == {'id', 'name', 'description', 'version', 'enabled'}
    assert sw['name'] == pr.PLUGINS['smart-writing'].name
    assert sw['version'] == pr.PLUGINS['smart-writing'].version
    assert sw['enabled'] is True


# ---------------------------------------------------------------------------
# 2. PUT：非 admin（guest/developer）403，且不动库
# ---------------------------------------------------------------------------
def test_put_non_admin_403(plugins_client, db_conn):
    for user in (GUEST, DEVELOPER):
        r = plugins_client.put('/plugins/smart-writing', json={'enabled': False},
                               headers=_hdr(user))
        assert r.status_code == 403
    with db_conn.cursor() as cur:
        cur.execute("SELECT enabled FROM platform_plugins "
                    "WHERE plugin_id = 'smart-writing'")
        assert cur.fetchone()[0] is True


# ---------------------------------------------------------------------------
# 3. PUT admin：200 + 同进程缓存即时失效 + oplog 行 + updated_by
# ---------------------------------------------------------------------------
def test_put_admin_disables_and_audits(plugins_client, db_conn):
    # 暖缓存：此刻缓存值 = True（TTL 3s），PUT 后的 GET 只有在路由调用了
    # invalidate 才会翻成 False——不调就仍是缓存里的 True
    warm = plugins_client.get('/plugins', headers=_hdr(ADMIN))
    assert _smart_writing(warm.get_json())['enabled'] is True

    r = plugins_client.put('/plugins/smart-writing', json={'enabled': False},
                           headers=_hdr(ADMIN))
    assert r.status_code == 200
    assert r.get_json() == {'id': 'smart-writing', 'enabled': False}

    # 同进程缓存已被 invalidate：立即 GET 必须看到新值
    r2 = plugins_client.get('/plugins', headers=_hdr(ADMIN))
    assert _smart_writing(r2.get_json())['enabled'] is False

    with db_conn.cursor() as cur:
        cur.execute("SELECT enabled, updated_by FROM platform_plugins "
                    "WHERE plugin_id = 'smart-writing'")
        enabled, updated_by = cur.fetchone()
        assert enabled is False
        assert updated_by == 'user-admin'
        cur.execute(
            "SELECT action, target_type, target_id FROM operation_logs "
            "WHERE target_type='platform_plugin' AND target_id='smart-writing' "
            "ORDER BY created_at DESC LIMIT 1")
        assert cur.fetchone() == ('plugin.update', 'platform_plugin',
                                  'smart-writing')


# ---------------------------------------------------------------------------
# 4. PUT：未知 id 404；非布尔 body 400
# ---------------------------------------------------------------------------
def test_put_unknown_id_404(plugins_client):
    r = plugins_client.put('/plugins/no-such-plugin', json={'enabled': True},
                           headers=_hdr(ADMIN))
    assert r.status_code == 404


def test_put_non_bool_body_400(plugins_client, db_conn):
    r = plugins_client.put('/plugins/smart-writing', json={},
                           headers=_hdr(ADMIN))
    assert r.status_code == 400
    r = plugins_client.put('/plugins/smart-writing', json={'enabled': 'yes'},
                           headers=_hdr(ADMIN))
    assert r.status_code == 400
    with db_conn.cursor() as cur:
        cur.execute("SELECT enabled FROM platform_plugins "
                    "WHERE plugin_id = 'smart-writing'")
        assert cur.fetchone()[0] is True  # 400 不落库
