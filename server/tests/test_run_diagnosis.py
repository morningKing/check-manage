# -*- coding: utf-8 -*-
"""O1 运行诊断测试：维度判定规则 / overall 聚合 / 幂等 / fail 必带证据。

fixture 说明：全部用例只用 conftest 的真实 DB `db_conn`（casemanage_test，
sessionstart 自动引导）。种子先经 db_conn 提交，聚合器再走 db.get_db 池
连接读取——与 test_skill_fit_routes.py 的真实 DB 模式一致。
"""
import uuid

from utils.run_diagnosis import build_run_diagnosis, DIMENSION_KEYS


def test_table_exists(db_conn):
    # 存在性检查（Controller 2026-10-27）：不再断言行数——跨运行残留的
    # ai_run_diagnoses 行不影响本用例，只验证迁移已建表。
    with db_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('ai_run_diagnoses') IS NOT NULL")
        assert cur.fetchone()[0] is True


def _seed_attempt(db_conn, *, session_status='completed', exp_status=None,
                  step_rows=None, fit_status=None):
    """种子 attempt + 可选的门禁期望行/步骤核对行/拟合行。
    返回 (attempt_id, session_id)。"""
    uid, sid, attempt = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (id, username, password_hash, display_name, role) "
                    "VALUES (%s, %s, 'x', 'FR', 'developer')", (uid, f'rd_{uid[:8]}'))
        cur.execute("INSERT INTO ai_chat_sessions (id, user_id, status, session_token) "
                    "VALUES (%s, %s, %s, %s)", (sid, uid, session_status, f'tok-{sid[:12]}'))
        cur.execute("INSERT INTO ai_execution_attempts (id, session_id, source_type, "
                    "  operation, started_at, finished_at) "
                    "VALUES (%s, %s, 'batch', 'send', NOW(), NOW())", (attempt, sid))
        if exp_status is not None:
            cur.execute("INSERT INTO action_expectations (scope_type, scope_id, name, tool, "
                        "  args_pattern, source, last_status, last_checked_at, last_evidence) "
                        "VALUES ('tree', %s, 'e1', 'bash', 'git clone', 'policy', %s, NOW(), 1)",
                        (sid, exp_status))
        if step_rows is not None:
            for sr in step_rows:
                cur.execute("INSERT INTO ai_execution_step_results (id, attempt_id, step_id, "
                            "  expected, declared_by_agent, observed, status, evidence_level, "
                            "  confidence, duration_ms, evidence_refs, reason) "
                            "VALUES (%s, %s, %s, true, %s, %s, %s, 'observed', 0.9, 10, "
                            "  '[]'::jsonb, 'seed')",
                            ('stpr_' + uuid.uuid4().hex[:10], attempt,
                             sr.get('stepId', 's1'), sr.get('declared', True),
                             sr.get('observed', True), sr.get('status', 'completed')))
        if fit_status is not None:
            cur.execute("INSERT INTO ai_skill_fit_results (id, attempt_id, session_id, "
                        "  def_kind, def_name, def_hash, steps_total, steps_hit, score, "
                        "  status, per_step) "
                        "VALUES (%s, %s, %s, 'skill', 'demo-skill', 'h1', 2, 1, 50, %s, '[]'::jsonb)",
                        ('fit_' + uuid.uuid4().hex[:10], attempt, sid, fit_status))
    # 聚合器经 db.get_db 池连接读取，种子必须先提交（test_skill_fit_routes.py:104 同模式）
    db_conn.commit()
    return attempt, sid


def test_gate_failed_overrides_to_fail(db_conn):
    attempt, _ = _seed_attempt(db_conn, exp_status='failed')
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['rule_compliance']['status'] == 'fail'
    assert d['dimensions']['rule_compliance']['evidence']  # fail 必带证据
    assert d['overall'] == 'fail'


def test_no_expectations_is_uncertain_not_pass(db_conn):
    attempt, _ = _seed_attempt(db_conn)
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['rule_compliance']['status'] == 'uncertain'


def test_promise_action_claimed_without_evidence_fails(db_conn):
    attempt, _ = _seed_attempt(db_conn, step_rows=[
        {'status': 'completed_claimed', 'declared': True, 'observed': False}])
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['promise_action']['status'] == 'fail'
    assert d['dimensions']['promise_action']['evidence'][0]['type'] == 'step_result'


def test_overall_partial_when_only_fit_partial(db_conn):
    attempt, _ = _seed_attempt(db_conn, fit_status='partial')
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['skill_fit']['status'] in ('fail', 'pass')
    assert d['overall'] in ('partial', 'fail')


def test_all_five_dimensions_always_present(db_conn):
    attempt, _ = _seed_attempt(db_conn)
    d = build_run_diagnosis(attempt)
    assert set(d['dimensions'].keys()) == set(DIMENSION_KEYS)


def test_upsert_idempotent(db_conn):
    attempt, _ = _seed_attempt(db_conn, exp_status='passed')
    d1 = build_run_diagnosis(attempt)
    d2 = build_run_diagnosis(attempt)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_run_diagnoses WHERE attempt_id=%s", (attempt,))
        assert cur.fetchone()[0] == 1
    # 该种子的确定性产出：task_result/rule_compliance=pass，
    # promise_action/skill_fit/performance 无证据=uncertain → overall=partial。
    #（'pass' 不在 overall 词表 success|partial|fail|uncertain 内，brief 原断言
    #  d2['overall']=='pass' 系笔误——实现与迁移/设计文档口径一致，见报告。）
    assert d2['overall'] == d1['overall'] == 'partial'


def test_missing_attempt_returns_none(db_conn):
    assert build_run_diagnosis('no-such-attempt') is None


def test_perf_uncertain_without_metrics(db_conn):
    attempt, _ = _seed_attempt(db_conn)
    d = build_run_diagnosis(attempt)
    assert d['dimensions']['performance']['status'] == 'uncertain'


def test_promise_action_lazy_fill_via_existing_audit(db_conn, monkeypatch):
    """lazy-fill：无步骤核对行时 best-effort 调既有 ensure_diagnosis_for_session
    现算再读（2026-10-27 认可）；直接种子了 step 行的上面几个用例即证明
    「已有行不触发」。"""
    import utils.trace_auditor as ta
    attempt, sid = _seed_attempt(db_conn)          # 不种 step_rows → 无行
    calls = []

    def fake_ensure(session_id):
        calls.append(session_id)
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ai_execution_step_results (id, attempt_id, "
                        "  step_id, expected, declared_by_agent, observed, status, "
                        "  evidence_level, confidence, duration_ms, evidence_refs, "
                        "  reason) VALUES (%s, %s, 's1', true, true, false, "
                        "  'completed_claimed', 'observed', 0.9, 10, '[]'::jsonb, "
                        "  'fake')",
                        ('stpr_' + uuid.uuid4().hex[:10], attempt))
        db_conn.commit()

    monkeypatch.setattr(ta, 'ensure_diagnosis_for_session', fake_ensure)
    d = build_run_diagnosis(attempt)
    assert calls == [sid]
    assert d['dimensions']['promise_action']['status'] == 'fail'
