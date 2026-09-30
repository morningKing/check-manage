"""子代理（subtask/subagent 委托）数据的查询函数。

供两条持久化路径（chat_persist.py / batch_engine.py，见 Task 4/5）与两个只读
端点（routes/ai_chat.py 的所有权校验版本、routes/ai_batch_admin.py 的管理员
版本）共用——跟 utils/batch_repo.py 里 `api_key_id: str | None = None` 那个既有
模式一致：一个可选的作用域参数决定是否额外校验归属，而不是写成两个互相独立、
容易漏掉某一层校验的函数。
"""
from psycopg2.extras import RealDictCursor

from db import get_db

MAX_SUBTASK_MESSAGES = 500


def get_subtask_messages(subtask_id: str, *, owner_user_id: str | None = None,
                         limit: int = MAX_SUBTASK_MESSAGES) -> dict | None:
    """某个子代理的完整对话（只读）。

    `owner_user_id` 非 None 时，额外校验该子代理归属的顶层会话（沿 root_session_id
    一路追溯，不管嵌套多深）属于该用户——实时聊天端点这样调用。为 None 时不做
    归属校验，调用方必须已经用别的方式鉴权（管理员端点靠 require_permission）。

    按 `seq` 排序（不是 `created_at`）：子代理消息由持久化路径批量写入，同事务
    内 created_at 可能相同，seq 才是可靠的顺序来源。取最近 `limit` 条后反转为
    升序，调用方拿到的始终是自然阅读顺序。
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = "SELECT * FROM ai_chat_subtasks WHERE id = %s"
            params = [subtask_id]
            if owner_user_id is not None:
                sql += (" AND root_session_id IN "
                       "(SELECT id FROM ai_chat_sessions WHERE user_id = %s)")
                params.append(owner_user_id)
            cur.execute(sql, tuple(params))
            subtask = cur.fetchone()
            if not subtask:
                return None

            cur.execute("SELECT count(*) AS n FROM ai_chat_subtask_messages "
                       "WHERE subtask_id = %s", (subtask_id,))
            total = cur.fetchone()['n']
            cur.execute(
                "SELECT id, role, content, created_at, meta FROM ai_chat_subtask_messages "
                " WHERE subtask_id = %s ORDER BY seq DESC LIMIT %s",
                (subtask_id, limit))
            rows = [dict(r) for r in cur.fetchall()]
    rows.reverse()
    return {'subtask': dict(subtask), 'messages': rows,
            'truncated': total > len(rows), 'total': total}
