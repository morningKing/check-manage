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



__all__ = [
    'annotations',
    '_open_conn',
    'resolve_session_runs',
    '_run_attempt_row',
]
