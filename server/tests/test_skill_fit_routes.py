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


# ── 终审 Fix 3：generate/apply 的 path confinement ─────────────────────────

# Windows 下跨盘符绝对路径 / POSIX 下的系统绝对路径，二者都落在允许根外
_ESCAPE_PATH = 'C:\\Windows\\win.ini' if os.name == 'nt' else '/etc/passwd'


def test_skill_def_steps_generate_rejects_path_escape(client, admin_headers):
    """generate 的 path 逃出允许根（AI 工作区根/全局技能根）→ 400，
    不读文件不打 LLM。"""
    r = client.post('/ai/chat/admin/skill-def-steps/generate',
                    headers=admin_headers,
                    json={'kind': 'skill', 'path': _ESCAPE_PATH})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'path escapes allowed roots'


def test_skill_def_steps_apply_rejects_path_escape(client, admin_headers):
    """apply 的 path 逃出允许根 → 400（先于 isfile/写盘判定）。"""
    r = client.post('/ai/chat/admin/skill-def-steps/apply',
                    headers=admin_headers,
                    json={'path': _ESCAPE_PATH, 'steps': []})
    assert r.status_code == 400
    assert r.get_json()['error'] == 'path escapes allowed roots'


def test_skill_def_steps_generate_allows_workspace_path(client, admin_headers,
                                                        tmp_path, monkeypatch):
    """confinement 正例：允许根内的定义文件正常走 generate——防止把合法
    路径一并拦死的回归（AI 通道打桩，不产生 LLM 调用）。"""
    import config as _config
    import routes.ai_session_admin as _admin
    import utils.skill_fit_ai as _fa
    monkeypatch.setattr(_config, 'AI_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr(_fa, '_llm_json', lambda system, user: {'steps': [
        {'id': 'clone', 'name': '克隆仓库',
         'expect': [{'tool': 'bash', 'args_pattern': 'git clone'}]}]})
    monkeypatch.setattr(_admin, 'log_operation', lambda *a, **kw: None)
    d = tmp_path / 'demo-skill'
    d.mkdir()
    (d / 'SKILL.md').write_text('# 数据拉取\n1. clone\n', encoding='utf-8')
    r = client.post('/ai/chat/admin/skill-def-steps/generate',
                    headers=admin_headers,
                    json={'kind': 'skill', 'path': str(d / 'SKILL.md')})
    assert r.status_code == 200
    assert r.get_json()['steps'][0]['id'] == 'clone'


def test_skill_fit_list_filters_by_def(client, admin_headers, db_conn, tmp_path):
    """defKind/defName 过滤：同 attempt 两条不同定义，过滤后只回对应定义。"""
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    agent_name = f'demo-agent-{uuid.uuid4().hex[:6]}'
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, steps_total, steps_hit, score, status, per_step) "
                        "VALUES (%s, %s, %s, 'agent', %s, 1, 0, 0, 'diverged', %s::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, agent_name,
                         _json.dumps([])))
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-fit', headers=admin_headers,
                       query_string={'defName': agent_name})
        assert r.status_code == 200
        rows = [f for f in r.get_json()['fits'] if f['attemptId'] == attempt]
        assert rows and {f['defName'] for f in rows} == {agent_name}
        r2 = client.get('/ai/chat/admin/skill-fit', headers=admin_headers,
                        query_string={'defKind': 'skill'})
        mine = [f for f in r2.get_json()['fits'] if f['attemptId'] == attempt]
        assert mine and {f['defKind'] for f in mine} == {'skill'}
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()


def test_skill_def_patterns_filter_by_def_name(client, admin_headers, db_conn, tmp_path):
    """defName 过滤：只回该定义的步骤级偏离聚合。"""
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    agent_name = f'demo-agent-{uuid.uuid4().hex[:6]}'
    step_row = _json.dumps([{'id': 'clone', 'name': '克隆仓库',
                             'status': 'miss', 'evidence': []}])
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, steps_total, steps_hit, score, status, per_step) "
                        "VALUES (%s, %s, %s, 'agent', %s, 1, 0, 0, 'diverged', %s::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, agent_name, step_row))
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-def-patterns', headers=admin_headers,
                       query_string={'defName': agent_name})
        assert r.status_code == 200
        pats = r.get_json()['patterns']
        assert pats and all(p['defName'] == agent_name for p in pats)
        assert any(p['stepId'] == 'clone' for p in pats)
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()


