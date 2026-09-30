"""控制命令平面（ai-harness-p1 spec §4.4/§5.5）。

所有控制意图（内部 UI、外部 API）统一落 ai_execution_commands：
- `Idempotency-Key`（或派生的自然键）唯一——重复提交返回既有命令，不重复执行；
- 命令的**应用**仍走 P0 的 CAS 状态转移（batch_repo.*）；应用结果
  （applied/rejected + result_snapshot）回写命令行，形成完整命令历史。

与 spec 的差异（实现取舍，已在 spec 附录说明）：内部/外部路由同步应用命令
（保持既有 HTTP 语义：pause 立即生效），不引入异步 worker 消费——命令表
承担幂等与审计，异步化留给需要时再开。
"""
import json
import logging
import secrets

logger = logging.getLogger(__name__)

COMMAND_TYPES = ('pause', 'resume', 'cancel', 'retry', 'continue',
                 'reexecute', 'force_stop')


def submit_command(command_type: str, *, batch_id: str | None,
                   session_id: str | None = None,
                   requested_by: str | None = None,
                   requested_by_kind: str = 'user',
                   payload: dict | None = None,
                   expected_generation: int | None = None,
                   idempotency_key: str | None = None) -> dict:
    """登记命令（幂等）。返回 {'command', 'duplicate': bool}。

    idempotency_key 缺省时由 (type, batch, session, requester) 派生自然键——
    同一发起人对同一对象的同类命令天然幂等；显式传入（对外 API 要求）
    优先。"""
    if command_type not in COMMAND_TYPES:
        raise ValueError(f'unsupported command type: {command_type}')
    from db import get_db
    key = idempotency_key or (
        f'{command_type}:{batch_id or "-"}:{session_id or "-"}:'
        f'{requested_by or "-"}')
    cid = 'cmd_' + secrets.token_hex(7)
    with get_db() as conn:
        with conn.cursor() as cur:
            # 幂等：同键已存在 → 返回既有命令（不重复执行）
            cur.execute(
                "SELECT id FROM ai_execution_commands WHERE idempotency_key = %s",
                (key[:200],))
            row = cur.fetchone()
            if row:
                conn.commit()
                return {'id': row[0], 'status': 'accepted', 'duplicate': True}
            cur.execute(
                """
                INSERT INTO ai_execution_commands
                    (id, idempotency_key, batch_id, session_id, command_type,
                     requested_by, requested_by_kind, payload,
                     expected_generation, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'accepted')
                """,
                (cid, key[:200], batch_id, session_id, command_type,
                 requested_by, requested_by_kind,
                 json.dumps(payload or {}, ensure_ascii=False),
                 expected_generation),
            )
        conn.commit()
    return {'id': cid, 'status': 'accepted', 'duplicate': False}


def finish_command(command_id: str, status: str, *,
                   result_snapshot: dict | None = None,
                   error_code: str | None = None) -> None:
    """命令应用结果回写（applied | rejected | expired | failed）。best-effort。"""
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_execution_commands SET status = %s, "
                    "  result_snapshot = %s::jsonb, error_code = %s, "
                    "  applied_at = NOW() "
                    "WHERE id = %s AND status = 'accepted'",
                    (status,
                     json.dumps(result_snapshot or {}, ensure_ascii=False,
                                default=str),
                     error_code, command_id),
                )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning('command finish failed id=%s: %s', command_id, e)


def get_command(command_id: str) -> dict | None:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, idempotency_key, batch_id, session_id, "
                "command_type, requested_by, requested_by_kind, payload, "
                "status, result_snapshot, error_code, created_at, applied_at "
                "FROM ai_execution_commands WHERE id = %s", (command_id,))
            row = cur.fetchone()
    if not row:
        return None
    cols = ('id', 'idempotencyKey', 'batchId', 'sessionId', 'commandType',
            'requestedBy', 'requestedByKind', 'payload', 'status',
            'resultSnapshot', 'errorCode', 'createdAt', 'appliedAt')
    out = dict(zip(cols, row))
    for k in ('createdAt', 'appliedAt'):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out
