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

补偿（C4）：
    compensate_effect(eff_id)  # mcp_write POST 创建的记录 → 删除，effect → compensated
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
                  external_ref: str | None = None,
                  conn=None) -> dict | None:
    """登记（或复用）一个 effect。返回行 dict（含当前 status）或 None（失败）。

    `external_ref` 在登记时即写入（C4 补偿依据：存储回退所需的外部请求
    信息，如 mcp_write 的 method/path/body）；settle 时的 COALESCE 只在其
    为 NULL 的旧行上补写，不会覆盖登记值。
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
                 effect_type, idempotency_key, request_hash, external_ref,
                 status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'planned')
            ON CONFLICT (session_id, effect_type, idempotency_key)
            DO UPDATE SET id = ai_execution_effects.id
            RETURNING id, status, external_ref, result_hash
            """,
            (eid, session_id, attempt_id, batch_id, step_key,
             effect_type, idempotency_key[:200], request_hash, external_ref),
        )
        row = cur.fetchone()
        return {'id': row[0], 'status': row[1], 'external_ref': row[2],
                'result_hash': row[3]}

    if conn is not None:
        with conn.cursor() as cur:
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
    或人工重放成功的正向收口）；unknown → failed（后续拿到确定性否定，
    12 号 §4.3：避免首次超时即粘滞 unknown、永久阻断自动恢复）。
    committed 粘性：终态不可再改。"""
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
                    "           AND %s = 'committed') "
                    "       OR (status = 'unknown' AND %s = 'failed'))",
                    (status, external_ref, result_hash, status, effect_id,
                     status, status),
                )
                ok = cur.rowcount > 0
            conn.commit()
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning('effect settle failed id=%s: %s', effect_id, e)
        return False


def settle_effect_by_key(effect_type: str, idempotency_key: str, status: str, *,
                         external_ref: str | None = None,
                         session_id: str | None = None) -> bool:
    """按 (effect_type, idempotency_key) 定位并 settle（H7：投递器无 effect id
    场景）。`session_id` 传入时定位同时限定会话——effect 幂等键本身是
    (session_id, effect_type, idempotency_key)，不带 session 的查找在跨会话
    key 碰撞时会 settle 到别的会话的行。找不到行（effect 未登记，如旧数据、
    session 不匹配）返回 False。投递器无 session 上下文，保持 None。"""
    try:
        from db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM ai_execution_effects "
                    "WHERE effect_type = %s AND idempotency_key = %s"
                    + (" AND session_id = %s" if session_id else ""),
                    (effect_type, idempotency_key[:200], session_id)
                    if session_id
                    else (effect_type, idempotency_key[:200]),
                )
                row = cur.fetchone()
        if not row:
            return False
        return settle_effect(row[0], status, external_ref=external_ref)
    except Exception as e:  # noqa: BLE001
        logger.warning('effect settle_by_key failed key=%s: %s', idempotency_key, e)
        return False


def compensate_effect(effect_id: str, conn=None) -> dict | None:
    """C4：补偿一个 effect（仅支持 mcp_write POST 创建的记录 → 删除）。

    流程：
      1. 读取 effect 行（status 必须 committed，external_ref 含 method/path/body）
      2. 解析 external_ref JSON → 取 method/path/body
      3. 仅 method=POST 且 path 匹配 /{collection} 格式时补偿
         （PUT 旧值未知无法回滚、DELETE 无逆操作、/menus 等非 dynamic_data
         集合不可删）——其余一律跳过返回 None
      4. 从 body 中取记录 id（body['id']，POST 创建时由调用方提供）
      5. 数据库层直接 DELETE dynamic_data 行（effect 只关心"这条记录不再
         存在"，无需走 Flask test client 的完整转发链）
      6. settle 原 effect 为 compensated（独立收口路径：committed 粘性不让
         settle_effect 改终态，补偿在此单独开洞且仅 committed → compensated）
      7. 返回补偿结果 dict；不满足条件/失败返回 None

    `unknown` 状态禁止自动补偿（对齐"禁自动重放"语义）。
    `conn` 传入时在调用方事务内执行（SAVEPOINT 隔离）；不传时自开短事务。
    """
    import json as _json
    import re as _re

    def _run(cur):
        cur.execute(
            "SELECT status, effect_type, external_ref "
            "FROM ai_execution_effects WHERE id = %s", (effect_id,))
        row = cur.fetchone()
        if not row:
            return None
        status, effect_type, external_ref = row
        # 仅 committed 可补偿；unknown/planned/failed 一律不动
        if status != 'committed' or effect_type != 'mcp_write':
            return None
        try:
            ref = _json.loads(external_ref) \
                if isinstance(external_ref, str) else None
        except Exception:
            ref = None
        if not isinstance(ref, dict):
            return None
        method = (ref.get('method') or '').upper().strip()
        path = (ref.get('path') or '').strip()
        body = ref.get('body')
        if method != 'POST':
            return None
        m = _re.match(r'^/([A-Za-z0-9][A-Za-z0-9_-]*)$', path)
        if not m:
            return None
        collection = m.group(1)
        if collection == 'menus':
            return None  # 数据菜单不走 dynamic_data，无行可删
        try:
            from routes.dynamic import RESERVED
            if collection in RESERVED:
                return None
        except Exception:
            pass  # 路由不可导入时放行——转发白名单已在写入侧挡过保留集合
        if not isinstance(body, dict) or not body.get('id'):
            return None  # 无 id 无法定位，跳过（autoSequence 类创建不自动补偿）
        record_id = str(body['id'])
        cur.execute(
            "DELETE FROM dynamic_data WHERE id = %s AND collection = %s",
            (record_id, collection))
        deleted = cur.rowcount > 0
        # 独立收口：不走 settle_effect 状态机（committed 终态粘性），
        # 条件带 status='committed' 防并发双补偿
        cur.execute(
            "UPDATE ai_execution_effects SET status = 'compensated' "
            "WHERE id = %s AND status = 'committed'", (effect_id,))
        if cur.rowcount == 0:
            return None  # 并发下已被补偿/状态已变
        return {'id': effect_id, 'compensated': True,
                'collection': collection, 'record_id': record_id,
                'deleted': deleted}

    if conn is not None:
        with conn.cursor() as cur:
            try:
                cur.execute('SAVEPOINT eff_comp')
                out = _run(cur)
                cur.execute('RELEASE SAVEPOINT eff_comp')
                return out
            except Exception as e:  # noqa: BLE001
                try:
                    cur.execute('ROLLBACK TO SAVEPOINT eff_comp')
                except Exception:  # noqa: BLE001
                    pass
                logger.warning('effect compensate failed id=%s: %s',
                               effect_id, e)
                return None
    try:
        from db import get_db
        with get_db() as conn2:
            with conn2.cursor() as cur:
                out = _run(cur)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning('effect compensate failed id=%s: %s', effect_id, e)
        return None


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
