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
                   tool_intervals: list[tuple[int, int]] | None = None,
                   task_intervals: list[tuple[int, int]] | None = None) -> dict:
    """attempt 墙钟 → 互斥覆盖时长。v2 优先级分解（工具 > 子代理等待 > 模型
    > 间隙），四类之和恒 = wallMs：

    - toolMs          非 task 工具覆盖（bash/read 等真实工具执行）
    - subagentWaitMs  子代理跨度 ∪ task 工具跨度（等委托），扣除非 task 工具
                      ——task 跨度无论是否落在轮内都归等待（轮次时长是毛
                      跨度含挂起等委托，不切出来会被误标为模型/工具）
    - modelMs         模型轮次跨度扣除以上两类（纯推理）
    - idleMs          其余
    task_intervals 缺省为空：此时 subagentWait = 子代理跨度原值。"""
    wall_ms = max(0, wall_end_ms - wall_start_ms)
    model = _union(model_intervals, wall_start_ms, wall_end_ms)
    sub = _union(subagent_intervals, wall_start_ms, wall_end_ms)
    tool = _union(tool_intervals or [], wall_start_ms, wall_end_ms)
    task = _union(task_intervals or [], wall_start_ms, wall_end_ms)
    delegate = _union(sub + task, wall_start_ms, wall_end_ms)
    tool_ms = _covered_ms(tool)
    wait_ms = _covered_ms(_subtract(delegate, tool))
    model_ms = _covered_ms(_subtract(_subtract(model, tool), delegate))
    idle_ms = max(0, wall_ms - tool_ms - wait_ms - model_ms)
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


# ---- 端到端 run 口径（2026-11-02）----
# 性能分析只算「端到端一次性」耗时：run = 一条 attempt 链（root =
# parent_attempt_id IS NULL + 全部 parent 后代），链墙钟 = root.started →
# 末成员.finished——自动恢复续跑（AUTO_RETRY/GATE_RETRY/RECONCILE_REQUEUE
# 串链，见 batch_engine.AUTO_REQUEUE_CODES）连同重排队间隙计入；人工接管
# （无链 continue root）及其后续整条 run 剔除。判据根基：parent_attempt_id
# 只有批引擎自动重排队才写（batch_engine._close_attempt_for_requeue → 下一次
# create_attempt 串链，跨重启由 _consume_parent_attempt 查库兜底）；人工
# continue 的 attempt 恒 NULL。交互会话每次 send 自成 run（口径不变）。
# 实现注记：SQL 一律字面量内联在 execute 调用处、参数全 %s（SQL 常量经
# 变量中转进 execute 会被静态扫描误判为拼接——同 _MSG_SQL 上方注记）。


