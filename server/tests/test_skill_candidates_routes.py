# -*- coding: utf-8 -*-
"""O2 候选端点测试：创建/验证/发布/拒绝 + R2 去重 + 基线漂移防护 + 过期联动。"""
import hashlib
import uuid

import pytest

BASE_MD = (
    '---\ndescription: demo\nfit:\n  steps:\n'
    '    - id: clone\n      name: 克隆\n      expect:\n'
    "        - tool: bash\n          args_pattern: 'git clone'\n"
    '---\n正文\n')
EDITS = [{'old_str': '正文', 'new_str': '正文 v2', 'reason': '补一句'}]


@pytest.fixture
def client():
    """真实 DB test client（shadow conftest 的 mock-DB client，
    同 test_skill_fit_routes.py:35-58 模式）。"""
    import sys
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


def _seed(client, db_conn, tmp_path):
    """种子 def 版本（正文归档）+ 指向真实临时文件的 manifest（供 promote
    定位路径）。种子文件必须落在 _path_in_allowed_roots 的允许根内——
    系统 temp 不在根内必 400，用 AI_WORKSPACE_ROOT 下子目录（T9 预检裁定）。"""
    import json as _j
    from pathlib import Path
    from config import AI_WORKSPACE_ROOT
    uid, sid, aid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    name = f'route_{uuid.uuid4().hex[:8]}'
    chash = hashlib.sha256(BASE_MD.encode()).hexdigest()
    md_dir = Path(AI_WORKSPACE_ROOT) / f'test-cand-{uuid.uuid4().hex[:8]}'
    md_dir.mkdir(parents=True, exist_ok=True)
    md = md_dir / 'SKILL.md'
    md.write_text(BASE_MD, encoding='utf-8')
    fit_id = 'fit_' + uuid.uuid4().hex[:10]
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'cr_{uid[:8]}'))
        cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                    "VALUES (%s, %s, 'completed', %s)", (sid, uid, f'tok-{sid[:12]}'))
        cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (aid, sid))
        cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                    "  source, path, content_hash, injected) "
                    "VALUES (%s, %s, 'skill', %s, 'session', %s, %s, true)",
                    ('man_' + uuid.uuid4().hex[:8], aid, name, str(md), chash))
        cur.execute("INSERT INTO ai_skill_invocations (id, session_id, attempt_id, "
                    "  skill_name, skill_hash, source, evidence_level, evidence_refs) "
                    "VALUES (%s, %s, %s, %s, '', 'runtime', 'confirmed', '[]'::jsonb)",
                    ('inv_' + uuid.uuid4().hex[:10], sid, aid, name))
        cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                    "  def_kind, def_name, def_hash, steps_total, steps_hit, score, "
                    "  status, per_step, diagnosis) "
                    "VALUES (%s, %s, %s, 'skill', %s, %s, 1, 0, 0, 'diverged', "
                    "  '[]'::jsonb, %s::jsonb)",
                    (fit_id, aid, sid, name, chash,
                     _j.dumps({'cause': 'definition_stale', 'suggestions': [],
                               'revised_steps': [],
                               'edits': EDITS,
                               'expected_repair': 'E',
                               'possibly_harmed': []})))
        cur.execute("INSERT INTO ai_skill_def_versions (id, def_kind, def_name, "
                    "  content_hash, version_label, content, content_captured_at, "
                    "  first_seen_at) VALUES (%s, 'skill', %s, %s, 'base', %s, "
                    "  NOW(), NOW())",
                    ('defv_' + uuid.uuid4().hex[:12], name, chash, BASE_MD))
    db_conn.commit()  # db_conn 非 autocommit（T7-6）
    return {'name': name, 'chash': chash, 'attemptId': aid, 'fitResultId': fit_id,
            'mdPath': str(md), 'mdDir': str(md_dir),
            'userId': uid, 'sessionId': sid}


def _cleanup(db_conn, seeded):
    import shutil
    if seeded.get('mdDir'):
        shutil.rmtree(seeded['mdDir'], ignore_errors=True)
    name = seeded['name']
    with db_conn.cursor() as cur:
        for table, col in (('ai_skill_candidates', 'def_name'),
                           ('ai_skill_fit_results', 'def_name'),
                           ('ai_skill_def_versions', 'def_name')):
            cur.execute(f"DELETE FROM {table} WHERE {col} = %s", (name,))
        cur.execute("DELETE FROM agent_tool_calls WHERE root_session_id = %s",
                    (seeded['sessionId'],))
        cur.execute("DELETE FROM ai_skill_invocations WHERE session_id = %s",
                    (seeded['sessionId'],))
        cur.execute("DELETE FROM ai_execution_manifests WHERE attempt_id = %s",
                    (seeded['attemptId'],))
        cur.execute("DELETE FROM ai_execution_attempts WHERE id = %s",
                    (seeded['attemptId'],))
        cur.execute("DELETE FROM ai_chat_sessions WHERE id = %s",
                    (seeded['sessionId'],))
        cur.execute("DELETE FROM users WHERE id = %s", (seeded['userId'],))
    db_conn.commit()


