"""副作用账本（ai-harness-p1 spec §4.3/§5.4）。

不承诺 LLM 请求 exactly-once；承诺**业务副作用**按 effect key 幂等：
- 重复 (session_id, effect_type, idempotency_key) 只会有一行；
- `unknown`（超时/连接中断等不确定结局）禁止自动重放 → 恢复决策把它
  分流到 needs_review；
- `committed` 后的重试直接复用（调用方先 record → 执行 → settle）。

用法：
    eff = record_effect(sid, 'callback', key, batch_id=bid)
    if eff and eff['status'] == 'committed': 复用既有结果，不重做
    ... 执行副作用 ...
    settle_effect(eff['id'], 'committed', external_ref=...)
"""
import hashlib
import logging
import secrets

logger = logging.getLogger(__name__)


def record_effect(session_id: str, effect_type: str, idempotency_key: str, *,
                  batch_id: str | None = None,
                  attempt_id: str | None = None,
                  step_key: str | None = None,
                  request: str | None = None,
                  conn=None) -> dict | None:
    """登记（或复用）一个 effect。返回行 dict（含当前 status）或 None（失败）。

    `conn` 传入时在调用方事务内登记（SAVEPOINT 隔离，失败只回滚 effect
    本身）——effect 与 outbox 入队同生共死，不留孤儿 planned（10 号 §3.2）；
    不传时自开短事务（兼容旧调用方）。"""
    eid = 'eff_' + secrets.token_hex(6)
    request_hash = hashlib.sha256(request.encode('utf-8', 'replace')).hexdigest() \
        if request else None

    def _run(cur):
        cur.execute(
            """
            INSERT INTO ai_execution_effects
                (id, session_id, attempt_id, batch_id, step_key,
                 effect_type, idempotency_key, request_hash, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'planned')
            ON CONFLICT (session_id, effect_type, idempotency_key)
            DO UPDATE SET id = ai_execution_effects.id
            RETURNING id, status, external_ref, result_hash
            """,
            (eid, session_id, attempt_id, batch_id, step_key,
             effect_type, idempotency_key[:200], request_hash),
        )
        row = cur.fetchone()
        return {'id': row[0], 'status': row[1], 'external_ref': row[2],
                'result_hash': row[3]}

    if conn is not None:
        cur = conn.cursor()
        try:
            cur.execute('SAVEPOINT eff_rec')
            out = _run(cur)
            cur.execute('RELEASE SAVEPOINT eff_rec')
            return out
        except Exception as e:  # noqa: BLE001
            try:
                cur.execute('ROLLBACK TO SAVEPOINT eff_rec')
            except Exception:  # noqa: BLE001
                pass
            logger.warning('effect record failed sid=%s key=%s: %s',
                           session_id, idempotency_key, e)
            return None
    try:
        from db import get_db
        with get_db() as conn2:
            with conn2.cursor() as cur:
                out = _run(cur)
            conn2.commit()
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning('effect record failed sid=%s key=%s: %s',
                       session_id, idempotency_key, e)
        return None


def settle_effect(effect_id: str, status: str, *,
                  external_ref: str | None = None,
                  result_hash: str | None = None) -> bool:
    """planned/started → 终态；failed/unknown → committed（后续退避重试
    成功或人工重放成功的正向收口，10 号 §3.2）。幂等：committed 后不再改。"""
    if status not in ('committed', 'failed', 'unknown', 'compensated'):
        raise ValueError(f'invalid effect status: {status}')
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_execution_effects SET status = %s, "
                    "  external_ref = COALESCE(%s, external_ref), "
                    "  result_hash = COALESCE(%s, result_hash), "
                    "  committed_at = CASE WHEN %s = 'committed' "
                    "                      THEN now() ELSE committed_at END "
                    "WHERE id = %s "
                    "  AND (status IN ('planned', 'started') "
                    "       OR (status IN ('failed', 'unknown') "
                    "           AND %s = 'committed'))",
                    (status, external_ref, result_hash, status, effect_id,
                     status),
                )
                ok = cur.rowcount > 0
            conn.commit()
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning('effect settle failed id=%s: %s', effect_id, e)
        return False


def settle_effect_by_key(effect_type: str, idempotency_key: str, status: str, *,
                         external_ref: str | None = None) -> bool:
    """按 (effect_type, idempotency_key) 定位并 settle（H7：投递器无 effect id
    场景）。找不到行（effect 未登记，如旧数据）返回 False。"""
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_execution_effects "
                    "WHERE effect_type = %s AND idempotency_key = %s",
                    (effect_type, idempotency_key[:200]),
                )
                row = cur.fetchone()
        if not row:
            return False
        return settle_effect(row[0], status, external_ref=external_ref)
    except Exception as e:  # noqa: BLE001
        logger.warning('effect settle_by_key failed key=%s: %s', idempotency_key, e)
        return False


def has_unknown_effects(session_id: str) -> bool:
    """恢复决策用：存在 unknown 结局的副作用时禁止自动重放（spec §4.2）。"""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ai_execution_effects "
                "WHERE session_id = %s AND status = 'unknown' LIMIT 1",
                (session_id,),
            )
            return cur.fetchone() is not None
