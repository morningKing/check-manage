"""SkillOpt 任务拟合（设计 docs/design/ai/SkillOpt任务拟合设计.md §3-§4）。

以 attempt 为维度：定义（SKILL.md / agent md 的 frontmatter fit.steps）与
实际工具调用轨迹做贪心顺序匹配，得出步骤级拟合度并落
ai_skill_fit_results。判定为纯规则（无 LLM）；轨迹统一事实源是
agent_tool_calls 账本（含子代理，root+subtask 双标识）。
"""
import json
import logging
import os
import re
import secrets
from collections import OrderedDict

import yaml

from db import get_db as _default_get_db

log = logging.getLogger(__name__)

_PARSE_CACHE_MAX = 256
_parse_cache: 'OrderedDict[str, list[dict]]' = OrderedDict()

# (attempt_id, def_name) 唯一索引：迁移建表时未带该约束，而 upsert 的
# ON CONFLICT 需要它。首次调用幂等 ensure（模块级 once 标志，成功后才置位）。
_UQ_INDEX_SQL = ("CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_fit_attempt_def "
                 "ON ai_skill_fit_results(attempt_id, def_name)")
_uq_index_ready = False


class FitParseError(ValueError):
    """frontmatter 缺失/解析失败（定义不参与拟合，结果行标 parse_error）。"""


def parse_fit_steps(text: str) -> list[dict]:
    """从 md 全文解析 frontmatter fit.steps。无 fit 块 → []；坏 yaml → 抛错。"""
    m = re.match(r'\A---\r?\n(.*?)\r?\n---\r?\n', text or '', re.S)
    if not m:
        return []
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise FitParseError(f'frontmatter YAML 解析失败: {e}') from e
    if not isinstance(meta, dict):
        return []
    fit = meta.get('fit')
    if not isinstance(fit, dict):
        return []
    steps = fit.get('steps')
    if steps in (None, []):
        return []
    if not isinstance(steps, list):
        raise FitParseError('fit.steps 必须是数组')
    out = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise FitParseError(f'fit.steps[{i}] 必须是对象')
        expect = s.get('expect') or []
        if not isinstance(expect, list):
            raise FitParseError(f'fit.steps[{i}].expect 必须是数组')
        out.append({
            'id': str(s.get('id') or f'step-{i + 1}'),
            'name': str(s.get('name') or s.get('id') or f'步骤 {i + 1}'),
            'expect': [{'tool': str(e.get('tool', '')),
                        **({'args_pattern': str(e['args_pattern'])}
                           if e.get('args_pattern') else {})}
                       for e in expect if isinstance(e, dict) and e.get('tool')],
        })
    return out


def parse_cached(path: str, content_hash: str) -> list[dict]:
    """按 content_hash 缓存的解析入口：文件缺失时仍可命中（定义被清理后
    历史拟合的重算依据）。均无 → FitParseError。"""
    if content_hash and content_hash in _parse_cache:
        _parse_cache.move_to_end(content_hash)
        return _parse_cache[content_hash]
    try:
        with open(path, encoding='utf-8') as f:
            steps = parse_fit_steps(f.read())
    except FileNotFoundError:
        raise FitParseError(f'定义文件不存在: {path}')
    if content_hash:
        _parse_cache[content_hash] = steps
        while len(_parse_cache) > _PARSE_CACHE_MAX:
            _parse_cache.popitem(last=False)
    return steps


def match_steps(steps: list[dict], trace: list[dict]) -> dict:
    """贪心顺序匹配：指针扫描轨迹，按步骤序寻找首个满足
    tool 相等 AND (无 args_pattern OR args_text ~ pattern) 的调用。
    返回 per_step/steps_total/steps_hit/score/status（spec §4 口径）。"""
    per_step, ptr, hits = [], 0, 0
    trace_empty = not trace
    for s in steps:
        expect = s.get('expect') or []
        if not expect:
            per_step.append({'id': s['id'], 'name': s['name'],
                             'status': 'skipped', 'evidence': []})
            continue
        hit = None
        while ptr < len(trace):
            t = trace[ptr]
            ptr += 1
            for e in expect:                      # OR 语义：任一命中即该步 hit
                if t['tool'] == e['tool'] and (
                        not e.get('args_pattern')
                        or re.search(e['args_pattern'], t['args'] or '')):
                    hit = {'tool': t['tool'], 'args': (t['args'] or '')[:160],
                           'occurredAt': t['occurredAt']}
                    break
            if hit:
                break
        if hit:
            hits += 1
            per_step.append({'id': s['id'], 'name': s['name'],
                             'status': 'hit', 'evidence': [hit]})
        else:
            per_step.append({'id': s['id'], 'name': s['name'],
                             'status': 'miss', 'evidence': []})
    total = sum(1 for p in per_step if p['status'] in ('hit', 'miss'))
    score = round(hits / total * 100) if total else 0
    if trace_empty:
        status = 'no_trace'
    elif hits == total:
        status = 'fit'
    elif hits == 0:
        status = 'diverged'
    else:
        status = 'partial'
    return {'per_step': per_step, 'steps_total': total, 'steps_hit': hits,
            'score': score, 'status': status}


