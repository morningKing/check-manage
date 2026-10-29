# -*- coding: utf-8 -*-
"""O1 统一运行诊断（设计 docs/superpowers/specs/2026-10-26-agent-self-evolution-design.md §3）。

一次 attempt 的结构化多维诊断，零 LLM：五维度全部来自确定性数据——
task_result=会话终态；rule_compliance=action_expectations 终态核对结果；
promise_action=ai_execution_step_results 声称 vs 观察；skill_fit=拟合结果行；
performance=perf_analysis 四段/慢因。证据缺失一律 uncertain，不冒充 pass；
status=fail 的维度必须带 evidence（写入前校验，违者拒绝落库——无证据的
fail 判定是生成器 bug，不能进学习信号）。
"""
import json
import logging
import secrets

from db import get_db as _default_get_db
from utils.perf_analysis import attempt_payload

log = logging.getLogger(__name__)

GENERATOR_VERSION = 'v1'
DIMENSION_KEYS = ('task_result', 'rule_compliance', 'promise_action',
                  'skill_fit', 'performance')
_DIM_STATUSES = ('pass', 'fail', 'uncertain')


def _dim(status: str, summary: str, evidence: list | None = None) -> dict:
    assert status in _DIM_STATUSES, status
    if status == 'fail' and not evidence:
        raise ValueError(f'fail 维度必须带证据: {summary}')
    return {'status': status, 'summary': summary,
            'evidence': evidence or []}


def _dim_task_result(cur, session_id: str) -> dict:
    cur.execute("SELECT status, error_message FROM ai_chat_sessions WHERE id = %s",
                (session_id,))
    row = cur.fetchone()
    if not row:
        return _dim('uncertain', '会话不存在')
    status, err = row[0], row[1]
    if status == 'completed':
        return _dim('pass', '会话终态 completed')
    if status in ('failed', 'error', 'cancelled'):
        return _dim('fail', f'会话终态 {status}: {err or ""}'.strip(),
                    [{'type': 'session', 'id': session_id}])
    return _dim('uncertain', f'会话终态 {status}（非终态或不可判）')


def _dim_rule_compliance(cur, session_id: str) -> dict:
    # tree 作用域直接挂根会话 id；session 作用域挂子会话 oc id——经账本
    # root_session_id 反查本次运行实际用过的 oc 会话（与 agent_ledger 的
    # _subtree_oc_ids 同方向，不重复实现树遍历）。
    cur.execute(
        """
        SELECT e.id, e.name, e.last_status, e.last_evidence
        FROM action_expectations e
        WHERE (e.scope_type = 'tree' AND e.scope_id = %s)
           OR (e.scope_type = 'session' AND e.scope_id IN (
                SELECT oc_session_id FROM agent_tool_calls
                WHERE root_session_id = %s))
        """, (session_id, session_id))
    rows = cur.fetchall()
    if not rows:
        return _dim('uncertain', '本运行未登记门禁期望')
    failed = [r for r in rows if r[2] == 'failed']
    if failed:
        return _dim('fail', f'{len(failed)}/{len(rows)} 条期望未达标',
                    [{'type': 'gate_expectation', 'id': r[0], 'hint': r[1]}
                     for r in failed])
    return _dim('pass', f'{len(rows)} 条期望全部达标',
                [{'type': 'gate_expectation', 'id': r[0]} for r in rows])


def _dim_promise_action(cur, attempt_id: str, session_id: str, db_ctx) -> dict:
    cur.execute(
        "SELECT id, step_id, status, declared_by_agent, observed "
        "FROM ai_execution_step_results WHERE attempt_id = %s", (attempt_id,))
    rows = cur.fetchall()
    if not rows:
        # lazy-fill（设计 §3.3，2026-10-27 认可）：步骤核对行由执行审计按需
        # 生成（仅管理端打开时）；聚合时无行则 best-effort 调既有确定性审计
        # 现算，再读一次。已有行绝不重算；失败/仍无行 → uncertain，不阻塞。
        # 函数体内延迟 import：保住 trace_auditor 模块属性的可打桩性。
        try:
            from utils.trace_auditor import ensure_diagnosis_for_session
            ensure_diagnosis_for_session(session_id)
        except Exception as e:  # noqa: BLE001
            log.warning('run_diagnosis: lazy-fill audit failed sid=%s: %s',
                        session_id, e)
        cur.execute(
            "SELECT id, step_id, status, declared_by_agent, observed "
            "FROM ai_execution_step_results WHERE attempt_id = %s", (attempt_id,))
        rows = cur.fetchall()
    if not rows:
        return _dim('uncertain', '无步骤核对记录（执行审计不可用）')
    # status 枚举以 trace_auditor.build_step_results 实际产出为准
    # （out_of_order/failed/completed_confirmed/completed_claimed/missing/
    # unknown_due_to_missing_data）：判定主条件「declared 且非 observed」、
    # 辅条件「status 含 claimed」（completed_claimed），两者或关系。
    bad = [r for r in rows
           if (r[3] and not r[4]) or (r[2] and 'claimed' in str(r[2]))]
    if bad:
        return _dim('fail', f'{len(bad)}/{len(rows)} 步声称完成但无工具证据',
                    [{'type': 'step_result', 'id': r[0],
                      'stepId': r[1], 'status': r[2]} for r in bad])
    return _dim('pass', f'{len(rows)} 步声称与证据一致',
                [{'type': 'step_result', 'id': r[0]} for r in rows])


