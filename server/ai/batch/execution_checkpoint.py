"""执行 checkpoint（ai-harness-p1 spec §5.3）。

子任务/回合级进度快照：重启后知道"执行到哪、哪些 effect 已提交"。
不解决跨小时业务 DAG 的中间步骤跳过（P2）。

写入时机：派发前（dispatch）、回合收敛（turn_complete）、恢复动作后
（recovery）。`is_latest` 唯一部分索引保证每会话至多一份最新快照——写入
是"旧行 is_latest=false + 新行"同一事务完成。
"""
import json
import logging
import secrets

logger = logging.getLogger(__name__)


def write_checkpoint(session_id: str, *, checkpoint_type: str,
                     attempt_id: str | None = None,
                     execution_generation: int = 0,
                     opencode_session_id: str | None = None,
                     message_seq: int | None = None,
                     workspace_manifest_hash: str | None = None,
                     completed_effect_ids: list | None = None,
                     artifact_refs: list | None = None,
                     context_snapshot: dict | None = None) -> str | None:
    """写一份新 checkpoint 并把旧 latest 置 false（同一事务）。best-effort。"""
    cid = 'ckpt_' + secrets.token_hex(6)

    def _impl() -> str:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_execution_checkpoints SET is_latest = FALSE "
                    "WHERE session_id = %s AND is_latest", (session_id,))
                cur.execute(
                    """
                    INSERT INTO ai_execution_checkpoints
                        (id, session_id, attempt_id, execution_generation,
                         checkpoint_type, message_seq, opencode_session_id,
                         workspace_manifest_hash, completed_effect_ids,
                         artifact_refs, context_snapshot, is_latest)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                            %s::jsonb, %s::jsonb, TRUE)
                    """,
                    (cid, session_id, attempt_id, execution_generation,
                     checkpoint_type, message_seq, opencode_session_id,
                     workspace_manifest_hash,
                     json.dumps(completed_effect_ids or []),
                     json.dumps(artifact_refs or []),
                     json.dumps(context_snapshot or {}, ensure_ascii=False,
                                default=str)),
                )
            conn.commit()
        return cid

    try:
        return _impl()
    except Exception as e:  # noqa: BLE001 —— checkpoint 失败不阻断执行
        logger.warning('checkpoint write failed sid=%s: %s', session_id, e)
        return None


def latest_checkpoint(session_id: str) -> dict | None:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, attempt_id, execution_generation, checkpoint_type, "
                "message_seq, opencode_session_id, completed_effect_ids, "
                "artifact_refs, context_snapshot, created_at "
                "FROM ai_execution_checkpoints "
                "WHERE session_id = %s AND is_latest", (session_id,))
            row = cur.fetchone()
    if not row:
        return None
    cols = ('id', 'attempt_id', 'execution_generation', 'checkpoint_type',
            'message_seq', 'opencode_session_id', 'completed_effect_ids',
            'artifact_refs', 'context_snapshot', 'created_at')
    return dict(zip(cols, row))
