"""进程内、按客服 session 分发的极简 pub/sub 事件总线。

单进程部署（waitress/Werkzeug）下，用于把管理端的人工回复/接管/释放事件
实时投递给访客 SSE 连接。事件为"信号"，客户端收到后自行 reload 历史。
"""
import queue
import threading

_subscribers: dict[str, set] = {}   # sid -> set[queue.Queue]
_lock = threading.Lock()
_MAX_Q = 100                        # 每订阅者有界队列，防内存膨胀


def subscribe(sid: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=_MAX_Q)
    with _lock:
        _subscribers.setdefault(sid, set()).add(q)
    return q


def unsubscribe(sid: str, q: queue.Queue) -> None:
    with _lock:
        subs = _subscribers.get(sid)
        if subs:
            subs.discard(q)
            if not subs:
                _subscribers.pop(sid, None)


def publish(sid: str, event: dict) -> None:
    with _lock:
        subs = list(_subscribers.get(sid, ()))   # 快照，锁外投递
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass   # 信号型事件，丢弃无害（下条/重连 reload 兜底）
