"""跨连接的平台侧事件扇出（undo-redo spec §2.6）。

撤销/重做/提交是平台 DB 权威变更，其他标签页必须即时收敛——不能依赖 OC
事件（1.x 的 message.removed 覆盖不了平台 user 行——其 id 在 OC 侧无对应；
2.x 连 message.removed 都没有）。SSE 代理每连接注册一个队列到这里，
ai_revert 提交后 broadcast；帧形状与 OC pump 一致，主循环零特判。
"""
import logging
import queue
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_subscribers: dict = {}      # sid -> set[queue.Queue]


def register(sid: str) -> queue.Queue:
    q = queue.Queue()
    with _lock:
        _subscribers.setdefault(sid, set()).add(q)
    return q


def unregister(sid: str, q: queue.Queue) -> None:
    with _lock:
        subs = _subscribers.get(sid)
        if subs is not None:
            subs.discard(q)
            if not subs:
                _subscribers.pop(sid, None)


def broadcast(sid: str, etype: str, payload: dict) -> int:
    frame = {'event': etype, 'data': {'type': etype, 'properties': payload}}
    with _lock:
        targets = list(_subscribers.get(sid, ()))
    n = 0
    for q in targets:
        try:
            q.put_nowait(frame)
            n += 1
        except queue.Full:
            logger.warning('event bus queue full session=%s', sid)
    return n