# ── 编排（薄 I/O）：定义发现 → 轨迹 → 匹配 → upsert 结果行 ────────────────

def _ensure_uq_index(db_ctx) -> None:
    """(attempt_id, def_name) 唯一索引幂等 ensure——迁移建表未带该约束，
    upsert 的 ON CONFLICT 依赖它。成功后置模块级 once 标志（失败不置位，
    下次调用重试）。"""
    global _uq_index_ready
    if _uq_index_ready:
        return
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(_UQ_INDEX_SQL)
    _uq_index_ready = True
    log.info('skill_fit: 唯一索引 uq_skill_fit_attempt_def 已就绪')


def _load_trace(db_ctx, session_id, started_at, finished_at) -> list[dict]:
    """attempt 时窗（started_at ~ finished_at，未结束则到 NOW）内的账本工具
    调用时序。root_session_id = attempt.session_id 天然含全部子代理的调用
    （账本落账时已带 root+subtask 双标识），按时序稳定排序。"""
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tool, args_text, occurred_at FROM agent_tool_calls "
                "WHERE root_session_id = %s "
                "  AND occurred_at BETWEEN %s AND COALESCE(%s, NOW()) "
                "ORDER BY occurred_at ASC NULLS LAST, id ASC",
                (session_id, started_at, finished_at))
            rows = cur.fetchall()
    return [{'tool': tool, 'args': args_text or '',
             'occurredAt': occurred_at.isoformat() if occurred_at else None}
            for (tool, args_text, occurred_at) in rows]


def _register_def_version(cur, vals: dict) -> None:
    """定义版本自动注册（spec §3.4）：拟合计算遇到新 (def_kind, def_name,
    content_hash) 即 upsert ai_skill_def_versions——冲突 DO NOTHING，
    first_seen_at 只记首次，重复 compute 幂等不翻倍。manifest 无 hash 时
    按空串注册（版本表 content_hash NOT NULL，未知 hash 无法区分版本）。"""
    cur.execute(
        """
        INSERT INTO ai_skill_def_versions (id, def_kind, def_name, content_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (def_kind, def_name, content_hash) DO NOTHING
        """,
        ('defv_' + secrets.token_hex(6), vals['def_kind'],
         vals['def_name'], vals['def_hash'] or ''))


