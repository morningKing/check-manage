import logging

from db import get_db as _default_get_db

from .record import _fill_subtask_meta, record_messages

log = logging.getLogger(__name__)


def reconcile_task_durations(session_id: str, *, get_db=None,
                             oc_client=None) -> dict:
    """存量对账（2026-10-12）：从 OC REST 重放该会话消息，补齐性能分解
    依赖的三类存量缺口——

    1. task（开子代理）行缺时长/缺子会话链接：时长采集（10-10 迁移）之前
       落账的 task 行 started_at/duration_ms 恒 NULL；且首插时 part 的
       metadata.sessionId 未到、upsert 又不更新 subtask_id——这类行永远
       挂在 NULL 上。时间轴画不出「开子代理」段，嵌套分解也没有归属锚点。
    2. 交互路径 task 行整行缺失：map_part 把 tool:'task' 映成 subtask_use
       占位、提取器曾整类跳过（10-12 修复）——老会话一行都没有，只能重放。
    3. 子代理自身调用（read/bash…）缺时长：同因（10-10 前落账）。逐个已
       登记子代理重放其 REST 消息补；顺带把缺 meta 的子代理消息行补上
       （嵌套分解的轮次窗口）。

    门禁：task 行覆盖全部已登记子代理且无 NULL 字段才短路 clean。OC
    不可达/会话已删返回 skipped。best-effort：失败不抛。"""
    db_ctx = get_db or _default_get_db
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT opencode_session_id, workspace_path FROM ai_chat_sessions "
                "WHERE id = %s", (session_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return {'status': 'skipped', 'reason': 'no oc session'}
            oc_sid, ws = row
            cur.execute(
                "SELECT count(*) FILTER (WHERE started_at IS NULL"
                "                      OR duration_ms IS NULL"
                "                      OR subtask_id IS NULL),"
                "       count(*) FROM agent_tool_calls"
                " WHERE oc_session_id = %s AND tool = 'task'", (oc_sid,))
            incomplete, task_rows = cur.fetchone()
            cur.execute(
                "SELECT id FROM ai_chat_subtasks WHERE root_session_id = %s",
                (session_id,))
            child_ids = [r[0] for r in cur.fetchall()]
            # 落库时刻贴回执行时刻（2026-10-12）：对账补建的行 occurred_at=
            # 对账时刻，落在历史 attempt 的 occurred_at 夹窗之外——性能分解
            # 整体看不到它们；旧批次「委派完成才落账」的长委派行同理。偏差
            # >5 分钟（正常轮询延迟是秒级）即贴回 started_at。放在完好性
            # 判定之前：已治愈（字段齐全）的会话同样可能有本条残留偏差。
            cur.execute(
                "UPDATE agent_tool_calls SET occurred_at = started_at "
                "WHERE root_session_id = %s AND started_at IS NOT NULL "
                "  AND occurred_at > started_at + interval '5 minutes'",
                (session_id,))
            # 完好 = 无缺字段且行数覆盖全部已登记子代理（无委托时为 0/0）
            if incomplete == 0 and task_rows >= len(child_ids):
                return {'status': 'clean'}
    try:
        from utils.runtime import get_runtime
        client = oc_client or get_runtime().get_client()
        msgs = client.get_messages(oc_sid, directory=(ws or '')) or []
        if not msgs:
            return {'status': 'skipped', 'reason': 'oc messages empty'}
        record_messages(oc_sid, msgs, root_session_id=session_id,
                        get_db=get_db)
        # 子代理自身调用行同样存量缺时长（10-10 前落账）——逐个重放补；
        # 单个子会话不可达不拖垮整次对账。
        for child_sid in child_ids:
            try:
                child_msgs = client.get_messages(
                    child_sid, directory=(ws or '')) or []
            except Exception:  # noqa: BLE001
                continue
            if not child_msgs:
                continue
            record_messages(child_sid, child_msgs, root_session_id=session_id,
                            subtask_id=child_sid, get_db=get_db)
            _fill_subtask_meta(db_ctx, child_sid, child_msgs)
        with db_ctx() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM agent_tool_calls "
                    "WHERE oc_session_id = %s AND tool = 'task' "
                    "  AND duration_ms IS NULL", (oc_sid,))
                left = cur.fetchone()[0]
        return {'status': 'ok', 'remainingNull': left}
    except Exception as e:  # noqa: BLE001
        log.warning('reconcile_task_durations failed sid=%s: %s', session_id, e)
        return {'status': 'error', 'error': str(e)[:200]}


def list_tool_calls(root_session_id: str, *, tool: str | None = None,
                    limit: int = 1000, get_db=None) -> list[dict]:
    """树作用域工具调用时间线（根会话 + 全部子代理），供前端"工具调用"卡片。

    occurred_at 是落账时刻（批任务在 worker 持久化时、交互会话在回合收敛
    时），非真实执行时刻；同刻多行按 id 插入序稳定排序。"""
    db_ctx = get_db or _default_get_db
    sql = ("SELECT oc_session_id, subtask_id, agent, tool, args_text, state, "
           "       occurred_at, id "
           "FROM agent_tool_calls WHERE root_session_id = %s")
    params: list = [root_session_id]
    if tool:
        sql += " AND tool = %s"
        params.append(tool)
    sql += " ORDER BY occurred_at ASC NULLS LAST, id ASC LIMIT %s"
    params.append(limit)
    with db_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    out = []
    for (oc, subtask, agent, tl, args, st, at, rid) in rows:
        out.append({
            'id': rid,
            'ocSessionId': oc,
            'subtaskId': subtask,
            'agent': agent,
            'tool': tl,
            'argsPreview': (args or '')[:400] or None,
            'state': st,
            'occurredAt': at.isoformat() if at else None,
        })
    return out



__all__ = [
    '_default_get_db',
    '_fill_subtask_meta',
    'list_tool_calls',
    'log',
    'record_messages',
    'reconcile_task_durations',
]