def resolve_session_runs(cur, session_ids) -> dict:
    """会话集 → 端到端 run（链）解析。返回 {session_id: [run, ...]}，run 按
    根起点升序，键：sessionId / root / members / anchor / excludedRounds。

    - members 时间序；anchor = 链末成员（run 的收口 attempt：聚合行的
      attemptId/状态/model 取它）；
    - root operation='continue' 的 run = 人工接管/人工续跑 → 整条剔除，
      成员数累计进本会话各 run 的 excludedRounds（供展示「另有 M 轮人工
      接管未计入」）；
    - kefu / 未派发（started_at NULL）不产生 run。"""
    ids = [s for s in dict.fromkeys(session_ids) if s]
    if not ids:
        return {}
    cur.execute(
        "SELECT id, session_id, source_type, source_id, parent_attempt_id,"
        " attempt_no, operation, status, started_at, finished_at,"
        " requested_model, effective_model, effective_agent"
        " FROM ai_execution_attempts"
        " WHERE session_id = ANY(%s) AND source_type <> 'kefu'"
        " AND started_at IS NOT NULL"
        " ORDER BY session_id, started_at, attempt_no", (ids,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_parent: dict = {}
    for r in rows:
        if r['parent_attempt_id']:
            by_parent.setdefault(r['parent_attempt_id'], []).append(r)
    out: dict = {}
    for r in rows:
        sid = r['session_id']
        if sid in out:
            continue
        sess_rows = [x for x in rows if x['session_id'] == sid]
        roots = [x for x in sess_rows if not x['parent_attempt_id']]
        excluded = 0
        runs = []
        for root in roots:
            members, stack, seen = [], [root], {root['id']}
            while stack:
                node = stack.pop()
                members.append(node)
                for child in by_parent.get(node['id'], []):
                    if child['id'] not in seen:
                        seen.add(child['id'])
                        stack.append(child)
            members.sort(key=lambda m: (m['started_at'], m['attempt_no']))
            if root['operation'] == 'continue':
                excluded += len(members)
                continue
            runs.append({'sessionId': sid, 'root': root, 'members': members,
                         'anchor': members[-1], 'excludedRounds': 0})
        if excluded:
            for run in runs:
                run['excludedRounds'] = excluded
        out[sid] = runs
    return out


def _run_attempt_row(run: dict) -> dict:
    """run → _attempt_metrics 的 attempt 行形态（窗口拉通到链跨度；
    身份/状态/model 取 anchor）。"""
    anchor, root = run['anchor'], run['root']
    return {'id': anchor['id'], 'session_id': run['sessionId'],
            'source_type': anchor['source_type'],
            'source_id': anchor['source_id'], 'status': anchor['status'],
            'started_at': root['started_at'],
            'finished_at': anchor['finished_at'],
            'requested_model': anchor.get('requested_model'),
            'effective_model': anchor.get('effective_model')}


def _run_chain_out(run: dict) -> list:
    """run → 响应侧 chain 明细（时间序）。"""
    return [{'attemptId': m['id'], 'operation': m['operation'],
             'status': m['status'], 'startedAt': _iso(m['started_at']),
             'finishedAt': _iso(m['finished_at'])} for m in run['members']]


def _matched_attempt_ids(cur, kind: str, name: str, attempt_ids: list) -> set:
    """定义证据的成员级命中集（skill 只认 runtime 确认调用——与既有归因
    口径一致；agent 认 effective_agent；其余 kind 认 manifest）。"""
    if not attempt_ids:
        return set()
    if kind == 'skill':
        cur.execute(
            "SELECT attempt_id FROM ai_skill_invocations"
            " WHERE source = 'runtime' AND skill_name = %s"
            " AND attempt_id = ANY(%s)", (name, attempt_ids))
    elif kind == 'agent':
        cur.execute(
            "SELECT id FROM ai_execution_attempts"
            " WHERE effective_agent = %s AND id = ANY(%s)",
            (name, attempt_ids))
    else:
        cur.execute(
            "SELECT attempt_id FROM ai_execution_manifests"
            " WHERE kind = %s AND name = %s AND attempt_id = ANY(%s)",
            (kind, name, attempt_ids))
    return {r[0] for r in cur.fetchall()}


def _definition_run_set(db_ctx, kind: str, name: str, limit: int) -> list:
    """定义 → 端到端 run 集（证据口径不变：run 内任一成员 attempt 命中即算
    该定义跑过）。两步走：候选会话用既有 per-attempt 证据 WHERE（会话首
    attempt 时间倒序，3 倍缓冲——人工 run 剔除后按 run 收敛）；会话全量
    attempt 解析 run 后按成员证据精确过滤。返回按根起点倒序截取 limit。"""
    cand_limit = min(1000, max(limit * 3, 60))
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            if kind == 'skill':
                cur.execute((
                    "SELECT a.session_id FROM ai_execution_attempts a"
                    " WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL"
                    " AND EXISTS (SELECT 1 FROM ai_skill_invocations i"
                    "   WHERE i.attempt_id = a.id AND i.skill_name = %s"
                    "   AND i.source = 'runtime')"
                    " GROUP BY a.session_id"
                    " ORDER BY MIN(a.started_at) DESC NULLS LAST LIMIT %s"),
                    (name, cand_limit))
            elif kind == 'agent':
                cur.execute((
                    "SELECT a.session_id FROM ai_execution_attempts a"
                    " WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL"
                    " AND a.effective_agent = %s"
                    " GROUP BY a.session_id"
                    " ORDER BY MIN(a.started_at) DESC NULLS LAST LIMIT %s"),
                    (name, cand_limit))
            else:
                cur.execute((
                    "SELECT a.session_id FROM ai_execution_attempts a"
                    " WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL"
                    " AND EXISTS (SELECT 1 FROM ai_execution_manifests m"
                    "   WHERE m.attempt_id = a.id AND m.kind = %s"
                    "   AND m.name = %s)"
                    " GROUP BY a.session_id"
                    " ORDER BY MIN(a.started_at) DESC NULLS LAST LIMIT %s"),
                    (kind, name, cand_limit))
            cand = [r[0] for r in cur.fetchall()]
            if not cand:
                return []
            flat = [r for runs in resolve_session_runs(cur, cand).values()
                    for r in runs]
            matched = _matched_attempt_ids(
                cur, kind, name, [m['id'] for r in flat for m in r['members']])
            runs = [r for r in flat
                    if any(m['id'] in matched for m in r['members'])]
    runs.sort(key=lambda r: r['root']['started_at'], reverse=True)
    return runs[:limit]

# 定义→任务的证据口径（2026-10-11 归因修复）。旧口径 EXISTS(manifest
# kind+name) 把「清单里出现过」当「用过」：派发时全部启用技能都会被补录
# injected manifest（_scan_platform_skill_manifests），workspace 里的全部
# agent 定义同理——任何 attempt 都命中任何定义，skill 下钻/概览被批任务
# 占满（用户报告：点进 skill 看到的是 agent 批任务）。只认运行证据：
# - skill：ai_skill_invocations 有 runtime 确认调用（插件上报 confirmed；
#   heuristic inferred 行是「仅可见」的启发式，不作为呈现依据，与主聚合
#   「UI never presents inferred usage as proven」口径一致）；
# - agent：attempt.effective_agent 即该定义（实际执行者；批/交互派发时
#   选了 agent 才会落 effective_agent，未选不归因）。
# 实现注记：kind 按 URL 维度在 Python 侧选 SQL（skill/agent/其他各一条），
# SQL 以字面量内联在 execute/_list_metrics 调用处、kind/name 全部 %s
# 参数化——SQL 常量经变量中转进 execute 会被静态扫描误判为拼接。

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
     turn_index|int|None)。

    task 调用是「开子代理」的委派动作而非工具执行（其子代理内部的工具与
    推理另计）——不计入 byTool/重复/失败/hotspot 等工具统计，只在 calls
    明细保留并以 isDelegation 标注（前端渲染为「开子代理」）。"""
    by_tool: dict = {}
    key_counts: dict = {}
    errors = 0
    any_duration = False
    calls: list = []
    for part_id, tool, args_text, state, dur, started_at, subtask_id, turn_idx in rows:
        is_delegation = (tool == 'task')
        if not is_delegation:
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
                      'turnIndex': turn_idx, 'isDelegation': is_delegation})
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
            # 轮窗起点优先 meta.startedAt（消息自身起始，2026-10-12 起落库）：
            # 批路径的行在回合结束后才落库，created_at 晚于轮内全部调用——
            # 拿它当窗口起点会让调用全部落到窗口外（前端时间轴画不出来）。
            # 旧行无锚点回落 created_at（交互路径二者近似）。
            anchor = meta.get('startedAt')
            cms = _iso_ms(anchor) if anchor else None
            if cms is None:
                cms = _to_ms(created)
            model_ivs.append((cms, cms + int(dur)))
            turn_details.append({
                'messageId': mid,
                # 输出形态与旧口径一致（本地时区 aware ISO）；锚点是 epoch ms
                'createdAt': _iso(_dt.fromtimestamp(cms / 1000).astimezone()),
                'durationMs': int(dur), 'tokensIn': tin, 'tokensOut': tout,
                'preview': _first_text_preview(cur, mid)})
    cur.execute(
        "SELECT id, agent, description, status, created_at, completed_at "
        "FROM ai_chat_subtasks WHERE root_session_id = %s ORDER BY created_at", (sid,))
    sub_ivs, subtasks, running = [], [], 0
    sub_turns: dict = {}   # subtaskId → [{createdAt, durationMs}]（子代理轮次，
    #                        detail 路径下发给前端时间轴算「内部推理段」）
    for stid, agent, desc, status, created, finished in cur.fetchall():
        s_ms = _to_ms(created)
        # 已收尾但缺 completed_at 的子代理（中断残留/旧数据）按 attempt 终点
        # 截断——顶到当前时刻会把子代理 wallMs/P95/最慢 Top 顶成天文数字
        e_ms = _to_ms(finished)
        if e_ms is None:
            e_ms = end_ms
        if status == 'running':
            running += 1
        sub_ivs.append((s_ms, e_ms))
        subtasks.append({'subtaskId': stid, 'agent': agent, 'description': desc,
                         'status': status, 'startedAt': _iso(created),
                         'finishedAt': _iso(finished),
                         'wallMs': max(0, e_ms - s_ms)})
        if with_detail:
            # 子代理轮次（assistant 消息带 durationMs 的）：给前端把子代理
            # 内部时间切成「推理 ↔ 工具」。meta 2026-10-12 起才采集，旧数据
            # 无 durationMs → 空列表，前端自然退化为仅工具间隙。
            cur.execute(
                "SELECT created_at, meta FROM ai_chat_subtask_messages "
                "WHERE subtask_id = %s AND role = 'assistant' "
                "  AND meta ? 'durationMs' ORDER BY created_at", (stid,))
            turns_out = []
            for tc, tmeta in cur.fetchall():
                tmeta = tmeta or {}
                tdur = tmeta.get('durationMs')
                if tdur is None:
                    continue
                # 窗口起点优先 meta.startedAt（消息自身起始）：批路径的行是
                # 回合结束后才写的，created_at 滞后于子代理真实起点，拿它
                # 当窗口起点会让嵌套段的推理/工具切分整体右移。
                turns_out.append({'createdAt': tmeta.get('startedAt') or _iso(tc),
                                  'durationMs': int(tdur)})
            if turns_out:
                sub_turns[stid] = turns_out
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
        # 覆盖区间按口径拆两组：task（开子代理委派）归「子代理等待」，
        # 其余工具归「工具」——coverage_split 的 v2 语义要求 caller 显式
        # 传 task_intervals；此前漏传 → 委派跨度全被记进 toolMs，页面上
        # 「开子代理等待」对不上（10-12 实测复现）。
        tool_ivs, task_ivs = [], []
        for (_p, _t, _a, _st, d, s, _o, _sub) in tool_rows:
            if s is None or d is None:
                continue
            iv = (int(_to_ms(s)), int(_to_ms(s)) + int(d))
            (task_ivs if _t == 'task' else tool_ivs).append(iv)
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
            # 轮次「调用 N 次」计数与前端轮内工具列表同口径：子代理自己的
            # 调用归嵌套分解（避免一个调用既算父轮又算子代理）
            if c['turnIndex'] is not None and not (
                    c.get('subtaskId') and c['tool'] != 'task'):
                ti = c['turnIndex']
                counts[ti] = counts.get(ti, 0) + 1
        for i, t in enumerate(turn_details):
            t['toolCount'] = counts.get(i, 0)
    if with_detail:
        cov = coverage_split(start_ms, end_ms, model_ivs, sub_ivs,
                             tool_ivs, task_ivs)
    else:
        cov = coverage_split(start_ms, end_ms, model_ivs, sub_ivs)
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
        # 子代理内部轮次（推理↔工具切分的数据源；旧数据无 meta → 空 dict）
        out['subTurns'] = sub_turns
        out['tools'] = tools
        # 四类覆盖明细（含 toolMs）：顶层 modelMs 已是扣除工具后的净值，
        # coverage 与其同源，供端点/前端直接拆包
        out['coverage'] = {'wallMs': wall_ms, 'modelMs': cov['modelMs'],
                           'toolMs': cov['toolMs'],
                           'subagentWaitMs': cov['subagentWaitMs'],
                           'idleMs': cov['idleMs']}
    return out


def load_attempt_metrics(db_ctx, attempt_id: str):
    """单任务全指标（detail，端到端 run 口径），不存在返回 None。db_ctx 可
    注入（测试）。attempt 属于某条 included run（链）→ 返回链合并指标
    （窗口=root→anchor，附带 chain / excludedManualRounds / runAnchor
    AttemptId，attemptId 保持请求值）；孤立 attempt（人工轮直查/无链）→
    单 attempt 视图。kefu → None。"""
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.id, a.session_id, a.source_type, a.source_id, a.status,"
                " a.started_at, a.finished_at, a.requested_model, a.effective_model "
                "FROM ai_execution_attempts a WHERE a.id = %s"
                " AND a.source_type <> 'kefu'", (attempt_id,))
            row_ = cur.fetchone()
            if not row_:
                return None
            cols = [d[0] for d in cur.description]
            row = dict(zip(cols, row_))
            run = next((r for r in resolve_session_runs(
                            cur, [row['session_id']]).get(
                            row['session_id'], [])
                        if any(m['id'] == attempt_id for m in r['members'])),
                       None)
            if run is None:
                return _attempt_metrics(cur, row, with_detail=True)
            m = _attempt_metrics(cur, _run_attempt_row(run), with_detail=True)
            m['attemptId'] = attempt_id      # 身份保持请求值，anchor 另见下
            m['runAnchorAttemptId'] = run['anchor']['id']
            m['chain'] = _run_chain_out(run)
            m['excludedManualRounds'] = run['excludedRounds']
            return m


def list_definition_tasks(db_ctx, kind: str, name: str, limit: int = 50) -> list:
    """定义→任务列表（端到端 run 口径：一行 = 一次端到端执行链，attemptId
    = 链末成员 anchor；证据口径见 _definition_run_set）。"""
    runs = _definition_run_set(db_ctx, kind, name, limit)
    if not runs:
        return []
    out = []
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            for run in runs:
                out.append(_attempt_metrics(cur, _run_attempt_row(run),
                                            with_detail=False))
    return out


def definition_p50(db_ctx, kind: str, name: str) -> int | None:
    """该定义已完结「端到端 run」的墙钟 P50（毫秒）；无已完结 run 返回 None。

    原「单条 SQL 直查 attempt 墙钟」的热路径优化在 run 口径下失效（样本 =
    链），改走统一解析（候选会话 SQL + Python 归并），与任务列表/概览
    同源——P50 必须与 outlier_vs_peers 比较对象同口径。"""
    runs = _definition_run_set(db_ctx, kind, name, 200)
    walls = sorted(_to_ms(r['anchor']['finished_at'])
                   - _to_ms(r['root']['started_at'])
                   for r in runs if r['anchor']['finished_at'] is not None)
    return _percentile(walls, 0.5) if walls else None


def list_slow_tasks(db_ctx, limit: int = 10) -> list:
    """慢任务 Top（端到端 run 口径）：已完结 run 按链墙钟倒序，一行 = 一次
    端到端执行链（attemptId = anchor）。归属：run 内最早 runtime 确认调用
    的 skill 优先，否则（anchor 起倒序首个）effective_agent；两者皆无的
    run 无法诚实归因，不收录。候选池按收口时间倒序封顶（防全库扫描）。"""
    out = []
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute((
                "SELECT a.session_id FROM ai_execution_attempts a"
                " WHERE a.source_type <> 'kefu' AND a.started_at IS NOT NULL"
                "   AND a.finished_at IS NOT NULL"
                "   AND (EXISTS (SELECT 1 FROM ai_skill_invocations i"
                "     WHERE i.attempt_id = a.id AND i.source = 'runtime')"
                "     OR a.effective_agent IS NOT NULL)"
                " GROUP BY a.session_id"
                " ORDER BY MAX(a.finished_at) DESC LIMIT %s"),
                (min(1000, max(limit * 20, 200)),))
            cand = [r[0] for r in cur.fetchall()]
            if not cand:
                return []
            flat = [r for runs in resolve_session_runs(cur, cand).values()
                    for r in runs]
            finished = [r for r in flat
                        if r['anchor']['finished_at'] is not None]
            finished.sort(key=lambda r: (_to_ms(r['anchor']['finished_at'])
                                         - _to_ms(r['root']['started_at'])),
                          reverse=True)
            top = finished[:limit]
            if not top:
                return []
            # 归因：run 成员的 runtime 确认调用（每 attempt 首个），链上
            # 时间序首个命中的 skill 即全链最早调用
            cur.execute(
                "SELECT attempt_id, skill_name FROM ai_skill_invocations"
                " WHERE source = 'runtime' AND attempt_id = ANY(%s)"
                " ORDER BY invoked_at", ([m['id'] for r in top
                                          for m in r['members']],))
            skill_by_attempt: dict = {}
            for aid_, sname in cur.fetchall():
                skill_by_attempt.setdefault(aid_, sname)
            for run in top:
                skill = next((skill_by_attempt[m['id']]
                              for m in run['members']
                              if m['id'] in skill_by_attempt), None)
                if not skill:
                    agent = next((m.get('effective_agent')
                                  for m in reversed(run['members'])
                                  if m.get('effective_agent')), None)
                    if not agent:
                        continue      # 无法诚实归因（同旧口径不收录）
                m = _attempt_metrics(cur, _run_attempt_row(run),
                                     with_detail=False)
                if skill:
                    m['defKind'], m['defName'] = 'skill', skill
                else:
                    m['defKind'], m['defName'] = 'agent', agent
                out.append(m)
    return out


def definition_overview(db_ctx) -> list:
    """定义清单同口径：只列有运行证据的定义（runtime 确认调用的 skill /
    实际执行过的 agent），不再从 injected manifest 反推。"""
    pairs: list[tuple[str, str]] = []
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            cur.execute((
                "SELECT DISTINCT i.skill_name FROM ai_skill_invocations i"
                " JOIN ai_execution_attempts a ON a.id = i.attempt_id"
                " WHERE i.source = 'runtime' AND a.source_type <> 'kefu'"
                " ORDER BY i.skill_name"))
            pairs.extend(('skill', r[0]) for r in cur.fetchall())
            cur.execute((
                "SELECT DISTINCT a.effective_agent FROM ai_execution_attempts a"
                " WHERE a.source_type <> 'kefu' AND a.effective_agent IS NOT NULL"
                " ORDER BY a.effective_agent"))
            pairs.extend(('agent', r[0]) for r in cur.fetchall())
    out = []
    for kind, name in sorted(pairs):
        tasks = [t for t in list_definition_tasks(db_ctx, kind, name, 200)
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


def definition_cause_distribution(db_ctx, kind: str, name: str,
                                  limit: int = 200) -> dict:
    """定义级慢因分布（2026-10-26；2026-11-02 切端到端 run 口径）：已完结
    run 的四段覆盖加总（shares）、逐 run 主导慢因计数（dominantCounts）与
    归属清单（taskCauses，键 = anchor，与任务表行同源——PerfView 点击筛选
    依赖两处 attemptId 一致）。

    窗口 = 链跨度（root.started → anchor.finished，自动恢复计入）。列表
    口径的 modelMs 是毛值（轮内工具未拆出），聚合必须拆准四段——但不逐
    run 走 detail（每 run 2-3 查询过重）：按 session 集合三条批量查询
    （消息/子代理/工具调用），Python 侧按 run 窗口归属过滤（批量 SQL 无法
    逐会话给窗；同会话多 run 时窗口逐 run 键控互斥归属，见 _owning_run），
    逐 run 跑 coverage_split。轮窗锚点/子代理终点截断/工具 occurred_at 夹窗
    均与 _attempt_metrics 同口径。证据口径与任务表一致（skill 只认 runtime
    确认调用）。SQL 字面量内联、全部 %s 参数化。"""
    seg_keys = ('model', 'wait', 'tool', 'idle')
    zero = {'wallMs': 0, 'modelMs': 0, 'toolMs': 0, 'subagentWaitMs': 0,
            'idleMs': 0}
    runs = [r for r in _definition_run_set(db_ctx, kind, name, limit)
            if r['anchor']['finished_at'] is not None]
    if not runs:
        return {'taskCount': 0, 'shares': dict(zero),
                'dominantCounts': {k: 0 for k in seg_keys},
                'taskCauses': []}
    with _open_conn(db_ctx) as conn:
        with conn.cursor() as cur:
            # 每 run 自己的窗口（交互会话逐 send 一条 run，同会话可多条——
            # 窗口归属必须按 run 而非会话）
            by_session: dict = {}
            window_by_aid: dict = {}
            for run in runs:
                aid = run['anchor']['id']
                lo = _to_ms(run['root']['started_at']) or 0
                hi = _to_ms(run['anchor']['finished_at']) or _now_ms()
                window_by_aid[aid] = (lo, hi)
                by_session.setdefault(run['sessionId'], []).append((aid, lo, hi))
            sids = list(by_session)

            def _owning_run(rsid: str, ts: int):
                """时间戳 → 包含它的 run 窗口（窗互斥，至多一个；异常重叠取
                后派发的）。无归属返回 None——该行不进聚合（人工 run 的行
                落在 included 窗外，天然剔除）。"""
                for aid, lo, hi in by_session.get(rsid, []):
                    if lo <= ts <= hi:
                        return aid, lo, hi
                return None

            cur.execute(
                "SELECT session_id, created_at, meta FROM ai_chat_messages"
                " WHERE session_id = ANY(%s) AND role = 'assistant'"
                " AND meta ? 'durationMs'", (sids,))
            model_ivs: dict = {}
            for msid, m_created, m_meta in cur.fetchall():
                meta = m_meta or {}
                cms = _to_ms(m_created)
                if cms is None:
                    continue
                own = _owning_run(msid, cms)
                if own is None:
                    continue
                anchor = _iso_ms(meta['startedAt']) if meta.get('startedAt') else cms
                dur = int(meta['durationMs'])
                model_ivs.setdefault(own[0], []).append((anchor, anchor + dur))
            cur.execute(
                "SELECT root_session_id, created_at, completed_at"
                " FROM ai_chat_subtasks WHERE root_session_id = ANY(%s)"
                " ORDER BY created_at", (sids,))
            sub_ivs: dict = {}
            for rsid, s_created, s_finished in cur.fetchall():
                s_ms = _to_ms(s_created)
                if s_ms is None:
                    continue
                own = _owning_run(rsid, s_ms)
                if own is None:
                    continue
                e_ms = _to_ms(s_finished)
                if e_ms is None:
                    e_ms = own[2]   # 中断残留/旧数据按所属 run 终点截断
                sub_ivs.setdefault(own[0], []).append((s_ms, e_ms))
            cur.execute(
                "SELECT root_session_id, tool, started_at, duration_ms, occurred_at"
                " FROM agent_tool_calls WHERE root_session_id = ANY(%s)"
                " AND started_at IS NOT NULL AND duration_ms IS NOT NULL", (sids,))
            tool_ivs: dict = {}
            task_ivs: dict = {}
            for rsid, t_tool, t_start, t_dur, t_occ in cur.fetchall():
                occ = _to_ms(t_occ)
                if occ is None:
                    continue
                own = _owning_run(rsid, occ)
                if own is None:
                    continue
                s_ms = int(_to_ms(t_start))
                iv = (s_ms, s_ms + int(t_dur))
                bucket = task_ivs if t_tool == 'task' else tool_ivs
                bucket.setdefault(own[0], []).append(iv)
    shares = dict(zero)
    counts = {k: 0 for k in seg_keys}
    causes: list = []
    for run in runs:
        aid = run['anchor']['id']
        lo, hi = window_by_aid[aid]
        cov = coverage_split(lo, hi, model_ivs.get(aid, []),
                             sub_ivs.get(aid, []), tool_ivs.get(aid, []),
                             task_ivs.get(aid, []))
        for k in shares:
            shares[k] += cov[k]
        if cov['wallMs'] > 0:
            seg_ms = {'model': cov['modelMs'], 'wait': cov['subagentWaitMs'],
                      'tool': cov['toolMs'], 'idle': cov['idleMs']}
            dominant = max(seg_ms, key=lambda k: seg_ms[k])
            counts[dominant] += 1
            causes.append({'attemptId': aid, 'dominant': dominant})
    return {'taskCount': len(runs), 'shares': shares,
            'dominantCounts': counts, 'taskCauses': causes}


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


def attempt_payload(db_ctx, attempt_id: str) -> dict | None:
    """perf/attempts 路由的响应装配（两处入口共用：技能拟合优化性能分析页、
    批任务子会话性能报告）。返回 None = attempt 不存在。

    2026-10-15 自 ai_session_admin.perf_attempt 路由抽出（新增批子会话入口，
    避免两处装配逻辑分叉）。响应形状与键名是前端 PerfAttemptDetail 契约。
    """
    m = load_attempt_metrics(db_ctx, attempt_id)
    if not m:
        return None
    cov = m.get('coverage') or {
        'wallMs': m['wallMs'], 'modelMs': m['modelMs'],
        'subagentWaitMs': m['subagentWaitMs'], 'idleMs': m['idleMs'],
        'toolMs': 0}
    # skill 调用耗时（方案 2）：runtime 精确 + 启发式 inferred，纯读推导
    _s = _dt.fromisoformat(m['startedAt']) if m.get('startedAt') else None
    _e = _dt.fromisoformat(m['finishedAt']) if m.get('finishedAt') else None
    skills = derive_skill_durations(
        db_ctx, attempt_id, m['sessionId'],
        int(_s.timestamp() * 1000) if _s else 0,
        int(_e.timestamp() * 1000) if _e else 0)
    return {'attempt': {**{k: v for k, v in m.items()
                           if k not in ('turnDetails', 'subtasks', 'tools',
                                        'coverage', 'subTurns')},
                        'toolMs': cov.get('toolMs', 0)},
            'coverage': cov,
            'turns': m.get('turnDetails') or [],
            'subtasks': m.get('subtasks') or [],
            # subTurns 必须在顶层：前端嵌套分解读 detail.subTurns——
            # 曾只留在 attempt 里，轮次窗模式在生产是死代码（自验证发现）
            'subTurns': m.get('subTurns') or {},
            'tools': m.get('tools') or {},
            'skills': skills,
            'completeness': m.get('completeness')}
