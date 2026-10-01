# -*- coding: utf-8 -*-
"""存量会话工具调用回填（2026-09-29）。

「工具调用」面板读 agent_tool_calls 账本，但账本从动作门禁功能上线才
开始落账——上线之前的存量消息（ai_chat_messages / ai_chat_subtask_messages）
里的工具调用不在账本里，老会话面板显示"暂无记录"。

本迁移把存量消息里的 tool_use 片段按账本形状补录：
- 幂等键 (oc_session_id, part_id)，ON CONFLICT DO NOTHING——与实时落账
  共存，实时行优先，脚本可重跑；
- part_id = f"{message_id}:{序号}"（同 part 多轮持久化按序号稳定）；
- state 取 part.status（pending/running/completed/error），缺省 completed
  （存量消息都是已结束回合的持久化）；
- 分批处理，避免大表长事务。

随启动幂等执行；重跑只补增量（新产生的未落账消息），代价为一次全表
消息扫描。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

BATCH = 500


def _args_to_text(inp) -> str:
    """与 agent_ledger.args_to_text 同口径（独立实现避免跨模块 import 开销）。"""
    import json as _json
    if inp is None:
        return ''
    if isinstance(inp, str):
        return inp
    if isinstance(inp, dict):
        parts = []
        for k, v in inp.items():
            if isinstance(v, str):
                parts.append(f'{k}={v}')
            else:
                try:
                    parts.append(f'{k}={_json.dumps(v, ensure_ascii=False)}')
                except (TypeError, ValueError):
                    parts.append(f'{k}={v}')
        return '\n'.join(parts)
    return str(inp)


def _parse_parts(message_id: str, content) -> list[tuple]:
    """存储消息 content（list）→ [(part_id, tool, args_text, state)]。"""
    out = []
    if not isinstance(content, list):
        return out
    for i, p in enumerate(content):
        if not isinstance(p, dict):
            continue
        if p.get('type') not in ('tool', 'tool_use'):
            continue
        tool = p.get('tool') or p.get('name')
        if not tool:
            continue
        state = p.get('status') or p.get('state')
        if isinstance(state, dict):
            state = state.get('status')
        out.append((
            f'{message_id}:{i}', str(tool),
            _args_to_text(p.get('input'))[:8192],
            str(state or 'completed')[:20],
        ))
    return out


def _backfill_stream(cur, select_sql, tag) -> int:
    """按 id 游标分批拉消息 → 解析 → 幂等补录。行必须带别名
    (mid, oc, root, subtask, agent, content)。返回补录 part 数。"""
    total = 0
    last_id = ''
    while True:
        cur.execute(select_sql + ' AND m.id > %s ORDER BY m.id LIMIT %s',
                    (last_id, BATCH))
        rows = cur.fetchall()
        if not rows:
            break
        insert_rows = []
        for (mid, oc, root, subtask, agent, content) in rows:
            last_id = mid
            for (pid, tool, args, st) in _parse_parts(mid, content):
                insert_rows.append((oc, root, subtask, agent, pid, tool, args, st))
        if insert_rows:
            from psycopg2.extras import execute_values
            execute_values(cur, """
                INSERT INTO agent_tool_calls
                    (oc_session_id, root_session_id, subtask_id, agent,
                     part_id, tool, args_text, state)
                VALUES %s
                ON CONFLICT (oc_session_id, part_id) DO NOTHING
            """, insert_rows)
            total += cur.rowcount
    print(f'  {tag}: backfilled {total} tool calls')
    return total


def run():
    with get_db() as conn:
        cur = conn.cursor()
        # 数据源 A：平台会话消息（root=平台会话 id，oc=当前绑定的 OpenCode 会话；
        # 换代过的历史消息统一记到现 oc——root 才是面板查询键）
        _backfill_stream(cur, """
            SELECT m.id AS mid,
                   COALESCE(s.opencode_session_id, m.session_id) AS oc,
                   m.session_id AS root,
                   NULL AS subtask,
                   NULL AS agent,
                   m.content
            FROM ai_chat_messages m
            JOIN ai_chat_sessions s ON s.id = m.session_id
            WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(m.content) p
                          WHERE p->>'type' IN ('tool', 'tool_use'))
        """, 'sessions')
        # 数据源 B：子代理消息（subtask_id 即子代理 OC 会话 id；root/agent 来自
        # ai_chat_subtasks——agent_tool_calls.subtask_id 的外键目标）
        _backfill_stream(cur, """
            SELECT m.id AS mid,
                   m.subtask_id AS oc,
                   st.root_session_id AS root,
                   m.subtask_id AS subtask,
                   st.agent AS agent,
                   m.content
            FROM ai_chat_subtask_messages m
            JOIN ai_chat_subtasks st ON st.id = m.subtask_id
            WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(m.content) p
                          WHERE p->>'type' IN ('tool', 'tool_use'))
        """, 'subtasks')
        conn.commit()
    print('tool-calls backfill ready.')


if __name__ == '__main__':
    run()