def test_skill_fit_definition_summary_union_and_metrics(client, admin_headers,
                                                        db_conn, tmp_path):
    """清单 = 版本表 ∪ 拟合结果并集（裸版本定义也出现）；指标与版本端点同口径。"""
    uid, bid, sid, attempt = _seed_fit_fixture(db_conn, tmp_path)
    agent_name = f'demo-agent-{uuid.uuid4().hex[:6]}'
    bare_name = f'bare-def-{uuid.uuid4().hex[:6]}'
    try:
        with db_conn.cursor() as cur:
            # def_hash='h1'：版本端点按 (def_kind, def_name, def_hash) 归因
            # 拟合行，末尾「与版本端点 fitRate 相等」断言要求该行可归因到
            # h1 版本（brief 种子漏了该列，NULL 永不等于 content_hash）。
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, def_hash, steps_total, steps_hit, score, status, per_step) "
                        "VALUES (%s, %s, %s, 'agent', %s, 'h1', 1, 0, 0, 'diverged', %s::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, agent_name,
                         _json.dumps([])))
            cur.execute("INSERT INTO ai_skill_def_versions (id, def_kind, def_name, content_hash) "
                        "VALUES (%s, 'agent', %s, 'h1'), (%s, 'agent', %s, 'h2')",
                        ('dv_' + uuid.uuid4().hex[:10], agent_name,
                         'dv_' + uuid.uuid4().hex[:10], bare_name))
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-fit/definition-summary', headers=admin_headers)
        assert r.status_code == 200
        defs = {f"{x['defKind']}/{x['defName']}": x
                for x in r.get_json()['definitions']}
        # 并集：从未跑任务的裸版本定义也在清单里
        bare = defs[f'agent/{bare_name}']
        assert bare['tasks'] == 0 and bare['fitRate'] is None
        # 指标口径
        d = defs[f'agent/{agent_name}']
        assert d['tasks'] == 1 and d['fitRate'] == 0.0 and d['divergedCount'] == 1
        assert d['versions'] == 1 and d['latestHash'] == 'h1'
        assert d['lastActivity'] is not None
        # 与版本端点口径一致（同一份数据 fitRate 相等）
        v = client.get('/ai/chat/admin/skill-def-versions', headers=admin_headers,
                       query_string={'defName': agent_name})
        vr = next(x for x in v.get_json()['versions'] if x['contentHash'] == 'h1')
        assert vr['fitRate'] == d['fitRate']
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_fit_results WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id=%s", (attempt,))
            cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
            cur.execute("DELETE FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s", (bid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
            cur.execute("DELETE FROM ai_skill_def_versions WHERE def_kind='agent' "
                        "AND def_name IN (%s, %s)", (agent_name, bare_name))
        db_conn.commit()


def test_skill_def_steps_apply_registers_version(client, admin_headers, db_conn,
                                                 tmp_path, monkeypatch):
    """apply 写盘后登记定义版本并归档正文（spec §4.2）：hash 取自落盘
    内容回读；默认 label「AI步骤优化 <日期>」；versionLabel 显式覆盖。"""
    import config as _config
    import routes.ai_session_admin as _admin
    monkeypatch.setattr(_config, 'AI_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr(_admin, 'log_operation', lambda *a, **kw: None)
    name = f'demo-skill-{uuid.uuid4().hex[:6]}'
    d = tmp_path / name
    d.mkdir()
    p = d / 'SKILL.md'
    p.write_text('# v1\n', encoding='utf-8')
    steps = [{'id': 's1', 'name': '步骤1', 'expect': [{'tool': 'bash'}]}]
    try:
        r = client.post('/ai/chat/admin/skill-def-steps/apply',
                        headers=admin_headers,
                        json={'path': str(p), 'steps': steps})
        assert r.status_code == 200
        chash = hashlib.sha256(p.read_bytes()).hexdigest()
        with db_conn.cursor() as cur:
            cur.execute("SELECT content, version_label, content_captured_at "
                        "FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, chash))
            row = cur.fetchone()
        assert row and row[0] == p.read_bytes().decode('utf-8')
        assert row[1] and row[1].startswith('AI步骤优化 ')
        assert row[2] is not None
        r2 = client.post('/ai/chat/admin/skill-def-steps/apply',
                         headers=admin_headers,
                         json={'path': str(p), 'steps': steps,
                               'versionLabel': '手工标注'})
        assert r2.status_code == 200
        chash2 = hashlib.sha256(p.read_bytes()).hexdigest()
        with db_conn.cursor() as cur:
            cur.execute("SELECT version_label FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s AND content_hash=%s",
                        (name, chash2))
            assert cur.fetchone()[0] == '手工标注'
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def _seed_two_versions(db_conn, name):
    """种子：同一定义两个已归档版本 + 一个未归档版本。返回 (id_v1, id_v2, id_v0)。"""
    from utils.skill_fit import register_def_version
    h1 = hashlib.sha256('v1 正文'.encode('utf-8')).hexdigest()
    h2 = hashlib.sha256('v1 正文\n+v2 新增行'.encode('utf-8')).hexdigest()
    ids = {}
    with db_conn.cursor() as cur:
        register_def_version(cur, 'skill', name, h1, content='v1 正文')
        cur.execute("SELECT id FROM ai_skill_def_versions WHERE def_kind='skill' "
                    "AND def_name=%s AND content_hash=%s", (name, h1))
        ids['v1'] = cur.fetchone()[0]
        register_def_version(cur, 'skill', name, h2,
                             content='v1 正文\n+v2 新增行')
        cur.execute("SELECT id FROM ai_skill_def_versions WHERE def_kind='skill' "
                    "AND def_name=%s AND content_hash=%s", (name, h2))
        ids['v2'] = cur.fetchone()[0]
        register_def_version(cur, 'skill', name, 'a' * 64)   # 未归档
        cur.execute("SELECT id FROM ai_skill_def_versions WHERE def_kind='skill' "
                    "AND def_name=%s AND content_hash=%s", (name, 'a' * 64))
        ids['v0'] = cur.fetchone()[0]
    db_conn.commit()
    return ids['v1'], ids['v2'], ids['v0']


def test_skill_def_version_content_endpoint(client, admin_headers, db_conn):
    """content 端点：归档版本回全文；未归档 400；不存在 404。"""
    name = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    v1, v2, v0 = _seed_two_versions(db_conn, name)
    try:
        r = client.get(f'/ai/chat/admin/skill-def-versions/{v2}/content',
                       headers=admin_headers)
        assert r.status_code == 200
        body = r.get_json()
        assert body['content'] == 'v1 正文\n+v2 新增行'
        assert body['defName'] == name and body['defKind'] == 'skill'
        assert body['contentHash'] == hashlib.sha256(
            'v1 正文\n+v2 新增行'.encode('utf-8')).hexdigest()
        r0 = client.get(f'/ai/chat/admin/skill-def-versions/{v0}/content',
                        headers=admin_headers)
        assert r0.status_code == 400 and r0.get_json()['error'] == '版本未归档'
        r404 = client.get('/ai/chat/admin/skill-def-versions/defv_nosuch/content',
                          headers=admin_headers)
        assert r404.status_code == 404
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()


def test_skill_def_versions_compare_endpoint(client, admin_headers, db_conn):
    """compare 端点：相邻版本 unified diff；未归档 400；跨定义 400。"""
    from utils.skill_fit import register_def_version
    name = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    v1, v2, v0 = _seed_two_versions(db_conn, name)
    other = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    try:
        with db_conn.cursor() as cur:
            register_def_version(cur, 'skill', other,
                                 hashlib.sha256(b'x').hexdigest(), content='x')
            cur.execute("SELECT id FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (other,))
            other_id = cur.fetchone()[0]
        db_conn.commit()
        r = client.get('/ai/chat/admin/skill-def-versions/compare',
                       headers=admin_headers,
                       query_string={'fromId': v1, 'toId': v2})
        assert r.status_code == 200
        body = r.get_json()
        assert '+v2 新增行' in body['diff']
        assert body['from']['id'] == v1 and body['to']['id'] == v2
        r0 = client.get('/ai/chat/admin/skill-def-versions/compare',
                        headers=admin_headers,
                        query_string={'fromId': v0, 'toId': v2})
        assert r0.status_code == 400 and r0.get_json()['error'] == '版本未归档'
        rx = client.get('/ai/chat/admin/skill-def-versions/compare',
                        headers=admin_headers,
                        query_string={'fromId': v1, 'toId': other_id})
        assert rx.status_code == 400 and '同一定义' in rx.get_json()['error']
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name IN (%s, %s)",
                        (name, other))
        db_conn.commit()


def test_skill_def_versions_list_has_archived_flag(client, admin_headers, db_conn):
    """列表端点补 archived/contentCapturedAt（UI 置灰依据）。"""
    name = f'cmp-skill-{uuid.uuid4().hex[:6]}'
    v1, v2, v0 = _seed_two_versions(db_conn, name)
    try:
        r = client.get('/ai/chat/admin/skill-def-versions', headers=admin_headers,
                       query_string={'defName': name})
        assert r.status_code == 200
        rows = {v['id']: v for v in r.get_json()['versions']}
        assert rows[v2]['archived'] is True and rows[v2]['contentCapturedAt']
        assert rows[v0]['archived'] is False and rows[v0]['contentCapturedAt'] is None
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_skill_def_versions "
                        "WHERE def_kind='skill' AND def_name=%s", (name,))
        db_conn.commit()
