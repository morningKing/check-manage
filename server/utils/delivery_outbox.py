"""可靠投递 outbox（ai-harness-p1 spec §5.7/§7.4）。

批次终态提交与 outbox 插入同事务（enqueue 在 batch_repo 的事务里调）；
投递器是独立线程，持 `lease_kind='delivery'` 租约（多进程只有一个投递者）：
- 取 `status IN ('pending','failed') AND next_retry_at <= NOW()`，
  FOR UPDATE SKIP LOCKED；
- 复用 webhook_engine 的 HMAC 签名与 HTTP 逻辑；
- 退避 1s/5s/30s/5m/30m，最多 8 次后 dead_letter；
sending 状态超过 10 分钟未回到终态（进程被 kill）会被重新捞起（M12）；
- 每次投递结果写 ai_batch_events（delivery.sent / delivery.failed）；
- 人工重放：dead_letter → pending（管理端点调 replay）。

开关 `AI_DELIVERY_OUTBOX_ENABLED=0` 回退为旧直接 POST 路径（spec §11.3）。
"""
import logging
import secrets
import threading
import time

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = [1, 5, 30, 300, 1800]
MAX_ATTEMPTS = 8


def outbox_enabled() -> bool:
    import os
    return os.getenv('AI_DELIVERY_OUTBOX_ENABLED', '1').strip() not in ('0', 'false', 'no')


def enqueue(batch_id: str, *, target_url: str, payload: dict,
            secret: str, event_type: str = 'ai_batch_completed',
            event_id: str | None = None, conn=None) -> str | None:
    """投递入队（应在批次终态的同一事务里调用：conn 传入时不开新事务）。
    幂等键 = event_id + target_url（唯一索引去重）。best-effort。"""
    if not target_url:
        return None
    oid = 'obx_' + secrets.token_hex(7)
    eid = event_id or ('bevt_' + secrets.token_hex(7))
    import json as _json
    try:
        if conn is not None:
            _enqueue_cur(conn, oid, eid, batch_id, event_type, target_url,
                         payload, secret, _json)
            return oid
        from db import get_db
        with get_db() as conn2:
            _enqueue_cur(conn2, oid, eid, batch_id, event_type, target_url,
                         payload, secret, _json)
            conn2.commit()
        return oid
    except Exception as e:  # noqa: BLE001
        logger.warning('outbox enqueue failed batch=%s: %s', batch_id, e)
        return None


def _enqueue_cur(conn, oid, eid, batch_id, event_type, target_url, payload,
                 secret, _json):
    # M14：SAVEPOINT 包裹——入队失败不毒化外层终态事务
    with conn.cursor() as cur:
        cur.execute('SAVEPOINT be_obx')
        try:
            _insert(cur, oid, eid, batch_id, event_type, target_url, payload,
                    secret, _json)
            cur.execute('RELEASE SAVEPOINT be_obx')
        except Exception:
            cur.execute('ROLLBACK TO SAVEPOINT be_obx')
            raise


def _insert(cur, oid, eid, batch_id, event_type, target_url, payload,
            secret, _json):
        cur.execute(
            """
            INSERT INTO ai_delivery_outbox
                (id, event_id, batch_id, event_type, target_url, payload,
                 signature, idempotency_key, status, next_retry_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, 'pending', NOW())
            ON CONFLICT (event_id, target_url) DO NOTHING
            """,
            (oid, eid, batch_id, event_type, target_url,
             _json.dumps(payload, ensure_ascii=False, default=str),
             secret or '', f'{eid}:{target_url}'),
        )


