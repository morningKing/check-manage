# -*- coding: utf-8 -*-
"""O2 候选评估（设计 §4.3+§4.6）：diff 校验 + 双集回归，全部零 LLM。

匹配器直接复用 skill_fit 的纯函数（parse_fit_steps/match_steps/_load_trace），
拟合口径 37c7057 不受影响。门槛（第 8 章 8.2.2 发布条件）：
- 边界集（partial/diverged 的近期 attempt）：候选得分逐条 ≥ 现行，且严格
  提升条数 ≥1，恶化条数 ≤ 20%；
- 保留集（fit 的近期 attempt）：现行已 fit 的 attempt 候选后必须仍 fit
  （零退化是硬门槛）；
- 集合不足（边界 <2 或保留 <3）→ insufficient_evidence，候选停留 pending
  继续积累（8.3.1：证据不足的偶发故障不触发学习）。
R3 结构守卫：edits 不得增删步骤 id；expect 收窄须声明 expected_repair。
R4 双模分类：纯正文（未触及 fit.steps 匹配面）修改走 behavioral——回归
打分对其必然无变化，直接 validated 交人工发布；触及匹配面的走 mechanical。
"""
import json
import logging
import re
import secrets
from datetime import datetime

from db import get_db as _default_get_db
from utils.skill_fit import _load_trace, match_steps, parse_fit_steps

log = logging.getLogger(__name__)

BOUNDARY_MIN, HOLDOUT_MIN, BOUNDARY_MAX = 2, 3, 20
_DEGRADE_RATE_MAX = 0.2


def apply_edits(base_text: str, edits: list[dict]) -> tuple[str, str | None]:
    """逐条精确替换；old_str 必须在当前文本中唯一命中，new_str != old_str，
    且不得增删 fit.steps 的步骤 id（R3）。返回 (new_text, None) 或
    (base_text, 错误说明)。"""
    text = base_text
    for i, e in enumerate(edits or []):
        old, new = e.get('old_str'), e.get('new_str')
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return base_text, f'edits[{i}] 缺 old_str/new_str'
        if old == new:
            return base_text, f'edits[{i}] old_str 与 new_str 相同（无变化）'
        n = text.count(old)
        if n != 1:
            return base_text, f'edits[{i}] old_str 命中 {n} 次（要求唯一命中）'
        text = text.replace(old, new, 1)
    if text == base_text:
        return base_text, 'edits 未产生任何变化'
    old_ids = {s['id'] for s in parse_fit_steps(base_text)}
    new_ids = {s['id'] for s in parse_fit_steps(text)}
    if old_ids != new_ids:
        return base_text, (f'edits 不得增删步骤 id'
                           f'（差异: {sorted(old_ids ^ new_ids)}）——结构性改动'
                           f'请人工走定义编辑，不走候选区')
    return text, None


def structural_guard(base_text: str, new_text: str,
                     expected_repair: str | None) -> str | None:
    """R3 作用域守卫：任一步骤的 expect 条目被收窄（断言变少）而 manifest
    未声明 expected_repair 时返回错误说明，否则 None。"""
    old_steps = {s['id']: s for s in parse_fit_steps(base_text)}
    new_steps = {s['id']: s for s in parse_fit_steps(new_text)}
    narrowed = [sid for sid, s in old_steps.items()
                if sid in new_steps
                and len(new_steps[sid]['expect']) < len(s['expect'])]
    if narrowed and not (expected_repair or '').strip():
        return (f'步骤 {narrowed} 的 expect 被收窄，须在 expectedRepair '
                '声明预期修复（受影响行为）')
    return None


def _matching_signature(steps: list[dict]) -> list:
    """回归打分真正消费的匹配面：id + expect 序列（tool, args_pattern）。"""
    return [(s['id'], [(e.get('tool'), e.get('args_pattern'))
                       for e in s['expect']]) for s in steps]


def _touches_steps(base_text: str, new_text: str, edits: list[dict]) -> bool:
    """R4 分类：True=mechanical（触及 fit.steps 匹配面）。
    任一 edit 落在 frontmatter 区且匹配签名变化 → mechanical；
    纯正文修改或仅步骤名等表面改动 → behavioral。"""
    m = re.match(r'\A---\r?\n.*?\r?\n---\r?\n', base_text, re.S)
    fm_end = m.end() if m else 0
    in_fm = any(0 <= base_text.find(e.get('old_str') or '') < fm_end
                for e in (edits or []))
    if not in_fm:
        return False
    return (_matching_signature(parse_fit_steps(base_text))
            != _matching_signature(parse_fit_steps(new_text)))


