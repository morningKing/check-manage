# -*- coding: utf-8 -*-
"""SkillOpt 拟合端点测试（admin 权限/camelCase 形状/recompute 幂等）。

fixture 对齐说明（brief 注记已授权）：`dev_headers`/`admin_headers`/`db_conn`
均与 conftest 同名 fixture 一致；唯一差异是 `client`——conftest 的 `app`
fixture 把 db.get_db patch 成 mock，而本文件的断言依赖真库种子行，故在本
模块内 shadow 一个真实 DB 的 client（test_ai_batch_admin_files.py 的
real_client 同模式：rebind 已导入模块的 get_db 回真实实现）。
"""
import hashlib
import json as _json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SKILL_MD = (
    '---\n'
    'description: 数据拉取\n'
    'fit:\n'
    '  steps:\n'
    "    - id: clone\n"
    '      name: 克隆仓库\n'
    '      expect:\n'
    '        - tool: bash\n'
    "          args_pattern: 'git clone'\n"
    '---\n'
    '正文。\n'
)


@pytest.fixture
def client():
    """真实 DB 的 test client（shadow 掉 conftest 的 mock-DB `client`）。

    权限判定仍走 conftest autouse 的 RBAC 预置缓存（admin=superuser /
    developer 无 admin 键），不需要 DB；数据面走真实 dev 库。utils.skill_fit
    的模块级绑定叫 `_default_get_db`，通用 get_db 扫不到，单独补齐——否则
    recompute 在早前 mock 导入的进程态下会打到 mock 连接。
    """
    import db as _db
    from app import app as flask_app
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if (mod_name.startswith('routes.') or mod_name.startswith('utils.')
                or mod_name == 'auth'):
            for attr in ('get_db', '_default_get_db'):
                if getattr(mod, attr, None) is not None:
                    try:
                        setattr(mod, attr, _db.get_db)
                    except (AttributeError, TypeError):
                        pass
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def _seed_fit_fixture(db_conn, tmp_path):
    """users + 批 + 会话 + attempt + manifest（path 指向带 fit 的定义文件）
    + 一条 fit 结果行。返回 (uid, bid, sid, attempt)。"""
    uid, bid, sid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    attempt = str(uuid.uuid4())
    md = tmp_path / 'SKILL.md'
    md.write_text(SKILL_MD, encoding='utf-8')
    ws = tmp_path / 'ws'
    ws.mkdir()
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'fr_{uid[:8]}'))
        cur.execute("INSERT INTO ai_chat_batches (id, user_id, name, prompt, total) "
                    "VALUES (%s, %s, 'fr', 'p', 1)", (bid, uid))
        cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, batch_id, "
                    "  batch_seq, workspace_path, session_token) "
                    "VALUES (%s, %s, 'completed', %s, 0, %s, %s)",
                    (sid, uid, bid, str(ws), f'tok-{sid[:12]}'))
        cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW() - interval '1 minute', NOW())",
                    (attempt, sid))
        cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                    "  source, path, content_hash, injected) "
                    "VALUES (%s, %s, 'skill', 'demo-skill', 'session', %s, %s, true)",
                    ('man_' + uuid.uuid4().hex[:8], attempt, str(md),
                     hashlib.sha256(md.read_bytes()).hexdigest()))
        cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                    "  def_kind, def_name, def_hash, steps_total, steps_hit, "
                    "  score, status, per_step) "
                    "VALUES (%s, %s, %s, 'skill', 'demo-skill', %s, 1, 1, 100, "
                    "  'fit', %s::jsonb)",
                    ('fit_' + uuid.uuid4().hex[:10], attempt, sid,
                     hashlib.sha256(md.read_bytes()).hexdigest(),
                     _json.dumps([{'id': 'clone', 'name': '克隆仓库',
                                   'status': 'hit', 'evidence': []}])))
    db_conn.commit()
    return uid, bid, sid, attempt


def test_skill_fit_requires_admin(client, dev_headers):
    r = client.get('/ai/chat/admin/skill-fit', headers=dev_headers)
    assert r.status_code in (401, 403)


def test_skill_fit_list_and_detail(client, admin_headers, db_conn, tmp_path):
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    try:
        r = client.get('/ai/chat/admin/skill-fit', headers=admin_headers)
        assert r.status_code == 200
        row = next(f for f in r.get_json()['fits'] if f['attemptId'] == attempt)
        assert row['defName'] == 'demo-skill' and row['score'] == 100
        assert row['status'] == 'fit'
        d = client.get(f'/ai/chat/admin/skill-fit/{attempt}', headers=admin_headers)
        assert d.status_code == 200
        assert d.get_json()['fits'][0]['perStep'][0]['id'] == 'clone'
        rc = client.post(f'/ai/chat/admin/skill-fit/{attempt}/recompute',
                         headers=admin_headers)
        assert rc.status_code == 200
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_skill_fit_results WHERE attempt_id=%s",
                        (attempt,))
            assert cur.fetchone()[0] == 1        # recompute 幂等
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()


def test_skill_fit_detail_missing_404(client, admin_headers):
    r = client.get('/ai/chat/admin/skill-fit/no-such-attempt', headers=admin_headers)
    assert r.status_code == 404