def _claim_due(limit: int = 10) -> list[dict]:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, batch_id, event_type, event_id, target_url, "
                "payload::text, signature, attempt_count FROM ai_delivery_outbox "
                "WHERE (status IN ('pending', 'failed') "
                "        AND next_retry_at <= NOW()) "
                "   OR (status = 'sending' "
                "        AND next_retry_at < NOW() - interval '10 minutes') "
                "ORDER BY next_retry_at LIMIT %s "
                "FOR UPDATE SKIP LOCKED",
                (limit,),
            )
            rows = [dict(zip(('id', 'batch_id', 'event_type', 'event_id',
                              'target_url', 'payload', 'signature',
                              'attempt_count'), r))
                    for r in cur.fetchall()]
            for r in rows:
                # M12 回修：claim 时把 next_retry_at 锚到 NOW()+10min——
                # sending 回收窗口从 claim 起算，而非上次到期时间
                cur.execute("UPDATE ai_delivery_outbox SET status = 'sending', "
                            "  next_retry_at = NOW() + interval '10 minutes' "
                            "WHERE id = %s", (r['id'],))
        conn.commit()
    return rows


def _settle(oid: str, batch_id: str, ok: bool, attempt_count: int,
            error: str | None, event_ref: str | None):
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            if ok:
                cur.execute(
                    "UPDATE ai_delivery_outbox SET status='delivered', "
                    "  delivered_at=NOW(), last_error=NULL WHERE id=%s",
                    (oid,))
            else:
                dead = attempt_count >= MAX_ATTEMPTS
                backoff = BACKOFF_SECONDS[min(attempt_count,
                                              len(BACKOFF_SECONDS) - 1)]
                cur.execute(
                    "UPDATE ai_delivery_outbox "
                    "SET status = %s, attempt_count = %s, last_error = %s, "
                    "    next_retry_at = NOW() + (%s || ' seconds')::interval "
                    "WHERE id = %s",
                    ('dead_letter' if dead else 'failed', attempt_count,
                     (error or '')[:500], str(backoff), oid),
                )
        conn.commit()
    # H7 回修：投递终态同步 settle callback effect（committed/failed）——
    # event_ref 传的是 outbox 行的 event_id，effect 幂等键即 'outbox:<eid>'
    try:
        from utils import execution_effect
        if event_ref:
            execution_effect.settle_effect_by_key(
                'callback', f'outbox:{event_ref}',
                'committed' if ok else 'failed',
                external_ref=oid)
    except Exception:  # noqa: BLE001
        pass
    # 投递结果进事件流（管理面可观测；失败时也写，外部可感知）
    from utils import batch_events
    batch_events.append_event(
        batch_id, 'delivery.sent' if ok else 'delivery.failed',
        aggregate_type='delivery', aggregate_id=oid,
        payload={'targetUrl': _redact(None), 'attempt': attempt_count,
                 'error': (error or '')[:200], 'eventId': event_ref})


def _redact(url):
    return None  # 事件里不回显完整目标 URL（避免签名/凭据类信息入事件流）


def deliver_one(row: dict) -> bool:
    """投递单行（复用 webhook_engine 的 HMAC 与 HTTP）。"""
    import json as _json
    from utils.webhook_engine import _fire_single_webhook
    try:
        _fire_single_webhook(
            rule_id=f'outbox-{row["id"]}', rule_name='AI批任务完成回调(outbox)',
            webhook_url=row['target_url'], secret=row['signature'] or '',
            event_type=row['event_type'],
            payload=_json.loads(row['payload']) if isinstance(row['payload'], str)
            else row['payload'],
            timeout=30, retries=0,  # 重试由 outbox 自己的退避管理
        )
        return True
    except Exception as e:  # noqa: BLE001 —— 投递失败进退避
        logger.warning('outbox deliver failed id=%s: %s', row['id'], e)
        return False


def drain_due_once() -> int:
    """投递一轮到期任务，返回投递成功的条数（测试与投递线程共用）。"""
    rows = _claim_due()
    ok_count = 0
    for r in rows:
        ok = deliver_one(r)
        ok_count += 1 if ok else 0
        _settle(r['id'], r['batch_id'], ok, r['attempt_count'] + 1,
                None if ok else 'delivery failed', r['event_id'])
    return ok_count