def test_create_validate_reject_cycle(client, admin_headers, db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        resp = client.post('/ai/chat/admin/skill-candidates',
                           headers=admin_headers,
                           json={'fitResultId': s['fitResultId']})
        assert resp.status_code == 201
        cand = resp.get_json()['candidate']
        assert cand['status'] == 'pending'
        assert cand['edits'] == EDITS
        assert cand['source']['expectedRepair'] == 'E'
        # 正式定义文件未被触碰
        assert open(s['mdPath'], encoding='utf-8').read() == BASE_MD

        # 拒绝后不可再验证
        resp2 = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/reject",
            headers=admin_headers)
        assert resp2.status_code == 200
        resp3 = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/validate",
            headers=admin_headers)
        assert resp3.status_code == 400  # 非 pending 不可验证
    finally:
        _cleanup(db_conn, s)


def test_promote_writes_file_and_registers_version(client, admin_headers,
                                                   db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        cand = client.post('/ai/chat/admin/skill-candidates',
                           headers=admin_headers,
                           json={'fitResultId': s['fitResultId']}
                           ).get_json()['candidate']
        # 单步定义凑不齐双集 → validate 得 insufficient；为走通 promote，
        # 人工把状态抬到 validated（测试专用路径）
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_skill_candidates SET status='validated' "
                        "WHERE id=%s", (cand['id'],))
        db_conn.commit()
        resp = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/promote",
            headers=admin_headers)
        assert resp.status_code == 200
        new_text = open(s['mdPath'], encoding='utf-8').read()
        assert '正文 v2' in new_text
        assert hashlib.sha256(new_text.encode()).hexdigest() == \
            resp.get_json()['contentHash']
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, promoted_version_id FROM ai_skill_candidates "
                        "WHERE id=%s", (cand['id'],))
            status, vid = cur.fetchone()
            assert status == 'promoted' and vid
            cur.execute("SELECT content FROM ai_skill_def_versions WHERE id=%s",
                        (vid,))
            assert cur.fetchone()[0] == new_text
    finally:
        _cleanup(db_conn, s)


def test_promote_rejects_hash_drift(client, admin_headers, db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        cand = client.post('/ai/chat/admin/skill-candidates',
                           headers=admin_headers,
                           json={'fitResultId': s['fitResultId']}
                           ).get_json()['candidate']
        with db_conn.cursor() as cur:
            cur.execute("UPDATE ai_skill_candidates SET status='validated' "
                        "WHERE id=%s", (cand['id'],))
            drifted = hashlib.sha256(b'drifted').hexdigest()
            # T9-2：def_versions.id 无默认值，显式 id
            cur.execute("INSERT INTO ai_skill_def_versions (id, def_kind, def_name, "
                        "  content_hash, first_seen_at) "
                        "VALUES (%s, 'skill', %s, %s, NOW() + interval '1 second')",
                        ('defv_' + uuid.uuid4().hex[:12], s['name'], drifted))
        db_conn.commit()
        resp = client.post(
            f"/ai/chat/admin/skill-candidates/{cand['id']}/promote",
            headers=admin_headers)
        assert resp.status_code == 409
        assert open(s['mdPath'], encoding='utf-8').read() == BASE_MD
        # 漂移防护把未决候选一起过期
        with db_conn.cursor() as cur:
            cur.execute("SELECT status FROM ai_skill_candidates WHERE id=%s",
                        (cand['id'],))
            assert cur.fetchone()[0] == 'expired'
    finally:
        _cleanup(db_conn, s)


def test_feedback_applied_with_candidate_draft(client, admin_headers,
                                               db_conn, tmp_path):
    s = _seed(client, db_conn, tmp_path)
    try:
        # feedback 依赖 ai_execution_diagnoses 行——T9-1：表无 operation 列，
        # 种 (id, target_session_id)，status 走默认 pending
        did = 'diag_' + uuid.uuid4().hex[:8]
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_execution_diagnoses (id, target_session_id) "
                        "VALUES (%s, %s)", (did, s['sessionId']))
        db_conn.commit()
        resp = client.post(
            f"/ai/chat/admin/analyses/{did}/suggestions/sug1/feedback",
            headers=admin_headers,
            json={'action': 'applied',
                  'appliedValue': '{"skillName": "%s"}' % s['name'],
                  'candidate': {'fitResultId': s['fitResultId']}})
        assert resp.status_code == 200
        assert resp.get_json().get('candidateId')
        # 反馈行与候选同事务落库
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai_suggestion_feedback "
                        "WHERE diagnosis_id=%s AND action='applied'", (did,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM ai_skill_candidates "
                        "WHERE def_name=%s AND status='pending'", (s['name'],))
            assert cur.fetchone()[0] == 1
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_suggestion_feedback WHERE diagnosis_id=%s",
                        (did,))
            cur.execute("DELETE FROM ai_execution_diagnoses WHERE id=%s", (did,))
        db_conn.commit()
        _cleanup(db_conn, s)


def test_create_duplicate_returns_409_with_existing_id(client, admin_headers,
                                                       db_conn, tmp_path):
    """R2 去重：同一 (def, 基线, diff) 的未决候选重复创建 → 409 + 既有 id。"""
    s = _seed(client, db_conn, tmp_path)
    try:
        first = client.post('/ai/chat/admin/skill-candidates',
                            headers=admin_headers,
                            json={'fitResultId': s['fitResultId']})
        assert first.status_code == 201
        dup = client.post('/ai/chat/admin/skill-candidates',
                          headers=admin_headers,
                          json={'fitResultId': s['fitResultId']})
        assert dup.status_code == 409
        assert dup.get_json()['existingId'] == \
            first.get_json()['candidate']['id']
    finally:
        _cleanup(db_conn, s)
