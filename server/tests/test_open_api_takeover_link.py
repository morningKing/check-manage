"""开放 API 接管链接端点（/v1/ai-batches/<batch_id>/sessions/<child_id>/takeover-link*）。

模式照抄 test_open_api_batches_crud.py：真实的 api_key_required 网关放行 +
打桩的 _current_key 控制视图取值；takeover_repo 整体 monkeypatch，不碰真库
（令牌哈希/TTL 钳制/三元组 SQL 隔离等语义归 takeover_repo 自己的测试，
路由层这里只断言契约形状与归属隔离）。
"""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = '/v1/ai-batches'
HDR = {'X-API-Key': 'cm_x'}


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


@contextmanager
def _fake_auth_db(mock_conn):
    """同 test_open_api_batches_crud.py 的模式：`auth.py` 在模块顶层
    `from db import get_db` 做了一次性绑定，conftest.py 的 `app` fixture 只
    patch 了 `db.get_db`（不含 `auth.get_db`），所以要让真实的 `api_key_required`
    网关在单独跑本文件时可控，必须单独 patch `auth.get_db`。"""
    yield mock_conn


def _auth_passes(mock_cursor, owner='user-42'):
    mock_cursor.fetchone.return_value = ('ak-1', '集成密钥', True, owner, None, None)


def _key(owner='user-42'):
    return {'id': 'ak-1', 'name': 'k', 'ownerUserId': owner}


def test_issue_link_requires_owned_batch(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value=None):
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link', headers=HDR, json={})
    assert resp.status_code == 404


def test_issue_link_returns_token_path_expires(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    fake_exp = datetime.now(timezone.utc) + timedelta(hours=24)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value={'id': 'b1'}), \
         patch('routes.open_api_batches._resolve_child',
               return_value=({'id': 's1', 'batch_seq': 1}, None)), \
         patch('utils.takeover_repo.create_takeover_token',
               return_value=('tk_abc', fake_exp)) as mk:
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link', headers=HDR,
                           json={'ttlHours': 48})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['token'] == 'tk_abc' and body['path'] == '/takeover/session?t=tk_abc'
    assert body['expiresAt'] == fake_exp.isoformat()
    assert mk.call_args.kwargs['ttl_hours'] == 48
    # 归属三元组 + 来源密钥必须原样传下去（owner 在首位、api_key_id 在末位）
    assert mk.call_args.args == ('user-42', 'b1', 's1', 'ak-1')


def test_revoke_scoped_to_triple(client, mock_conn, mock_cursor):
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value={'id': 'b1'}), \
         patch('routes.open_api_batches._resolve_child', return_value=({'id': 's1'}, None)), \
         patch('utils.takeover_repo.revoke_takeover_token', return_value=False) as rk:
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link/revoke',
                           headers=HDR, json={'tokenId': 'tkt_x'})
    assert resp.status_code == 404
    assert rk.call_args.args == ('user-42', 'b1', 's1', 'tkt_x')


def test_revoke_success_returns_200(client, mock_conn, mock_cursor):
    """revoke 命中（三元组全匹配）→ 200 {revoked: true}。"""
    _auth_passes(mock_cursor)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value={'id': 'b1'}), \
         patch('routes.open_api_batches._resolve_child', return_value=({'id': 's1'}, None)), \
         patch('utils.takeover_repo.revoke_takeover_token', return_value=True) as rk:
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link/revoke',
                           headers=HDR, json={'tokenId': 'tkt_ok'})
    assert resp.status_code == 200
    assert resp.get_json() == {'revoked': True}
    assert rk.call_args.args == ('user-42', 'b1', 's1', 'tkt_ok')


def test_issue_link_passes_ttl_through_unclamped(client, mock_conn, mock_cursor):
    """ttlHours=9999 仍 201：钳制（1..168）在 repo 层已测并有值兜底，路由层
    不重复拒绝、也不得自行截断——原值透传给 repo。"""
    _auth_passes(mock_cursor)
    fake_exp = datetime.now(timezone.utc) + timedelta(hours=24)
    with patch('auth.get_db', lambda: _fake_auth_db(mock_conn)), \
         patch('routes.open_api_batches._current_key', return_value=_key()), \
         patch('routes.open_api_batches.get_batch_detail', return_value={'id': 'b1'}), \
         patch('routes.open_api_batches._resolve_child',
               return_value=({'id': 's1', 'batch_seq': 2}, None)), \
         patch('utils.takeover_repo.create_takeover_token',
               return_value=('tk_big', fake_exp)) as mk:
        resp = client.post(f'{BASE}/b1/sessions/s1/takeover-link', headers=HDR,
                           json={'ttlHours': 9999})
    assert resp.status_code == 201
    assert mk.call_args.kwargs['ttl_hours'] == 9999
