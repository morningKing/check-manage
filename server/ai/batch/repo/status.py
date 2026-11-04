# 批次终态（cancel/计数落定后重算用；'paused' 是非终态中间态）
BATCH_TERMINAL_STATUSES = ('completed', 'partial', 'failed')


def recompute_batch_status_tx(cur, batch_id: str, conn=None,
                              session_id: str | None = None) -> dict:
    """在同一事务/游标里重算批次状态并取回（status + 回调字段）。
    transition_child 用；引擎的 _recompute_batch_status（带回调通知）保持独立。

    P1：批次落终态时在同一事务里追加 batch.status 事件并把回调投入
    delivery outbox（spec §7.1/§7.4）——进程崩溃也不会丢回调。"""
    cur.execute("SELECT done, failed, total FROM ai_chat_batches WHERE id=%s",
                (batch_id,))
    row = cur.fetchone()
    if not row:
        return {}
    done, failed, total = row
    cur.execute(
        "SELECT count(*) FROM ai_chat_sessions WHERE batch_id=%s AND status='paused'",
        (batch_id,))
    paused = cur.fetchone()[0]
    if done + failed == 0 and paused == 0:
        status = 'pending'
    elif done + failed < total and paused > 0:
        status = 'paused'
    elif done + failed < total:
        status = 'running'
    elif failed == total:
        status = 'failed'
    elif done == total:
        status = 'completed'
    else:
        status = 'partial'
    cur.execute(
        "UPDATE ai_chat_batches SET status=%s, "
        "completed_at = CASE WHEN %s = total THEN now() ELSE NULL END "
        "WHERE id=%s RETURNING status, done, failed, total, "
        "callback_url, callback_secret",
        (status, done + failed, batch_id))
    out = dict(zip(('status', 'done', 'failed', 'total',
                    'callback_url', 'callback_secret'), cur.fetchone()))
    if out.get('status') in BATCH_TERMINAL_STATUSES and conn is not None:
        from ai.batch import batch_events as _be
        from ai.batch import delivery_outbox as _outbox
        eid = _be.append_event(
            batch_id, 'batch.status', aggregate_type='batch',
            aggregate_id=batch_id,
            payload={'status': out['status'], 'done': out['done'],
                     'failed': out['failed'], 'total': out['total']},
            conn=conn)
        if out.get('callback_url') and _outbox.outbox_enabled():
            _outbox.enqueue(
                batch_id, target_url=out['callback_url'],
                payload={'event': 'ai_batch_completed', 'batchId': batch_id,
                         'status': out['status'], 'total': out['total'],
                         'done': out['done'], 'failed': out['failed']},
                secret=out.get('callback_secret') or '', event_id=eid,
                conn=conn)
            # H7 回修（10 号 §3.2）：effect 与 outbox 入队同事务登记——
            # conn 传入时 SAVEPOINT 隔离、随外层一起提交/回滚，外层回滚
            # 不再留下孤儿 planned effect
            if session_id:
                from ai.batch import execution_effect as _eff
                _eff.record_effect(session_id, 'callback', f'outbox:{eid}',
                                   batch_id=batch_id, request=eid, conn=conn)
    return out



__all__ = [
    'BATCH_TERMINAL_STATUSES',
    'recompute_batch_status_tx',
]
