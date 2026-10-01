# -*- coding: utf-8 -*-
"""AI 步骤生成器 / 回写 / 偏差诊断单元（打桩 AI 通道）。"""
import json as _json
import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _patch_ai(monkeypatch, content):
    """照抄 test_action_check_extractor.py 的打桩模式：AI 设置 + HTTP 会话。"""
    import utils.skill_fit_ai as fa
    monkeypatch.setattr(fa, 'get_ai_settings',
                        lambda: {'enabled': True, 'apiKey': 'k', 'endpoint': 'http://x',
                                 'model': 'm', 'timeout': 5, 'maxTokens': 1024})
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'choices': [{'message': {'content': content}}]}
    monkeypatch.setattr(fa, 'get_http_session', lambda: MagicMock(post=lambda *a, **kw: resp))


GOOD_STEPS = _json.dumps({'steps': [
    {'id': 'clone', 'name': '克隆仓库',
     'expect': [{'tool': 'bash', 'args_pattern': 'git clone'}]},
    {'id': 'report', 'name': '写报告',
     'expect': [{'tool': 'write', 'args_pattern': 'outputs?/'}]},
]}, ensure_ascii=False)


def test_generate_steps_schema_constrained(monkeypatch):
    _patch_ai(monkeypatch, GOOD_STEPS)
    from utils.skill_fit_ai import generate_steps
    steps = generate_steps('# 数据拉取技能\n1. clone 2. report')
    assert [s['id'] for s in steps] == ['clone', 'report']
    assert steps[0]['expect'] == [{'tool': 'bash', 'args_pattern': 'git clone'}]


def test_generate_steps_invalid_output_raises(monkeypatch):
    _patch_ai(monkeypatch, '完全不是 JSON')
    from utils.skill_fit_ai import generate_steps
    with pytest.raises(RuntimeError):
        generate_steps('x')


def test_apply_steps_merges_frontmatter(tmp_path):
    from utils.skill_fit_ai import apply_steps
    f = tmp_path / 'SKILL.md'
    f.write_text('---\ndescription: 数据拉取\n其他: 保留\n---\n\n正文内容。\n',
                 encoding='utf-8')
    steps = [{'id': 'clone', 'name': '克隆仓库',
              'expect': [{'tool': 'bash', 'args_pattern': 'git clone'}]}]
    apply_steps(str(f), steps)
    out = f.read_text(encoding='utf-8')
    assert 'description: 数据拉取' in out and '其他: 保留' in out   # 原字段保留
    assert 'fit:' in out and 'git clone' in out
    assert out.endswith('正文内容。\n')                              # 正文保留


def test_apply_steps_rejects_bad_regex(tmp_path):
    from utils.skill_fit_ai import apply_steps
    f = tmp_path / 'SKILL.md'
    f.write_text('---\ndescription: x\n---\n正文\n', encoding='utf-8')
    with pytest.raises(ValueError):
        apply_steps(str(f), [{'id': 'a', 'name': 'a',
                              'expect': [{'tool': 'bash', 'args_pattern': '('}]}])
    assert 'fit:' not in f.read_text(encoding='utf-8')   # 未写文件


def test_diagnose_shape_and_cache(monkeypatch, db_conn, tmp_path):
    """种子 partial 结果行 → diagnose 返回结构化诊断并落 diagnosis 列；
    同轨迹签名二次调用不再打 LLM（缓存命中）。"""
    import uuid as _uuid
    from db import get_db
    import utils.skill_fit_ai as fa
    uid, sid, attempt, result = (str(_uuid.uuid4()) for _ in range(4))
    md = tmp_path / 'SKILL.md'
    md.write_text('---\nfit:\n  steps:\n    - id: a\n      name: A\n'
                  '      expect:\n        - tool: bash\n---\n正文\n', encoding='utf-8')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'DG', 'developer')", (uid, f'dg_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                        "VALUES (%s, %s, 'completed', %s)",
                        (sid, uid, f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, def_hash, status, per_step) "
                        "VALUES (%s, %s, %s, 'skill', 'demo', 'h1', 'partial', %s::jsonb)",
                        (result, attempt, sid,
                         _json.dumps([{'id': 'a', 'name': 'A', 'status': 'miss',
                                       'evidence': []}])))
    db_conn.commit()
    diagnosis = {'cause': 'definition_stale', 'suggestions': ['更新步骤'],
                 'revised_steps': [{'id': 'a', 'name': 'A', 'expect': []}]}
    calls = []
    monkeypatch.setattr(fa, '_llm_json',
                        lambda system, user: calls.append(1) or diagnosis)
    try:
        from utils.skill_fit_ai import diagnose_result
        d1 = diagnose_result(result, get_db=get_db)
        assert d1['cause'] == 'definition_stale'
        d2 = diagnose_result(result, get_db=get_db)
        assert len(calls) == 1                      # 缓存命中，LLM 只调一次
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT diagnosis->>'cause' FROM ai_skill_fit_results "
                            "WHERE id=%s", (result,))
                assert cur.fetchone()[0] == 'definition_stale'
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_skill_fit_results WHERE id=%s", (result,))
                cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
                cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        db_conn.commit()