def collect_validation_sets(db_ctx, def_kind: str, def_name: str) -> dict:
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT attempt_id, status FROM (
                    SELECT attempt_id, status,
                           row_number() OVER (PARTITION BY status
                               ORDER BY computed_at DESC) AS rn
                    FROM ai_skill_fit_results
                    WHERE def_kind = %s AND def_name = %s
                      AND status IN ('partial', 'diverged', 'fit')
                ) t WHERE rn <= %s
                """, (def_kind, def_name, BOUNDARY_MAX))
            boundary, holdout = [], []
            for attempt_id, status in cur.fetchall():
                (boundary if status in ('partial', 'diverged')
                 else holdout).append(attempt_id)
    return {'boundary': boundary, 'holdout': holdout}


def score_steps_on_attempts(steps: list[dict], attempt_ids: list[str],
                            db_ctx=None) -> list[dict]:
    db_ctx = db_ctx or _default_get_db
    out = []
    with db_ctx() as conn:
        with conn.cursor() as cur:
            for aid in attempt_ids:
                cur.execute(
                    "SELECT session_id, started_at, finished_at "
                    "FROM ai_execution_attempts WHERE id = %s", (aid,))
                row = cur.fetchone()
                if not row:
                    out.append({'attemptId': aid, 'score': 0,
                                'status': 'no_attempt'})
                    continue
                trace = _load_trace(db_ctx, row[0], row[1], row[2])
                m = match_steps(steps, trace)
                out.append({'attemptId': aid, 'score': m.get('score', 0),
                            'status': m.get('status', 'miss')})
    return out


def run_validation(candidate_id: str, get_db=None) -> dict:
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT def_kind, def_name, base_content_hash, edits, "
                "       fallback_content, status, source "
                "FROM ai_skill_candidates WHERE id = %s", (candidate_id,))
            row = cur.fetchone()
            if not row:
                raise LookupError(f'候选不存在: {candidate_id}')
            kind, name, base_hash, edits_raw, fallback, status, source_raw = row
            source = source_raw if isinstance(source_raw, dict) else {}
            if status != 'pending':
                raise ValueError(f'候选状态 {status} 不允许验证（仅 pending）')
            cur.execute(
                "SELECT content FROM ai_skill_def_versions "
                "WHERE def_kind = %s AND def_name = %s AND content_hash = %s",
                (kind, name, base_hash))
            base_row = cur.fetchone()
            if not base_row or not base_row[0]:
                raise ValueError('基线版本未归档正文，无法离线回归'
                                 '（需先经 apply 路径归档一次）')
            base_text = base_row[0]
            edits = edits_raw if isinstance(edits_raw, list) else []
            if fallback:
                new_text = fallback
            else:
                new_text, err = apply_edits(base_text, edits)
                if err:
                    raise ValueError(f'候选 diff 非法: {err}')
            guard_err = structural_guard(base_text, new_text,
                                         source.get('expectedRepair'))
            if guard_err:
                raise ValueError(guard_err)
            behavioral = (not fallback
                          and not _touches_steps(base_text, new_text, edits))
            old_steps = parse_fit_steps(base_text)
            new_steps = parse_fit_steps(new_text)
            sets = None if behavioral else collect_validation_sets(db_ctx, kind, name)
    if behavioral:
        # R4 行为类修改：回归打分不适用（分数必然无变化），直接 validated，
        # 人工 promote 仍是唯一发布闸门。
        validation = {
            'mode': 'behavioral',
            'note': '纯正文/非匹配面修改，双集回归不适用；请人工审阅后发布',
            'validatedAt': datetime.now().isoformat(timespec='seconds'),
        }
        decision = 'validated'
        boundary = holdout = []
    else:
        # 评分放事务外（_load_trace 只读）
        before_b = score_steps_on_attempts(old_steps, sets['boundary'], db_ctx)
        after_b = score_steps_on_attempts(new_steps, sets['boundary'], db_ctx)
        before_h = score_steps_on_attempts(old_steps, sets['holdout'], db_ctx)
        after_h = score_steps_on_attempts(new_steps, sets['holdout'], db_ctx)

        boundary = [{'attemptId': b['attemptId'],
                     'before': {'score': b['score'], 'status': b['status']},
                     'after': {'score': a['score'], 'status': a['status']}}
                    for b, a in zip(before_b, after_b)]
        holdout = [{'attemptId': b['attemptId'],
                    'before': {'score': b['score'], 'status': b['status']},
                    'after': {'score': a['score'], 'status': a['status']}}
                   for b, a in zip(before_h, after_h)]

        validation = {
            'mode': 'mechanical',
            'boundary': boundary, 'holdout': holdout,
            'boundaryCount': len(boundary), 'holdoutCount': len(holdout),
            'validatedAt': datetime.now().isoformat(timespec='seconds'),
        }
        if len(boundary) < BOUNDARY_MIN or len(holdout) < HOLDOUT_MIN:
            validation['insufficientEvidence'] = True
            decision = None                      # 停留 pending，继续积累
        else:
            improved = sum(1 for r in boundary
                           if r['after']['score'] > r['before']['score'])
            degraded = [r for r in boundary
                        if r['after']['score'] < r['before']['score']]
            regressed = [r for r in holdout
                         if r['before']['status'] == 'fit'
                         and r['after']['status'] != 'fit']
            validation.update({'improved': improved,
                               'degraded': [r['attemptId'] for r in degraded],
                               'regressed': [r['attemptId'] for r in regressed]})
            if regressed:
                validation['rejectReason'] = '保留集出现拟合退化（硬门槛）'
                decision = 'rejected'
            elif improved < 1 or len(degraded) > len(boundary) * _DEGRADE_RATE_MAX:
                validation['rejectReason'] = '边界集无改善或恶化超阈值'
                decision = 'rejected'
            else:
                decision = 'validated'

    with db_ctx() as conn:
        with conn.cursor() as cur:
            # 终审 Minor 加固：状态守卫——并发 reject/promote 与 validate
            # 竞态时不再覆盖已决状态（rowcount=0 → 400）
            if decision:
                cur.execute(
                    "UPDATE ai_skill_candidates SET validation = %s::jsonb, "
                    "  status = %s, decided_at = NOW() WHERE id = %s "
                    "  AND status = 'pending'",
                    (json.dumps(validation, ensure_ascii=False),
                     decision, candidate_id))
            else:
                cur.execute(
                    "UPDATE ai_skill_candidates SET validation = %s::jsonb "
                    "WHERE id = %s AND status = 'pending'",
                    (json.dumps(validation, ensure_ascii=False), candidate_id))
            if cur.rowcount == 0:
                raise ValueError('候选状态已变化（可能已被人工处理），验证结果未写入')
        conn.commit()
    validation['decision'] = decision or 'insufficient_evidence'
    return validation
