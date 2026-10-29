# -*- coding: utf-8 -*-
"""O2 候选评估测试：diff 校验/结构守卫/双集回归门槛/双模分类/promote 前置校验。"""
import json
import uuid

from utils.skill_candidate_eval import (
    apply_edits, collect_validation_sets, run_validation, score_steps_on_attempts,
    structural_guard,
)
from utils.skill_fit import parse_fit_steps

# 定义两步：clone（新旧同）+ install（旧模式 'pip install -r requirements'
# 拼错路径；候选改成 'pip install'）。边界轨迹只跑了错误路径 → 旧定义
# partial、新定义 fit；保留轨迹两步都正常 → 新旧都 fit（零退化）。
BASE_MD = (
    '---\ndescription: demo\nfit:\n  steps:\n'
    '    - id: clone\n      name: 克隆\n      expect:\n'
    "        - tool: bash\n          args_pattern: 'git clone'\n"
    '    - id: install\n      name: 安装\n      expect:\n'
    "        - tool: bash\n          args_pattern: 'pip install -r requirements'\n"
    '---\n正文\n')
EDITS = [{'old_str': "args_pattern: 'pip install -r requirements'",
          'new_str': "args_pattern: 'pip install'",
          'reason': '放宽安装匹配'}]


def test_apply_edits_unique_match():
    new, err = apply_edits(BASE_MD, EDITS)
    assert err is None and "args_pattern: 'pip install'\n" in new


def test_apply_edits_rejects_ambiguous():
    dup = BASE_MD.replace('正文\n', "args_pattern: 'pip install -r requirements'\n")
    _, err = apply_edits(dup, EDITS)
    assert err and '唯一' in err


def test_apply_edits_rejects_noop():
    _, err = apply_edits(BASE_MD, [
        {'old_str': 'x', 'new_str': 'x'}])
    assert err


def test_apply_edits_rejects_step_id_change():
    """R3 结构守卫：edits 不得增删步骤 id。"""
    _, err = apply_edits(BASE_MD, [
        {'old_str': "    - id: install\n      name: 安装\n      expect:\n",
         'new_str': '', 'reason': '删步骤'}])
    assert err and '步骤 id' in err


def test_structural_guard_requires_expected_repair():
    """R3：expect 收窄必须声明 expected_repair。"""
    narrowed = BASE_MD.replace(
        "        - tool: bash\n          args_pattern: 'pip install -r requirements'\n",
        '')
    assert structural_guard(BASE_MD, narrowed, '') and 'expect' in structural_guard(
        BASE_MD, narrowed, '')
    assert structural_guard(BASE_MD, narrowed, '安装改为可选') is None


def test_validation_body_only_edit_is_behavioral(db_conn):
    """R4：纯正文（不触及 fit.steps）修改走 behavioral 模式直接 validated，
    不会被「边界无改善」误杀；人工 promote 仍是闸门。"""
    name, chash, _ = _seed_def_and_attempts(db_conn)
    cid = 'cand_' + uuid.uuid4().hex[:10]
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO ai_skill_candidates (id, def_kind, def_name, "
                    "  base_content_hash, edits, source) "
                    "VALUES (%s, 'skill', %s, %s, %s::jsonb, '{}'::jsonb)",
                    (cid, name, chash,
                     json.dumps([{'old_str': '正文',
                                  'new_str': '正文（补充：安装前先确认网络）',
                                  'reason': '补充提醒'}])))
    db_conn.commit()  # T7-6
    v = run_validation(cid)
    assert v['mode'] == 'behavioral' and v['decision'] == 'validated'