def test_preview_steps_matches(tmp_path):
    """种子 attempt + 账本轨迹（bash git clone x）→ 建议 steps 试算命中：
    status='fit'、score=100（纯试算，无结果行落库）。"""
    import uuid as _uuid
    from db import get_db
    from utils.skill_fit_ai import preview_steps
    uid, sid, attempt, part = (str(_uuid.uuid4()) for _ in range(4))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                        "VALUES (%s, %s, 'x', 'PV', 'developer')", (uid, f'pv_{uid[:8]}'))
            cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                        "VALUES (%s, %s, 'completed', %s)",
                        (sid, uid, f'tok-{sid[:12]}'))
            cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                        "  operation, started_at, finished_at) "
                        "VALUES (%s, %s, 'batch', 'send', "
                        "  NOW() - interval '1 minute', NOW() + interval '1 minute')",
                        (attempt, sid))
            cur.execute("INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                        "  part_id, tool, args_text, occurred_at) "
                        "VALUES (%s, %s, %s, 'bash', 'git clone x', NOW())",
                        (sid, sid, f'pv-{part}', ))
    try:
        steps = [{'id': 'clone', 'name': '克隆仓库',
                  'expect': [{'tool': 'bash', 'args_pattern': 'git clone'}]}]
        preview = preview_steps(steps, attempt, get_db=get_db)
        assert preview['status'] == 'fit'
        assert preview['score'] == 100
        assert preview['steps_total'] == 1 and preview['steps_hit'] == 1
        assert preview['per_step'][0]['status'] == 'hit'
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM ai_skill_fit_results "
                            "WHERE attempt_id=%s", (attempt,))
                assert cur.fetchone()[0] == 0          # 纯试算不落库
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM agent_tool_calls WHERE oc_session_id=%s", (sid,))
                cur.execute("DELETE FROM ai_execution_attempts WHERE id=%s", (attempt,))
                cur.execute("DELETE FROM ai_chat_sessions WHERE id=%s", (sid,))
                cur.execute("DELETE FROM users WHERE id=%s", (uid,))


# ── 路由层（真实 DB 的 client，shadow 模式同 test_skill_fit_routes.py） ────

@pytest.fixture
def client():
    """真实 DB 的 test client（shadow 掉 conftest 的 mock-DB `client`）。

    断言走真库（attempt 缺失 → 404），并把 utils.skill_fit_ai 的
    `_default_get_db` 重绑回真实实现——防止该模块在早前 app 夹具的
    db.get_db mock 窗口内首次导入时绑到 mock 连接。权限判定走 conftest
    autouse 的 RBAC 预置缓存（admin=superuser），不需要 DB。"""
    import db as _db
    import utils.skill_fit_ai as fa
    from app import app as flask_app
    fa._default_get_db = _db.get_db
    flask_app.config['TESTING'] = True
    return flask_app.test_client()


def test_preview_steps_attempt_missing_404(client, admin_headers):
    """种子不建 attempt：preview 路由对不存在的 attemptId 返回 404。"""
    r = client.post('/ai/chat/admin/skill-def-steps/preview',
                    headers=admin_headers,
                    json={'attemptId': 'no-such-attempt',
                          'steps': [{'id': 'a', 'name': 'A', 'expect': []}]})
    assert r.status_code == 404
