"""管理端 SSE 短时一次性 ticket（进程内）。

EventSource 无法带 Authorization 头，故先经已鉴权接口换 ticket，
再用 ?ticket= 开 SSE。ticket 一次性、TTL 60s，绑定 user_id。
"""
import secrets
import threading
import time

_TTL = 60
_tickets: dict = {}   # ticket -> (user_id, expiry_ts)
_lock = threading.Lock()


def issue(user_id: str) -> str:
    t = secrets.token_urlsafe(24)
    with _lock:
        _tickets[t] = (user_id, time.time() + _TTL)
    return t


def consume(ticket: str):
    """一次性消费：命中且未过期→返回 user_id 并删除；否则 None。顺带清过期项。"""
    now = time.time()
    with _lock:
        for k in [k for k, (_, exp) in _tickets.items() if exp < now]:
            _tickets.pop(k, None)
        item = _tickets.pop(ticket, None)
    if not item:
        return None
    user_id, exp = item
    return user_id if exp >= now else None