def _dim_skill_fit(cur, attempt_id: str) -> dict:
    cur.execute(
        "SELECT id, def_name, status, score FROM ai_skill_fit_results "
        "WHERE attempt_id = %s", (attempt_id,))
    rows = cur.fetchall()
    if not rows:
        return _dim('uncertain', '无拟合结果行（未调用技能或无 runtime 证据）')
    bad = [r for r in rows if r[2] in ('diverged', 'parse_error')]
    warn = [r for r in rows if r[2] == 'partial']
    if bad:
        return _dim('fail', f'拟合偏离: {[r[1] for r in bad]}',
                    [{'type': 'fit_result', 'id': r[0], 'skill': r[1]}
                     for r in bad])
    if warn:
        # T2-4b（终审裁定）：partial 计 uncertain——overall 落 partial，
        # 兑现设计 §3.2「partial→计 partial」，不与「其余全 pass」混成 success
        return _dim('uncertain', f'拟合部分命中: {[r[1] for r in warn]}',
                    [{'type': 'fit_result', 'id': r[0], 'skill': r[1]}
                     for r in warn])
    return _dim('pass', f'{len(rows)} 个定义全部对齐')


def _dim_performance(db_ctx, attempt_id: str) -> dict:
    # 实现期核对（brief 注 1）：attempt_payload 返回体无顶层主导慢因字段；
    # 定义级口径（definition_cause_distribution.dominantCounts）由四段
    # coverage（model/wait/tool/idle）取 argmax 得 dominant——这里按同口径
    # 从 attempt 级 coverage 现算。窗口为空（wallMs<=0，种子/瞬时 attempt
    # 常态）= 设计 §3.2 的「无耗时数据」→ uncertain。perf 载荷失败走兜底
    # uncertain，不阻塞诊断落库。
    try:
        payload = attempt_payload(db_ctx, attempt_id)
    except Exception as e:  # noqa: BLE001
        log.warning('run_diagnosis: perf payload failed attempt=%s: %s',
                    attempt_id, e)
        payload = None
    if not payload:
        return _dim('uncertain', '无性能数据')
    cov = payload.get('coverage') or {}
    wall = cov.get('wallMs') or 0
    if wall <= 0:
        return _dim('uncertain', '无耗时数据（attempt 窗口为空，无轮次/工具调用）')
    seg_ms = {'model': cov.get('modelMs') or 0,
              'wait': cov.get('subagentWaitMs') or 0,
              'tool': cov.get('toolMs') or 0,
              'idle': cov.get('idleMs') or 0}
    dominant = max(seg_ms, key=lambda k: seg_ms[k])
    return _dim('pass', f'主导慢因: {dominant or "均衡"}',
                [{'type': 'perf_attempt', 'attemptId': attempt_id,
                  'dominant': dominant}])


def _overall(dims: dict) -> str:
    """设计 §3 聚合：任一 fail→fail；pass+uncertain 混合→partial；
    全 uncertain→uncertain；全 pass→success。"""
    statuses = {v['status'] for v in dims.values()}
    if 'fail' in statuses:
        return 'fail'
    if 'uncertain' in statuses:
        return 'uncertain' if statuses == {'uncertain'} else 'partial'
    return 'success'


def build_run_diagnosis(attempt_id: str, get_db=None) -> dict | None:
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT session_id FROM ai_execution_attempts WHERE id = %s",
                        (attempt_id,))
            row = cur.fetchone()
            if not row:
                return None
            session_id = row[0]
            dims = {
                'task_result': _dim_task_result(cur, session_id),
                'rule_compliance': _dim_rule_compliance(cur, session_id),
                'promise_action': _dim_promise_action(cur, attempt_id,
                                                      session_id, db_ctx),
                'skill_fit': _dim_skill_fit(cur, attempt_id),
                'performance': _dim_performance(db_ctx, attempt_id),
            }
            overall = _overall(dims)
            payload = json.dumps(dims, ensure_ascii=False)
            cur.execute(
                """
                INSERT INTO ai_run_diagnoses
                    (id, attempt_id, root_session_id, overall, dimensions,
                     generator_version)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (attempt_id, generator_version) DO UPDATE SET
                    root_session_id = EXCLUDED.root_session_id,
                    overall = EXCLUDED.overall,
                    dimensions = EXCLUDED.dimensions,
                    created_at = NOW()
                """,
                ('rdi_' + secrets.token_hex(6), attempt_id, session_id,
                 overall, payload, GENERATOR_VERSION))
        conn.commit()
    return {'attemptId': attempt_id, 'rootSessionId': session_id,
            'overall': overall, 'dimensions': dims,
            'generatorVersion': GENERATOR_VERSION}