def _upsert_result(cur, vals: dict) -> dict:
    """落一行拟合结果（attempt_id + def_name 冲突覆盖，结果以最后一次为准），
    返回 RETURNING 行字典。重算覆盖后旧诊断一并失效（§5b 缓存口径）。"""
    fid = 'fit_' + secrets.token_hex(6)
    cur.execute(
        """
        INSERT INTO ai_skill_fit_results
          (id, attempt_id, session_id, def_kind, def_name, def_hash,
           steps_total, steps_hit, score, status, per_step, computed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (attempt_id, def_name) DO UPDATE SET
          session_id  = EXCLUDED.session_id,
          def_kind    = EXCLUDED.def_kind,
          def_hash    = EXCLUDED.def_hash,
          steps_total = EXCLUDED.steps_total,
          steps_hit   = EXCLUDED.steps_hit,
          score       = EXCLUDED.score,
          status      = EXCLUDED.status,
          per_step    = EXCLUDED.per_step,
          diagnosis   = NULL,
          computed_at = NOW()
        RETURNING id, attempt_id, session_id, def_kind, def_name, def_hash,
                  steps_total, steps_hit, score, status, per_step, computed_at
        """,
        (fid, vals['attempt_id'], vals['session_id'], vals['def_kind'],
         vals['def_name'], vals['def_hash'], vals['steps_total'],
         vals['steps_hit'], vals['score'], vals['status'],
         json.dumps(vals['per_step'], ensure_ascii=False)))
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def compute_attempt_fit(attempt_id: str, get_db=None) -> list[dict]:
    """编排一次 attempt 的任务拟合（幂等，可重复执行，结果以最后一次为准）：

    1. 定义发现：该 attempt 的 manifests（kind∈skill|agent）→ 按 path 读
       定义文件解析 fit.steps（无 fit 块 → 跳过不出结果行；文件缺失按
       content_hash 查解析缓存，均无 → 不出结果行；yaml 坏 → parse_error
       结果行并日志留痕）；
    2. 轨迹：agent_tool_calls 里 root_session_id = attempt.session_id 的
       时窗内调用（含全部子代理）；
    3. 逐定义 match_steps → upsert 结果行，并按 (def_kind, def_name,
       content_hash) 自动注册定义版本（spec §3.4，首次遇见才建行），
       返回结果行列表。
    """
    db_ctx = get_db or _default_get_db
    _ensure_uq_index(db_ctx)
    # 1. attempt 与清单：一个读块取齐 DB 事实，文件解析放在事务外
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_id, started_at, finished_at "
                "FROM ai_execution_attempts WHERE id = %s", (attempt_id,))
            att = cur.fetchone()
            if not att:
                return []
            session_id, started_at, finished_at = att
            cur.execute(
                "SELECT kind, name, path, content_hash FROM ai_execution_manifests "
                "WHERE attempt_id = %s AND kind IN ('skill', 'agent') "
                "ORDER BY created_at, id", (attempt_id,))
            manifests = [{'kind': k, 'name': n, 'path': p, 'content_hash': h}
                         for (k, n, p, h) in cur.fetchall()]
    # 2. 轨迹序列
    trace = _load_trace(db_ctx, session_id, started_at, finished_at)
    # 3. 解析 + 匹配（文件 I/O 不占连接）
    vals_list: list[dict] = []
    for m in manifests:
        path = m.get('path')
        if not path:
            continue                # 清单行无 path，定位不了定义文件 → 不参与
        base = {'attempt_id': attempt_id, 'session_id': session_id,
                'def_kind': m['kind'], 'def_name': m['name'],
                'def_hash': m['content_hash']}
        try:
            steps = parse_cached(path, m['content_hash'] or '')
        except FitParseError as e:
            if not os.path.exists(path):
                # 文件缺失且缓存未命中 → 不出结果行（设计 §8）
                log.warning('skill_fit: 定义文件缺失且无解析缓存，跳过 %s（%s）',
                            path, e)
                continue
            # 文件在但 frontmatter 解析失败 → parse_error 结果行（设计 §3.2/§8）
            log.warning('skill_fit: 定义 frontmatter 解析失败 %s（%s）', path, e)
            vals_list.append({**base, 'steps_total': 0, 'steps_hit': 0,
                              'score': 0, 'status': 'parse_error',
                              'per_step': []})
            continue
        if not steps:
            continue                # 无 fit 块的定义不参与拟合（不出结果行）
        vals_list.append({**base, **match_steps(steps, trace)})
    # 4. 落库（幂等 upsert）；同时注册定义版本——拟合遇到新 hash 即登记，
    #    ON CONFLICT DO NOTHING 保证重复 compute 幂等（spec §3.4）
    out: list[dict] = []
    if vals_list:
        with db_ctx() as conn:
            with conn.cursor() as cur:
                for vals in vals_list:
                    out.append(_upsert_result(cur, vals))
                    _register_def_version(cur, vals)
    return out


def compute_for_session(session_id: str, get_db=None) -> list[dict] | None:
    """按 session 现查最新 attempt 后委托 compute_attempt_fit（接线用）。

    无 attempt → None；异常只记日志返回 None（best-effort，与
    collect_skill_invocations 同策略，手动重算兜底）。"""
    try:
        db_ctx = get_db or _default_get_db
        with db_ctx() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_execution_attempts WHERE session_id = %s "
                    "ORDER BY created_at DESC, attempt_no DESC LIMIT 1",
                    (session_id,))
                row = cur.fetchone()
        if not row:
            return None
        return compute_attempt_fit(row[0], get_db=get_db)
    except Exception as e:  # noqa: BLE001 —— best-effort：拟合失败不打断任务收敛
        log.warning('skill_fit.compute_for_session failed sid=%s: %s',
                    session_id, e)
        return None
