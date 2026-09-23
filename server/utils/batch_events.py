"""批次事件流（ai-harness-p1 spec §5.6/§7）。

事实源是 ai_batch_events 表；worker 不再"顺带"承担推送职责（SSE 端点与
对外 events 读取都查表）。

seq 分配（P1-5 根因修复）：禁止无锁 MAX+1。这里用事务级 advisory lock
（pg_advisory_xact_lock(hashtext(batch_id))）串行化同一批次的事件追加——
并发写同一批次时后到者等待，seq 严格单调、无冲突无丢失；不同批次互不阻塞。
对 ai_execution_events 的 per-attempt seq 修复见 execution_audit.record_event。
"""
import json
import logging
import secrets

logger = logging.getLogger(__name__)


def append_event(batch_id: str, event_type: str, *,
                 aggregate_type: str = 'batch',
                 aggregate_id: str | None = None,
                 execution_generation: int | None = None,
                 payload: dict | None = None,
                 event_id: str | None = None,
                 conn=None) -> str | None:
    """追加一条批次事件（原子 seq）。best-effort：失败记日志不抛。

    `conn` 传入时在调用方事务内追加（与状态写入同事务提交，spec §7.1）；
    不传时自行开短事务。返回 event_id（失败 None）。
    """
    eid = event_id or ('bevt_' + secrets.token_hex(7))
    payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)

    def _run(cur):
        # 同批次串行化：advisory xact lock 在事务结束时自动释放
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (batch_id,))
        cur.execute(
            """
            INSERT INTO ai_batch_events
                (batch_id, event_seq, event_id, event_type, aggregate_type,
                 aggregate_id, execution_generation, payload)
            SELECT %s, COALESCE(MAX(event_seq), 0) + 1, %s, %s, %s, %s, %s,
                   %s::jsonb
              FROM ai_batch_events WHERE batch_id = %s
            """,
            (batch_id, eid, event_type, aggregate_type, aggregate_id,
             execution_generation, payload_json, batch_id),
        )
        return eid

    if conn is not None:
        try:
            with conn.cursor() as cur:
                return _run(cur)
        except Exception as e:  # noqa: BLE001 —— 事件绝不打断业务
            logger.warning('batch event append failed batch=%s: %s', batch_id, e)
            return None
    from db import get_db
    try:
        with get_db() as conn2:
            with conn2.cursor() as cur:
                out = _run(cur)
            conn2.commit()
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning('batch event append failed batch=%s: %s', batch_id, e)
        return None


def read_events(batch_id: str, *, after_seq: int = 0, limit: int = 200) -> list[dict]:
    """按 afterSeq 增量读取（返回 event_seq > after_seq 的事件，升序）。"""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT event_seq, event_id, event_type, aggregate_type, "
                "aggregate_id, execution_generation, payload, created_at "
                "FROM ai_batch_events WHERE batch_id = %s AND event_seq > %s "
                "ORDER BY event_seq LIMIT %s",
                (batch_id, after_seq, limit),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        if r.get('created_at') is not None:
            r['createdAt'] = r.pop('created_at').isoformat()
    return rows


def latest_seq(batch_id: str) -> int:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(event_seq), 0) FROM ai_batch_events "
                        "WHERE batch_id = %s", (batch_id,))
            return int(cur.fetchone()[0])
