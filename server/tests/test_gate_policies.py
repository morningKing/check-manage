# -*- coding: utf-8 -*-
"""门禁策略库：CRUD + 引用防护 + 端点鉴权。"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from db import get_db
from utils import gate_policy_repo as repo

@pytest.fixture
def admin_h():
    from auth import create_token
    tok = create_token({'id': 'user-admin', 'username': 'admin', 'role': 'admin'})
    return {'Authorization': f'Bearer {tok}'}

@pytest.fixture
def client():
    """真实 DB 的 test client（shadow 掉 conftest 的 mock-DB `app` fixture，
    重绑定模式与 test_skill_fit_routes.client 一致）。"""
    import db as db_module
    db_module.pool = None
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if getattr(mod, 'get_db', None) is not None and (
                mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            try:
                mod.get_db = db_module.get_db
            except (AttributeError, TypeError):
                pass
    from app import app
    app.config['TESTING'] = True
    return app.test_client()

CHECKS = [{
    'name': '写前读知识库', 'mode': 'checkpoint', 'check_type': 'tool',
    'tool': 'write', 'args_pattern': 'outputs/report', 'timing': 'before',
    'prompt': '请先读取知识库 docs/kb/ 再写入', 'max_checks': 2,
    'assert_spec': {'tool': 'read', 'args_pattern': 'docs/kb/', 'min_count': 1},
}]

def _cleanup(pid):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
    conn.commit()

def test_policy_crud_roundtrip(db_conn):
    pid = repo.create_policy('user-admin', f'pol-{uuid.uuid4().hex[:6]}',
                             'd', CHECKS, enabled=True)['id']
    try:
        got = repo.get_policy(pid)
        assert got['checks'][0]['mode'] == 'checkpoint'
        assert got['checks'][0]['assert_spec']['tool'] == 'read'
        assert any(p['id'] == pid for p in repo.list_policies(enabled=True))
        repo.update_policy(pid, checks=None, enabled=False,
                           name=None, description=None)   # 只翻 enabled
        assert repo.get_policy(pid)['enabled'] is False
        assert all(p['id'] != pid for p in repo.list_policies(enabled=True))
    finally:
        _cleanup(pid)

def test_delete_blocked_by_active_batch_ref(db_conn):
    pid = repo.create_policy('user-admin', f'pol-{uuid.uuid4().hex[:6]}',
                             None, CHECKS)['id']
    bid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total,"
            " status, gate_policy_id) VALUES (%s,'user-admin','p','p',1,"
            " 'running', %s)", (bid, pid))
    db_conn.commit()
    try:
        with pytest.raises(repo.PolicyInUseError):
            repo.delete_policy(pid)
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_chat_batches SET status='completed' "
                        "WHERE id=%s", (bid,))
        db_conn.commit()
        repo.delete_policy(pid)          # 仅终态引用 → 允许删
        assert repo.get_policy(pid) is None
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
        db_conn.commit()

def test_fetch_action_checks_merge(db_conn):
    """合并语义（spec §8.2）：先策略后批侧、同名批侧覆盖、模板兜底保留。

    注：合并数组按登记顺序含同名两份（策略在前、批侧在后），真正的
    「同名批侧覆盖」发生在 register_session_expectations 按 name 幂等覆盖
    （后登记者胜，ON CONFLICT 全列替换）——fetch 层不去重。brief 原稿断言
    count==1 与其第 3 条断言（策略 prompt 存活）自相矛盾，按 spec §8.2
    与其自带实现（纯拼接）修正为本形态。"""
    from utils.batch_engine import BatchWorker
    pid = repo.create_policy('user-admin', f'pol-{uuid.uuid4().hex[:6]}',
                             None, CHECKS)['id']
    bid = str(uuid.uuid4())
    own = [{'name': '写前读知识库', 'mode': 'checkpoint', 'check_type': 'tool',
            'tool': 'write', 'args_pattern': 'outputs/own', 'timing': 'before',
            'prompt': '批侧覆盖版提示词', 'max_checks': 1}]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_chat_batches (id, user_id, name, prompt, total,"
            " status, action_checks, gate_policy_id) "
            "VALUES (%s,'user-admin','p','p',1,'pending',%s,%s)",
            (bid, json.dumps(own), pid))
    db_conn.commit()
    try:
        worker = BatchWorker.__new__(BatchWorker)
        merged = worker._fetch_action_checks(bid)
        names = [c['name'] for c in merged]
        assert names.count('写前读知识库') == 2          # 先策略后批侧，两份都在
        own_hit = [c for c in merged if c['name'] == '写前读知识库']
        assert own_hit[0]['args_pattern'] == 'outputs/report'   # 策略侧在前
        assert own_hit[-1]['args_pattern'] == 'outputs/own'     # 批侧在后，登记时覆盖
        assert any(c['mode'] == 'checkpoint' and
                   c.get('prompt', '').startswith('请先读取') for c in merged)
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM gate_policies WHERE id=%s", (pid,))
    db_conn.commit()

def test_create_batch_rejects_missing_policy(db_conn):
    from utils.batch_repo import create_batch
    with pytest.raises(ValueError, match='策略'):
        create_batch('user-admin', name='p', prompt='p', template_id=None,
                     files=[{'name': 'a.txt', 'path': 'batch-staging/u/a.txt'}],
                     gate_policy_id='gpol_nope')

def test_policy_endpoints_auth_and_validation(client, admin_h, db_conn):
    # 写权限：非 admin 403
    from auth import create_token
    dev = create_token({'id': 'u-dev', 'username': 'd', 'role': 'developer'})
    r = client.post('/ai/chat/gate-policies',
                    headers={'Authorization': f'Bearer {dev}'},
                    json={'name': 'x', 'checks': CHECKS})
    assert r.status_code == 403
    # 非法 checks（坏 prompt 缺失）→ 400 不带病入库
    r = client.post('/ai/chat/gate-policies', headers=admin_h,
                    json={'name': f'bad-{uuid.uuid4().hex[:6]}', 'checks': [
                        {'name': 'cp', 'mode': 'checkpoint', 'tool': 'w',
                         'args_pattern': 'x'}]})
    assert r.status_code == 400
    # 正常创建 + PATCH 显式传 checks 才更新
    name = f'pol-{uuid.uuid4().hex[:6]}'
    r = client.post('/ai/chat/gate-policies', headers=admin_h,
                    json={'name': name, 'description': 'd', 'checks': CHECKS})
    assert r.status_code == 201
    pid = r.get_json()['id']
    try:
        r2 = client.patch(f'/ai/chat/gate-policies/{pid}', headers=admin_h,
                          json={'enabled': False})
        assert r2.status_code == 200
        with db_conn.cursor() as cur:
            cur.execute("SELECT enabled, checks FROM gate_policies WHERE id=%s",
                        (pid,))
            enabled, checks = cur.fetchone()
        assert enabled is False and len(checks) == 1
    finally:
        _cleanup(pid)