def replay_dead_letter(oid: str) -> bool:
    """人工重放：dead_letter/failed → pending。"""
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_delivery_outbox SET status='pending', "
                        "  next_retry_at=NOW(), attempt_count=0 "
                        "WHERE id=%s AND status IN ('failed','dead_letter')",
                        (oid,))
            ok = cur.rowcount > 0
        conn.commit()
    return ok


def list_outbox(batch_id: str, limit: int = 50) -> list[dict]:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, event_id, event_type, target_url, status, "
                "attempt_count, next_retry_at, last_error, delivered_at, "
                "created_at FROM ai_delivery_outbox WHERE batch_id = %s "
                "ORDER BY created_at DESC LIMIT %s", (batch_id, limit))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        for k in ('next_retry_at', 'delivered_at', 'created_at'):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
    return rows


def list_outbox_by_id(oid: str) -> dict | None:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, event_id, batch_id, event_type, target_url, "
                "status, attempt_count, next_retry_at, last_error, "
                "delivered_at, created_at FROM ai_delivery_outbox "
                "WHERE id = %s", (oid,))
            row = cur.fetchone()
    if not row:
        return None
    cols = ('id', 'eventId', 'batchId', 'eventType', 'targetUrl', 'status',
            'attemptCount', 'nextRetryAt', 'lastError', 'deliveredAt',
            'createdAt')
    out = dict(zip(cols, row))
    for k in ('nextRetryAt', 'deliveredAt', 'createdAt'):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out


class OutboxDeliveryLoop:
    """独立投递线程（持 delivery 租约）。随 app 启动；stop() 供测试。"""

    POLL_SEC = 5.0

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner: str | None = None
        self._holds_lease = False

    # 复核报告 §4.4：抢占失败进入后台重试（旧租约过期即接管），
    # 快速重启 TTL 窗口内不再永久放弃投递器
    RETRY_SEC = 5.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        from utils import execution_lease
        self._owner = execution_lease.owner_id()
        ok, _ = execution_lease.acquire('delivery', self._owner,
                                        lease_kind='delivery')
        if not ok:
            logger.warning('outbox delivery lease NOT acquired; retrying '
                           'every %ss until the lease becomes available',
                           self.RETRY_SEC)
            self._holds_lease = False
            retry = threading.Thread(target=self._acquire_retry, daemon=True,
                                     name='outbox-delivery-lease-retry')
            retry.start()
            return
        self._holds_lease = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='outbox-delivery')
        self._thread.start()

    def _acquire_retry(self):
        from utils import execution_lease
        while not self._stop.is_set():
            if self._stop.wait(self.RETRY_SEC):
                return
            ok, _ = execution_lease.acquire('delivery', self._owner,
                                            lease_kind='delivery')
            if not ok:
                continue
            logger.info('outbox delivery lease acquired after retry; '
                        'starting delivery loop')
            self._holds_lease = True
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name='outbox-delivery')
            self._thread.start()
            return

    def _loop(self):
        from utils import execution_lease
        logger.info('outbox delivery loop started')
        while not self._stop.is_set():
            try:
                drain_due_once()
            except Exception:  # noqa: BLE001
                logger.exception('outbox delivery tick failed')
            if not execution_lease.heartbeat('delivery', self._owner):
                logger.warning('outbox delivery lease lost; exiting loop')
                self._holds_lease = False
                break
            self._stop.wait(self.POLL_SEC)
        logger.info('outbox delivery loop exited')

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        if self._holds_lease:
            from utils import execution_lease
            execution_lease.release('delivery', self._owner)
            self._holds_lease = False


_LOOP: OutboxDeliveryLoop | None = None


def start_delivery_loop():
    global _LOOP
    if not outbox_enabled():
        logger.info('outbox disabled (AI_DELIVERY_OUTBOX_ENABLED=0); '
                    'callbacks use legacy direct POST')
        return None
    if _LOOP is None:
        _LOOP = OutboxDeliveryLoop()
    _LOOP.start()
    return _LOOP