def _seed_def_and_attempts(db_conn, *, boundary_n=2, holdout_n=3):
    """种子 def 版本（content=BASE_MD）+ boundary（只跑 clone+错误路径）
    与 holdout（clone+正确路径）两类 attempt，含 manifest/invocation/
    agent_tool_calls/fit 结果行。返回 (def_name, chash, attempt_ids)。"""
    uid = str(uuid.uuid4())
    name = f'cand_{uuid.uuid4().hex[:8]}'
    import hashlib
    chash = hashlib.sha256(BASE_MD.encode()).hexdigest()
    ids = {'boundary': [], 'holdout': []}
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'ce_{uid[:8]}'))
        cur.execute("INSERT INTO ai_skill_def_versions (id, def_kind, def_name, content_hash, "
                    "  version_label, content, content_captured_at, first_seen_at) "
                    "VALUES (%s, 'skill', %s, %s, 'base', %s, NOW(), NOW())",
                    ('defv_' + uuid.uuid4().hex[:12], name, chash, BASE_MD))
        for kind, tools in (('boundary', ["git clone origin", "pip install -r ./wrong.txt"]),
                            ('holdout', ["git clone origin", "pip install -r requirements.txt"])):
            for i in range({'boundary': boundary_n, 'holdout': holdout_n}[kind]):
                sid, aid = str(uuid.uuid4()), str(uuid.uuid4())
                ids[kind].append(aid)
                cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                            "VALUES (%s, %s, 'completed', %s)", (sid, uid, f'tok-{sid[:12]}'))
                # T7-1 裁定：_load_trace 窗口 BETWEEN started AND COALESCE(finished,NOW())，
                # autocommit 逐语句 NOW() 递进——finished 必须留 NULL，后插的账本调用才在窗内
                cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                            "  operation, started_at, finished_at) "
                            "VALUES (%s, %s, 'batch', 'send', NOW() - interval '5 minutes', NULL)",
                            (aid, sid))
                cur.execute("INSERT INTO ai_execution_manifests (id, attempt_id, kind, name, "
                            "  source, path, content_hash, injected) "
                            "VALUES (%s, %s, 'skill', %s, 'session', '/tmp/na.md', %s, true)",
                            ('man_' + uuid.uuid4().hex[:8], aid, name, chash))
                cur.execute("INSERT INTO ai_skill_invocations (id, session_id, attempt_id, "
                            "  skill_name, skill_hash, source, evidence_level, evidence_refs) "
                            "VALUES (%s, %s, %s, %s, '', 'runtime', 'confirmed', '[]'::jsonb)",
                            ('inv_' + uuid.uuid4().hex[:10], sid, aid, name))
                for t in tools:
                    cur.execute("INSERT INTO agent_tool_calls (oc_session_id, root_session_id, "
                                "  part_id, tool, args_text, state, occurred_at) "
                                "VALUES (%s, %s, %s, 'bash', %s, 'completed', NOW())",
                                (f'oc-{aid[:8]}-{t[:6]}', sid,
                                 f'p-{uuid.uuid4().hex[:8]}', t))
                fit_status = 'partial' if kind == 'boundary' else 'fit'
                fit_score = 50 if kind == 'boundary' else 100
                cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                            "  def_kind, def_name, def_hash, steps_total, steps_hit, score, "
                            "  status, per_step) "
                            "VALUES (%s, %s, %s, 'skill', %s, %s, 2, %s, %s, %s, '[]'::jsonb)",
                            ('fit_' + uuid.uuid4().hex[:10], aid, sid, name, chash,
                             1 if kind == 'boundary' else 2, fit_score, fit_status))
    db_conn.commit()  # db_conn 非 autocommit：get_db 读侧要看见种子必须提交（T7-6）
    return name, chash, ids


def _seed_candidate(db_conn, name, chash):
    cid = 'cand_' + uuid.uuid4().hex[:10]
    with db_conn.cursor() as cur:
        # T7-2 裁定：json.dumps（str().replace 对含单引号值产出非法 JSON）
        cur.execute("INSERT INTO ai_skill_candidates (id, def_kind, def_name, "
                    "  base_content_hash, edits, source) "
                    "VALUES (%s, 'skill', %s, %s, %s::jsonb, '{}'::jsonb)",
                    (cid, name, chash, json.dumps(EDITS)))
    db_conn.commit()  # T7-6
    return cid


def test_validation_boundary_improves_holdout_stable(db_conn):
    name, chash, ids = _seed_def_and_attempts(db_conn)
    cid = _seed_candidate(db_conn, name, chash)
    v = run_validation(cid)
    assert v['decision'] == 'validated'
    b = {r['attemptId']: r for r in v['boundary']}
    for aid in ids['boundary']:
        assert b[aid]['after']['score'] > b[aid]['before']['score']
    for r in v['holdout']:
        assert r['after']['status'] == 'fit'


def test_validation_rejects_on_holdout_regression(db_conn):
    """终审必修②：保留集零退化硬门槛的否定用例——边界改善但保留集退化
    必须 rejected（regressed 分支），rejectReason 指向保留集。"""
    name, chash, _ = _seed_def_and_attempts(db_conn)
    cid = 'cand_' + uuid.uuid4().hex[:10]
    # 候选把安装模式改成只匹配 './' 路径：边界（./wrong.txt）改善，
    # 保留（requirements.txt，无 ./）退化 → 命中 regressed 分支
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO ai_skill_candidates (id, def_kind, def_name, "
                    "  base_content_hash, edits, source) "
                    "VALUES (%s, 'skill', %s, %s, %s::jsonb, '{}'::jsonb)",
                    (cid, name, chash,
                     json.dumps([{'old_str': "args_pattern: 'pip install -r requirements'",
                                  'new_str': "args_pattern: 'pip install -r \\./'",
                                  'reason': '只认相对路径'}])))
    db_conn.commit()
    v = run_validation(cid)
    assert v['decision'] == 'rejected'
    assert v['regressed'], v
    assert '保留集' in v['rejectReason']


def test_validation_insufficient_evidence_stays_pending(db_conn):
    name, chash, _ = _seed_def_and_attempts(db_conn)
    cid = _seed_candidate(db_conn, name, chash)
    # holdout 3→2 < 门槛 3 → insufficient，status 仍 pending
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_skill_fit_results WHERE def_name=%s AND status='fit' "
                    "AND attempt_id IN (SELECT attempt_id FROM ai_skill_fit_results "
                    "WHERE def_name=%s AND status='fit' LIMIT 1)", (name, name))
    db_conn.commit()  # T7-6
    v = run_validation(cid)
    assert v.get('insufficientEvidence') and v['holdoutCount'] < 3
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM ai_skill_candidates WHERE id=%s", (cid,))
        assert cur.fetchone()[0] == 'pending'


def test_score_matches_trace_not_definition_file(db_conn):
    name, chash, ids = _seed_def_and_attempts(db_conn)
    steps = parse_fit_steps(BASE_MD)
    rows = score_steps_on_attempts(steps, ids['holdout'])
    assert all(r['status'] == 'fit' for r in rows)
