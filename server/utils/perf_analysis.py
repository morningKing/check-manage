"""SkillOpt 任务性能分析（2026-10-09 spec）：纯读聚合层。

覆盖切分（§3.2）：模型轮次与子代理区间在真实执行中重叠——把区间投到 attempt
时间轴切出互斥的时长（模型活跃 / 子代理等待 / 引擎间隙；二期 detail 路径再
从模型活跃中切出第四类工具执行 toolMs），和恒等于墙钟。
全部函数 get_db 参数注入（skill_fit 惯例），时间统一 epoch 毫秒。
口径注记：工具区间（toolMs 覆盖）按 agent_tool_calls.started_at 夹窗，
而 byTool/repeats 时长聚合沿用一期的 occurred_at 夹窗——两列在批路径可差
一个持久化轮询周期，边界调用的覆盖段与聚合表可能轻微对不上，属已知取舍。
"""
from __future__ import annotations

import time as _time
from datetime import datetime as _dt


def _to_ms(dt) -> int | None:
    """psycopg2 timestamptz（tz-aware datetime）→ epoch 毫秒。"""
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _union(intervals: list[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """夹到 [lo,hi] 后合并重叠区间，返回升序不重叠列表。"""
    clamped = [(max(s, lo), min(e, hi)) for s, e in intervals if min(e, hi) > max(s, lo)]
    out: list[tuple[int, int]] = []
    for s, e in sorted(clamped):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _covered_ms(intervals: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in intervals)


def _subtract(base: list[tuple[int, int]], mask: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """base - mask（都为合并过的升序区间），返回剩余区间。"""
    out: list[tuple[int, int]] = []
    for s, e in base:
        cur = s
        for ms, me in mask:
            if me <= cur or ms >= e:
                continue
            if ms > cur:
                out.append((cur, ms))
            cur = max(cur, me)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def _intersect(base: list[tuple[int, int]], mask: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """base ∩ mask（都为合并过的升序区间），返回相交区间。"""
    out: list[tuple[int, int]] = []
    for s, e in base:
        for ms, me in mask:
            if me <= s or ms >= e:
                continue
            out.append((max(s, ms), min(e, me)))
    return out


def coverage_split(wall_start_ms: int, wall_end_ms: int,
                   model_intervals: list[tuple[int, int]],
                   subagent_intervals: list[tuple[int, int]],
                   tool_intervals: list[tuple[int, int]] | None = None) -> dict:
    """attempt 墙钟 → 互斥覆盖时长。二期（spec §3.2/§7）：tool_intervals
    提供时产出第四类 toolMs = 工具覆盖 ∩ 模型覆盖（工具发生在模型回合内），
    modelMs 相应扣除——四类之和仍 = wallMs。缺省 tool 0，一期调用零改动。"""
    wall_ms = max(0, wall_end_ms - wall_start_ms)
    model = _union(model_intervals, wall_start_ms, wall_end_ms)
    sub = _union(subagent_intervals, wall_start_ms, wall_end_ms)
    tool_ivs = _union(tool_intervals or [], wall_start_ms, wall_end_ms)
    tool_ms = _covered_ms(_intersect(model, tool_ivs))
    model_ms = _covered_ms(_subtract(model, tool_ivs))
    wait = _subtract(sub, model)               # 子代理等待仍相对模型总覆盖
    wait_ms = _covered_ms(wait)
    idle_ms = max(0, wall_ms - model_ms - tool_ms - wait_ms)
    return {'wallMs': wall_ms, 'modelMs': model_ms, 'toolMs': tool_ms,
            'subagentWaitMs': wait_ms, 'idleMs': idle_ms}


# ---- 任务指标与聚合（SQL 层）----


def _now_ms() -> int:
    return int(_time.time() * 1000)


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _percentile(sorted_vals: list, q: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def _open_conn(db_ctx):
    """db_ctx 兼容两种形态：get_db 工厂（生产，skill_fit 惯例）或现成连接
    （测试注入 db_conn）；None 回落默认池。"""
    ctx = db_ctx
    if ctx is None:
        from db import get_db as _default
        ctx = _default
    return ctx() if callable(ctx) else ctx


_ATTEMPT_SQL = (
    "SELECT a.id, a.session_id, a.source_type, a.source_id, a.status,"
    " a.started_at, a.finished_at, a.requested_model, a.effective_model "
    "FROM ai_execution_attempts a WHERE a.id = %s"
    " AND a.source_type <> 'kefu'")

_TASK_LIST_SQL = (
    "SELECT a.id, a.session_id, a.source_type, a.status, a.started_at, a.finished_at"
    " FROM ai_execution_attempts a"
    " WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL AND EXISTS ("
    "   SELECT 1 FROM ai_execution_manifests m WHERE m.attempt_id = a.id"
    "   AND m.kind = %s AND m.name = %s)"
    " ORDER BY a.started_at DESC NULLS LAST LIMIT %s")

_DEF_P50_SQL = (
    "SELECT EXTRACT(EPOCH FROM (a.finished_at - a.started_at)) * 1000"
    " FROM ai_execution_attempts a"
    " WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL"
    "   AND a.finished_at IS NOT NULL AND EXISTS ("
    "   SELECT 1 FROM ai_execution_manifests m WHERE m.attempt_id = a.id"
    "   AND m.kind = %s AND m.name = %s)")

_SLOW_SQL = (
    "SELECT a.id, a.session_id, a.source_type, a.status, a.started_at, a.finished_at,"
    " (SELECT m.kind FROM ai_execution_manifests m WHERE m.attempt_id = a.id"
    "   AND m.kind IN ('skill','agent') ORDER BY m.kind, m.name LIMIT 1) AS kind,"
    " (SELECT m.name FROM ai_execution_manifests m WHERE m.attempt_id = a.id"
    "   AND m.kind IN ('skill','agent') ORDER BY m.kind, m.name LIMIT 1) AS name "
    "FROM ai_execution_attempts a "
    "WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL"
    "   AND a.finished_at IS NOT NULL "
    "ORDER BY EXTRACT(EPOCH FROM (a.finished_at - a.started_at)) DESC LIMIT %s")

_MSG_SQL = (
    "SELECT id, created_at, meta FROM ai_chat_messages "
    "WHERE session_id = %s AND role = 'assistant'"
    " AND created_at >= to_timestamp(%s/1000.0)"
    " AND created_at <= to_timestamp(%s/1000.0) ORDER BY created_at")


def _first_text_preview(cur, mid: str) -> str:
    cur.execute("SELECT content FROM ai_chat_messages WHERE id = %s", (mid,))
    row = cur.fetchone()
    for part in (row[0] if row else []) or []:
        if isinstance(part, dict) and part.get('type') == 'text' and part.get('text'):
            return part['text'].strip()[:80]
    return ''


def _norm_args(args_text) -> str:
    return ' '.join((args_text or '').split())[:200]


def _tool_aggregates(rows: list) -> dict:
    """工具调用行 → 聚合 + 逐调用明细。rows（时间序）：
    (part_id, tool, args_text, state, duration_ms, started_at, subtask_id,
     turn_index|int|None)。"""
    by_tool: dict = {}
    key_counts: dict = {}
    errors = 0
    any_duration = False
    calls: list = []
    for part_id, tool, args_text, state, dur, started_at, subtask_id, turn_idx in rows:
        if state == 'error':
            errors += 1
        entry = by_tool.setdefault(tool, {'count': 0, 'totalMs': 0,
                                          'hasDuration': False})
        entry['count'] += 1
        if dur is not None:
            any_duration = True
            entry['hasDuration'] = True
            entry['totalMs'] += int(dur)
        key = f'{tool}|{_norm_args(args_text)}'
        kc = key_counts.setdefault(key, {'count': 0, 'totalMs': 0,
                                         'hasDuration': False, 'tool': tool,
                                         'argsPreview': _norm_args(args_text)})
        kc['count'] += 1
        if dur is not None:
            kc['hasDuration'] = True
            kc['totalMs'] += int(dur)
        calls.append({'partId': part_id, 'tool': tool,
                      'args': _norm_args(args_text),
                      'state': state, 'startedAt': _iso(started_at),
                      'durationMs': dur, 'subtaskId': subtask_id,
                      'turnIndex': turn_idx})
    by = [{'tool': t, 'count': v['count'],
           'totalMs': v['totalMs'] if v['hasDuration'] else None}
          for t, v in sorted(by_tool.items(), key=lambda kv: -kv[1]['totalMs'])]
    repeats = [{'tool': v['tool'], 'argsPreview': v['argsPreview'],
                'count': v['count'],
                'totalMs': v['totalMs'] if v['hasDuration'] else None}
               for v in key_counts.values() if v['count'] >= REPEAT_TOOL_COUNT]
    repeats.sort(key=lambda r: -(r['totalMs'] or 0))
    truncated = len(calls) > TOOL_CALLS_MAX
    return {'errorCount': errors, 'repeats': repeats, 'byTool': by,
            'durationAvailable': any_duration,
            'calls': calls[:TOOL_CALLS_MAX], 'callsTruncated': truncated}


def _attempt_metrics(cur, row: dict, *, with_detail: bool) -> dict:
    sid = row['session_id']
    start_ms = _to_ms(row['started_at']) or 0
    end_ms = _to_ms(row['finished_at']) or _now_ms()
    model_ivs: list = []
    turns, turn_details = [], []
    missing = 0
    cur.execute(_MSG_SQL, (sid, start_ms, end_ms))
    for mid, created, meta in cur.fetchall():
        meta = meta or {}
        dur = meta.get('durationMs')
        tin = int(meta.get('tokensInput') or 0)
        tout = int(meta.get('tokensOutput') or 0)
        turns.append({'messageId': mid, 'durationMs': dur,
                      'tokensIn': tin, 'tokensOut': tout})
        if dur is None:
            missing += 1
        else:
            cms = _to_ms(created)
            model_ivs.append((cms, cms + int(dur)))
            turn_details.append({
                'messageId': mid, 'createdAt': _iso(created),
                'durationMs': int(dur), 'tokensIn': tin, 'tokensOut': tout,
                'preview': _first_text_preview(cur, mid)})
    cur.execute(
        "SELECT id, agent, description, status, created_at, completed_at "
        "FROM ai_chat_subtasks WHERE root_session_id = %s ORDER BY created_at", (sid,))
    sub_ivs, subtasks, running = [], [], 0
    for stid, agent, desc, status, created, finished in cur.fetchall():
        s_ms, e_ms = _to_ms(created), _to_ms(finished) or _now_ms()
        if status == 'running':
            running += 1
        sub_ivs.append((s_ms, e_ms))
        subtasks.append({'subtaskId': stid, 'agent': agent, 'description': desc,
                         'status': status, 'startedAt': _iso(created),
                         'finishedAt': _iso(finished),
                         'wallMs': max(0, e_ms - s_ms)})
    # 二期 detail 路径：工具调用一次取齐（覆盖区间/聚合/逐调用明细共用；
    # 非 detail 路径不查询）
    tool_ivs: list | None = None
    tools: dict = {'errorCount': 0, 'repeats': [], 'calls': [],
                   'callsTruncated': False}
    if with_detail:
        cur.execute(
            "SELECT part_id, tool, args_text, state, duration_ms, started_at,"
            " occurred_at, subtask_id FROM agent_tool_calls "
            "WHERE root_session_id = %s"
            " AND occurred_at >= to_timestamp(%s/1000.0)"
            " AND occurred_at <= to_timestamp(%s/1000.0) ORDER BY occurred_at",
            (sid, start_ms, end_ms))
        tool_rows = cur.fetchall()
        tool_ivs = [(int(_to_ms(s)) , int(_to_ms(s)) + int(d))
                    for (_p, _t, _a, _st, d, s, _o, _sub) in tool_rows
                    if s is not None and d is not None]
        # 轮次归组：调用归属「最后一条 start ≤ 调用时刻」的轮次（时间序；
        # 早于首条轮次或子代理调用 → None）
        bounds = [(_iso_ms(t['createdAt']), i)
                  for i, t in enumerate(turn_details) if t['createdAt']]
        bounds.sort()

        def _turn_index(call_start: int | None):
            if call_start is None:
                return None
            idx = None
            for b_start, i in bounds:
                if b_start <= call_start:
                    idx = i
                else:
                    break
            return idx

        agg_rows = []
        for part_id, tool, args_text, state, dur, started_at, occurred_at, sub in tool_rows:
            c_start = _to_ms(started_at) or _to_ms(occurred_at)
            agg_rows.append((part_id, tool, args_text, state, dur,
                             started_at, sub, _turn_index(c_start)))
        tools = _tool_aggregates(agg_rows)
        counts: dict = {}
        for c in tools['calls']:
            ti = c['turnIndex']
            if ti is not None:
                counts[ti] = counts.get(ti, 0) + 1
        for i, t in enumerate(turn_details):
            t['toolCount'] = counts.get(i, 0)
    cov = coverage_split(start_ms, end_ms, model_ivs, sub_ivs, tool_ivs)
    wall_ms = cov['wallMs']
    out = {
        'attemptId': row['id'], 'sessionId': sid,
        'sourceType': row['source_type'], 'status': row['status'],
        'startedAt': _iso(row['started_at']), 'finishedAt': _iso(row['finished_at']),
        'wallMs': wall_ms, 'modelMs': cov['modelMs'],
        'modelRatio': round(cov['modelMs'] / wall_ms, 4) if wall_ms else 0,
        'subagentWaitMs': cov['subagentWaitMs'], 'idleMs': cov['idleMs'],
        'turns': len(turns),
        'tokensIn': sum(t['tokensIn'] for t in turns),
        'tokensOut': sum(t['tokensOut'] for t in turns),
        'subtaskCount': len(subtasks),
        'completeness': {'turnsWithoutDuration': missing, 'runningSubtasks': running},
    }
    if with_detail:
        cur.execute("SELECT subagent_reuse FROM ai_chat_batches WHERE id = %s",
                    (row.get('source_id'),))
        br = cur.fetchone()
        out['batchReuseAgents'] = (br[0] if br and br[0] else None)
        out['requestedModel'] = row.get('requested_model')
        out['effectiveModel'] = row.get('effective_model')
        out['turnDetails'] = turn_details
        out['subtasks'] = subtasks
        out['tools'] = tools
        # 四类覆盖明细（含 toolMs）：顶层 modelMs 已是扣除工具后的净值，
        # coverage 与其同源，供端点/前端直接拆包
        out['coverage'] = {'wallMs': wall_ms, 'modelMs': cov['modelMs'],
                           'toolMs': cov['toolMs'],
                           'subagentWaitMs': cov['subagentWaitMs'],
                           'idleMs': cov['idleMs']}
    return out


def _list_metrics(db_ctx, sql: str, params: tuple, *, with_detail: bool) -> list:
    out = []
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                out.append(_attempt_metrics(cur, dict(zip(cols, r)),
                                            with_detail=with_detail))
    return out


def load_attempt_metrics(db_ctx, attempt_id: str):
    """单任务全指标（detail），不存在返回 None。db_ctx 可注入（测试）。"""
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(_ATTEMPT_SQL, (attempt_id,))
            row_ = cur.fetchone()
            if not row_:
                return None
            cols = [d[0] for d in cur.description]
            return _attempt_metrics(cur, dict(zip(cols, row_)), with_detail=True)


def list_definition_tasks(db_ctx, kind: str, name: str, limit: int = 50) -> list:
    return _list_metrics(db_ctx, _TASK_LIST_SQL, (kind, name, limit), with_detail=False)


def definition_p50(db_ctx, kind: str, name: str) -> int | None:
    """该定义已完结任务的墙钟 P50（毫秒）；无已完结任务返回 None。

    单条 SQL 只取墙钟列（_TASK_LIST_SQL 的 EXISTS/kefu 过滤形态 + 完结过滤），
    供诊断端点热路径用——替代全量 definition_overview（D×200×2-3 查询）。"""
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(_DEF_P50_SQL, (kind, name))
            walls = sorted(int(r[0]) for r in cur.fetchall())
    return _percentile(walls, 0.5) if walls else None


def list_slow_tasks(db_ctx, limit: int = 10) -> list:
    out = []
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(_SLOW_SQL, (limit,))
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                m = _attempt_metrics(cur, row, with_detail=False)
                m['defKind'], m['defName'] = row.get('kind'), row.get('name')
                out.append(m)
    return out


def definition_overview(db_ctx) -> list:
    defs_sql = (
        "SELECT DISTINCT m.kind, m.name FROM ai_execution_manifests m "
        "JOIN ai_execution_attempts a ON a.id = m.attempt_id "
        "WHERE m.kind IN ('skill','agent') AND a.source_type <> 'kefu' "
        "ORDER BY m.kind, m.name")
    out = []
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(defs_sql)
            pairs = cur.fetchall()
    for kind, name in pairs:
        tasks = [t for t in _list_metrics(
            db_ctx, _TASK_LIST_SQL, (kind, name, 200), with_detail=False)
            if t['finishedAt'] is not None]
        walls = sorted(t['wallMs'] for t in tasks)
        out.append({
            'defKind': kind, 'defName': name, 'tasks': len(tasks),
            'p50Ms': _percentile(walls, 0.5),
            'p95Ms': _percentile(walls, 0.95),
            'avgModelRatio': (round(sum(t['modelRatio'] for t in tasks)
                                    / len(tasks), 4) if tasks else None),
            'lastActivity': (tasks[0]['startedAt'] if tasks else None)})
    return out


# ---- 诊断规则（spec §6，阈值常量逐字）----
SUBAGENT_WAIT_RATIO = 0.5
SUBAGENT_MIN_WALL_MS = 60_000
MODEL_RATIO = 0.7
SLOW_TURN_MS = 30_000
BIG_CONTEXT_TOKENS = 80_000
REPEAT_TOOL_COUNT = 3
TOOL_ERROR_COUNT = 3
TOOL_HOTSPOT_RATIO = 0.4
IDLE_RATIO = 0.3
IDLE_MIN_WALL_MS = 120_000
OUTLIER_P50_FACTOR = 4
SEQUENTIAL_OVERLAP = 0.1
TOOL_CALLS_MAX = 500          # detail 逐调用明细条数封顶（超出截断标记）


def _overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = max(1, min(a[1] - a[0], b[1] - b[0]))
    return inter / shorter


def _iso_ms(v):
    """startedAt/finishedAt（ISO 串或 datetime）→ epoch 毫秒；缺省 None。"""
    if isinstance(v, str):
        return int(_dt.fromisoformat(v).timestamp() * 1000)
    return _to_ms(v)


def diagnose(breakdown: dict, peer_p50_ms: int | None = None) -> list:
    wall = breakdown.get('wallMs') or 0
    out: list = []

    def add(rule_id, severity, text, anchor_type, ref):
        out.append({'ruleId': rule_id, 'severity': severity, 'text': text,
                    'anchor': {'type': anchor_type, 'ref': ref}})

    subtasks = breakdown.get('subtasks') or []
    wait_ms = breakdown.get('subagentWaitMs') or 0
    if wall > SUBAGENT_MIN_WALL_MS and wait_ms / wall > SUBAGENT_WAIT_RATIO:
        slowest = max(subtasks, key=lambda s: s.get('wallMs') or 0) if subtasks else None
        add('subagent_wait_dominant', 'warn',
            f'{round(wait_ms / wall * 100)}% 时间在等子代理（{len(subtasks)} 次委派）',
            'subtask', slowest['subtaskId'] if slowest else 'subtasks')
        if breakdown.get('sourceType') == 'batch' and not breakdown.get('batchReuseAgents'):
            agents = sorted({s.get('agent') for s in subtasks if s.get('agent')})
            add('subagent_reuse_hint', 'info',
                f'批任务未启用子代理会话复用（委派 agent：{",".join(agents) or "未识别"}）'
                '——多轮委派场景建议在批配置开启 subagent_reuse',
                'subtask', slowest['subtaskId'] if slowest else 'subtasks')

    model_ms = breakdown.get('modelMs') or 0
    if wall and model_ms / wall > MODEL_RATIO:
        turns = [t for t in (breakdown.get('turnDetails') or [])
                 if t.get('durationMs') is not None]
        slow = max(turns, key=lambda t: t['durationMs']) if turns else None
        add('model_dominant', 'info',
            f'时间主要花在模型推理（共 {model_ms // 1000}s / '
            f'{breakdown.get("turns") or 0} 轮）',
            'turn', slow['messageId'] if slow else 'turns')

    for i, t in enumerate(breakdown.get('turnDetails') or [], start=1):
        if (t.get('durationMs') or 0) > SLOW_TURN_MS \
                and (t.get('tokensIn') or 0) > BIG_CONTEXT_TOKENS:
            add('slow_turn_big_context', 'warn',
                f'第 {i} 轮 {t["durationMs"] // 1000}s、输入 '
                f'{round(t["tokensIn"] / 1000)}k token——建议拆分任务或压缩历史',
                'turn', t['messageId'])

    tools = breakdown.get('tools') or {}
    for r in tools.get('repeats') or []:
        total = r.get('totalMs')
        suffix = f'（共约 {total / 1000:.1f}s）' if total else ''
        add('repeated_tool_calls', 'warn',
            f'{r["tool"]} 同一参数重复 {r["count"]} 次{suffix}'
            '——考虑在指令里要求一次读全/批量操作', 'segment', 'tools')
    if wall and tools.get('durationAvailable'):
        for t in tools.get('byTool') or []:
            total = t.get('totalMs')
            if total and total / wall > TOOL_HOTSPOT_RATIO:
                add('tool_hotspot', 'warn',
                    f'{round(total / wall * 100)}% 时间在 {t["tool"]}'
                    f'（共 {total / 1000:.1f}s / {t["count"]} 次）',
                    'segment', 'tools')
    if (tools.get('errorCount') or 0) >= TOOL_ERROR_COUNT:
        add('tool_error_storm', 'warn',
            f'{tools["errorCount"]} 次工具失败重试，检查工具参数与环境',
            'segment', 'tools')

    idle = breakdown.get('idleMs') or 0
    if wall > IDLE_MIN_WALL_MS and idle / wall > IDLE_RATIO:
        add('engine_overhead', 'warn',
            f'引擎开销占 {round(idle / wall * 100)}%——检查 OpenCode 服务状态/'
            '同时段任务是否普遍如此', 'segment', 'idle')

    if peer_p50_ms and wall > peer_p50_ms * OUTLIER_P50_FACTOR:
        add('outlier_vs_peers', 'warn',
            f'比该定义典型水平慢 {round(wall / max(1, peer_p50_ms))} 倍'
            f'（P50 {peer_p50_ms // 1000}s vs 本次 {wall // 1000}s）', 'def', 'self')

    if len(subtasks) >= 2:
        times = [(_iso_ms(s.get('startedAt')), _iso_ms(s.get('finishedAt')))
                 for s in subtasks]
        if all(a is not None and b is not None for a, b in times):
            ivs = times  # 真实时间戳齐全：按实际重叠形态判定
        else:
            # 相对序即可判重叠形态——无完整时间戳时按列表序排成串行块
            ivs, cur = [], 0
            for s in subtasks:
                w = s.get('wallMs') or 0
                ivs.append((cur, cur + w))
                cur += w
        pairs = [(ivs[i], ivs[j]) for i in range(len(ivs))
                 for j in range(i + 1, len(ivs))]
        if all(_overlap_ratio(a, b) < SEQUENTIAL_OVERLAP for a, b in pairs):
            add('sequential_subagents', 'info',
                f'{len(subtasks)} 个子代理串行执行，评估可否并行委托',
                'subtask', subtasks[0]['subtaskId'])
    return out


def _def_of_attempt(db_ctx, attempt_id: str):
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kind, name FROM ai_execution_manifests "
                "WHERE attempt_id = %s AND kind IN ('skill','agent') "
                "ORDER BY kind, name LIMIT 1", (attempt_id,))
            row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


# ---- Skill 调用耗时推导（方案 2：runtime 精确 + 启发式 inferred，纯读）----

_SKILL_INV_SQL = (
    "SELECT skill_name, source, evidence_level, invoked_at, evidence_refs "
    "FROM ai_skill_invocations WHERE attempt_id = %s ORDER BY invoked_at")

_TOOL_TIMELINE_SQL = (
    "SELECT part_id, tool, args_text, started_at, duration_ms, occurred_at "
    "FROM agent_tool_calls "
    "WHERE root_session_id = %s"
    " AND occurred_at >= to_timestamp(%s/1000.0)"
    " AND occurred_at <= to_timestamp(%s/1000.0) ORDER BY occurred_at")


def _part_ref(evidence_refs) -> str | None:
    """evidence_refs（JSONB 数组）解析 runtime 上报的 partID。"""
    for ref in evidence_refs or []:
        if isinstance(ref, str) and ref.startswith('event:skill:'):
            return ref[len('event:skill:'):] or None
    return None


def derive_skill_durations(db_ctx, attempt_id: str, session_id: str,
                           start_ms: int, end_ms: int) -> list:
    """skill 调用耗时推导（性能下钻 Skill 耗时表数据源，纯读两级）：

    - runtime 确认行（evidence_refs 含 event:skill:<partID>）：按 part_id 回查
      agent_tool_calls 的二期时长列（started_at/duration_ms）——精确值；
    - 启发式（inferred）行：工具时间线里「读该 skill 的 SKILL.md」的调用为
      起点，证据链（args 引用 skill 目录名）最后一次调用为终点取跨度；仅
      加载无后续引用时跨度到墙钟终点；找不到 SKILL.md 读取 → durationMs
      为 None（宁缺不估）。
    runtime 行回查不到账本行也出 None。列表条目：
    {name, source, evidenceLevel, invokedAt, durationMs|None}。"""
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(_SKILL_INV_SQL, (attempt_id,))
            inv_cols = [d[0] for d in cur.description]
            invocations = [dict(zip(inv_cols, r)) for r in cur.fetchall()]
            cur.execute(_TOOL_TIMELINE_SQL, (session_id, start_ms, end_ms))
            timeline = cur.fetchall()

    # 工具时间线：每行 (start_ms 有效值, dur, args)
    rows = []
    for part_id, tool, args_text, started_at, dur_ms, occurred_at in timeline:
        s = _to_ms(started_at) or _to_ms(occurred_at)
        rows.append({'partId': part_id, 'tool': tool,
                     'args': args_text or '', 'start': s,
                     'dur': int(dur_ms) if dur_ms is not None else 0})
    skill_parts = {r['partId']: r for r in rows if r['tool'] == 'skill'}

    def _skill_dir_refs(name: str) -> list:
        return [r for r in rows if name in r['args'] and 'SKILL.md' in r['args']]

    def _any_ref_after(name: str, after_start: int) -> list:
        return [r for r in rows
                if name in r['args'] and r['start'] > after_start]

    out: list = []
    for inv in invocations:
        name = inv['skill_name']
        refs = inv.get('evidence_refs') or []
        duration = None
        if inv['source'] == 'runtime':
            part = _part_ref(refs)
            hit = skill_parts.get(part) if part else None
            if hit:
                duration = hit['dur'] or None
        else:
            loads = _skill_dir_refs(name)
            if loads:
                start = min(r['start'] for r in loads)
                chain = _any_ref_after(name, start)
                end = (max(r['start'] + r['dur'] for r in chain)
                       if chain else end_ms)
                duration = max(0, end - start)
        out.append({'name': name, 'source': inv['source'],
                    'evidenceLevel': inv['evidence_level'],
                    'invokedAt': _iso(inv['invoked_at']),
                    'durationMs': duration})
    return out
